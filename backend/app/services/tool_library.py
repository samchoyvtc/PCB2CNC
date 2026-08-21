"""Parse machine tool libraries (PAEN_TOOLS.tlslibrary and similar)."""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY_CANDIDATES = (
    ROOT / "samples" / "PAEN_TOOLS.tlslibrary",
    ROOT / "PAEN_TOOLS.tlslibrary",
    ROOT / "backend" / "data" / "PAEN_TOOLS.tlslibrary",
    ROOT / "data" / "PAEN_TOOLS.tlslibrary",
)

# Prefer these keys when present; remaining keys still shown.
PREFERRED_COLUMNS = (
    "tool",
    "number",
    "name",
    "type",
    "geometry",
    "diameter",
    "diameter_mm",
    "tip",
    "angle",
    "flutes",
    "feed",
    "feed_mm_min",
    "plunge",
    "plunge_mm_min",
    "spindle",
    "rpm",
    "spindle_rpm",
    "depth",
    "depth_mm",
    "stepover",
    "notes",
    "description",
)


def resolve_library_path(explicit: Path | None = None) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    for path in DEFAULT_LIBRARY_CANDIDATES:
        if path.is_file():
            return path
    return None


def load_tool_library(path: Path | None = None) -> dict[str, Any]:
    resolved = resolve_library_path(path)
    if resolved is None:
        return {
            "source": None,
            "columns": [],
            "tools": [],
            "message": "PAEN_TOOLS.tlslibrary not found. Place it under samples/ or upload it.",
        }
    tools, columns = parse_tool_library_bytes(resolved.read_bytes(), resolved.name)
    return {
        "source": str(resolved.name),
        "path": str(resolved),
        "columns": columns,
        "tools": tools,
        "message": f"Loaded {len(tools)} tools from {resolved.name}",
    }


def parse_tool_library_bytes(data: bytes, filename: str = "library") -> tuple[list[dict[str, Any]], list[str]]:
    text = _decode_text(data)
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return [], []

    # JSON
    if stripped[0] in "{[":
        try:
            payload = json.loads(stripped)
            rows = _rows_from_json(payload)
            if rows:
                return _normalize_rows(rows)
        except json.JSONDecodeError:
            pass

    # XML
    if stripped.startswith("<"):
        try:
            rows = _rows_from_xml(stripped)
            if rows:
                return _normalize_rows(rows)
        except ET.ParseError:
            pass

    # Delimited table (CSV / TSV / semicolon — Estlcam-style)
    rows = _rows_from_delimited(stripped)
    if rows:
        return _normalize_rows(rows)

    # INI / key=value blocks
    rows = _rows_from_ini_blocks(stripped)
    if rows:
        return _normalize_rows(rows)

    # Fallback: treat non-empty lines as name-only tools
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if lines:
        rows = [{"name": ln} for ln in lines]
        return _normalize_rows(rows)

    raise ValueError(f"Unrecognized tool library format in {filename}")


def save_uploaded_library(data: bytes, dest: Path | None = None) -> Path:
    target = dest or DEFAULT_LIBRARY_CANDIDATES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    # Validate parse before writing
    parse_tool_library_bytes(data, target.name)
    target.write_bytes(data)
    return target


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _rows_from_json(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("tools", "Tools", "library", "items", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
        # Single tool object with scalar fields
        if any(isinstance(v, (str, int, float, bool)) or v is None for v in payload.values()):
            nested = [v for v in payload.values() if isinstance(v, list)]
            if not nested:
                return [payload]
    return []


def _rows_from_xml(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text)
    rows: list[dict[str, Any]] = []
    candidates = list(root.findall(".//tool")) + list(root.findall(".//Tool"))
    if not candidates:
        candidates = [el for el in root.iter() if el is not root and (el.attrib or list(el))]
    for el in candidates:
        row: dict[str, Any] = {}
        row.update({k: v for k, v in el.attrib.items()})
        for child in list(el):
            if len(list(child)) == 0:
                key = child.tag.split("}")[-1]
                row[key] = (child.text or "").strip()
        if row:
            rows.append(row)
    return rows


def _rows_from_delimited(text: str) -> list[dict[str, Any]]:
    sample = "\n".join(text.splitlines()[:8])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        if "\t" in sample:
            delimiter = "\t"
        elif sample.count(";") >= sample.count(","):
            delimiter = ";"
        else:
            delimiter = ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return []
    # Require at least one header that looks tool-related, or >=2 columns
    headers = [h for h in reader.fieldnames if h is not None]
    if len(headers) < 1:
        return []
    rows = []
    for raw in reader:
        row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        if any(str(v).strip() for v in row.values() if v is not None):
            rows.append(row)
    return rows


def _rows_from_ini_blocks(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    rows: list[dict[str, Any]] = []
    section_re = re.compile(r"^\[(.+)\]\s*$")
    kv_re = re.compile(r"^([A-Za-z0-9_ .-]+)\s*[=:]\s*(.*)$")
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith(("#", ";"))]
        if not lines:
            continue
        row: dict[str, Any] = {}
        for ln in lines:
            m_sec = section_re.match(ln)
            if m_sec:
                row.setdefault("name", m_sec.group(1).strip())
                continue
            m_kv = kv_re.match(ln)
            if m_kv:
                row[m_kv.group(1).strip()] = m_kv.group(2).strip()
        if len(row) >= 2 or ("name" in row and len(row) >= 1):
            rows.append(row)
    return rows


def _normalize_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    cleaned: list[dict[str, Any]] = []
    key_set: list[str] = []
    seen: set[str] = set()

    def add_key(k: str) -> None:
        if k not in seen:
            seen.add(k)
            key_set.append(k)

    for row in rows:
        out: dict[str, Any] = {}
        for k, v in row.items():
            key = str(k).strip()
            if not key:
                continue
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                out[key] = json.dumps(v, ensure_ascii=False)
            else:
                out[key] = v
            add_key(key)
        if out:
            cleaned.append(out)

    preferred = [k for k in PREFERRED_COLUMNS if k in seen]
    # Case-insensitive preferred match
    lower_map = {k.lower(): k for k in key_set}
    for pref in PREFERRED_COLUMNS:
        actual = lower_map.get(pref.lower())
        if actual and actual not in preferred:
            preferred.append(actual)
    rest = [k for k in key_set if k not in preferred]
    columns = preferred + rest
    # Stable display rows with only known columns order
    ordered = [{c: r.get(c, "") for c in columns} for r in cleaned]
    return ordered, columns
