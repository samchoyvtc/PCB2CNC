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


def test_parse_gcode_separates_rapids_from_cuts():
    from app.services.toolpath import parse_gcode_paths, parse_gcode_polylines

    text = (
        "G90 G21\n"
        "G0 X0 Y0\n"
        "G1 X10 Y0 F2000\n"
        "G1 X10 Y10\n"
        "G0 Z15\n"
        "G0 X20 Y20\n"
        "G1 X30 Y20\n"
    )
    kinds = [kind for kind, pts in parse_gcode_polylines(text)]
    assert kinds == ["cut", "rapid", "cut"]
    cuts = parse_gcode_paths(text)
    assert cuts[0][0] == (0.0, 0.0)
    assert cuts[0][-1] == (10.0, 10.0)
    assert cuts[1][0] == (20.0, 20.0)
    rapids = [pts for kind, pts in parse_gcode_polylines(text) if kind == "rapid"]
    assert rapids[0][0] == (10.0, 10.0)
    assert rapids[0][-1] == (20.0, 20.0)


def test_nc_job_sequence_reads_tool_changes():
    from app.services.postprocess import nc_job_sequence

    isolation = nc_job_sequence("; Isolation Engraving\nT2 M6\nM5\n", "isolation.nc")
    assert isolation == [
        {
            "step": 1,
            "job": "Copper engraving",
            "detail": "Isolation",
            "tool": 2,
            "file": "isolation.nc",
        }
    ]
    bottom = nc_job_sequence(
        "; Isolation Engraving (bottom)\nT2 M6\nM5\n",
        "isolation_bottom.nc",
    )
    assert bottom[0]["job"] == "Copper bottom engraving"
    assert bottom[0]["detail"] == "Isolation"
    drill = nc_job_sequence(
        "; 2D Drilling\n"
        "; T3 holes Ø 0.8 mm drill (4)\nT3 M6\n"
        "; T4 holes Ø 1.0 mm pocket (2)\nT4 M6\n",
        "drill.nc",
    )
    assert [row["tool"] for row in drill] == [3, 4]
    assert drill[0]["detail"] == "Ø 0.8 mm · drill · 4 holes"
    assert drill[1]["detail"] == "Ø 1.0 mm · pocket · 2 holes"
    outline = nc_job_sequence("; Board Outline (outside)\nT4 M6\n", "outline.nc")
    assert outline[0]["job"] == "Board outline"
    assert outline[0]["tool"] == 4


def test_tool_change_is_its_own_job():
    from app.services.postprocess import nc_job_sequence, with_tool_change_jobs

    rows = with_tool_change_jobs(
        [
            *nc_job_sequence("; Isolation Engraving\nT2 M6\n", "isolation.nc"),
            *nc_job_sequence(
                "; T3 holes Ø 0.8 mm drill (4)\nT3 M6\n"
                "; T4 holes Ø 1.0 mm drill (2)\nT4 M6\n"
                "; T4 holes Ø 1.1 mm pocket (1)\nT4 M6\n",
                "drill.nc",
            ),
            *nc_job_sequence("; Board Outline (outside)\nT4 M6\n", "outline.nc"),
        ]
    )
    jobs = [(row["job"], row["detail"], row["tool"]) for row in rows]
    assert jobs[0] == ("Tool change", "Load T2", 2)
    assert jobs[1][0] == "Copper engraving"
    assert jobs[2] == ("Tool change", "T2 → T3", 3)
    assert ("Tool change", "T3 → T4", 4) in jobs
    assert [row["job"] for row in rows].count("Tool change") == 3
    assert rows[-1]["job"] == "Board outline"
    assert rows[-2]["job"] == "Drilling"


def test_split_contour_leaves_holding_tabs():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    parts = _split_contour_with_tabs(square, tab_count=4, tab_width_mm=2.0)
    assert len(parts) == 4
    for part in parts:
        assert len(part) >= 2


