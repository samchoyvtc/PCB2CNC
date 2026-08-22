"""Unit tests for generate-plan helpers (tabs, tool defaults)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.toolpath import _default_drill_tool, _split_contour_with_tabs

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


def test_default_drill_tool_picks_closest_larger_tip():
    tools = [
        {"Number": 3, "Tip Diameter(F)": 0.8},
        {"Number": 4, "Tip Diameter(F)": 1.0},
        {"Number": 5, "Tip Diameter(F)": 1.5},
        {"Number": 6, "Tip Diameter(F)": 2.0},
    ]
    assert _default_drill_tool(0.8, tools) == 3
    assert _default_drill_tool(1.0, tools) == 4
    assert _default_drill_tool(1.2, tools) == 5
    assert _default_drill_tool(3.0, tools) == 6


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
