"""CNC toolpath generation and verification graphics.

Uses pcb2gcode when available; otherwise a built-in contour + drill generator.
"""

from __future__ import annotations

import base64
import logging
import math
import re
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
    tip, number = min(
        candidates,
        key=lambda item: (abs(item[0] - diameter), -item[0], item[1]),
    )
    return number


def _default_hole_strategy(hole_dia: float, tool_number: int) -> str:
    return "pocket" if float(hole_dia) > _tool_tip_mm(tool_number) + 0.05 else "drill"


def _closed_contour(
    pts: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[float], float]:
    work = list(pts)
    if len(work) < 2:
        return work, [0.0], 0.0
    if work[0] != work[-1]:
        work.append(work[0])
    dists = [0.0]
    for i in range(1, len(work)):
        dists.append(
            dists[-1]
            + math.hypot(work[i][0] - work[i - 1][0], work[i][1] - work[i - 1][1])
        )
    return work, dists, dists[-1]


def _point_at_distance(
    work: list[tuple[float, float]],
    dists: list[float],
    s: float,
) -> tuple[float, float]:
    if not work:
        return (0.0, 0.0)
    total = dists[-1] if dists else 0.0
    if total <= 0:
        return work[0]
    s = max(0.0, min(total, s))
    for i in range(1, len(work)):
        if dists[i] >= s - 1e-9:
            span = dists[i] - dists[i - 1]
            t = 0.0 if span <= 1e-12 else (s - dists[i - 1]) / span
            x = work[i - 1][0] + (work[i][0] - work[i - 1][0]) * t
            y = work[i - 1][1] + (work[i][1] - work[i - 1][1]) * t
            return (x, y)
    return work[-1]


def _tab_center_distances(total: float, tab_count: int, tab_offset: float = 0.0) -> list[float]:
    offset = float(tab_offset) % 1.0
    if offset < 0:
        offset += 1.0
    return [((i + 0.5) / tab_count + offset) % 1.0 * total for i in range(tab_count)]


def _split_contour_with_tabs(
    pts: list[tuple[float, float]],
    tab_count: int,
    tab_width_mm: float,
    tab_offset: float = 0.0,
) -> list[list[tuple[float, float]]]:
    """Leave holding-tab gaps along a closed outline contour."""
    if len(pts) < 2 or tab_count <= 0 or tab_width_mm <= 0:
        return [pts]
    work, dists, total = _closed_contour(pts)
    if total <= tab_width_mm * tab_count * 2:
        return [work]
    centers = _tab_center_distances(total, tab_count, tab_offset)
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
        return _point_at_distance(work, dists, s)

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


def _pixel_to_mm(
    px: float,
    py: float,
    bounds: parser.GerberBounds,
    dpmm: int,
    pad_px: int = 0,
) -> tuple[float, float]:
    return (
        bounds.min_x + (px - pad_px) / dpmm,
        bounds.max_y - (py - pad_px) / dpmm,
    )


def _simplify_contour(cnt: np.ndarray, dpmm: int) -> np.ndarray:
    """Keep copper-following curves; do not stride down to a fixed point cap."""
    epsilon = max(0.6, 0.015 * dpmm)
    approx = cv2.approxPolyDP(cnt, epsilon, True)
    return approx if len(approx) >= 3 else cnt


def _mask_to_paths(
    binary: np.ndarray,
    bounds: parser.GerberBounds,
    dpmm: int,
    *,
    retrieve: int = cv2.RETR_LIST,
    pad_px: int = 0,
    outers_only: bool = False,
) -> list[list[tuple[float, float]]]:
    mode = cv2.RETR_CCOMP if outers_only else retrieve
    contours, hierarchy = cv2.findContours(
        binary.copy(), mode, cv2.CHAIN_APPROX_SIMPLE
    )
    paths: list[list[tuple[float, float]]] = []
    min_area = (dpmm * 0.15) ** 2
    parents = hierarchy[0] if hierarchy is not None else None
    for index, cnt in enumerate(contours):
        parent = int(parents[index][3]) if parents is not None else -1
        if outers_only and parent != -1:
            continue
        if cv2.contourArea(cnt) < min_area:
            continue
        approx = _simplify_contour(cnt, dpmm)
        pts = [
            _pixel_to_mm(float(p[0][0]), float(p[0][1]), bounds, dpmm, pad_px)
            for p in approx
        ]
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            paths.append(pts)
    return paths