def test_step_down_does_not_chord_open_outline(tmp_path):
    from app.models import MachineSettings
    from app.services.postprocess import write_path_nc
    from app.services.toolpath import parse_gcode_paths

    nc = tmp_path / "outline.nc"
    write_path_nc(
        nc,
        [[(0.0, 50.0), (50.0, 50.0), (50.0, 0.0)]],
        settings=MachineSettings(step_down_mm=0.8),
        operation="Board Outline (outside)",
        tool_number=4,
        depth_mm=1.6,
        step_down_mm=0.8,
        close_open_paths=False,
    )
    cuts = parse_gcode_paths(nc.read_text())
    assert len(cuts) >= 2
    for poly in cuts:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            assert x0 == x1 or y0 == y1


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


def test_mask_paths_keep_more_than_400_points_on_large_curve():
    import cv2
    import numpy as np

    from app.services import parser
    from app.services.toolpath import _mask_to_paths

    dpmm = 50
    copper = np.zeros((160, 2200), dtype=np.uint8)
    cv2.rectangle(copper, (10, 60), (2190, 100), 255, thickness=-1)
    for x in range(20, 2180, 4):
        cv2.rectangle(copper, (x, 50), (x + 1, 110), 255, thickness=-1)
    bounds = parser.GerberBounds(0.0, 0.0, 2200 / dpmm, 160 / dpmm)
    paths = _mask_to_paths(copper, bounds, dpmm, retrieve=cv2.RETR_EXTERNAL)
    assert paths
    assert max(len(path) for path in paths) > 400


def test_offset_isolation_stays_outside_copper():
    import cv2
    import numpy as np

    from app.services import parser
    from app.services.toolpath import _expand_mask, _mask_to_paths

    dpmm = 50
    size = 400
    center = (200, 200)
    radius_px = 40
    offset_px = 10
    copper = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(copper, center, radius_px, 255, thickness=-1)
    expanded, pad = _expand_mask(copper, offset_px)
    bounds = parser.GerberBounds(0.0, 0.0, size / dpmm, size / dpmm)
    paths = _mask_to_paths(
        expanded, bounds, dpmm, retrieve=cv2.RETR_EXTERNAL, pad_px=pad
    )
    assert paths
    for path in paths:
        for x_mm, y_mm in path:
            px = x_mm * dpmm
            py = size - y_mm * dpmm
            dist = ((px - center[0]) ** 2 + (py - center[1]) ** 2) ** 0.5
            assert dist >= radius_px - 1.5
            assert dist <= radius_px + offset_px + 2.5


def test_isolation_skips_inner_pad_holes():
    import cv2
    import numpy as np

    from app.services import parser
    from app.services.toolpath import _expand_mask, _mask_to_paths

    dpmm = 50
    size = 300
    copper = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(copper, (150, 150), 50, 255, thickness=-1)
    cv2.circle(copper, (150, 150), 18, 0, thickness=-1)
    expanded, pad = _expand_mask(copper, 8)
    bounds = parser.GerberBounds(0.0, 0.0, size / dpmm, size / dpmm)
    skipped = _mask_to_paths(
        expanded, bounds, dpmm, outers_only=True, pad_px=pad
    )
    listed = _mask_to_paths(
        expanded, bounds, dpmm, retrieve=cv2.RETR_LIST, pad_px=pad
    )
    assert len(skipped) == 1
    assert len(listed) >= 2


