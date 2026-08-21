"""Build multi-layer preview payloads for the canvas UI."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from app.models import Bounds, DrillHit, LayerPreview, PreviewResponse
from app.services import parser, zip_ingest

logger = logging.getLogger(__name__)

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


def build_preview(job_id: str, dpmm: int = 35) -> PreviewResponse:
    root = zip_ingest.job_dir(job_id)
    files = zip_ingest.list_cam_files(job_id)
    warnings: list[str] = []
    layers: list[LayerPreview] = []
    all_bounds: list[parser.GerberBounds] = []
    drills: list[DrillHit] = []

    for meta in files:
        kind = meta["kind"]
        path = root / meta["path"]
        if kind == "drill":
            try:
                hits = parser.parse_excellon(path)
                drills.extend(
                    DrillHit(x=h.x, y=h.y, diameter=h.diameter, tool=h.tool) for h in hits
                )
                color, _, visible = LAYER_COLORS["drill"]
                layers.append(
                    LayerPreview(
                        name=meta["name"],
                        kind="drill",
                        color=color,
                        visible_default=visible,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Failed to parse drill {meta['name']}: {exc}")
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

    # Expand overall bounds with drill hits
    merged = _merge_bounds(all_bounds)
    if drills:
        xs = [d.x for d in drills]
        ys = [d.y for d in drills]
        drill_bounds = parser.GerberBounds(
            min_x=min(xs), min_y=min(ys), max_x=max(xs), max_y=max(ys)
        )
        merged = _merge_bounds([b for b in [merged, drill_bounds] if b])

    return PreviewResponse(
        job_id=job_id,
        layers=layers,
        drills=drills,
        bounds=_bounds_model(merged) if merged else None,
        files=files,
        warnings=warnings,
    )


def preview_summary(job_id: str) -> dict[str, Any]:
    files = zip_ingest.list_cam_files(job_id)
    return {"job_id": job_id, "file_count": len(files), "files": files}
