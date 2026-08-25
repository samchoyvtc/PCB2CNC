"""GRBL-oriented G-code writers and merger."""

from __future__ import annotations

import math
import re
from pathlib import Path

from app.models import MachineSettings
from app.services import tool_library
from app.services.parser import DrillHit

# XY hops longer than this lift to Safe Z; shorter hops stay at Retract Height.
LONG_TRAVEL_MM = 40.0


def _header(settings: MachineSettings, title: str) -> list[str]:
    lines = [
        "%",
        f"; {title}",
        "; Material: PCB",
        f"; Board: {settings.board_width_mm:g} x {settings.board_length_mm:g} mm",
        f"; Drill depth: {settings.drill_depth_mm} mm",
        f"; Clearance height: {settings.safe_z_mm} mm",
        f"; Retract height: {settings.retract_z_mm} mm",
        "G90 G21",
        "G17",
    ]
    return lines


def _retract_z(settings: MachineSettings) -> float:
    return min(settings.retract_z_mm, settings.safe_z_mm)


def _coolant(settings: MachineSettings, on: bool) -> str:
    if not settings.coolant:
        return "; coolant disabled"
    return "M8" if on else "M9"


def _xy_dist(
    a: tuple[float, float] | None,
    b: tuple[float, float] | None,
) -> float:
    if a is None or b is None:
        return float("inf")
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_closed_path(
    pts: list[tuple[float, float]],
    *,
    tol: float = 1e-4,
) -> bool:
    if len(pts) < 3:
        return False
    return _xy_dist(pts[0], pts[-1]) <= tol


def _rotate_closed_start(
    pts: list[tuple[float, float]],
    toward: tuple[float, float] | None,
) -> list[tuple[float, float]]:
    """Rotate a closed polyline so cutting starts nearest to ``toward``."""
    if toward is None or len(pts) < 3 or not _is_closed_path(pts):
        return pts
    body = pts[:-1] if _xy_dist(pts[0], pts[-1]) <= 1e-4 else list(pts)
    if not body:
        return pts
    best = min(range(len(body)), key=lambda i: _xy_dist(body[i], toward))
    if best == 0:
        return pts if _xy_dist(pts[0], pts[-1]) <= 1e-4 else body + [body[0]]
    rotated = body[best:] + body[:best]
    rotated.append(rotated[0])
    return rotated


def _order_contours_nearest(
    contours: list[list[tuple[float, float]]],
    *,
    start: tuple[float, float] | None = None,
) -> list[list[tuple[float, float]]]:
    """Greedy nearest-neighbor visit order; rotate closed starts toward approach."""
    remaining = [list(c) for c in contours if len(c) >= 2]
    if not remaining:
        return []
    ordered: list[list[tuple[float, float]]] = []
    current: tuple[float, float] | None = start if start is not None else (0.0, 0.0)
    while remaining:
        best_i = 0
        best_path = remaining[0]
        best_dist = float("inf")
        for i, path in enumerate(remaining):
            candidate = _rotate_closed_start(path, current)
            dist = _xy_dist(current, candidate[0])
            if dist < best_dist:
                best_dist = dist
                best_i = i
                best_path = candidate
        ordered.append(best_path)
        current = best_path[-1]
        remaining.pop(best_i)
    return ordered


def _order_hits_nearest(
    hits: list[DrillHit],
    *,
    start: tuple[float, float] | None = None,
) -> list[DrillHit]:
    remaining = list(hits)
    if not remaining:
        return []
    ordered: list[DrillHit] = []
    current: tuple[float, float] | None = start if start is not None else (0.0, 0.0)
    while remaining:
        best_i = min(
            range(len(remaining)),
            key=lambda i: _xy_dist(current, (remaining[i].x, remaining[i].y)),
        )
        hit = remaining.pop(best_i)
        ordered.append(hit)
        current = (hit.x, hit.y)
    return ordered


def _depth_passes(depth_mm: float, step_mm: float) -> list[float]:
    """Z depths to cut. One pass when total depth fits in a single step-down."""
    depth = max(float(depth_mm), 0.0)
    step = max(float(step_mm), 0.01)
    if depth <= step + 1e-9:
        return [round(depth, 6)] if depth > 0 else []
    passes: list[float] = []
    current = 0.0
    while current < depth - 1e-9:
        current = min(depth, current + step)
        passes.append(round(current, 6))
    return passes


def _travel_z(settings: MachineSettings, distance_mm: float) -> float:
    """Retract for short hops; Safe Z when the XY hop is long."""
    if distance_mm > LONG_TRAVEL_MM:
        return settings.safe_z_mm
    return _retract_z(settings)


