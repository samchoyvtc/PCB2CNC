"""CNC toolpath generation and verification graphics.

Uses pcb2gcode when available; otherwise a built-in contour + drill generator.
"""

from __future__ import annotations

import base64
import logging
import math
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.models import GeneratePlan, MachineSettings
from app.services import parser, tool_library, zip_ingest
from app.services.postprocess import (
    merge_nc_files,
    write_drill_nc,
    write_drill_nc_grouped,
    write_path_nc,
)

logger = logging.getLogger(__name__)


def _cam_by_name(job_id: str, name: str) -> Path:
    for meta in zip_ingest.list_cam_files(job_id):
        if meta["name"] == name:
            return zip_ingest.job_dir(job_id) / meta["path"]
    raise FileNotFoundError(f"CAM file not found: {name}")


def _settings_for_tool(settings: MachineSettings, tool_number: int) -> MachineSettings:
    row = tool_library.cuts_for_tool_number(tool_number)
    updated = settings.model_copy()
    updated.tool_number = tool_number
    if not row:
        return updated
    spindle = row.get("Spindle Speed")
    feed = row.get("Feed Rate")
    plunge = row.get("Plunge Rate")
    step_over = row.get("Step Over")
    step_down = row.get("Step Down")
    coolant = row.get("Coolant")
    if spindle is not None:
        updated.spindle_rpm = int(spindle)
    if feed is not None:
        updated.feed_mm_min = float(feed)
    if plunge is not None:
        updated.plunge_mm_min = float(plunge)
    if step_over is not None:
        updated.step_over_percent = float(step_over)
    if step_down is not None:
        updated.step_down_mm = float(step_down)
    if coolant is not None:
        updated.coolant = str(coolant).strip().upper() in {"Y", "YES", "TRUE", "1", "ON"}
    return updated


def _default_drill_tool(diameter: float, tools: list[dict[str, Any]]) -> int:
    candidates: list[tuple[float, int]] = []
    for row in tools:
        try:
            number = int(row.get("Number") or 0)
            tip = float(row.get("Tip Diameter(F)") or row.get("Diameter(D)") or 0)
        except (TypeError, ValueError):
            continue
        if number < 1 or tip <= 0:
            continue
        candidates.append((tip, number))
    if not candidates:
        return 4
    ge = [(tip, n) for tip, n in candidates if tip + 1e-6 >= diameter]
    pool = ge or candidates
    tip, number = min(pool, key=lambda item: (abs(item[0] - diameter), item[1]))
    return number


def _split_contour_with_tabs(
    pts: list[tuple[float, float]],
    tab_count: int,
    tab_width_mm: float,
) -> list[list[tuple[float, float]]]:
    """Leave holding-tab gaps along a closed outline contour."""
    if len(pts) < 2 or tab_count <= 0 or tab_width_mm <= 0:
        return [pts]
    work = list(pts)
    if work[0] != work[-1]:
        work.append(work[0])
    dists = [0.0]
    for i in range(1, len(work)):
        dists.append(
            dists[-1]
            + math.hypot(work[i][0] - work[i - 1][0], work[i][1] - work[i - 1][1])
        )
    total = dists[-1]
    if total <= tab_width_mm * tab_count * 2:
        return [work]
    centers = [(i + 0.5) * total / tab_count for i in range(tab_count)]
    gaps = [((c - tab_width_mm / 2) % total, (c + tab_width_mm / 2) % total) for c in centers]

    def _mod(s: float) -> float:
        if s >= total - 1e-12 or s < 0:
            return 0.0
        return s

    def in_gap(s: float) -> bool:
        s = _mod(s)
        for a, b in gaps:
            if a <= b and a < s < b:
                return True
            if a > b and (s > a or s < b):
                return True
        return False

    def point_at(s: float) -> tuple[float, float]:
        s = max(0.0, min(total, s))
        for i in range(1, len(work)):
            if dists[i] >= s - 1e-9:
                span = dists[i] - dists[i - 1]
                t = 0.0 if span <= 1e-12 else (s - dists[i - 1]) / span
                x = work[i - 1][0] + (work[i][0] - work[i - 1][0]) * t
                y = work[i - 1][1] + (work[i][1] - work[i - 1][1]) * t
                return (x, y)
        return work[-1]

    events: list[tuple[float, tuple[float, float]]] = []
    seen: set[float] = set()

    def add_event(s: float, pt: tuple[float, float] | None = None) -> None:
        key = round(s, 6)
        if key in seen:
            return
        seen.add(key)
        events.append((s, pt if pt is not None else point_at(s)))

    for i, pt in enumerate(work):
        add_event(dists[i], pt)
    for a, b in gaps:
        add_event(a)
        add_event(b)
    events.sort(key=lambda item: item[0])

    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for i, (dist, pt) in enumerate(events):
        if not current:
            current = [pt]
            continue
        mid = (events[i - 1][0] + dist) / 2.0
        if in_gap(mid):
            if len(current) >= 2:
                paths.append(current)
            current = [pt]
            continue
        if pt != current[-1]:
            current.append(pt)
    if len(current) >= 2:
        paths.append(current)
    if len(paths) >= 2 and not in_gap(0.0):
        first, last = paths[0], paths[-1]
        merged = last + first[1:] if last[-1] == first[0] else last + first
        paths = [merged, *paths[1:-1]]
    return paths or [work]


