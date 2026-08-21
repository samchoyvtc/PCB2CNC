"""Regression smoke tests for zip ingest, preview, and NC generation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import parser, zip_ingest

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
ZIP_PATH = SAMPLES / "TEST_Gerber.zip"
COPPER = SAMPLES / "CAMOutputs" / "GerberFiles" / "copper_top.gbr"
DRILL = SAMPLES / "CAMOutputs" / "DrillFiles" / "drill_1_64.xln"

client = TestClient(app)


@pytest.fixture(scope="module")
def sample_zip_bytes() -> bytes:
    assert ZIP_PATH.exists(), f"missing fixture {ZIP_PATH}"
    return ZIP_PATH.read_bytes()


def test_classify_names():
    assert zip_ingest.classify_filename("copper_top.gbr") == "copper_top"
    assert zip_ingest.classify_filename("profile.gbr") == "profile"
    assert zip_ingest.classify_filename("drill_1_64.xln") == "drill"
    assert zip_ingest.classify_filename("._copper_top.gbr") is None
    assert zip_ingest.classify_filename(".DS_Store") is None
    assert zip_ingest.is_junk_cam_path(Path("__MACOSX/._copper_top.gbr"))


def test_skip_appledouble_in_zip(tmp_path: Path, sample_zip_bytes: bytes):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        with zipfile.ZipFile(io.BytesIO(sample_zip_bytes)) as src:
            for info in src.infolist():
                if info.is_dir():
                    continue
                zf.writestr(info.filename, src.read(info))
        # macOS resource-fork junk that previously broke preview
        zf.writestr(
            "CAMOutputs/GerberFiles/._copper_top.gbr",
            b"\x00\x05\x16\x07" + b"\x00" * 50,
        )
        zf.writestr("__MACOSX/._profile.gbr", b"\x00\x00junk")
    result = zip_ingest.extract_zip(buf.getvalue())
    names = [f["name"] for f in result["files"]]
    assert all(not n.startswith("._") for n in names)
    assert any(f["kind"] == "copper_top" for f in result["files"])


def test_excellon_parse():
    hits = parser.parse_excellon(DRILL)
    assert len(hits) == 4
    xs = sorted(h.x for h in hits)
    assert xs[0] == pytest.approx(11.9, abs=0.05)
    assert hits[0].diameter == pytest.approx(1.0, abs=0.01)


def test_upload_preview_generate(sample_zip_bytes: bytes):
    res = client.post(
        "/api/jobs/upload",
        files={"file": ("TEST_Gerber.zip", sample_zip_bytes, "application/zip")},
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    files = res.json()["files"]
    kinds = {f["kind"] for f in files}
    assert "copper_top" in kinds
    assert "drill" in kinds

    prev = client.get(f"/api/jobs/{job_id}/preview")
    assert prev.status_code == 200, prev.text
    body = prev.json()
    assert body["bounds"] is not None
    assert body["bounds"]["width"] > 0
    assert any(l["kind"] == "copper_top" and l["image_png_base64"] for l in body["layers"])
    assert len(body["drills"]) == 4

    gen = client.post(
        f"/api/jobs/{job_id}/generate",
        json={
            "settings": {
                "engraving_depth_mm": 0.15,
                "feed_mm_min": 2000,
                "spindle_rpm": 12000,
                "safe_z_mm": 15,
                "stock_thickness_mm": 1.5,
            }
        },
    )
    assert gen.status_code == 200, gen.text
    gbody = gen.json()
    assert "all.nc" in gbody["files"]
    assert gbody["toolpath_preview_png_base64"]

    nc = client.get(f"/api/jobs/{job_id}/nc/all.nc")
    assert nc.status_code == 200
    text = nc.text
    assert "G90" in text
    assert "G1" in text or "G0" in text
    assert len(text) > 100


def test_reject_non_zip():
    res = client.post(
        "/api/jobs/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 400