def _expand_mask(binary: np.ndarray, offset_px: int) -> tuple[np.ndarray, int]:
    """Grow copper by offset_px using Euclidean distance (tool-center keep-out)."""
    pad = max(int(offset_px), 0) + 2
    padded = cv2.copyMakeBorder(
        binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0
    )
    if offset_px <= 0:
        return padded, pad
    dist = cv2.distanceTransform(cv2.bitwise_not(padded), cv2.DIST_L2, 5)
    expanded = np.where(dist <= float(offset_px), 255, 0).astype(np.uint8)
    return expanded, pad


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
    expanded, pad = _expand_mask(binary, offset_px)
    return (
        _mask_to_paths(
            expanded,
            bounds,
            dpmm,
            outers_only=True,
            pad_px=pad,
        ),
        bounds,
    )


def _cutter_radius_mm(tool_number: int) -> float:
    """Cutting radius from PAEN diameter (or tip) for outside compensation."""
    row = tool_library.cuts_for_tool_number(tool_number)
    try:
        diameter = float(row.get("Diameter(D)") or 0)
    except (TypeError, ValueError):
        diameter = 0.0
    try:
        tip = float(row.get("Tip Diameter(F)") or 0)
    except (TypeError, ValueError):
        tip = 0.0
    cutter = diameter if diameter > 0 else tip
    return max(cutter, 0.1) / 2.0


def _outside_outline_contour(
    path: Path,
    tool_number: int,
    *,
    dpmm: int = 50,
) -> list[tuple[float, float]]:
    """Tool-center path offset outside the board outline by cutter radius."""
    radius_mm = _cutter_radius_mm(tool_number)
    offset_px = max(1, int(round(radius_mm * dpmm)))
    bounds = parser.parse_gerber_bounds(path)
    png = parser.render_gerber_png(path, rgba=(255, 255, 255, 255), dpmm=dpmm)
    img = Image.open(BytesIO(png)).convert("L")
    arr = np.array(img)
    _, binary = cv2.threshold(arr, 10, 255, cv2.THRESH_BINARY)
    pad = offset_px + 2
    padded = cv2.copyMakeBorder(binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (offset_px * 2 + 1, offset_px * 2 + 1)
    )
    dilated = cv2.dilate(padded, kernel, iterations=1)
    paths = _mask_to_paths(
        dilated,
        bounds,
        dpmm,
        retrieve=cv2.RETR_EXTERNAL,
        pad_px=pad,
    )
    if not paths:
        raise ValueError("No outline path found")
    return max(paths, key=_path_length)


def _isolation_offsets_px(
    tool_number: int,
    passes: int,
    *,
    dpmm: int = 50,
) -> list[int]:
    """Pixel offsets for successive isolation passes around copper."""
    row = tool_library.cuts_for_tool_number(tool_number)
    try:
        tip = float(row.get("Tip Diameter(F)") or row.get("Diameter(D)") or 0.2)
    except (TypeError, ValueError):
        tip = 0.2
    try:
        step_pct = float(row.get("Step Over") or 50)
    except (TypeError, ValueError):
        step_pct = 50.0
    tip = max(tip, 0.05)
    step_mm = max(tip * (step_pct / 100.0), 0.05)
    offsets: list[int] = []
    for index in range(max(1, int(passes))):
        offset_mm = (tip / 2.0) + index * step_mm
        offsets.append(max(1, int(round(offset_mm * dpmm))))
    return offsets