def _path_length(pts: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        for i in range(1, len(pts))
    )


def _cam_by_kind(job_id: str) -> dict[str, Path]:
    root = zip_ingest.job_dir(job_id)
    out: dict[str, Path] = {}
    for meta in zip_ingest.list_cam_files(job_id):
        kind = meta["kind"]
        if kind not in out:
            out[kind] = root / meta["path"]
    return out


def _pcb2gcode_available() -> bool:
    return shutil.which("pcb2gcode") is not None


def _run_pcb2gcode(
    job_id: str,
    settings: MachineSettings,
    files: dict[str, Path],
    out_dir: Path,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pcb2gcode",
        f"--front={files['copper_top']}",
        f"--zwork=-{settings.engraving_depth_mm}",
        f"--zsafe={settings.safe_z_mm}",
        f"--zchange={settings.safe_z_mm}",
        f"--mill-feed={settings.feed_mm_min}",
        f"--mill-speed={settings.spindle_rpm}",
        f"--drill-feed={settings.plunge_mm_min}",
        f"--drill-speed={settings.spindle_rpm}",
        f"--zdrill=-{settings.drill_depth_mm}",
        f"--outdir={out_dir}",
    ]
    if "drill" in files:
        cmd.append(f"--drill={files['drill']}")
    if "profile" in files:
        cmd.append(f"--outline={files['profile']}")
        cmd.append(f"--cutter-diameter=1.0")
        cmd.append(f"--zcut=-{settings.drill_depth_mm}")

    logger.info("Running pcb2gcode: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pcb2gcode failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )

    produced = sorted(p.name for p in out_dir.glob("*.ngc")) + sorted(
        p.name for p in out_dir.glob("*.nc")
    )
    # Normalize names
    mapping = {
        "front.ngc": "isolation.nc",
        "front.nc": "isolation.nc",
        "drill.ngc": "drill.nc",
        "outline.ngc": "outline.nc",
    }
    result = []
    for name in produced:
        src = out_dir / name
        dest_name = mapping.get(name, name if name.endswith(".nc") else name.replace(".ngc", ".nc"))
        dest = out_dir / dest_name
        if src != dest:
            src.replace(dest)
        result.append(dest_name)
    return result


def _contours_from_gerber(
    path: Path,
    *,
    dpmm: int = 50,
    offset_px: int = 2,
) -> tuple[list[list[tuple[float, float]]], parser.GerberBounds]:
    """Extract mm-space contours from a Gerber raster (optionally dilated)."""
    bounds = parser.parse_gerber_bounds(path)
    png = parser.render_gerber_png(
        path, rgba=(255, 255, 255, 255), dpmm=dpmm
    )
    img = Image.open(BytesIO(png)).convert("L")
    arr = np.array(img)
    _, binary = cv2.threshold(arr, 10, 255, cv2.THRESH_BINARY)
    if offset_px > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (offset_px * 2 + 1, offset_px * 2 + 1)
        )
        binary = cv2.dilate(binary, kernel, iterations=1)

    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h = arr.shape[0]
    paths: list[list[tuple[float, float]]] = []
    min_area = (dpmm * 0.2) ** 2
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue
        pts: list[tuple[float, float]] = []
        for p in cnt[:: max(1, len(cnt) // 400)]:  # downsample long contours
            px, py = float(p[0][0]), float(p[0][1])
            x_mm = bounds.min_x + px / dpmm
            y_mm = bounds.max_y - py / dpmm  # image Y down → board Y up
            pts.append((x_mm, y_mm))
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            paths.append(pts)
    return paths, bounds


def _builtin_generate(
    job_id: str,
    settings: MachineSettings,
    files: dict[str, Path],
    out_dir: Path,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[str] = []

    if "copper_top" in files:
        paths, _ = _contours_from_gerber(files["copper_top"], offset_px=3)
        isolation = out_dir / "isolation.nc"
        write_path_nc(
            isolation,
            paths,
            settings=settings,
            operation="Isolation Engraving",
            tool_number=settings.tool_number,
            depth_mm=settings.engraving_depth_mm,
            include_header=True,
            step_down_mm=settings.step_down_mm,
        )
        produced.append("isolation.nc")

    if "drill" in files:
        hits = parser.parse_excellon(files["drill"])
        drill_path = out_dir / "drill.nc"
        write_drill_nc(
            drill_path,
            hits,
            settings=settings,
            depth_mm=settings.drill_depth_mm,
            tool_number=4,
            include_header=True,
        )
        produced.append("drill.nc")

    if "profile" in files:
        paths, _ = _contours_from_gerber(files["profile"], offset_px=0)
        # Prefer the longest contour as board outline
        paths = sorted(paths, key=lambda p: len(p), reverse=True)[:3]
        outline = out_dir / "outline.nc"
        write_path_nc(
            outline,
            paths,
            settings=settings,
            operation="Board Outline",
            tool_number=4,
            depth_mm=settings.drill_depth_mm,
            include_header=True,
            step_down_mm=0.4,
        )
        produced.append("outline.nc")

    if not produced:
        raise RuntimeError("No copper/drill/profile inputs available for generation")

    merge_nc_files(
        [out_dir / name for name in produced],
        out_dir / "all.nc",
        settings=settings,
    )
    produced.append("all.nc")
    return produced


def _plan_generate(
    job_id: str,
    settings: MachineSettings,
    plan: GeneratePlan,
    out_dir: Path,
    *,
    merge: bool = True,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[str] = []
    tool_rows = tool_library.load_tool_library().get("tools") or []

    if plan.copper:
        copper_file = _cam_by_name(job_id, plan.copper.layer)
        copper_settings = _settings_for_tool(settings, plan.copper.tool_number)
        paths, _ = _contours_from_gerber(copper_file, offset_px=3)
        write_path_nc(
            out_dir / "isolation.nc",
            paths,
            settings=copper_settings,
            operation="Isolation Engraving",
            tool_number=plan.copper.tool_number,
            depth_mm=settings.engraving_depth_mm,
            include_header=True,
            step_down_mm=copper_settings.step_down_mm,
        )
        produced.append("isolation.nc")

    drill_ops = [op for op in plan.drills if op.layers]
    for index, drill_op in enumerate(drill_ops):
        hits: list[parser.DrillHit] = []
        for layer in drill_op.layers:
            hits.extend(parser.parse_excellon(_cam_by_name(job_id, layer)))
        size_to_tool = {
            round(float(item.diameter_mm), 3): int(item.tool_number)
            for item in drill_op.size_map
        }
        grouped: dict[tuple[int, float], list[parser.DrillHit]] = {}
        for hit in hits:
            diameter = round(float(hit.diameter), 3)
            tool_number = size_to_tool.get(diameter)
            if tool_number is None and size_to_tool:
                nearest = min(size_to_tool, key=lambda value: abs(value - diameter))
                tool_number = size_to_tool[nearest]
            if tool_number is None:
                tool_number = _default_drill_tool(diameter, tool_rows)
            grouped.setdefault((tool_number, diameter), []).append(hit)
        groups = [
            (tool_number, _settings_for_tool(settings, tool_number), group_hits, diameter)
            for (tool_number, diameter), group_hits in sorted(
                grouped.items(), key=lambda item: (item[0][1], item[0][0])
            )
        ]
        name = "drill.nc" if len(drill_ops) == 1 else f"drill-{index + 1}.nc"
        write_drill_nc_grouped(
            out_dir / name,
            groups,
            settings=settings,
            depth_mm=settings.drill_depth_mm,
        )
        produced.append(name)

    if plan.outline:
        outline_file = _cam_by_name(job_id, plan.outline.layer)
        outline_settings = _settings_for_tool(settings, plan.outline.tool_number)
        paths, _ = _contours_from_gerber(outline_file, offset_px=0)
        if not paths:
            raise ValueError(f"No outline path found in {plan.outline.layer}")
        longest = max(paths, key=_path_length)
        segments = _split_contour_with_tabs(
            longest,
            plan.outline.tab_count,
            plan.outline.tab_width_mm,
        )
        write_path_nc(
            out_dir / "outline.nc",
            segments,
            settings=outline_settings,
            operation="Board Outline",
            tool_number=plan.outline.tool_number,
            depth_mm=settings.drill_depth_mm,
            include_header=True,
            step_down_mm=outline_settings.step_down_mm,
            close_open_paths=False,
        )
        produced.append("outline.nc")

    if not produced:
        raise ValueError("Generate plan has no copper, drill, or outline operations")

    if merge:
        merge_nc_files(
            [out_dir / name for name in produced],
            out_dir / "all.nc",
            settings=settings,
        )
        produced.append("all.nc")
    return produced


def generate_toolpaths(
    job_id: str,
    settings: MachineSettings | None = None,
    plan: GeneratePlan | None = None,
) -> dict[str, Any]:
    settings = settings or MachineSettings()
    if settings.engraving_depth_mm > settings.drill_depth_mm:
        raise ValueError("Copper engrave depth exceeds drill depth")
    settings.spindle_rpm = max(1000, min(settings.spindle_rpm, 60000))
    settings.feed_mm_min = max(50.0, min(settings.feed_mm_min, 10000.0))
    settings.plunge_mm_min = max(20.0, min(settings.plunge_mm_min, 3000.0))

    files = _cam_by_kind(job_id)
    out_dir = zip_ingest.job_dir(job_id) / "nc"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    if plan is not None:
        produced = _plan_generate(job_id, settings, plan, out_dir)
        engine = "builtin"
    else:
        if "copper_top" not in files:
            raise ValueError("Job is missing copper_top Gerber")
        engine = "pcb2gcode" if _pcb2gcode_available() else "builtin"
        try:
            if engine == "pcb2gcode":
                produced = _run_pcb2gcode(job_id, settings, files, out_dir)
                if "all.nc" not in produced:
                    parts = [
                        out_dir / n
                        for n in ("isolation.nc", "drill.nc", "outline.nc")
                        if (out_dir / n).exists()
                    ]
                    if parts:
                        merge_nc_files(parts, out_dir / "all.nc", settings=settings)
                        produced.append("all.nc")
            else:
                produced = _builtin_generate(job_id, settings, files, out_dir)
        except Exception:
            if engine == "pcb2gcode":
                logger.exception("pcb2gcode failed; falling back to builtin generator")
                produced = _builtin_generate(job_id, settings, files, out_dir)
                engine = "builtin"
            else:
                raise

    preview_b64 = render_toolpath_preview(job_id)
    return {
        "job_id": job_id,
        "files": produced,
        "engine": engine,
        "toolpath_preview_png_base64": preview_b64,
    }


def preview_operation(
    job_id: str,
    settings: MachineSettings | None = None,
    plan: GeneratePlan | None = None,
) -> dict[str, Any]:
    """Build one operation into nc-preview/ and return a PNG of that path."""
    if plan is None:
        raise ValueError("Preview requires a generate plan")
    settings = settings or MachineSettings()
    settings.spindle_rpm = max(1000, min(settings.spindle_rpm, 60000))
    settings.feed_mm_min = max(50.0, min(settings.feed_mm_min, 10000.0))
    settings.plunge_mm_min = max(20.0, min(settings.plunge_mm_min, 3000.0))

    out_dir = zip_ingest.job_dir(job_id) / "nc-preview"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    produced = _plan_generate(job_id, settings, plan, out_dir, merge=False)
    return {
        "job_id": job_id,
        "files": produced,
        "paths": extract_preview_paths(out_dir),
        "image_png_base64": render_toolpath_preview(job_id, out_dir=out_dir),
    }


def extract_preview_paths(out_dir: Path) -> list[dict[str, Any]]:
    """Return cut polylines and drill hits from generated NC files."""
    import re

    def xy_hits(nc_text: str) -> list[list[float]]:
        pts: list[list[float]] = []
        seen: set[tuple[float, float]] = set()
        x = y = None
        for line in nc_text.splitlines():
            s = line.strip().upper()
            if not s or s.startswith(";") or s.startswith("("):
                continue
            mx = re.search(r"X([+-]?\d*\.?\d+)", s)
            my = re.search(r"Y([+-]?\d*\.?\d+)", s)
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            if not mx and not my:
                continue
            if x is None or y is None:
                continue
            key = (round(x, 4), round(y, 4))
            if key in seen:
                continue
            seen.add(key)
            pts.append([x, y])
        return pts

    out: list[dict[str, Any]] = []
    for path in sorted(out_dir.glob("*.nc")):
        if path.name == "all.nc":
            continue
        text = path.read_text(errors="replace")
        if path.name.startswith("drill"):
            hits = xy_hits(text)
            if hits:
                out.append({"file": path.name, "kind": "drill", "points": hits})
            continue
        for poly in parse_gcode_paths(text):
            if len(poly) < 2:
                continue
            out.append(
                {
                    "file": path.name,
                    "kind": "cut",
                    "points": [[float(x), float(y)] for x, y in poly],
                }
            )
    return out


def parse_gcode_paths(nc_text: str) -> list[list[tuple[float, float]]]:
    """Extract rapid/feed XY polylines from G-code for plotting."""
    import re

    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    for line in nc_text.splitlines():
        s = line.strip().upper()
        if not s or s.startswith("(") or s.startswith(";"):
            continue
        if s.startswith("G0") or s.startswith("G00"):
            if len(current) >= 2:
                paths.append(current)
            current = []
            mx = re.search(r"X([+-]?\d*\.?\d+)", s)
            my = re.search(r"Y([+-]?\d*\.?\d+)", s)
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            current = [(x, y)]
            continue
        if s.startswith("G1") or s.startswith("G01") or "X" in s or "Y" in s:
            if not (s.startswith("G1") or s.startswith("G01") or s.startswith("G0")):
                # bare coordinate words on continuation — treat as feed if we have current
                if not current:
                    continue
            mx = re.search(r"X([+-]?\d*\.?\d+)", s)
            my = re.search(r"Y([+-]?\d*\.?\d+)", s)
            if not mx and not my:
                continue
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            if not current:
                current = [(x, y)]
            else:
                current.append((x, y))
    if len(current) >= 2:
        paths.append(current)
    return paths


def render_toolpath_preview(
    job_id: str,
    size: int = 900,
    out_dir: Path | None = None,
) -> str:
    """Render a PNG verification graphic of generated NC toolpaths."""
    out_dir = out_dir or (zip_ingest.job_dir(job_id) / "nc")
    if not out_dir.exists():
        raise FileNotFoundError("No generated NC files for this job")

    colors = {
        "isolation.nc": (245, 166, 35, 255),
        "drill.nc": (239, 68, 68, 255),
        "outline.nc": (148, 163, 184, 255),
        "all.nc": (96, 165, 250, 120),
    }
    drill_palette = [
        (239, 68, 68, 255),
        (251, 146, 60, 255),
        (244, 63, 94, 255),
        (249, 115, 22, 255),
    ]

    all_pts: list[tuple[float, float]] = []
    op_paths: dict[str, list[list[tuple[float, float]]]] = {}
    names = [
        path.name
        for path in sorted(out_dir.glob("*.nc"))
        if path.name != "all.nc"
    ]
    drill_index = 0
    for name in names:
        path = out_dir / name
        paths = parse_gcode_paths(path.read_text(errors="replace"))
        op_paths[name] = paths
        if name.startswith("drill") and name not in colors:
            colors[name] = drill_palette[drill_index % len(drill_palette)]
            drill_index += 1
        for poly in paths:
            all_pts.extend(poly)

    if not all_pts:
        # fallback empty image
        img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 0.05 * max(max_x - min_x, max_y - min_y, 1.0)
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    bw = max_x - min_x
    bh = max_y - min_y
    scale = (size - 40) / max(bw, bh)

    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)

    def to_px(pt: tuple[float, float]) -> tuple[float, float]:
        x = 20 + (pt[0] - min_x) * scale
        y = size - 20 - (pt[1] - min_y) * scale
        return x, y

    for name, paths in op_paths.items():
        color = colors.get(name, (255, 255, 255, 255))
        for poly in paths:
            if len(poly) < 2:
                continue
            px = [to_px(p) for p in poly]
            if name.startswith("drill"):
                for p in px:
                    r = 3
                    draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)
            else:
                draw.line(px, fill=color, width=2)

    # Legend
    legend_y = 12
    for name, color in colors.items():
        if name == "all.nc" or name not in op_paths:
            continue
        draw.rectangle((12, legend_y, 28, legend_y + 12), fill=color)
        draw.text((34, legend_y - 1), name.replace(".nc", ""), fill=(226, 232, 240, 255))
        legend_y += 18

    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def list_nc_files(job_id: str) -> list[str]:
    out_dir = zip_ingest.job_dir(job_id) / "nc"
    if not out_dir.exists():
        return []
    return sorted(p.name for p in out_dir.glob("*.nc"))


def nc_file_path(job_id: str, filename: str) -> Path:
    path = (zip_ingest.job_dir(job_id) / "nc" / filename).resolve()
    root = (zip_ingest.job_dir(job_id) / "nc").resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise FileNotFoundError(filename)
    return path
