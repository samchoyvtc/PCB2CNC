"""Build multi-layer preview payloads for the canvas UI."""

from __future__ import annotations

import base64
import json
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from app.models import (
    BomItem,
    Bounds,
    DrillHit,
    DrillToolSummary,
    LayerPreview,
    PreviewResponse,
)
from app.services import parser, zip_ingest

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, str], None]

LAYER_COLORS: dict[str, tuple[str, tuple[int, int, int, int], bool]] = {
    # kind: (css_hex, rgba, visible_default)
    "copper_top": ("#EF4444", (239, 68, 68, 230), True),
    "copper_bottom": ("#2563EB", (37, 99, 235, 200), False),
    "profile": ("#94A3B8", (148, 163, 184, 220), True),
    "soldermask_top": ("#D4A017", (212, 160, 23, 180), False),
    "soldermask_bottom": ("#8B6914", (139, 105, 20, 190), False),
    "silkscreen_top": ("#D1D5DB", (209, 213, 219, 210), False),
    "silkscreen_bottom": ("#4B5563", (75, 85, 99, 200), False),
    "solderpaste_top": ("#C4B5FD", (196, 181, 253, 190), False),
    "solderpaste_bottom": ("#6B21A8", (107, 33, 168, 180), False),
    "gerber_other": ("#60A5FA", (96, 165, 250, 160), False),
    "drill": ("#F97316", (249, 115, 22, 255), True),
}

_preview_lock = threading.Lock()
_preview_threads: dict[str, threading.Thread] = {}


def _bounds_model(b: parser.GerberBounds) -> Bounds:
    return Bounds(
        min_x=b.min_x,
        min_y=b.min_y,
        max_x=b.max_x,
        max_y=b.max_y,
        width=b.width,
        height=b.height,
    )


def _merge_bounds(bounds_list: list[parser.GerberBounds]) -> parser.GerberBounds | None:
    if not bounds_list:
        return None
    return parser.GerberBounds(
        min_x=min(b.min_x for b in bounds_list),
        min_y=min(b.min_y for b in bounds_list),
        max_x=max(b.max_x for b in bounds_list),
        max_y=max(b.max_y for b in bounds_list),
    )


def _summarize_tools(hits: list[parser.DrillHit]) -> list[DrillToolSummary]:
    counts: dict[tuple[str, float], int] = defaultdict(int)
    for h in hits:
        key = (h.tool, round(float(h.diameter), 3))
        counts[key] += 1
    out = [
        DrillToolSummary(tool=tool, diameter=dia, count=count)
        for (tool, dia), count in counts.items()
    ]
    out.sort(key=lambda t: (t.diameter, t.tool))
    return out


def _progress_path(job_id: str):
    return zip_ingest.job_dir(job_id) / "preview_progress.json"


def _result_path(job_id: str):
    return zip_ingest.job_dir(job_id) / "preview_result.json"