def _isolation_paths(
    path: Path,
    offsets_px: list[int],
    *,
    dpmm: int = 50,
) -> tuple[list[list[tuple[float, float]]], parser.GerberBounds]:
    """Tool-center contours around each copper island."""
    bounds = parser.parse_gerber_bounds(path)
    png = parser.render_gerber_png(path, rgba=(255, 255, 255, 255), dpmm=dpmm)
    img = Image.open(BytesIO(png)).convert("L")
    arr = np.array(img)
    _, binary0 = cv2.threshold(arr, 10, 255, cv2.THRESH_BINARY)
    pad = max(max(offsets_px, default=0), 0) + 2
    padded = cv2.copyMakeBorder(
        binary0, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0
    )
    dist = cv2.distanceTransform(cv2.bitwise_not(padded), cv2.DIST_L2, 5)
    paths: list[list[tuple[float, float]]] = []
    for offset_px in offsets_px:
        expanded = (
            padded
            if offset_px <= 0
            else np.where(dist <= float(offset_px), 255, 0).astype(np.uint8)
        )
        paths.extend(
            _mask_to_paths(
                expanded,
                bounds,
                dpmm,
                outers_only=True,
                pad_px=pad,
            )
        )
    return paths, bounds


def _tool_tip_mm(tool_number: int) -> float:
    row = tool_library.cuts_for_tool_number(tool_number)
    try:
        tip = float(row.get("Tip Diameter(F)") or row.get("Diameter(D)") or 0.2)
    except (TypeError, ValueError):
        tip = 0.2
    return max(tip, 0.05)


def _tool_step_mm(tool_number: int) -> float:
    row = tool_library.cuts_for_tool_number(tool_number)
    try:
        step_pct = float(row.get("Step Over") or 50)
    except (TypeError, ValueError):
        step_pct = 50.0
    return max(_tool_tip_mm(tool_number) * (step_pct / 100.0), 0.05)


def _binary_from_gerber(path: Path, dpmm: int) -> tuple[np.ndarray, parser.GerberBounds]:
    bounds = parser.parse_gerber_bounds(path)
    png = parser.render_gerber_png(path, rgba=(255, 255, 255, 255), dpmm=dpmm)
    arr = np.array(Image.open(BytesIO(png)).convert("L"))
    _, binary = cv2.threshold(arr, 10, 255, cv2.THRESH_BINARY)
    return binary, bounds


def _union_bounds(
    *bounds_list: parser.GerberBounds,
    pad_mm: float = 2.0,
) -> parser.GerberBounds:
    return parser.GerberBounds(
        min_x=min(b.min_x for b in bounds_list) - pad_mm,
        min_y=min(b.min_y for b in bounds_list) - pad_mm,
        max_x=max(b.max_x for b in bounds_list) + pad_mm,
        max_y=max(b.max_y for b in bounds_list) + pad_mm,
    )


def _paste_mask(
    dst: np.ndarray,
    src: np.ndarray,
    src_bounds: parser.GerberBounds,
    canvas_bounds: parser.GerberBounds,
    dpmm: int,
) -> None:
    x0 = int(round((src_bounds.min_x - canvas_bounds.min_x) * dpmm))
    y0 = int(round((canvas_bounds.max_y - src_bounds.max_y) * dpmm))
    h, w = dst.shape
    sh, sw = src.shape
    dy0, dx0 = max(0, y0), max(0, x0)
    dy1, dx1 = min(h, y0 + sh), min(w, x0 + sw)
    if dy1 <= dy0 or dx1 <= dx0:
        return
    sy0, sx0 = dy0 - y0, dx0 - x0
    dst[dy0:dy1, dx0:dx1] = np.maximum(
        dst[dy0:dy1, dx0:dx1],
        src[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)],
    )