def write_path_nc(
    path: Path,
    contours: list[list[tuple[float, float]]],
    *,
    settings: MachineSettings,
    operation: str,
    tool_number: int,
    depth_mm: float,
    include_header: bool = True,
    step_down_mm: float | None = None,
    close_open_paths: bool = True,
) -> None:
    step = (
        step_down_mm
        if step_down_mm is not None
        else (settings.step_down_mm or min(0.1, depth_mm))
    )
    step = max(float(step), 0.01)
    depths = _depth_passes(depth_mm, step)
    lines: list[str] = []
    if include_header:
        lines.extend(_header(settings, operation))
    else:
        lines.append(f"; --- {operation} ---")

    lines.append(f"T{tool_number} M6")
    lines.append(f"S{settings.spindle_rpm} M3")
    lines.append(_coolant(settings, True))
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")

    ordered = _order_contours_nearest(contours, start=None)
    last_xy: tuple[float, float] | None = None
    retract = _retract_z(settings)

    for contour in ordered:
        x0, y0 = contour[0]
        hop = _xy_dist(last_xy, (x0, y0))
        if last_xy is None:
            travel_z = settings.safe_z_mm
        else:
            travel_z = _travel_z(settings, hop)
            if travel_z > retract + 1e-9:
                lines.append(f"G0 Z{travel_z:.3f}")

        lines.append(f"G0 X{x0:.4f} Y{y0:.4f}")
        if travel_z > retract + 1e-9:
            lines.append(f"G0 Z{retract:.3f}")

        for pass_i, depth in enumerate(depths):
            if pass_i > 0 and last_xy != (x0, y0):
                lines.append(f"G0 X{x0:.4f} Y{y0:.4f}")
            lines.append(f"G1 Z{-depth:.4f} F{settings.plunge_mm_min:.1f}")
            for x, y in contour[1:]:
                lines.append(f"G1 X{x:.4f} Y{y:.4f} F{settings.feed_mm_min:.1f}")
            # return to start for multi-pass closed paths
            if close_open_paths and contour[0] != contour[-1]:
                lines.append(
                    f"G1 X{contour[0][0]:.4f} Y{contour[0][1]:.4f} "
                    f"F{settings.feed_mm_min:.1f}"
                )
                last_xy = (contour[0][0], contour[0][1])
            else:
                last_xy = (contour[-1][0], contour[-1][1])
            lines.append(f"G0 Z{retract:.3f}")

    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append(_coolant(settings, False))
    lines.append("M5")
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append("M2")
    path.write_text("\n".join(lines) + "\n")


def write_drill_nc(
    path: Path,
    hits: list[DrillHit],
    *,
    settings: MachineSettings,
    depth_mm: float,
    tool_number: int = 4,
    include_header: bool = True,
) -> None:
    lines: list[str] = []
    if include_header:
        lines.extend(_header(settings, "2D Drilling"))
    else:
        lines.append("; --- Drilling ---")

    lines.append(f"T{tool_number} M6")
    lines.append(f"S{settings.spindle_rpm} M3")
    lines.append(_coolant(settings, True))
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")

    ordered = _order_hits_nearest(hits)
    last_xy: tuple[float, float] | None = None
    retract = _retract_z(settings)
    for hit in ordered:
        target = (hit.x, hit.y)
        hop = _xy_dist(last_xy, target)
        clear_z = _travel_z(settings, hop if last_xy is not None else float("inf"))
        if last_xy is not None and clear_z > retract + 1e-9:
            lines.append(f"G0 Z{clear_z:.3f}")
        lines.append(f"G0 X{hit.x:.4f} Y{hit.y:.4f}")
        lines.append(f"G0 Z{retract:.3f}")
        lines.append(f"G1 Z{-depth_mm:.4f} F{settings.plunge_mm_min:.1f}")
        lines.append(f"G0 Z{retract:.3f}")
        last_xy = target

    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append(_coolant(settings, False))
    lines.append("M5")
    lines.append("M2")
    path.write_text("\n".join(lines) + "\n")


def _tool_tip_and_step_mm(tool_number: int) -> tuple[float, float]:
    row = tool_library.cuts_for_tool_number(tool_number) or {}
    try:
        tip = float(row.get("Tip Diameter(F)") or row.get("Diameter(D)") or 0.2)
    except (TypeError, ValueError):
        tip = 0.2
    tip = max(tip, 0.05)
    try:
        step_pct = float(row.get("Step Over") or 50)
    except (TypeError, ValueError):
        step_pct = 50.0
    return tip, max(tip * (step_pct / 100.0), 0.05)


