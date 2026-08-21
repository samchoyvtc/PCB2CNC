"""Zip upload ingest and Gerber/Excellon file classification."""

from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

JOBS_ROOT = Path(__file__).resolve().parents[3] / "data" / "jobs"

GERBER_EXTS = {".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gko", ".gm1", ".pho"}
DRILL_EXTS = {".xln", ".drl", ".exc"}
SKIP_NAMES = {"gerber_job.gbrjob"}


def ensure_jobs_root() -> Path:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    return JOBS_ROOT


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def job_dir(job_id: str) -> Path:
    return ensure_jobs_root() / job_id


def is_junk_cam_path(path: Path) -> bool:
    """Skip macOS AppleDouble / resource-fork junk from zip extracts."""
    name = path.name
    if name.startswith("._") or name.startswith("."):
        return True
    parts = {p.lower() for p in path.parts}
    if "__macosx" in parts:
        return True
    return False


def _looks_like_bom(path: Path) -> bool:
    try:
        head = path.read_text(errors="replace")[:240].lower()
    except OSError:
        return False
    return "partlist" in head or head.lstrip().startswith("qty")


def classify_cam_path(path: Path) -> str | None:
    """Classify using filename + folder context (Assembly → BOM, etc.)."""
    name = path.name
    lower = name.lower()
    parts = [p.lower() for p in path.parts]

    if is_junk_cam_path(path):
        return None
    if name.lower() in SKIP_NAMES or lower.endswith(".gbrjob"):
        return None
    if "pnp_" in lower:
        return None

    # Assembly part list / BOM (e.g. PCB v2.txt) — ignored in UI
    if lower.endswith(".txt"):
        in_assembly = "assembly" in parts
        if in_assembly or _looks_like_bom(path):
            return None
        if "drill" in lower:
            return "drill"
        return None

    return classify_filename(name)


def classify_filename(name: str) -> str | None:
    """Return layer kind from filename, or None if ignored."""
    lower = name.lower()
    stem = Path(lower).stem
    ext = Path(lower).suffix

    # AppleDouble companions look like ._<realfile>.gbr and are not Gerber
    if name.startswith("._") or name.startswith("."):
        return None
    if name.lower() in SKIP_NAMES or lower.endswith(".gbrjob"):
        return None
    if "pnp_" in lower:
        return None

    # BOM filenames often look like "PCB v2.txt" — ignored
    if lower.endswith(".txt"):
        if "drill" in stem:
            return "drill"
        if "partlist" in stem or stem.startswith("pcb"):
            return None
        return None

    if ext in DRILL_EXTS or stem.startswith("drill") or re.search(r"(^|[_-])drill([_-]|$)", stem):
        return "drill"

    if ext not in GERBER_EXTS and ext not in {".gbr"}:
        if ext not in GERBER_EXTS:
            return None

    rules: list[tuple[str, str]] = [
        (r"copper[_-]?top|top[_-]?copper|\.gtl$|_gtl|frontcopper", "copper_top"),
        (r"copper[_-]?bottom|bottom[_-]?copper|\.gbl$|_gbl|backcopper", "copper_bottom"),
        (r"profile|outline|edge[_-]?cuts|\.gko$|dimension|boardoutline", "profile"),
        (r"soldermask[_-]?top|mask[_-]?top|\.gts$", "soldermask_top"),
        (r"soldermask[_-]?bottom|mask[_-]?bottom|\.gbs$", "soldermask_bottom"),
        (r"silkscreen[_-]?top|silk[_-]?top|\.gto$", "silkscreen_top"),
        (r"silkscreen[_-]?bottom|silk[_-]?bottom|\.gbo$", "silkscreen_bottom"),
        (r"solderpaste[_-]?top|paste[_-]?top|\.gtp$", "solderpaste_top"),
        (r"solderpaste[_-]?bottom|paste[_-]?bottom|\.gbp$", "solderpaste_bottom"),
    ]
    for pattern, kind in rules:
        if re.search(pattern, lower):
            return kind

    if ext in GERBER_EXTS or ext == ".gbr":
        return "gerber_other"
    return None


def extract_zip(upload_bytes: bytes, job_id: str | None = None) -> dict[str, Any]:
    """Unpack zip into a job directory and classify CAM files."""
    jid = job_id or new_job_id()
    root = job_dir(jid)
    if root.exists():
        shutil.rmtree(root)
    raw = root / "raw"
    raw.mkdir(parents=True)

    zip_path = root / "upload.zip"
    zip_path.write_bytes(upload_bytes)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(raw)

    classified: list[dict[str, Any]] = []
    for path in sorted(raw.rglob("*")):
        if not path.is_file():
            continue
        if is_junk_cam_path(path):
            continue
        # Skip binary AppleDouble payloads that somehow lost the ._ prefix
        try:
            head = path.read_bytes()[:4]
        except OSError:
            continue
        if head.startswith(b"\x00\x05\x16\x07") or head.startswith(b"\x00\x00"):
            continue
        kind = classify_cam_path(path)
        if kind is None:
            continue
        dest_dir = root / "cam"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / path.name
        # Avoid collisions
        if dest.exists():
            dest = dest_dir / f"{path.stem}_{kind}{path.suffix}"
        shutil.copy2(path, dest)
        classified.append(
            {
                "name": dest.name,
                "kind": kind,
                "path": str(dest.relative_to(root)),
                "size": dest.stat().st_size,
            }
        )

    if not any(f["kind"] == "copper_top" for f in classified):
        # Prefer any copper as top for single-sided MVP
        for f in classified:
            if f["kind"] == "copper_bottom":
                f["kind"] = "copper_top"
                break

    copper = [f for f in classified if f["kind"] == "copper_top"]
    if not copper:
        raise ValueError(
            "No copper Gerber layer found in zip. Expected a file like copper_top.gbr."
        )

    return {"job_id": jid, "files": classified, "root": str(root)}


def list_cam_files(job_id: str) -> list[dict[str, Any]]:
    root = job_dir(job_id)
    cam = root / "cam"
    if not cam.exists():
        raise FileNotFoundError(f"Job {job_id} not found")
    files = []
    for path in sorted(cam.iterdir()):
        if not path.is_file() or is_junk_cam_path(path):
            continue
        files.append(
            {
                "name": path.name,
                "kind": classify_filename(path.name) or "unknown",
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
            }
        )
    # Re-apply kind from stored classification when possible
    meta = root / "files.json"
    if meta.exists():
        import json

        stored = json.loads(meta.read_text())
        by_name = {f["name"]: f for f in stored}
        for f in files:
            if f["name"] in by_name:
                f["kind"] = by_name[f["name"]]["kind"]
    return files


def save_file_index(job_id: str, files: list[dict[str, Any]]) -> None:
    import json

    root = job_dir(job_id)
    (root / "files.json").write_text(json.dumps(files, indent=2))


def resolve_cam_path(job_id: str, relative: str) -> Path:
    root = job_dir(job_id)
    path = (root / relative).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ValueError("Invalid path")
    return path