def _filled_board_mask(outline_binary: np.ndarray) -> np.ndarray:
    """Treat a stroke or filled outline Gerber as the board interior."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closed = cv2.dilate(outline_binary, kernel, iterations=1)
    padded = cv2.copyMakeBorder(closed, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), dtype=np.uint8)
    cv2.floodFill(flood, mask, (0, 0), 255)
    interior = np.where(flood[1:-1, 1:-1] == 0, 255, 0).astype(np.uint8)
    return cv2.bitwise_or(interior, closed)


def _pocket_paths(
    copper_path: Path,
    tool_number: int,
    *,
    outline_path: Path,
    dpmm: int = 50,
) -> tuple[list[list[tuple[float, float]]], parser.GerberBounds]:
    """Clear unused copper inside the board outline, leaving traces."""
    copper_bin, copper_bounds = _binary_from_gerber(copper_path, dpmm)
    outline_bin, outline_bounds = _binary_from_gerber(outline_path, dpmm)
    bounds = _union_bounds(copper_bounds, outline_bounds)
    height = max(1, int(round(bounds.height * dpmm)))
    width = max(1, int(round(bounds.width * dpmm)))
    copper = np.zeros((height, width), dtype=np.uint8)
    outline = np.zeros((height, width), dtype=np.uint8)
    _paste_mask(copper, copper_bin, copper_bounds, bounds, dpmm)
    _paste_mask(outline, outline_bin, outline_bounds, bounds, dpmm)
    board = _filled_board_mask(outline)
    if cv2.countNonZero(board) < 16:
        raise ValueError(
            "Board outline did not form a closed region for pocket engraving"
        )
    radius_px = max(1, int(round((_tool_tip_mm(tool_number) / 2.0) * dpmm)))
    step_px = max(1, int(round(_tool_step_mm(tool_number) * dpmm)))
    radius_k = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius_px * 2 + 1, radius_px * 2 + 1)
    )
    step_k = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (step_px * 2 + 1, step_px * 2 + 1)
    )
    keep = cv2.dilate(copper, radius_k, iterations=1)
    clear = cv2.bitwise_and(
        cv2.erode(board, radius_k, iterations=1), cv2.bitwise_not(keep)
    )
    work = clear
    paths: list[list[tuple[float, float]]] = []
    min_pixels = max(4, int((dpmm * 0.15) ** 2))
    for _ in range(80):
        if cv2.countNonZero(work) < min_pixels:
            break
        layer_paths = _mask_to_paths(work, bounds, dpmm)
        if not layer_paths:
            break
        paths.extend(layer_paths)
        work = cv2.erode(work, step_k, iterations=1)
    if not paths:
        raise ValueError(
            "No pocket region inside the board outline; traces may fill the board or the tool is too large"
        )
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
        longest = _outside_outline_contour(files["profile"], 4)
        outline = out_dir / "outline.nc"
        write_path_nc(
            outline,
            [longest],
            settings=settings,
            operation="Board Outline (outside)",
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
        if plan.copper.engrave_mode == "pocket":
            outline_name = plan.copper.outline_layer or (
                plan.outline.layer if plan.outline else None
            )
            if not outline_name:
                raise ValueError(
                    "Pocket engraving needs a board outline layer"
                )
            paths, _ = _pocket_paths(
                copper_file,
                plan.copper.tool_number,
                outline_path=_cam_by_name(job_id, outline_name),
            )
            operation = "Pocket Engraving"
        else:
            offsets = _isolation_offsets_px(
                plan.copper.tool_number,
                plan.copper.isolation_passes,
            )
            paths, _ = _isolation_paths(copper_file, offsets)
            operation = "Isolation Engraving"
        write_path_nc(
            out_dir / "isolation.nc",
            paths,
            settings=copper_settings,
            operation=operation,
            tool_number=plan.copper.tool_number,
            depth_mm=plan.copper.depth_mm or settings.engraving_depth_mm,
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
        size_to_strategy = {
            round(float(item.diameter_mm), 3): (
                "pocket" if item.strategy == "pocket" else "drill"
            )
            for item in drill_op.size_map
        }
        grouped: dict[tuple[int, float, str], list[parser.DrillHit]] = {}
        for hit in hits:
            diameter = round(float(hit.diameter), 3)
            lookup = diameter
            if lookup not in size_to_tool and size_to_tool:
                lookup = min(size_to_tool, key=lambda value: abs(value - diameter))
            tool_number = size_to_tool.get(lookup)
            if tool_number is None:
                tool_number = _default_drill_tool(diameter, tool_rows)
            if lookup in size_to_strategy:
                strategy = size_to_strategy[lookup]
            else:
                strategy = _default_hole_strategy(diameter, tool_number)
            grouped.setdefault((tool_number, diameter, strategy), []).append(hit)
        groups = [
            (
                tool_number,
                _settings_for_tool(settings, tool_number),
                group_hits,
                diameter,
                strategy,
            )
            for (tool_number, diameter, strategy), group_hits in sorted(
                grouped.items(), key=lambda item: (item[0][1], item[0][0])
            )
        ]
        name = "drill.nc" if len(drill_ops) == 1 else f"drill-{index + 1}.nc"
        write_drill_nc_grouped(
            out_dir / name,
            groups,
            settings=settings,
            depth_mm=drill_op.depth_mm or settings.drill_depth_mm,
        )
        produced.append(name)

    if plan.outline:
        outline_file = _cam_by_name(job_id, plan.outline.layer)
        outline_settings = _settings_for_tool(settings, plan.outline.tool_number)
        try:
            longest = _outside_outline_contour(outline_file, plan.outline.tool_number)
        except ValueError as exc:
            raise ValueError(f"{exc} in {plan.outline.layer}") from exc
        segments = _split_contour_with_tabs(
            longest,
            plan.outline.tab_count,
            plan.outline.tab_width_mm,
            plan.outline.tab_offset,
        )
        write_path_nc(
            out_dir / "outline.nc",
            segments,
            settings=outline_settings,
            operation="Board Outline (outside)",
            tool_number=plan.outline.tool_number,
            depth_mm=plan.outline.depth_mm or settings.drill_depth_mm,
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
    engrave_depth = settings.engraving_depth_mm
    if plan is not None and plan.copper and plan.copper.depth_mm:
        engrave_depth = plan.copper.depth_mm
    if engrave_depth > settings.drill_depth_mm:
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
        "paths": extract_preview_paths(out_dir),
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


_DRILL_TOOL_RGB = [
    (249, 75, 4),
    (246, 148, 3),
    (26, 45, 241),
    (18, 195, 252),
    (79, 23, 137),
    (209, 0, 143),
]


def _drill_tool_rgba(tool_number: int) -> tuple[int, int, int, int]:
    if tool_number < 1:
        return (249, 75, 4, 255)
    red, green, blue = _DRILL_TOOL_RGB[(tool_number - 1) % len(_DRILL_TOOL_RGB)]
    return (red, green, blue, 255)


def _parse_hole_diameter_mm(line: str) -> float | None:
    match = re.search(r"(?:Ø|DIA(?:METER)?)\s*([0-9]*\.?[0-9]+)\s*mm", line, re.I)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _drill_groups(
    nc_text: str,
) -> list[tuple[int, float | None, list[list[float]], str]]:
    """Group unique G0 XY drill hits by T word, hole diameter, and strategy."""
    grouped: dict[tuple[int, float | None, str], list[list[float]]] = {}
    seen: dict[tuple[int, float | None, str], set[tuple[float, float]]] = {}
    tool = 0
    diameter: float | None = None
    strategy = "drill"
    x = y = None
    for line in nc_text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed_dia = _parse_hole_diameter_mm(raw)
        if parsed_dia is not None:
            diameter = parsed_dia
            strategy = "pocket" if re.search(r"\bpocket\b", raw, re.I) else "drill"
        s = raw.upper()
        if s.startswith(";") or s.startswith("("):
            continue
        tool_match = re.search(r"\bT(\d+)\b", s)
        if tool_match:
            tool = int(tool_match.group(1))
        is_rapid = s.startswith("G00") or (s.startswith("G0") and not s.startswith("G01"))
        if not is_rapid:
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
        gkey = (tool, round(diameter, 3) if diameter else None, strategy)
        point = (round(x, 4), round(y, 4))
        bucket = seen.setdefault(gkey, set())
        if point in bucket:
            continue
        bucket.add(point)
        grouped.setdefault(gkey, []).append([x, y])
    return [
        (tool_number, dia, hits, mode)
        for (tool_number, dia, mode), hits in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1] or 0.0),
        )
    ]


def _drill_feed_polylines(nc_text: str) -> list[tuple[int, list[list[float]]]]:
    """XY feed moves from a drill file (pocket rings). Rapid moves start a new path."""
    out: list[tuple[int, list[list[float]]]] = []
    current: list[list[float]] = []
    tool = 0
    x: float | None = None
    y: float | None = None
    for line in nc_text.splitlines():
        s = line.strip().upper()
        if not s or s.startswith(";") or s.startswith("("):
            continue
        tool_match = re.search(r"\bT(\d+)\b", s)
        if tool_match:
            tool = int(tool_match.group(1))
        is_rapid = s.startswith("G00") or (s.startswith("G0") and not s.startswith("G01"))
        is_feed = s.startswith("G1") or s.startswith("G01")
        mx = re.search(r"X([+-]?\d*\.?\d+)", s)
        my = re.search(r"Y([+-]?\d*\.?\d+)", s)
        if is_rapid:
            if len(current) >= 2:
                out.append((tool, current))
            current = []
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            continue
        if not is_feed or (not mx and not my):
            continue
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if x is None or y is None:
            continue
        current.append([x, y])
    if len(current) >= 2:
        out.append((tool, current))
    return out


def extract_preview_paths(out_dir: Path) -> list[dict[str, Any]]:
    """Return cut polylines and drill hits from generated NC files."""
    out: list[dict[str, Any]] = []
    for path in sorted(out_dir.glob("*.nc")):
        if path.name == "all.nc":
            continue
        text = path.read_text(errors="replace")
        if path.name.startswith("drill"):
            for tool_number, hole_diameter, hits, strategy in _drill_groups(text):
                if not hits:
                    continue
                item: dict[str, Any] = {
                    "file": path.name,
                    "kind": "drill",
                    "tool_number": tool_number,
                    "diameter_mm": _tool_tip_mm(tool_number),
                    "strategy": strategy,
                    "points": hits,
                }
                if hole_diameter:
                    item["hole_diameter_mm"] = hole_diameter
                out.append(item)
            for tool_number, poly in _drill_feed_polylines(text):
                if len(poly) < 2:
                    continue
                out.append(
                    {
                        "file": path.name,
                        "kind": "cut",
                        "tool_number": tool_number,
                        "points": poly,
                    }
                )
            for kind, poly in parse_gcode_polylines(text):
                if kind != "rapid" or len(poly) < 2:
                    continue
                out.append(
                    {
                        "file": path.name,
                        "kind": "rapid",
                        "points": [[float(x), float(y)] for x, y in poly],
                    }
                )
            continue
        for kind, poly in parse_gcode_polylines(text):
            if len(poly) < 2:
                continue
            out.append(
                {
                    "file": path.name,
                    "kind": kind,
                    "points": [[float(x), float(y)] for x, y in poly],
                }
            )
    return out


def _is_rapid(s: str) -> bool:
    return bool(re.match(r"^G00?(?!\d)", s))


def _is_feed(s: str) -> bool:
    return bool(re.match(r"^G0?1(?!\d)", s))


def parse_gcode_polylines(nc_text: str) -> list[tuple[str, list[tuple[float, float]]]]:
    """Split G-code into cut (G1) and rapid (G0) XY polylines."""
    out: list[tuple[str, list[tuple[float, float]]]] = []
    current: list[tuple[float, float]] = []
    current_kind = ""
    modal = "G0"
    x = y = 0.0
    have_pos = False

    def flush() -> None:
        nonlocal current, current_kind
        if current_kind and len(current) >= 2:
            out.append((current_kind, current))
        current = []
        current_kind = ""

    for line in nc_text.splitlines():
        s = line.strip().upper()
        if not s or s.startswith("(") or s.startswith(";") or s.startswith("%"):
            continue
        if _is_rapid(s):
            modal = "G0"
        elif _is_feed(s):
            modal = "G1"
        mx = re.search(r"X([+-]?\d*\.?\d+)", s)
        my = re.search(r"Y([+-]?\d*\.?\d+)", s)
        if not mx and not my:
            continue
        nx, ny = x, y
        if mx:
            nx = float(mx.group(1))
        if my:
            ny = float(my.group(1))
        kind = "rapid" if modal == "G0" else "cut"
        if have_pos and (nx != x or ny != y):
            if kind != current_kind:
                flush()
                current = [(x, y)]
                current_kind = kind
            current.append((nx, ny))
        x, y = nx, ny
        have_pos = True
    flush()
    return out


def parse_gcode_paths(nc_text: str) -> list[list[tuple[float, float]]]:
    """Extract feed XY polylines from G-code for plotting."""
    return [pts for kind, pts in parse_gcode_polylines(nc_text) if kind == "cut"]


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
        "outline.nc": (148, 163, 184, 255),
        "all.nc": (96, 165, 250, 120),
    }

    all_pts: list[tuple[float, float]] = []
    op_paths: dict[str, list[list[tuple[float, float]]]] = {}
    drill_groups: list[tuple[int, float | None, list[tuple[float, float]], str]] = []
    drill_rings: list[tuple[int, list[tuple[float, float]]]] = []
    names = [
        path.name
        for path in sorted(out_dir.glob("*.nc"))
        if path.name != "all.nc"
    ]
    for name in names:
        path = out_dir / name
        text = path.read_text(errors="replace")
        if name.startswith("drill"):
            for tool_number, diameter, hits, strategy in _drill_groups(text):
                pts = [(pt[0], pt[1]) for pt in hits]
                drill_groups.append((tool_number, diameter, pts, strategy))
                all_pts.extend(pts)
            for tool_number, poly in _drill_feed_polylines(text):
                ring = [(pt[0], pt[1]) for pt in poly]
                drill_rings.append((tool_number, ring))
                all_pts.extend(ring)
            continue
        paths = parse_gcode_paths(text)
        op_paths[name] = paths
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
            draw.line([to_px(p) for p in poly], fill=color, width=2)

    for tool_number, hole_diameter, pts, strategy in drill_groups:
        color = _drill_tool_rgba(tool_number)
        tool_dia = _tool_tip_mm(tool_number)
        draw_dia = (
            hole_diameter
            if strategy == "pocket" and hole_diameter
            else tool_dia
        )
        radius = max(3.0, (float(draw_dia) / 2.0) * scale)
        for pt in pts:
            p = to_px(pt)
            draw.ellipse(
                (p[0] - radius, p[1] - radius, p[0] + radius, p[1] + radius),
                fill=color,
            )
    for tool_number, ring in drill_rings:
        if len(ring) < 2:
            continue
        draw.line(
            [to_px(p) for p in ring],
            fill=_drill_tool_rgba(tool_number),
            width=2,
        )

    # Legend
    legend_y = 12
    for name, color in colors.items():
        if name == "all.nc" or name not in op_paths:
            continue
        draw.rectangle((12, legend_y, 28, legend_y + 12), fill=color)
        draw.text((34, legend_y - 1), name.replace(".nc", ""), fill=(226, 232, 240, 255))
        legend_y += 18
    seen_tools: set[int] = set()
    for tool_number, _hole_diameter, _pts, _strategy in drill_groups:
        if tool_number in seen_tools:
            continue
        seen_tools.add(tool_number)
        tool_dia = _tool_tip_mm(tool_number)
        draw.rectangle(
            (12, legend_y, 28, legend_y + 12),
            fill=_drill_tool_rgba(tool_number),
        )
        draw.text(
            (34, legend_y - 1),
            f"T{tool_number}  Ø {tool_dia:g} mm",
            fill=(226, 232, 240, 255),
        )
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