def test_isolation_includes_slots_in_copper_pour():
    import cv2
    import numpy as np

    from app.services import parser
    from app.services.toolpath import _expand_mask, _mask_to_paths

    dpmm = 50
    size = 400
    copper = np.zeros((size, size), dtype=np.uint8)
    cv2.rectangle(copper, (10, 10), (390, 390), 255, thickness=-1)
    cv2.rectangle(copper, (240, 40), (360, 130), 0, thickness=-1)
    cv2.rectangle(copper, (280, 80), (330, 200), 0, thickness=-1)
    cv2.circle(copper, (80, 80), 22, 0, thickness=-1)
    cv2.circle(copper, (80, 80), 14, 255, thickness=-1)
    cv2.circle(copper, (80, 80), 6, 0, thickness=-1)
    expanded, pad = _expand_mask(copper, 5)
    bounds = parser.GerberBounds(0.0, 0.0, size / dpmm, size / dpmm)
    with_slots = _mask_to_paths(
        expanded, bounds, dpmm, skip_pad_holes=True, pad_px=pad
    )
    outers = _mask_to_paths(
        expanded, bounds, dpmm, outers_only=True, pad_px=pad
    )
    assert len(with_slots) > len(outers)
    slot_hits = []
    for path in with_slots:
        for x_mm, y_mm in path:
            x = x_mm * dpmm
            y = size - y_mm * dpmm
            if 230 <= x <= 370 and 30 <= y <= 210:
                slot_hits.append((x, y))
    assert slot_hits
    pad_hole = [
        (x_mm * dpmm, size - y_mm * dpmm)
        for path in with_slots
        for x_mm, y_mm in path
        if abs(x_mm * dpmm - 80) < 8 and abs(size - y_mm * dpmm - 80) < 8
    ]
    assert not pad_hole


