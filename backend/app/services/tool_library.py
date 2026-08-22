"""Parse machine tool libraries (PAEN_TOOLS.tlslibrary and similar)."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import struct
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Large tool libraries (JSON with geometry blobs) exceed the default 128 KiB CSV field cap.
csv.field_size_limit(min(sys.maxsize, 32 * 1024 * 1024))

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY_CANDIDATES = (
    ROOT / "samples" / "PAEN_TOOLS.tlslibrary",
    ROOT / "PAEN_TOOLS.tlslibrary",
    ROOT / "backend" / "data" / "PAEN_TOOLS.tlslibrary",
    ROOT / "data" / "PAEN_TOOLS.tlslibrary",
)

PREFERRED_COLUMNS = (
    "tool",
    "number",
    "name",
    "type",
    "geometry",
    "diameter",
    "diameter_mm",
    "Diameter(D)",
    "Angle(A)",
    "tip",
    "Tip Diameter(F)",
    "Half Angle(A)",
    "CornerRadius(R)",
    "Specification",
    "Screw pitch",
    "Hole Diameter",
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
    "Material",
    "Spindle Speed",
    "Step Over",
    "Step Down",
    "Feed Rate",
    "Plunge Rate",
    "Coolant",
    "notes",
    "description",
)

_PAEN_MAGIC = b"\x00\x00\x00\x01\xff\xff\xff\xff"
_PAEN_GUID_PREFIX = b"\x00\x00\x00\x4c\x00\x7b"
_PAEN_MATERIAL_NAMES = frozenset(
    {
        "copper",
        "aluminum",
        "plastic",
        "pcb",
        "softwood",
        "hardwood",
        "brass",
        "carbon fiber",
        "paen tools",
    }
)
_PAEN_TYPE_NAMES = {
    1: "Flat End",
    2: "Ball End",
    3: "Engraving",
    4: "Drill",
}

_TOOLISH_KEYS = {
    "diameter",
    "diameter_mm",
    "tool",
    "tool_number",
    "toolnumber",
    "number",
    "flutes",
    "flute",
    "feed",
    "feedrate",
    "spindle",
    "rpm",
    "geometry",
    "type",
    "tipdiameter",
    "cuttingdiameter",
}


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
    if not data:
        return [], []

    # SQLite tool DBs (Vectric / MillMage-style)
    if data[:16].startswith(b"SQLite format 3"):
        rows = _rows_from_sqlite(data)
        if rows:
            return _normalize_rows(rows)

    # Zip containers (some CAM libraries)
    if data[:2] == b"PK":
        rows = _rows_from_zip(data)
        if rows:
            return _normalize_rows(rows)

    # PAEN / MillMage-style binary .tlslibrary (UTF-16-BE length-prefixed records)
    if _looks_like_paen_library(data):
        rows = _rows_from_paen_library(data)
        if rows:
            return _normalize_rows(rows)

    text = _decode_text(data)
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return [], []

    # JSON (including Fusion-style nested libraries)
    json_payload = _try_load_json(stripped)
    if json_payload is not None:
        rows = _rows_from_json(json_payload)
        if rows:
            return _normalize_rows(rows)

    # XML
    if stripped.lstrip().startswith("<"):
        try:
            rows = _rows_from_xml(stripped)
            if rows:
                return _normalize_rows(rows)
        except ET.ParseError:
            pass

    # Delimited table (CSV / TSV / semicolon — Estlcam-style)
    try:
        rows = _rows_from_delimited(stripped)
        if rows:
            return _normalize_rows(rows)
    except csv.Error:
        # Oversized / binary-looking content — try other strategies below.
        pass

    # INI / key=value blocks
    rows = _rows_from_ini_blocks(stripped)
    if rows:
        return _normalize_rows(rows)

    # Fallback: non-empty lines as name-only tools (skip huge blobs)
    lines = [
        ln.strip()
        for ln in stripped.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and len(ln) < 500
    ]
    if lines and len(lines) <= 500:
        rows = [{"name": ln} for ln in lines]
        return _normalize_rows(rows)

    raise ValueError(
        f"Unrecognized tool library format in {filename} "
        f"({len(data)} bytes). Export as CSV/JSON if possible."
    )


def save_uploaded_library(data: bytes, dest: Path | None = None) -> Path:
    """Persist upload first, then validate parse (keeps file for retry/debug)."""
    target = dest or DEFAULT_LIBRARY_CANDIDATES[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    parse_tool_library_bytes(data, target.name)
    return target


def _looks_like_paen_library(data: bytes) -> bool:
    if len(data) < 16:
        return False
    if data[:8] == _PAEN_MAGIC:
        return True
    return _PAEN_GUID_PREFIX in data[:4096] and b"\x00P\x00A\x00E\x00N" in data[:4096]


def _paen_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _paen_i32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">i", data, offset)[0]


def _paen_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from(">d", data, offset)[0]


def _paen_str(data: bytes, offset: int) -> tuple[str | None, int]:
    if offset + 4 > len(data):
        raise ValueError("truncated PAEN string")
    nbytes = _paen_u32(data, offset)
    if nbytes == 0xFFFFFFFF:
        return None, offset + 4
    if nbytes > 4000 or nbytes % 2 != 0 or offset + 4 + nbytes > len(data):
        raise ValueError("invalid PAEN string")
    raw = data[offset + 4 : offset + 4 + nbytes]
    text = raw.decode("utf-16-be") if nbytes else ""
    return text, offset + 4 + nbytes


def _paen_short_number(value: float) -> float | int:
    rounded = round(float(value), 4)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def _paen_display_name(name: str) -> str:
    return re.sub(r"(?<=\S)\(", " (", name).strip()


def _rows_from_paen_library(data: bytes) -> list[dict[str, Any]]:
    """Extract tool table rows from a PAEN binary .tlslibrary.

    Records are UTF-16-BE length-prefixed. 3D mesh blobs after each tool are
    skipped by scanning for the next GUID header rather than walking them.
    """
    rows: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    idx = 0
    while True:
        start = data.find(_PAEN_GUID_PREFIX, idx)
        if start < 0:
            break
        idx = start + 1
        try:
            guid, after_guid = _paen_str(data, start)
            name, after_name = _paen_str(data, after_guid)
        except ValueError:
            continue
        if not guid or not guid.startswith("{") or guid.count("-") != 4:
            continue
        if not name or name.startswith("{") or name.lower() in _PAEN_MATERIAL_NAMES:
            continue
        header_end = after_name + 8 + 64
        if header_end + 4 > len(data):
            continue
        number = _paen_u32(data, after_name)
        type_id = _paen_u32(data, after_name + 4)
        if not (1 <= number <= 99 and type_id <= 20):
            continue
        geom = [_paen_f64(data, after_name + 8 + i * 8) for i in range(8)]
        if not (0 <= geom[0] <= 50 and 0 <= geom[2] <= 50):
            continue
        if number in seen_numbers:
            continue
        seen_numbers.add(number)
        row: dict[str, Any] = {
            "Number": number,
            "Name": _paen_display_name(name),
            "Type": _PAEN_TYPE_NAMES.get(type_id, str(type_id)),
            "Diameter(D)": _paen_short_number(geom[0]),
            "Angle(A)": _paen_short_number(geom[1]),
            "Tip Diameter(F)": _paen_short_number(geom[2]),
            "Half Angle(A)": _paen_short_number(geom[3]),
            "CornerRadius(R)": _paen_short_number(geom[4]),
            "Specification": _paen_short_number(geom[5]),
            "Screw pitch": _paen_short_number(geom[6]),
            "Hole Diameter": _paen_short_number(geom[7]),
        }
        cuts = _paen_pcb_cuts(data, header_end)
        if cuts:
            row.update(cuts)
        rows.append(row)
        idx = header_end
    rows.sort(key=lambda r: int(r.get("Number") or 0))
    return rows


def _paen_pcb_cuts(data: bytes, nmat_offset: int) -> dict[str, Any]:
    """PCB-only cutting properties that follow each tool's geometry block."""
    try:
        nmat = _paen_u32(data, nmat_offset)
    except struct.error:
        return {}
    if nmat == 0 or nmat > 32:
        return {}
    offset = nmat_offset + 4
    for _ in range(nmat):
        try:
            _g1, offset = _paen_str(data, offset)
            _g2, offset = _paen_str(data, offset)
            material, offset = _paen_str(data, offset)
            if offset + 36 > len(data):
                break
            spindle = _paen_i32(data, offset)
            feed = _paen_i32(data, offset + 4)
            plunge = _paen_i32(data, offset + 8)
            stepover = _paen_f64(data, offset + 12)
            stepdown = _paen_f64(data, offset + 20)
            coolant = _paen_i32(data, offset + 28)
            offset += 36
        except (ValueError, struct.error):
            break
        if (material or "").strip().lower() != "pcb":
            continue
        if not (1000 <= spindle <= 60000 and 0 < feed <= 10000 and 0 < plunge <= 3000):
            continue
        return {
            "Material": "PCB",
            "Spindle Speed": spindle,
            "Step Over": _paen_short_number(stepover),
            "Step Down": _paen_short_number(stepdown),
            "Feed Rate": feed,
            "Plunge Rate": plunge,
            "Coolant": "Y" if coolant else "N",
        }
    return {}


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _try_load_json(text: str) -> Any | None:
    candidates = [text.strip()]
    # Sometimes libraries have a BOM/preamble before the JSON object.
    for opener in ("{", "["):
        idx = text.find(opener)
        if idx > 0:
            candidates.append(text[idx:].strip())
    for cand in candidates:
        if not cand or cand[0] not in "{[":
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _rows_from_json(payload: Any) -> list[dict[str, Any]]:
    # Direct list / known wrappers
    if isinstance(payload, list):
        dicts = [r for r in payload if isinstance(r, dict)]
        if dicts and _looks_like_tool_rows(dicts):
            return [_flatten_tool(r) for r in dicts]
        # List of nested tool wrappers
        collected: list[dict[str, Any]] = []
        for item in payload:
            collected.extend(_rows_from_json(item))
        return collected

    if not isinstance(payload, dict):
        return []

    for key in (
        "tools",
        "Tools",
        "library",
        "items",
        "data",
        "data2",
        "toolList",
        "ToolList",
    ):
        val = payload.get(key)
        if isinstance(val, list) and val:
            rows = _rows_from_json(val)
            if rows:
                return rows

    # Fusion / CAM nested: walk for tool-like dicts
    found = _collect_toolish_dicts(payload)
    if found:
        return [_flatten_tool(r) for r in found]

    # Single flat tool object
    if _is_toolish_dict(payload):
        return [_flatten_tool(payload)]

    return []


