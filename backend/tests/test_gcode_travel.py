"""Tests for G-code travel optimizations (retract, order, single-pass)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import MachineSettings
from app.services.parser import DrillHit
from app.services.postprocess import (
    LONG_TRAVEL_MM,
    _depth_passes,
    _order_contours_nearest,
    _order_hits_nearest,
    _rotate_closed_start,
    ensure_program_end,
    merge_nc_files,
    write_drill_nc,
    write_drill_nc_grouped,
    write_path_nc,
)


def test_depth_passes_single_when_depth_fits_step():
    assert _depth_passes(0.15, 0.15) == [0.15]
    assert _depth_passes(0.1, 0.2) == [0.1]
    assert _depth_passes(0.15, 0.1) == [0.1, 0.15]
    assert _depth_passes(1.6, 0.4) == [0.4, 0.8, 1.2, 1.6]


def test_rotate_closed_start_picks_nearest_vertex():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    rotated = _rotate_closed_start(square, toward=(9.0, 9.0))
    assert rotated[0] == pytest.approx((10.0, 10.0))
    assert rotated[-1] == rotated[0]
    assert len(rotated) == len(square)


def test_order_contours_nearest_visits_closest_first():
    far = [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0), (100.0, 100.0)]
    near = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0), (1.0, 1.0)]
    ordered = _order_contours_nearest([far, near], start=(0.0, 0.0))
    assert ordered[0][0] == pytest.approx((1.0, 1.0))
    assert ordered[1][0][0] == pytest.approx(100.0)


def test_order_hits_nearest():
    hits = [
        DrillHit(x=50.0, y=50.0, diameter=1.0, tool="T4"),
        DrillHit(x=1.0, y=1.0, diameter=1.0, tool="T4"),
        DrillHit(x=2.0, y=1.5, diameter=1.0, tool="T4"),
    ]
    ordered = _order_hits_nearest(hits, start=(0.0, 0.0))
    assert [(h.x, h.y) for h in ordered] == [(1.0, 1.0), (2.0, 1.5), (50.0, 50.0)]


def test_write_path_stays_at_retract_between_nearby_islands(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0, step_down_mm=0.2)
    a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    b = [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0), (2.0, 1.0), (2.0, 0.0)]
    nc = tmp_path / "iso.nc"
    write_path_nc(
        nc,
        [a, b],
        settings=settings,
        operation="Isolation Engraving",
        tool_number=2,
        depth_mm=0.15,
        step_down_mm=0.15,
    )
    text = nc.read_text()
    # One plunge per island (single pass each), same depth
    assert re.findall(r"^G1 Z(-[\d.]+)", text, re.M) == ["-0.1500", "-0.1500"]
    # Between islands: retract, not a mid-job Safe Z cycle per contour
    body = text.split("T2 M6", 1)[1]
    z_safe = [float(z) for z in re.findall(r"^G0 Z([\d.]+)", body, re.M) if float(z) >= 14.9]
    assert len(z_safe) <= 3
    assert "G0 Z3.000" in text


def test_write_path_uses_safe_z_for_long_hop(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0)
    a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    far_x = LONG_TRAVEL_MM + 10.0
    b = [
        (far_x, 0.0),
        (far_x + 1.0, 0.0),
        (far_x + 1.0, 1.0),
        (far_x, 1.0),
        (far_x, 0.0),
    ]
    nc = tmp_path / "iso.nc"
    write_path_nc(
        nc,
        [a, b],
        settings=settings,
        operation="Isolation Engraving",
        tool_number=2,
        depth_mm=0.15,
        step_down_mm=0.15,
    )
    text = nc.read_text()
    assert text.count("G0 Z15.000") >= 2


def test_write_drill_retract_between_holes_and_orders(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0)
    hits = [
        DrillHit(x=40.0, y=0.0, diameter=1.0, tool="T4"),
        DrillHit(x=1.0, y=0.0, diameter=1.0, tool="T4"),
        DrillHit(x=2.0, y=0.0, diameter=1.0, tool="T4"),
    ]
    nc = tmp_path / "drill.nc"
    write_drill_nc(nc, hits, settings=settings, depth_mm=1.6, tool_number=4)
    text = nc.read_text()
    xs = [float(x) for x in re.findall(r"^G0 X([\d.]+)", text, re.M)]
    assert xs[:3] == [1.0, 2.0, 40.0]
    # Between short hops: retract, not Safe Z after every hole
    assert text.count("G0 Z3.000") >= 4
    # Final lift to Safe Z once at end (plus start)
    assert "G0 Z15.000" in text


def test_write_drill_grouped_orders_and_retracts(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0)
    hits = [
        DrillHit(x=30.0, y=0.0, diameter=1.0, tool="T4"),
        DrillHit(x=0.0, y=0.0, diameter=1.0, tool="T4"),
    ]
    nc = tmp_path / "drill.nc"
    write_drill_nc_grouped(
        nc,
        [(4, settings, hits, 1.0, "drill")],
        settings=settings,
        depth_mm=1.6,
    )
    text = nc.read_text()
    xs = [float(x) for x in re.findall(r"^G0 X([\d.]+)", text, re.M)]
    assert xs[:2] == [0.0, 30.0]
    assert "G0 Z3.000" in text
    # No per-hole Safe Z before the final group lift
    mid = text.split("G1 Z-1.6000", 1)[1].split("G1 Z-1.6000", 1)[0]
    assert "G0 Z15.000" not in mid


def test_downloaded_nc_returns_tool_then_homes(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0)
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    nc = tmp_path / "iso.nc"
    write_path_nc(
        nc,
        [square],
        settings=settings,
        operation="Isolation Engraving",
        tool_number=2,
        depth_mm=0.15,
        step_down_mm=0.15,
    )
    text = nc.read_text()
    assert "; Return Tool" in text
    assert "T0 M6" in text
    assert "; Home position" in text
    assert re.search(r"^G28\s*$", text, re.M)
    assert text.strip().endswith("M2")
    assert text.rfind("T0 M6") < text.rfind("G28")
    assert text.rfind("G28") < text.rfind("M2")


def test_ensure_program_end_patches_legacy_file(tmp_path: Path):
    nc = tmp_path / "old.nc"
    nc.write_text("%\n; Clearance height: 12 mm\nG90 G21\nT2 M6\nG1 X1 Y1\nM5\nM2\n")
    settings = MachineSettings(safe_z_mm=15.0)
    ensure_program_end(nc, settings)
    text = nc.read_text()
    assert "; Return Tool" in text
    assert "T0 M6" in text
    assert "; Home position" in text
    assert re.search(r"^G28\s*$", text, re.M)
    assert "G0 Z12.000" in text
    assert text.strip().endswith("M2")
    ensure_program_end(nc, settings)
    assert nc.read_text().count("; Return Tool") == 1


def test_merged_nc_returns_tool_once(tmp_path: Path):
    settings = MachineSettings(safe_z_mm=15.0, retract_z_mm=3.0)
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    a = tmp_path / "isolation.nc"
    b = tmp_path / "outline.nc"
    write_path_nc(
        a,
        [square],
        settings=settings,
        operation="Isolation Engraving",
        tool_number=2,
        depth_mm=0.15,
        step_down_mm=0.15,
    )
    write_path_nc(
        b,
        [square],
        settings=settings,
        operation="Board Outline (outside)",
        tool_number=4,
        depth_mm=1.6,
        step_down_mm=0.4,
        close_open_paths=False,
    )
    dest = tmp_path / "all.nc"
    merge_nc_files([a, b], dest, settings=settings)
    text = dest.read_text()
    assert text.count("; Return Tool") == 1
    assert text.count("; Home position") == 1
    assert text.rfind("T0 M6") < text.rfind("G28")
    assert text.strip().endswith("M2")
