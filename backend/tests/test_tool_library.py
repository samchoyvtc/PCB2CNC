"""Tests for PAEN_TOOLS.tlslibrary parsing and API."""

from __future__ import annotations

import json
import struct
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


def _u16be(text: str) -> bytes:
    raw = text.encode("utf-16-be")
    return len(raw).to_bytes(4, "big") + raw


def _pack_paen_tool(
    *,
    guid: str,
    name: str,
    number: int,
    type_id: int,
    geom: list[float],
    materials: list[tuple] | None = None,
) -> bytes:
    body = _u16be(guid) + _u16be(name)
    body += struct.pack(">II", number, type_id)
    body += b"".join(struct.pack(">d", v) for v in geom)
    mats = materials or []
    body += struct.pack(">I", len(mats))
    for mat in mats:
        material, rpm, feed, plunge, *rest = mat
        stepover = rest[0] if len(rest) > 0 else 50.0
        stepdown = rest[1] if len(rest) > 1 else 0.1
        coolant = rest[2] if len(rest) > 2 else 1
        body += _u16be("{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}")
        body += _u16be("{ffffffff-1111-2222-3333-444444444444}")
        body += _u16be(material)
        body += struct.pack(">iii", rpm, feed, plunge)
        body += struct.pack(">ddii", stepover, stepdown, coolant, 0)
    return body


def _pack_paen_library(tools: list[bytes]) -> bytes:
    header = struct.pack(">i", 1)
    header += struct.pack(">i", -1)
    header += _u16be("{795a771c-638a-4bf0-a738-436113655de0}")
    header += _u16be("PAEN Tools")
    header += struct.pack(">i", -1)
    header += struct.pack(">I", len(tools))
    return header + b"".join(tools)


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


def test_large_json_does_not_hit_csv_field_limit():
    """Regression: nested JSON without a top-level tools[] used to fall through to CSV
    and raise '_csv.Error: field larger than field limit (131072)'."""
    tool = {
        "name": "Engraver",
        "type": "vbit",
        "geometry": {"diameter": 0.1, "angle": 30},
        "feed": 800,
        "rpm": 12000,
        "blob": "x" * 200_000,  # oversized nested string should be ignored
    }
    payload = {"library": {"categories": [{"tools": [tool]}]}}
    raw = json.dumps(payload).encode("utf-8")
    assert len(raw) > 131072
    tools, columns = tool_library.parse_tool_library_bytes(raw, "PAEN_TOOLS.tlslibrary")
    assert len(tools) >= 1
    assert any("name" in c.lower() or c == "name" for c in columns)
    assert tools[0].get("name") == "Engraver"


def test_csv_field_limit_raised_for_wide_cells():
    huge = "A" * 200_000
    text = f"Tool,Name,Notes\n1,Bit,{huge}\n"
    tools, columns = tool_library.parse_tool_library_bytes(
        text.encode("utf-8"), "wide.csv"
    )
    assert len(tools) == 1
    assert "Notes" in columns
    assert str(tools[0]["Notes"]).endswith("...")


def test_api_upload_and_get(tmp_path: Path, monkeypatch):
    dest = tmp_path / "PAEN_TOOLS.tlslibrary"
    monkeypatch.setattr(
        tool_library,
        "DEFAULT_LIBRARY_CANDIDATES",
        (dest,),
    )

    res = client.get("/api/machine/tools")
    assert res.status_code == 200
    assert res.json()["tools"] == []

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


