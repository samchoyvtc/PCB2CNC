"""Unit tests for generate-plan helpers (tabs, tool defaults)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.toolpath import (
    _default_drill_tool,
    _isolation_offsets_px,
    _split_contour_with_tabs,
)

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
SIMPLE_ZIP = SAMPLES / "TEST_Gerber_Simple.zip"
client = TestClient(app)


def test_split_contour_leaves_holding_tabs():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    parts = _split_contour_with_tabs(square, tab_count=4, tab_width_mm=2.0)
    assert len(parts) == 4
    for part in parts:
        assert len(part) >= 2


def test_split_contour_no_tabs_keeps_path():
    line = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)]
    assert _split_contour_with_tabs(line, tab_count=0, tab_width_mm=2.0) == [line]


def test_tab_offset_rotates_gaps():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    a = _split_contour_with_tabs(square, tab_count=4, tab_width_mm=2.0, tab_offset=0.0)
    b = _split_contour_with_tabs(square, tab_count=4, tab_width_mm=2.0, tab_offset=0.125)
    assert len(a) == 4
    assert len(b) == 4
    assert a != b


def test_outline_tab_offset_defaults_to_zero():
    from app.models import OutlineOp

    op = OutlineOp(layer="profile.gbr")
    assert op.tab_offset == 0.0


def test_default_drill_tool_picks_nearest_tip():
    tools = [
        {"Number": 3, "Tip Diameter(F)": 0.8},
        {"Number": 4, "Tip Diameter(F)": 1.0},
        {"Number": 5, "Tip Diameter(F)": 1.5},
        {"Number": 6, "Tip Diameter(F)": 2.0},
    ]
    assert _default_drill_tool(0.8, tools) == 3
    assert _default_drill_tool(1.0, tools) == 4
    assert _default_drill_tool(1.016, tools) == 4
    assert _default_drill_tool(1.1, tools) == 4
    assert _default_drill_tool(1.2, tools) == 4
    assert _default_drill_tool(1.7, tools) == 5
    assert _default_drill_tool(3.0, tools) == 6


def test_copper_engrave_mode_defaults_to_contour():
    from app.models import CopperOp

    op = CopperOp(layer="copper_top.gbr")
    assert op.engrave_mode == "contour"
    assert CopperOp(layer="copper_top.gbr", engrave_mode="pocket").engrave_mode == "pocket"


def test_drill_hits_split_by_tool():
    from app.services.toolpath import _drill_groups, _drill_tool_rgba

    nc = """