def test_isolation_keeps_pads_inside_copper_pour():
    import cv2
    import numpy as np

    from app.services import parser
    from app.services.toolpath import _expand_mask, _mask_to_paths

    dpmm = 50
    size = 400
    copper = np.zeros((size, size), dtype=np.uint8)
    cv2.rectangle(copper, (20, 20), (380, 380), 255, thickness=-1)
    cv2.circle(copper, (200, 200), 40, 0, thickness=-1)
    cv2.circle(copper, (200, 200), 18, 255, thickness=-1)
    expanded, pad = _expand_mask(copper, 6)
    bounds = parser.GerberBounds(0.0, 0.0, size / dpmm, size / dpmm)
    isolation = _mask_to_paths(
        expanded, bounds, dpmm, outers_only=True, pad_px=pad
    )
    external = _mask_to_paths(
        expanded, bounds, dpmm, retrieve=cv2.RETR_EXTERNAL, pad_px=pad
    )
    assert len(isolation) >= 2
    assert len(external) == 1


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_simple_isolation_covers_pour_slot():
    import zipfile
    import tempfile
    from pathlib import Path

    from app.services.toolpath import _isolation_offsets_px, _isolation_paths

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(SIMPLE_ZIP).extractall(tmp)
        copper = next(Path(tmp).rglob("copper_top.gbr"))
        paths, _ = _isolation_paths(copper, _isolation_offsets_px(2, 1), dpmm=50)
    slot = [
        (x, y)
        for path in paths
        for x, y in path
        if 18.0 < x < 21.5 and 26.0 < y < 34.0
    ]
    assert slot


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
    assert result.json()["paths"]
    nc = client.get(f"/api/jobs/{job_id}/nc/all.nc")
    assert nc.status_code == 200
    text = nc.text
    assert "G90" in text
    assert "G1" in text or "G0" in text
    assert len(text) > 100
    assert "; Job sequence" in text
    assert "; SEQ 1 | Tool change |" in text
    assert text.find("; Begin isolation.nc") < text.find("; Begin drill.nc") < text.find(
        "; Begin outline.nc"
    )


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
def test_generate_copper_bottom_same_process():
    uploaded = client.post(
        "/api/jobs/upload",
        files={"file": ("simple.zip", SIMPLE_ZIP.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 200
    payload = uploaded.json()
    job_id = payload["job_id"]
    copper_top = next(item["name"] for item in payload["files"] if item["kind"] == "copper_top")
    copper_bottom = next(
        item["name"] for item in payload["files"] if item["kind"] == "copper_bottom"
    )
    settings = {"engraving_depth_mm": 0.15, "drill_depth_mm": 1.6}

    both = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": settings,
            "plan": {
                "copper": {
                    "layer": copper_top,
                    "tool_number": 2,
                    "engrave_mode": "contour",
                },
                "copper_bottom": {
                    "layer": copper_bottom,
                    "tool_number": 2,
                    "engrave_mode": "contour",
                },
            },
        },
    )
    assert both.status_code == 200, both.text
    names = both.json()["files"]
    assert "isolation.nc" in names
    assert "isolation_bottom.nc" in names
    top_nc = client.get(f"/api/jobs/{job_id}/nc/isolation.nc")
    bot_nc = client.get(f"/api/jobs/{job_id}/nc/isolation_bottom.nc")
    assert top_nc.status_code == 200
    assert bot_nc.status_code == 200
    assert b"Isolation Engraving" in top_nc.content
    assert b"Isolation Engraving (bottom)" in bot_nc.content
    assert top_nc.content != bot_nc.content
    assert any(path["file"] == "isolation_bottom.nc" for path in both.json()["paths"])

    preview = client.post(
        f"/api/jobs/{job_id}/preview-path",
        json={
            "settings": settings,
            "plan": {
                "copper_bottom": {
                    "layer": copper_bottom,
                    "tool_number": 2,
                    "engrave_mode": "contour",
                }
            },
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["paths"]
    assert all(
        path["file"] == "isolation_bottom.nc" for path in preview.json()["paths"]
    )

    pocket = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": settings,
            "plan": {
                "copper_bottom": {
                    "layer": copper_bottom,
                    "tool_number": 2,
                    "engrave_mode": "pocket",
                }
            },
        },
    )
    assert pocket.status_code == 200, pocket.text
    pocket_nc = client.get(f"/api/jobs/{job_id}/nc/isolation_bottom.nc")
    assert pocket_nc.status_code == 200
    assert b"Pocket Engraving (bottom)" in pocket_nc.content
    assert "isolation.nc" not in pocket.json()["files"]
    xs = [
        pt[0]
        for path in pocket.json()["paths"]
        if path.get("kind") == "cut"
        for pt in path["points"]
    ]
    ys = [
        pt[1]
        for path in pocket.json()["paths"]
        if path.get("kind") == "cut"
        for pt in path["points"]
    ]
    assert min(xs) <= 0.5
    assert max(xs) >= 35.0
    assert min(ys) <= 0.5
    assert max(ys) >= 36.0


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_pocket_includes_board_outline_for_other_copper_layer():
    import zipfile
    import tempfile
    from pathlib import Path

    from app.services import parser
    from app.services.toolpath import _pocket_paths

    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(SIMPLE_ZIP).extractall(tmp)
        root = Path(tmp)
        profile = next(root.rglob("profile.gbr"))
        silk = next(root.rglob("silkscreen_top.gbr"))
        outline = parser.parse_gerber_bounds(profile)
        paths, _ = _pocket_paths(silk, 2, outline_path=profile, dpmm=20)
    xs = [pt[0] for path in paths for pt in path]
    ys = [pt[1] for path in paths for pt in path]
    assert min(xs) <= outline.min_x + 0.6
    assert max(xs) >= outline.max_x - 0.6
    assert min(ys) <= outline.min_y + 0.6
    assert max(ys) >= outline.max_y - 0.6


@pytest.mark.skipif(not SIMPLE_ZIP.exists(), reason="missing TEST_Gerber_Simple.zip")
def test_copper_pocket_uses_profile_when_outline_omitted():
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
    assert result.status_code == 200, result.text
    xs = [
        pt[0]
        for path in result.json()["paths"]
        if path.get("kind") == "cut"
        for pt in path["points"]
    ]
    assert min(xs) <= 0.5
    assert max(xs) >= 35.0


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