def _pocket_ring_radii(hole_dia: float, tool_dia: float, step_mm: float) -> list[float]:
    """Tool-center radii that mill a hole larger than the cutter."""
    outer = (float(hole_dia) - float(tool_dia)) / 2.0
    if outer <= 1e-3:
        return []
    step = max(float(step_mm) or 0.05, 0.05)
    radii: list[float] = []
    radius = outer
    guard = 0
    while radius > 1e-3 and guard < 40:
        radii.append(radius)
        radius -= step
        guard += 1
    return radii


def _circle_xy(cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    count = max(16, int(round(2 * math.pi * max(radius, 0.05) / 0.15)))
    count = min(count, 96)
    return [
        (
            cx + radius * math.cos(2 * math.pi * index / count),
            cy + radius * math.sin(2 * math.pi * index / count),
        )
        for index in range(count + 1)
    ]


def write_drill_nc_grouped(
    path: Path,
    groups: list[tuple],
    *,
    settings: MachineSettings,
    depth_mm: float,
) -> None:
    """Write one drill file with a tool change per hole-size group.

    Each group is ``(tool_number, tool_settings, hits, diameter[, strategy])``.
    Pocket groups mill concentric circles when the hole is larger than the tool.
    """
    lines = _header(settings, "2D Drilling")
    for group in groups:
        tool_number, tool_settings, hits, diameter = group[:4]
        strategy = group[4] if len(group) > 4 else "drill"
        if not hits:
            continue
        tool_dia, step_mm = _tool_tip_and_step_mm(tool_number)
        radii = (
            _pocket_ring_radii(diameter, tool_dia, step_mm)
            if strategy == "pocket"
            else []
        )
        mode = "pocket" if radii else "drill"
        lines.append(f"; T{tool_number} holes Ø {diameter:g} mm {mode} ({len(hits)})")
        lines.append(f"T{tool_number} M6")
        lines.append(f"S{tool_settings.spindle_rpm} M3")
        lines.append(_coolant(tool_settings, True))
        lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
        feed = tool_settings.feed_mm_min
        step_down = float(tool_settings.step_down_mm or settings.step_down_mm or 0.1)
        step_down = max(step_down, 0.01)
        depth_levels = _depth_passes(depth_mm, step_down)
        retract = _retract_z(settings)
        ordered_hits = _order_hits_nearest(hits)
        last_xy: tuple[float, float] | None = None
        for hit in ordered_hits:
            target = (hit.x, hit.y)
            hop = _xy_dist(last_xy, target)
            clear_z = _travel_z(settings, hop if last_xy is not None else float("inf"))
            if last_xy is not None and clear_z > retract + 1e-9:
                lines.append(f"G0 Z{clear_z:.3f}")
            lines.append(f"G0 X{hit.x:.4f} Y{hit.y:.4f}")
            lines.append(f"G0 Z{retract:.3f}")
            if not radii:
                lines.append(f"G1 Z{-depth_mm:.4f} F{tool_settings.plunge_mm_min:.1f}")
                lines.append(f"G0 Z{retract:.3f}")
                last_xy = target
                continue
            for pass_i, depth in enumerate(depth_levels):
                if pass_i > 0:
                    lines.append(f"G0 X{hit.x:.4f} Y{hit.y:.4f}")
                lines.append(f"G1 Z{-depth:.4f} F{tool_settings.plunge_mm_min:.1f}")
                for radius in radii:
                    for x, y in _circle_xy(hit.x, hit.y, radius):
                        lines.append(f"G1 X{x:.4f} Y{y:.4f} F{feed:.1f}")
                lines.append(f"G1 X{hit.x:.4f} Y{hit.y:.4f} F{feed:.1f}")
                lines.append(f"G0 Z{retract:.3f}")
            last_xy = target
        lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
        lines.append(_coolant(tool_settings, False))
        lines.append("M5")
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append("M2")
    path.write_text("\n".join(lines) + "\n")


_HOLE_COMMENT = re.compile(
    r"^;\s*T(\d+)\s+holes\s+[Øø]\s*([\d.]+)\s*mm\s+(\w+)\s*\((\d+)\)",
    re.I,
)
_TOOL_CHANGE = re.compile(r"^T(\d+)\s*M6\b", re.I)
_SKIP_TITLE = re.compile(
    r"^(material:|board:|drill depth:|clearance height:|retract height:"
    r"|combined |merged |coolant)",
    re.I,
)


def _job_from_part(file_name: str, title: str, hole: dict | None) -> tuple[str, str]:
    name = (file_name or "").lower()
    heading = (title or "").strip()
    if name.startswith("isolation") or "engrav" in heading.lower():
        job = (
            "Copper bottom engraving"
            if "bottom" in name or "bottom" in heading.lower()
            else "Copper engraving"
        )
        detail = "Pocket" if "pocket" in heading.lower() else "Isolation"
    elif name.startswith("drill") or hole or "drill" in heading.lower():
        job = "Drilling"
        if hole:
            count = int(hole["count"])
            holes = "hole" if count == 1 else "holes"
            detail = f"Ø {hole['diameter']} mm · {hole['mode']} · {count} {holes}"
        else:
            detail = "Holes"
    elif name.startswith("outline") or "outline" in heading.lower():
        job = "Board outline"
        detail = "Outside cut"
    else:
        job = heading or file_name or "Job"
        detail = ""
    return job, detail


def nc_job_sequence(text: str, file_name: str = "") -> list[dict]:
    """Read mill order from G-code comments and T…M6 tool changes."""
    rows: list[dict] = []
    current_file = file_name
    title = ""
    hole: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        begin = re.match(r"^;\s*Begin\s+(\S+)", line, re.I)
        if begin:
            current_file = begin.group(1)
            title = ""
            hole = None
            continue
        if re.match(r"^;\s*End\s+", line, re.I):
            current_file = file_name
            title = ""
            hole = None
            continue
        hole_match = _HOLE_COMMENT.match(line)
        if hole_match:
            hole = {
                "tool": int(hole_match.group(1)),
                "diameter": hole_match.group(2),
                "mode": hole_match.group(3).lower(),
                "count": int(hole_match.group(4)),
            }
            continue
        if line.startswith(";"):
            inner = re.sub(r"^;\s*", "", line)
            inner = re.sub(r"^---\s*", "", inner)
            inner = re.sub(r"\s*---$", "", inner).strip()
            if inner and not _SKIP_TITLE.match(inner) and not inner.startswith("SEQ "):
                title = inner
            continue
        tool_match = _TOOL_CHANGE.match(line)
        if not tool_match:
            continue
        tool = int(tool_match.group(1))
        job, detail = _job_from_part(current_file, title, hole)
        rows.append(
            {
                "step": len(rows) + 1,
                "job": job,
                "detail": detail,
                "tool": tool,
                "file": current_file,
            }
        )
        hole = None
    return rows


def with_tool_change_jobs(rows: list[dict]) -> list[dict]:
    """Insert a wait step before the first tool and whenever T-word changes."""
    out: list[dict] = []
    previous: int | None = None
    for row in rows:
        if row.get("job") == "Tool change":
            out.append(dict(row))
            previous = int(row["tool"])
            continue
        tool = int(row["tool"])
        if previous is None:
            out.append(
                {
                    "job": "Tool change",
                    "detail": f"Load T{tool}",
                    "tool": tool,
                    "file": "",
                }
            )
        elif tool != previous:
            out.append(
                {
                    "job": "Tool change",
                    "detail": f"T{previous} → T{tool}",
                    "tool": tool,
                    "file": "",
                }
            )
        out.append(dict(row))
        previous = tool
    for index, item in enumerate(out, start=1):
        item["step"] = index
    return out


def _sequence_comments(parts: list[Path]) -> list[str]:
    rows: list[dict] = []
    for part in parts:
        if not part.exists():
            continue
        rows.extend(nc_job_sequence(part.read_text(errors="replace"), part.name))
    rows = with_tool_change_jobs(rows)
    if not rows:
        return []
    lines = ["; Job sequence"]
    for index, row in enumerate(rows, start=1):
        detail = f" | {row['detail']}" if row["detail"] else ""
        lines.append(f"; SEQ {index} | {row['job']}{detail} | T{row['tool']}")
    return lines


def merge_nc_files(
    parts: list[Path],
    dest: Path,
    *,
    settings: MachineSettings,
) -> None:
    lines = _header(settings, "Merged PCB Job")
    lines.append("; Combined isolation / drill / outline")
    lines.extend(_sequence_comments(parts))
    for part in parts:
        if not part.exists():
            continue
        body = part.read_text(errors="replace").splitlines()
        # Strip program start/end from parts
        filtered = []
        for line in body:
            s = line.strip().upper()
            if s in {"%", "M2", "M30"}:
                continue
            if s.startswith("G90") or s.startswith("G21") or s.startswith("G17"):
                continue
            filtered.append(line)
        lines.append(f"; Begin {part.name}")
        lines.extend(filtered)
        lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
        lines.append(f"; End {part.name}")
    lines.append("M5")
    lines.append(_coolant(settings, False))
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append("M2")
    dest.write_text("\n".join(lines) + "\n")