; T3 holes Ø 0.8 mm (1)
T3 M6
G0 X1.0 Y1.0
G1 Z-1.6
G0 Z15
; T4 holes Ø 1.0 mm (1)
T4 M6
G0 X2.0 Y2.0
G1 Z-1.6
"""
    groups = _drill_groups(nc)
    assert [(tool, dia, pts, mode) for tool, dia, pts, mode in groups] == [
        (3, 0.8, [[1.0, 1.0]], "drill"),
        (4, 1.0, [[2.0, 2.0]], "drill"),
    ]
    assert _drill_tool_rgba(3) != _drill_tool_rgba(4)
    assert _drill_tool_rgba(1) == (249, 75, 4, 255)


def test_isolation_offsets_grow_with_passes():
    one = _isolation_offsets_px(2, 1)
    three = _isolation_offsets_px(2, 3)
    assert len(one) == 1
    assert len(three) == 3
    assert three[0] == one[0]
    assert three[1] > three[0]
    assert three[2] > three[1]


def test_cutter_radius_uses_tool_diameter():
    from app.services.toolpath import _cutter_radius_mm

    radius = _cutter_radius_mm(4)
    assert radius == pytest.approx(0.5, abs=0.05)


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_generate_with_plan_writes_nc():
    uploaded = client.post(
        "/api/jobs/upload",
        files={"file": ("simple.zip", SIMPLE_ZIP.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()
    job_id = payload["job_id"]
    files = payload["files"]
    copper = next(item["name"] for item in files if item["kind"] == "copper_top")
    profile = next(item["name"] for item in files if item["kind"] == "profile")
    drills = [item["name"] for item in files if item["kind"] == "drill"]
    result = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": {
                "engraving_depth_mm": 0.15,
                "drill_depth_mm": 1.6,
                "safe_z_mm": 15,
            },
            "plan": {
                "copper": {"layer": copper, "tool_number": 2},
                "drills": [
                    {"layers": drills, "size_map": [{"diameter_mm": 1.0, "tool_number": 4}]}
                ],
                "outline": {
                    "layer": profile,
                    "tool_number": 4,
                    "tab_count": 4,
                    "tab_width_mm": 2.0,
                },
            },
        },
    )
    assert result.status_code == 200, result.text
    names = result.json()["files"]
    assert {"isolation.nc", "drill.nc", "outline.nc", "all.nc"} <= set(names)


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_preview_path_for_outline():
    uploaded = client.post(
        "/api/jobs/upload",
        files={"file": ("simple.zip", SIMPLE_ZIP.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()
    profile = next(item["name"] for item in payload["files"] if item["kind"] == "profile")
    result = client.post(
        f"/api/jobs/{payload['job_id']}/preview-path",
        json={
            "settings": {"engraving_depth_mm": 0.15, "drill_depth_mm": 1.6},
            "plan": {
                "outline": {
                    "layer": profile,
                    "tool_number": 4,
                    "tab_count": 4,
                    "tab_width_mm": 2.0,
                }
            },
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["image_png_base64"]
    assert "outline.nc" in body["files"]
    assert body["paths"]
    assert body["paths"][0]["kind"] == "cut"
    assert len(body["paths"][0]["points"]) >= 2


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_copper_pocket_vs_contour_gcode():
    uploaded = client.post(
        "/api/jobs/upload",
        files={"file": ("simple.zip", SIMPLE_ZIP.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()
    job_id = payload["job_id"]
    copper = next(item["name"] for item in payload["files"] if item["kind"] == "copper_top")
    profile = next(item["name"] for item in payload["files"] if item["kind"] == "profile")
    settings = {"engraving_depth_mm": 0.15, "drill_depth_mm": 1.6}

    contour = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": settings,
            "plan": {
                "copper": {
                    "layer": copper,
                    "tool_number": 2,
                    "engrave_mode": "contour",
                }
            },
        },
    )
    assert contour.status_code == 200, contour.text
    contour_nc = client.get(f"/api/jobs/{job_id}/nc/isolation.nc")
    assert contour_nc.status_code == 200
    assert b"Isolation Engraving" in contour_nc.content

    pocket = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": settings,
            "plan": {
                "copper": {
                    "layer": copper,
                    "tool_number": 2,
                    "engrave_mode": "pocket",
                    "outline_layer": profile,
                }
            },
        },
    )
    assert pocket.status_code == 200, pocket.text
    pocket_nc = client.get(f"/api/jobs/{job_id}/nc/isolation.nc")
    assert pocket_nc.status_code == 200
    assert b"Pocket Engraving" in pocket_nc.content
    assert pocket_nc.content != contour_nc.content


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_copper_pocket_requires_outline_layer():
    uploaded = client.post(
        "/api/jobs/upload",
        files={"file": ("simple.zip", SIMPLE_ZIP.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()
    copper = next(item["name"] for item in payload["files"] if item["kind"] == "copper_top")
    result = client.post(
        f"/api/jobs/{payload['job_id']}/generate",
        json={
            "settings": {"engraving_depth_mm": 0.15, "drill_depth_mm": 1.6},
            "plan": {
                "copper": {
                    "layer": copper,
                    "tool_number": 2,
                    "engrave_mode": "pocket",
                }
            },
        },
    )
    assert result.status_code == 400
    assert "outline" in result.json()["detail"].lower()


def test_drill_strategy_defaults_to_drill():
    from app.models import DrillSizeMap

    item = DrillSizeMap(diameter_mm=1.1, tool_number=4)
    assert item.strategy == "drill"
    assert DrillSizeMap(diameter_mm=1.1, tool_number=4, strategy="pocket").strategy == "pocket"


def test_default_hole_strategy_pockets_when_hole_is_larger():
    from app.services.toolpath import _default_hole_strategy, _tool_tip_mm

    tip = _tool_tip_mm(4)
    assert _default_hole_strategy(tip, 4) == "drill"
    assert _default_hole_strategy(tip + 0.2, 4) == "pocket"


def test_pocket_ring_radii_steps_inward():
    from app.services.postprocess import _pocket_ring_radii

    radii = _pocket_ring_radii(3.2, 2.0, 0.4)
    assert radii[0] == pytest.approx(0.6)
    assert radii[-1] > 0
    assert radii == sorted(radii, reverse=True)
    assert _pocket_ring_radii(1.0, 1.0, 0.4) == []


def test_drill_groups_keeps_centers_not_pocket_rings():
    from app.services.toolpath import _drill_groups

    nc = """
; T4 holes Ø 1.1 mm pocket (1)
T4 M6
G0 X1.0 Y2.0
G0 Z3
G1 Z-1.6
G1 X1.4 Y2.0
G1 X1.0 Y2.4
G0 Z15
"""
    groups = _drill_groups(nc)
    assert groups == [(4, 1.1, [[1.0, 2.0]], "pocket")]


def test_write_drill_pocket_emits_xy_feed(tmp_path):
    from app.models import MachineSettings
    from app.services.parser import DrillHit
    from app.services.postprocess import write_drill_nc_grouped

    settings = MachineSettings()
    hits = [DrillHit(x=10.0, y=20.0, diameter=3.2, tool="T6")]
    path = tmp_path / "drill.nc"
    write_drill_nc_grouped(
        path,
        [(6, settings, hits, 3.2, "pocket")],
        settings=settings,
        depth_mm=1.6,
    )
    text = path.read_text()
    assert "pocket" in text
    assert "G1 X" in text
    assert text.count("G1 X") > 8

    drill_path = tmp_path / "drill-plunge.nc"
    write_drill_nc_grouped(
        drill_path,
        [(6, settings, hits, 3.2, "drill")],
        settings=settings,
        depth_mm=1.6,
    )
    drill_text = drill_path.read_text()
    assert " mm drill " in drill_text
    assert "G1 X" not in drill_text
    assert drill_text != text
