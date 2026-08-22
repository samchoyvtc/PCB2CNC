"""GRBL-oriented G-code writers and merger."""

from __future__ import annotations

import math
from pathlib import Path

from app.models import MachineSettings
from app.services import tool_library
from app.services.parser import DrillHit


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
    step = step_down_mm or settings.step_down_mm or min(0.1, depth_mm)
    lines: list[str] = []
    if include_header:
        lines.extend(_header(settings, operation))
    else:
        lines.append(f"; --- {operation} ---")

    lines.append(f"T{tool_number} M6")
    lines.append(f"S{settings.spindle_rpm} M3")
    lines.append(_coolant(settings, True))
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")

    for contour in contours:
        if len(contour) < 2:
            continue
        x0, y0 = contour[0]
        lines.append(f"G0 X{x0:.4f} Y{y0:.4f}")
        lines.append(f"G0 Z{_retract_z(settings):.3f}")

        depth = 0.0
        while depth < depth_mm - 1e-9:
            depth = min(depth_mm, depth + step)
            lines.append(f"G1 Z{-depth:.4f} F{settings.plunge_mm_min:.1f}")
            for x, y in contour[1:]:
                lines.append(f"G1 X{x:.4f} Y{y:.4f} F{settings.feed_mm_min:.1f}")
            # return to start for multi-pass closed paths
            if close_open_paths and contour[0] != contour[-1]:
                lines.append(
                    f"G1 X{contour[0][0]:.4f} Y{contour[0][1]:.4f} F{settings.feed_mm_min:.1f}"
                )
            lines.append(f"G0 Z{_retract_z(settings):.3f}")

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

    # peck-ish simple drill cycle
    for hit in hits:
        lines.append(f"G0 X{hit.x:.4f} Y{hit.y:.4f}")
        lines.append(f"G0 Z{_retract_z(settings):.3f}")
        lines.append(f"G1 Z{-depth_mm:.4f} F{settings.plunge_mm_min:.1f}")
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
        for hit in hits:
            lines.append(f"G0 X{hit.x:.4f} Y{hit.y:.4f}")
            lines.append(f"G0 Z{_retract_z(settings):.3f}")
            lines.append(f"G1 Z{-depth_mm:.4f} F{tool_settings.plunge_mm_min:.1f}")
            for radius in radii:
                for x, y in _circle_xy(hit.x, hit.y, radius):
                    lines.append(f"G1 X{x:.4f} Y{y:.4f} F{feed:.1f}")
            if radii:
                lines.append(f"G1 X{hit.x:.4f} Y{hit.y:.4f} F{feed:.1f}")
            lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
        lines.append(_coolant(tool_settings, False))
        lines.append("M5")
    lines.append(f"G0 Z{settings.safe_z_mm:.3f}")
    lines.append("M2")
    path.write_text("\n".join(lines) + "\n")


def merge_nc_files(
    parts: list[Path],
    dest: Path,
    *,
    settings: MachineSettings,
) -> None:
    lines = _header(settings, "Merged PCB Job")
    lines.append("; Combined isolation / drill / outline")
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