def _collect_toolish_dicts(node: Any, out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if out is None:
        out = []
    if isinstance(node, dict):
        if _is_toolish_dict(node):
            out.append(node)
            return out
        for v in node.values():
            _collect_toolish_dicts(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_toolish_dicts(item, out)
    return out


def _is_toolish_dict(d: dict[str, Any]) -> bool:
    if not isinstance(d, dict) or len(d) < 2:
        return False
    keys = {str(k).lower().replace(" ", "").replace("-", "").replace("_", "") for k in d}
    hits = 0
    for k in _TOOLISH_KEYS:
        nk = k.lower().replace("_", "")
        if nk in keys:
            hits += 1
    # Name + any numeric geometry-ish field
    has_name = any(k in keys for k in ("name", "description", "description", "label", "title"))
    return hits >= 2 or (has_name and hits >= 1)


def _looks_like_tool_rows(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    sample = rows[: min(5, len(rows))]
    return sum(1 for r in sample if _is_toolish_dict(r) or len(r) >= 2) >= max(1, len(sample) // 2)


def _flatten_tool(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten one level of nested dicts for table display; drop huge blobs."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if v is None:
            continue
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str):
            if len(v) > 400:
                continue
            out[key] = v
        elif isinstance(v, dict):
            # Prefer common nested CAM shapes: geometry.diameter, start-values.feed etc.
            flat = _flatten_tool(v, prefix=key)
            # Keep only scalar leaves already filtered
            for fk, fv in flat.items():
                out[fk] = fv
        elif isinstance(v, list):
            if len(v) <= 8 and all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                out[key] = ", ".join("" if x is None else str(x) for x in v)
            # else skip large nested arrays (path geometry, etc.)
    return out


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
    sample_lines = text.splitlines()[:12]
    sample = "\n".join(sample_lines)
    if not sample.strip():
        return []

    # Reject obvious non-tabular blobs early (single enormous line, no delimiters)
    if len(sample_lines) <= 1 and len(sample) > 10_000:
        if not any(d in sample[:2000] for d in (",", ";", "\t", "|")):
            return []

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
    headers = [h for h in reader.fieldnames if h is not None]
    if len(headers) < 1:
        return []

    # If sniffer collapsed everything into one header, this is not a table.
    if len(headers) == 1 and len(str(headers[0])) > 200:
        return []

    rows = []
    for raw in reader:
        row = {
            str(k).strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
            if k
        }
        # Drop absurdly large cell values (embedded images / geometry dumps)
        row = {
            k: (v if not isinstance(v, str) or len(v) <= 500 else v[:497] + "...")
            for k, v in row.items()
        }
        if any(str(v).strip() for v in row.values() if v is not None):
            rows.append(row)
    return rows


def _rows_from_ini_blocks(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    rows: list[dict[str, Any]] = []
    section_re = re.compile(r"^\[(.+)\]\s*$")
    kv_re = re.compile(r"^([A-Za-z0-9_ .-]+)\s*[=:]\s*(.*)$")
    for block in blocks:
        lines = [
            ln.strip()
            for ln in block.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", ";"))
        ]
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


def _rows_from_zip(data: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                lower = name.lower()
                if lower.endswith(("/",)):
                    continue
                if not any(
                    lower.endswith(ext)
                    for ext in (".json", ".csv", ".tsv", ".txt", ".xml", ".tools", ".tlslibrary")
                ):
                    continue
                try:
                    inner = zf.read(name)
                except KeyError:
                    continue
                try:
                    part, _ = parse_tool_library_bytes(inner, name)
                except ValueError:
                    continue
                rows.extend(part)
    except zipfile.BadZipFile:
        return []
    return rows


def _rows_from_sqlite(data: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            conn = sqlite3.connect(tmp.name)
        except sqlite3.Error:
            return []
        try:
            cur = conn.cursor()
            tables = [
                r[0]
                for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            preferred = [
                t
                for t in tables
                if any(s in t.lower() for s in ("tool", "cutter", "bit", "drill"))
            ] or tables
            for table in preferred:
                try:
                    col_info = cur.execute(f'PRAGMA table_info("{table}")').fetchall()
                    colnames = [c[1] for c in col_info]
                    if not colnames:
                        continue
                    data_rows = cur.execute(f'SELECT * FROM "{table}" LIMIT 500').fetchall()
                except sqlite3.Error:
                    continue
                for data_row in data_rows:
                    row = {
                        colnames[i]: data_row[i]
                        for i in range(len(colnames))
                        if data_row[i] is not None and not isinstance(data_row[i], (bytes, memoryview))
                    }
                    if row:
                        rows.append(row)
                if rows:
                    break
        finally:
            conn.close()
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
                rendered = json.dumps(v, ensure_ascii=False)
                if len(rendered) > 500:
                    continue
                out[key] = rendered
            else:
                text = str(v)
                if len(text) > 500:
                    text = text[:497] + "..."
                out[key] = v if not isinstance(v, str) else text
            add_key(key)
        if out:
            cleaned.append(out)

    lower_map = {k.lower(): k for k in key_set}
    preferred = []
    for pref in PREFERRED_COLUMNS:
        actual = lower_map.get(pref.lower())
        if actual and actual not in preferred:
            preferred.append(actual)
    rest = [k for k in key_set if k not in preferred]
    columns = preferred + rest
    ordered = [{c: r.get(c, "") for c in columns} for r in cleaned]
    return ordered, columns
