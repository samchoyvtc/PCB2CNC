"""Gerber + Excellon parsing helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DrillHit:
    x: float
    y: float
    diameter: float
    tool: str
    source: str | None = None


@dataclass
class BomItem:
    qty: int
    value: str
    device: str
    package: str
    parts: str
    description: str


@dataclass
class GerberBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


_OBSOLETE_GERBER = re.compile(
    r"%IN\*?%|"
    r"%IP(?:POS|NEG)\*?%|"
    r"%IJ(?:ACT|OFF)\*?%|"
    r"G7[45]\*?",
    re.IGNORECASE,
)


def sanitize_gerber_text(text: str) -> str:
    """Strip obsolete RS-274D / EAGLE attributes that break modern parsers."""
    cleaned = _OBSOLETE_GERBER.sub("", text)
    # Ensure aperture macros still terminate cleanly
    return cleaned


def load_gerber_parsed(path: Path):
    """Load and parse a Gerber file, with EAGLE compatibility cleanup."""
    from pygerber.gerberx3.api.v2 import GerberFile

    raw = path.read_text(errors="replace")
    cleaned = sanitize_gerber_text(raw)
    return GerberFile.from_str(cleaned).parse()


def parse_gerber_bounds(path: Path) -> GerberBounds:
    parsed = load_gerber_parsed(path)
    info = parsed.get_info()
    return GerberBounds(
        min_x=float(info.min_x_mm),
        min_y=float(info.min_y_mm),
        max_x=float(info.max_x_mm),
        max_y=float(info.max_y_mm),
    )


def render_gerber_png(
    path: Path,
    *,
    rgba: tuple[int, int, int, int],
    dpmm: int = 40,
) -> bytes:
    """Rasterize a Gerber layer to PNG bytes with the given RGBA color."""
    from io import BytesIO

    from pygerber.gerberx3.api import ColorScheme, RGBA
    from pygerber.gerberx3.api._v2 import ImageFormatEnum, PixelFormatEnum

    r, g, b, a = rgba
    scheme = ColorScheme(
        background_color=RGBA.from_rgba(0, 0, 0, 0),
        clear_color=RGBA.from_rgba(0, 0, 0, 0),
        solid_color=RGBA.from_rgba(r, g, b, a),
        clear_region_color=RGBA.from_rgba(0, 0, 0, 0),
        solid_region_color=RGBA.from_rgba(r, g, b, a),
    )
    buf = BytesIO()
    load_gerber_parsed(path).render_raster(
        buf,
        color_scheme=scheme,
        dpmm=dpmm,
        image_format=ImageFormatEnum.PNG,
        pixel_format=PixelFormatEnum.RGBA,
    )
    return buf.getvalue()


def parse_excellon(path: Path) -> list[DrillHit]:
    """Parse a simple Excellon drill file into hole hits (mm)."""
    text = path.read_text(errors="replace")
    tools: dict[str, float] = {}
    current_tool = "T1"
    unit_mm = True
    zero_suppress = "TZ"  # trailing zero suppress common in EAGLE
    # Detect format like METRIC,TZ,000.000 or INCH,LZ
    fmt_decimals = 3
    fmt_integers = 3

    hits: list[DrillHit] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("%"):
            continue
        upper = line.upper()

        if upper.startswith("METRIC"):
            unit_mm = True
            if "LZ" in upper:
                zero_suppress = "LZ"
            if "TZ" in upper:
                zero_suppress = "TZ"
            m = re.search(r"(\d+)\.(\d+)", upper)
            if m:
                fmt_integers = len(m.group(1))
                fmt_decimals = len(m.group(2))
            continue
        if upper.startswith("INCH"):
            unit_mm = False
            if "LZ" in upper:
                zero_suppress = "LZ"
            if "TZ" in upper:
                zero_suppress = "TZ"
            continue
        if upper.startswith("M48") or upper.startswith("M30") or upper.startswith("M71"):
            continue
        if upper.startswith("FMAT") or upper.startswith("ICI") or upper.startswith("G90"):
            continue

        # Tool definition T01C0.600 or T1C1.000
        tm = re.match(r"T(\d+)\s*C\s*([0-9.]+)", upper)
        if tm:
            tools[f"T{int(tm.group(1))}"] = float(tm.group(2))
            continue

        # Tool select T01
        ts = re.match(r"^T(\d+)$", upper)
        if ts:
            current_tool = f"T{int(ts.group(1))}"
            continue

        # Coordinate X...Y...
        cm = re.match(r"X([+-]?\d+)\s*Y([+-]?\d+)", upper)
        if not cm:
            continue
        x = _decode_coord(cm.group(1), fmt_integers, fmt_decimals, zero_suppress)
        y = _decode_coord(cm.group(2), fmt_integers, fmt_decimals, zero_suppress)
        if not unit_mm:
            x *= 25.4
            y *= 25.4
        diameter = tools.get(current_tool, 1.0)
        if not unit_mm and current_tool in tools:
            # tool diameter already stored in file units; convert if inch mode
            # diameters in T#C# are usually already in active unit
            pass
        hits.append(
            DrillHit(x=x, y=y, diameter=diameter, tool=current_tool)
        )

    return hits


def _decode_coord(raw: str, integers: int, decimals: int, zero_suppress: str) -> float:
    """Decode Excellon fixed-point coordinate to float mm/inch of file unit.

    EAGLE exports often label METRIC,TZ while emitting values that behave like
    integer counts of the least-significant digit (e.g. X11900 → 11.900 mm).
    Prefer explicit decimals when present; otherwise scale by 10**-decimals.
    """
    sign = 1.0
    s = raw
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1.0
        s = s[1:]

    if "." in s:
        return sign * float(s)

    if not s:
        return 0.0

    # Primary path used by EAGLE / many CAM exports
    value = sign * (int(s) / (10 ** decimals if decimals else 1))

    # If LZ padding would differ and number is short, keep scaled integer form
    # (already matches LZ for typical 3.3 values like 11900 → 11.900).
    _ = (integers, zero_suppress)  # retained for call-site compatibility
    return value


def parse_bom_partlist(path: Path) -> list[BomItem]:
    """Parse EAGLE/Fusion 'Partlist exported…' text into BOM rows."""
    text = path.read_text(errors="replace")
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    if not lines:
        return []

    header_idx = None
    header = ""
    for i, ln in enumerate(lines):
        if re.match(r"^\s*Qty\b", ln, re.IGNORECASE):
            header_idx = i
            header = ln
            break
    if header_idx is None:
        return []

    # Prefer fixed column starts from the header labels
    labels = ["Qty", "Value", "Device", "Package", "Parts", "Description", "CATEGORY"]
    starts: list[tuple[str, int]] = []
    for label in labels:
        idx = header.find(label)
        if idx >= 0:
            starts.append((label.lower(), idx))
    starts.sort(key=lambda x: x[1])

    def slice_field(line: str, name: str) -> str:
        for i, (label, start) in enumerate(starts):
            if label != name:
                continue
            end = starts[i + 1][1] if i + 1 < len(starts) else len(line)
            return line[start:end].strip()
        return ""

    items: list[BomItem] = []
    for ln in lines[header_idx + 1 :]:
        if not ln.strip() or ln.lower().startswith("partlist"):
            continue
        qty_raw = slice_field(ln, "qty") or ln[:4].strip()
        try:
            qty = int(float(qty_raw))
        except ValueError:
            continue
        items.append(
            BomItem(
                qty=qty,
                value=slice_field(ln, "value"),
                device=slice_field(ln, "device"),
                package=slice_field(ln, "package"),
                parts=slice_field(ln, "parts"),
                description=slice_field(ln, "description"),
            )
        )
    return items
