"""Tests for PAEN_TOOLS.tlslibrary parsing and API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import tool_library

client = TestClient(app)


CSV_SAMPLE = """Tool;Name;Type;Diameter_mm;Flutes;Feed_mm_min;Spindle_RPM;Depth_mm
1;V-Bit 0.1;Engrave;0.1;1;800;12000;0.15
2;Endmill 1.0;Contour;1.0;2;1200;10000;1.5
3;Drill 0.8;Drill;0.8;2;300;8000;
"""

JSON_SAMPLE = """
{
  "tools": [
    {"tool": 1, "name": "Engraver", "diameter_mm": 0.2, "feed": 1000, "rpm": 12000},
    {"tool": 2, "name": "Cutout", "diameter_mm": 2.0, "feed": 1500, "rpm": 10000}
  ]
}
"""


def test_parse_csv_semicolon(tmp_path: Path):
    path = tmp_path / "PAEN_TOOLS.tlslibrary"
    path.write_text(CSV_SAMPLE, encoding="utf-8")
    tools, columns = tool_library.parse_tool_library_bytes(path.read_bytes(), path.name)
    assert len(tools) == 3
    assert "Tool" in columns or "Name" in columns
    assert tools[0]["Name"] == "V-Bit 0.1"
    assert float(tools[1]["Diameter_mm"]) == 1.0


def test_parse_json():
    tools, columns = tool_library.parse_tool_library_bytes(
        JSON_SAMPLE.encode("utf-8"), "x.json"
    )
    assert len(tools) == 2
    assert "name" in columns
    assert tools[1]["name"] == "Cutout"


def test_api_upload_and_get(tmp_path: Path, monkeypatch):
    dest = tmp_path / "PAEN_TOOLS.tlslibrary"
    monkeypatch.setattr(
        tool_library,
        "DEFAULT_LIBRARY_CANDIDATES",
        (dest,),
    )

    # Missing initially
    res = client.get("/api/machine/tools")
    assert res.status_code == 200
    body = res.json()
    assert body["tools"] == []

    up = client.post(
        "/api/machine/tools/upload",
        files={"file": ("PAEN_TOOLS.tlslibrary", CSV_SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["tools"]
    assert dest.is_file()

    again = client.get("/api/machine/tools")
    assert again.status_code == 200
    assert len(again.json()["tools"]) == 3