def _atomic_write_text(path, text: str) -> None:
    """Write via temp file + replace to avoid empty-file race on concurrent reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_preview_progress(
    job_id: str,
    *,
    state: str,
    current: int = 0,
    total: int = 0,
    message: str = "",
    error: str | None = None,
) -> None:
    percent = int(round((current / total) * 100)) if total else (100 if state == "done" else 0)
    payload = {
        "job_id": job_id,
        "state": state,
        "current": current,
        "total": total,
        "percent": min(100, max(0, percent)),
        "message": message,
        "error": error,
    }
    _atomic_write_text(_progress_path(job_id), json.dumps(payload))


def read_preview_progress(job_id: str) -> dict[str, Any]:
    path = _progress_path(job_id)
    idle = {
        "job_id": job_id,
        "state": "idle",
        "current": 0,
        "total": 0,
        "percent": 0,
        "message": "",
        "error": None,
        "result": None,
    }
    if not path.exists():
        return idle

    data = None
    for _ in range(8):
        try:
            raw = path.read_text()
            if not raw.strip():
                continue
            data = json.loads(raw)
            break
        except (OSError, json.JSONDecodeError):
            continue
    if data is None:
        return idle

    if data.get("state") == "done":
        result_path = _result_path(job_id)
        if result_path.exists():
            for _ in range(8):
                try:
                    raw = result_path.read_text()
                    if not raw.strip():
                        continue
                    data["result"] = json.loads(raw)
                    break
                except (OSError, json.JSONDecodeError):
                    continue
            else:
                data["result"] = None
        else:
            data["result"] = None
    else:
        data["result"] = None
    return data


def build_preview(
    job_id: str,
    dpmm: int = 35,
    progress_cb: ProgressCb | None = None,
) -> PreviewResponse:
    root = zip_ingest.job_dir(job_id)
    files = zip_ingest.list_cam_files(job_id)
    warnings: list[str] = []
    layers: list[LayerPreview] = []
    all_bounds: list[parser.GerberBounds] = []
    drills: list[DrillHit] = []
    bom: list[BomItem] = []
    bom_source: str | None = None

    total = max(len(files), 1)

    def report(i: int, label: str) -> None:
        if progress_cb:
            progress_cb(i, total, label)

    report(0, "Starting preview…")

    for index, meta in enumerate(files, start=1):
        kind = meta["kind"]
        path = root / meta["path"]
        report(index - 1, f"Processing {meta['name']}…")

        if kind == "bom":
            try:
                items = parser.parse_bom_partlist(path)
                if not bom_source:
                    bom_source = meta["name"]
                bom.extend(
                    BomItem(
                        qty=i.qty,
                        value=i.value,
                        device=i.device,
                        package=i.package,
                        parts=i.parts,
                        description=i.description,
                    )
                    for i in items
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse BOM {meta['name']}: {exc}")
            report(index, f"Parsed BOM {meta['name']}")
            continue

        if kind == "drill":
            try:
                hits = parser.parse_excellon(path)
                file_hits = [
                    parser.DrillHit(
                        x=h.x,
                        y=h.y,
                        diameter=h.diameter,
                        tool=h.tool,
                        source=meta["name"],
                    )
                    for h in hits
                ]
                drills.extend(
                    DrillHit(
                        x=h.x,
                        y=h.y,
                        diameter=h.diameter,
                        tool=h.tool,
                        source=meta["name"],
                    )
                    for h in file_hits
                )
                color, _, visible = LAYER_COLORS["drill"]
                layers.append(
                    LayerPreview(
                        name=meta["name"],
                        kind="drill",
                        color=color,
                        visible_default=visible,
                        drill_tools=_summarize_tools(file_hits),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse drill {meta['name']}: {exc}")
            report(index, f"Parsed drill {meta['name']}")
            continue

        color_hex, rgba, visible = LAYER_COLORS.get(
            kind, LAYER_COLORS["gerber_other"]
        )
        try:
            bounds = parser.parse_gerber_bounds(path)
            all_bounds.append(bounds)
            png = parser.render_gerber_png(path, rgba=rgba, dpmm=dpmm)
            layers.append(
                LayerPreview(
                    name=meta["name"],
                    kind=kind,
                    color=color_hex,
                    visible_default=visible,
                    image_png_base64=base64.b64encode(png).decode("ascii"),
                    bounds=_bounds_model(bounds),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gerber preview failed for %s", meta["name"])
            layers.append(
                LayerPreview(
                    name=meta["name"],
                    kind=kind,
                    color=color_hex,
                    visible_default=False,
                    error=str(exc),
                )
            )
            warnings.append(f"Failed to preview {meta['name']}: {exc}")
        report(index, f"Rendered {meta['name']}")

    merged = _merge_bounds(all_bounds)
    if drills:
        xs = [d.x for d in drills]
        ys = [d.y for d in drills]
        drill_bounds = parser.GerberBounds(
            min_x=min(xs), min_y=min(ys), max_x=max(xs), max_y=max(ys)
        )
        merged = _merge_bounds([b for b in [merged, drill_bounds] if b])

    result = PreviewResponse(
        job_id=job_id,
        layers=layers,
        drills=drills,
        bom=bom,
        bom_source=bom_source,
        bounds=_bounds_model(merged) if merged else None,
        files=files,
        warnings=warnings,
    )
    report(total, "Preview complete")
    return result


def start_preview_job(job_id: str) -> dict[str, Any]:
    """Kick off background preview generation with progress updates."""
    zip_ingest.list_cam_files(job_id)

    with _preview_lock:
        existing = _preview_threads.get(job_id)
        if existing and existing.is_alive():
            return read_preview_progress(job_id)

        write_preview_progress(
            job_id,
            state="running",
            current=0,
            total=max(len(zip_ingest.list_cam_files(job_id)), 1),
            message="Starting preview…",
        )

        def runner() -> None:
            try:
                def cb(current: int, total: int, message: str) -> None:
                    write_preview_progress(
                        job_id,
                        state="running",
                        current=current,
                        total=total,
                        message=message,
                    )

                result = build_preview(job_id, progress_cb=cb)
                _atomic_write_text(_result_path(job_id), result.model_dump_json())
                write_preview_progress(
                    job_id,
                    state="done",
                    current=1,
                    total=1,
                    message="Preview complete",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Async preview failed for %s", job_id)
                write_preview_progress(
                    job_id,
                    state="error",
                    current=0,
                    total=1,
                    message="Preview failed",
                    error=str(exc),
                )

        thread = threading.Thread(target=runner, daemon=True, name=f"preview-{job_id}")
        _preview_threads[job_id] = thread
        thread.start()

    return read_preview_progress(job_id)


def preview_summary(job_id: str) -> dict[str, Any]:
    files = zip_ingest.list_cam_files(job_id)
    return {"job_id": job_id, "file_count": len(files), "files": files}