def test_parse_paen_binary_library():
    blob = _pack_paen_library(
        [
            _pack_paen_tool(
                guid="{9c48a210-851f-4616-bc91-47f1947a3742}",
                name="0.2mm*30° Engraving(Metal)",
                number=2,
                type_id=3,
                geom=[3.175, 0.0, 0.2, 15.0, 0.0, 0.0, 0.0, 0.0],
                materials=[("PCB", 12000, 2000, 200, 50.0, 0.1, 1), ("Copper", 12000, 300, 100)],
            ),
            _pack_paen_tool(
                guid="{c6b81063-8fee-463e-98e5-dee46bdb69ef}",
                name="0.8mm Corn",
                number=3,
                type_id=1,
                geom=[0.8, 0.0, 0.8, 0.0, 0.0, 0.0, 0.0, 0.0],
                materials=[("PCB", 12000, 500, 300, 60.0, 0.3, 0)],
            ),
            _pack_paen_tool(
                guid="{7ca77d40-9304-47d0-9056-22276af5eaf0}",
                name="0.3mm*30° Solder Mask Removal",
                number=1,
                type_id=3,
                geom=[3.175, 0.0, 0.3, 15.0, 0.0, 0.0, 0.0, 0.0],
                materials=[("PCB", 6000, 400, 200, 66.667, 0.2, 1)],
            ),
        ]
    )
    tools, columns = tool_library.parse_tool_library_bytes(blob, "PAEN_TOOLS.tlslibrary")
    assert [t["Number"] for t in tools] == [1, 2, 3]
    assert tools[0]["Name"] == "0.3mm*30° Solder Mask Removal"
    assert tools[0]["Spindle Speed"] == 6000
    assert tools[0]["Step Over"] == 66.667
    assert tools[0]["Step Down"] == 0.2
    assert tools[0]["Feed Rate"] == 400
    assert tools[0]["Plunge Rate"] == 200
    assert tools[0]["Coolant"] == "Y"
    assert tools[1]["Name"] == "0.2mm*30° Engraving (Metal)"
    assert tools[1]["Type"] == "Engraving"
    assert tools[1]["Diameter(D)"] == 3.175
    assert tools[1]["Tip Diameter(F)"] == 0.2
    assert tools[1]["Half Angle(A)"] == 15
    assert tools[1]["Feed Rate"] == 2000
    assert tools[1]["Spindle Speed"] == 12000
    assert tools[1]["Material"] == "PCB"
    assert tools[2]["Type"] == "Flat End"
    assert tools[2]["Coolant"] == "N"
    assert "Name" in columns
    assert "Tip Diameter(F)" in columns
    assert "Step Over" in columns


def test_parse_real_paen_tools_library_if_present():
    path = Path(__file__).resolve().parents[2] / "PAEN_TOOLS.tlslibrary"
    if not path.is_file():
        return
    tools, columns = tool_library.parse_tool_library_bytes(path.read_bytes(), path.name)
    by_number = {int(t["Number"]): t for t in tools}
    assert set(by_number) == {1, 2, 3, 4, 5, 6}
    assert by_number[2]["Name"] == "0.2mm*30° Engraving (Metal)"
    assert by_number[1]["Name"] == "0.3mm*30° Solder Mask Removal"
    assert by_number[3]["Name"] == "0.8mm Corn"
    assert by_number[4]["Name"] == "1mm Corn"
    assert by_number[5]["Name"] == "1.5mm Corn"
    assert by_number[6]["Name"] == "2mm Corn"
    assert by_number[2]["Type"] == "Engraving"
    assert by_number[3]["Type"] == "Flat End"
    assert float(by_number[2]["Tip Diameter(F)"]) == 0.2
    assert float(by_number[2]["Half Angle(A)"]) == 15
    assert by_number[1]["Spindle Speed"] == 6000
    assert float(by_number[1]["Step Over"]) == 66.667
    assert float(by_number[1]["Step Down"]) == 0.2
    assert by_number[1]["Feed Rate"] == 400
    assert by_number[1]["Plunge Rate"] == 200
    assert by_number[1]["Coolant"] == "Y"
    assert by_number[2]["Spindle Speed"] == 12000
    assert by_number[2]["Feed Rate"] == 2000
    assert by_number[2]["Plunge Rate"] == 200
    assert float(by_number[2]["Step Over"]) == 50
    assert float(by_number[2]["Step Down"]) == 0.1
    assert "Number" in columns and "Name" in columns
    assert "Spindle Speed" in columns
