"""CNC toolpath generation and verification graphics.

Uses pcb2gcode when available; otherwise a built-in contour + drill generator.
"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.models import MachineSettings
from app.services import parser, zip_ingest
from app.services.postprocess import merge_nc_files, write_drill_nc, write_path_nc

logger = logging.getLogger(__name__)


def _cam_by_kind(job_id: str) -> dict[str, Path]:
    root = zip_ingest.job_dir(job_id)
    out: dict[str, Path] = {}
    for meta in zip_ingest.list_cam_files(job_id):
        kind = meta["kind"]
        if kind not in out:
            out[kind] = root / meta["path"]
    return out


def _pcb2gcode_available() -> bool:
    return shutil.which("pcb2gcode") is not None


def _run_pcb2gcode(
    job_id: str,
    settings: MachineSettings,
    files: dict[str, Path],
    out_dir: Path,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pcb2gcode",
        f"--front={files['copper_top']}",
        f"--zwork=-{settings.engraving_depth_mm}",
        f"--zsafe={settings.safe_z_mm}",
        f"--zchange={settings.safe_z_mm}",
        f"--mill-feed={settings.feed_mm_min}",
        f"--mill-speed={settings.spindle_rpm}",
        f"--drill-feed={settings.plunge_mm_min}",
        f"--drill-speed={settings.spindle_rpm}",
        f"--zdrill=-{settings.drill_depth_mm}",
        f"--outdir={out_dir}",
    ]
    if "drill" in files:
        cmd.append(f"--drill={files['drill']}")
    if "profile" in files:
        cmd.append(f"--outline={files['profile']}")
        cmd.append(f"--cutter-diameter=1.0")
        cmd.append(f"--zcut=-{settings.drill_depth_mm}")

    logger.info("Running pcb2gcode: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pcb2gcode failed ({proc.returncode}): {proc.stderr or proc.stdout}"
        )

    produced = sorted(p.name for p in out_dir.glob("*.ngc")) + sorted(
        p.name for p in out_dir.glob("*.nc")
    )
    # Normalize names
    mapping = {
        "front.ngc": "isolation.nc",
        "front.nc": "isolation.nc",
        "drill.ngc": "drill.nc",
        "outline.ngc": "outline.nc",
    }
    result = []
    for name in produced:
        src = out_dir / name
        dest_name = mapping.get(name, name if name.endswith(".nc") else name.replace(".ngc", ".nc"))
        dest = out_dir / dest_name
        if src != dest:
            src.replace(dest)
        result.append(dest_name)
    return result


def _contours_from_gerber(
    path: Path,
    *,
    dpmm: int = 50,
    offset_px: int = 2,
) -> tuple[list[list[tuple[float, float]]], parser.GerberBounds]:
    """Extract mm-space contours from a Gerber raster (optionally dilated)."""
    bounds = parser.parse_gerber_bounds(path)
    png = parser.render_gerber_png(
        path, rgba=(255, 255, 255, 255), dpmm=dpmm
    )
    img = Image.open(BytesIO(png)).convert("L")
    arr = np.array(img)
    _, binary = cv2.threshold(arr, 10, 255, cv2.THRESH_BINARY)
    if offset_px > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (offset_px * 2 + 1, offset_px * 2 + 1)
        )
        binary = cv2.dilate(binary, kernel, iterations=1)

    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h = arr.shape[0]
    paths: list[list[tuple[float, float]]] = []
    min_area = (dpmm * 0.2) ** 2
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue
        pts: list[tuple[float, float]] = []
        for p in cnt[:: max(1, len(cnt) // 400)]:  # downsample long contours
            px, py = float(p[0][0]), float(p[0][1])
            x_mm = bounds.min_x + px / dpmm
            y_mm = bounds.max_y - py / dpmm  # image Y down → board Y up
            pts.append((x_mm, y_mm))
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            paths.append(pts)
    return paths, bounds


def _builtin_generate(
    job_id: str,
    settings: MachineSettings,
    files: dict[str, Path],
    out_dir: Path,
) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: list[str] = []

    if "copper_top" in files:
        paths, _ = _contours_from_gerber(files["copper_top"], offset_px=3)
        isolation = out_dir / "isolation.nc"
        write_path_nc(
            isolation,
            paths,
            settings=settings,
            operation="Isolation Engraving",
            tool_number=settings.tool_number,
            depth_mm=settings.engraving_depth_mm,
            include_header=True,
            step_down_mm=settings.step_down_mm,
        )
        produced.append("isolation.nc")

    if "drill" in files:
        hits = parser.parse_excellon(files["drill"])
        drill_path = out_dir / "drill.nc"
        write_drill_nc(
            drill_path,
            hits,
            settings=settings,
            depth_mm=settings.drill_depth_mm,
            tool_number=4,
            include_header=True,
        )
        produced.append("drill.nc")

    if "profile" in files:
        paths, _ = _contours_from_gerber(files["profile"], offset_px=0)
        # Prefer the longest contour as board outline
        paths = sorted(paths, key=lambda p: len(p), reverse=True)[:3]
        outline = out_dir / "outline.nc"
        write_path_nc(
            outline,
            paths,
            settings=settings,
            operation="Board Outline",
            tool_number=4,
            depth_mm=settings.drill_depth_mm,
            include_header=True,
            step_down_mm=0.4,
        )
        produced.append("outline.nc")

    if not produced:
        raise RuntimeError("No copper/drill/profile inputs available for generation")

    merge_nc_files(
        [out_dir / name for name in produced],
        out_dir / "all.nc",
        settings=settings,
    )
    produced.append("all.nc")
    return produced


def generate_toolpaths(job_id: str, settings: MachineSettings | None = None) -> dict[str, Any]:
    settings = settings or MachineSettings()
    if settings.engraving_depth_mm > settings.drill_depth_mm:
        raise ValueError("Copper engrave depth exceeds drill depth")
    settings.spindle_rpm = max(1000, min(settings.spindle_rpm, 60000))
    settings.feed_mm_min = max(50.0, min(settings.feed_mm_min, 10000.0))
    settings.plunge_mm_min = max(20.0, min(settings.plunge_mm_min, 3000.0))

    files = _cam_by_kind(job_id)
    if "copper_top" not in files:
        raise ValueError("Job is missing copper_top Gerber")

    out_dir = zip_ingest.job_dir(job_id) / "nc"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    engine = "pcb2gcode" if _pcb2gcode_available() else "builtin"
    try:
        if engine == "pcb2gcode":
            produced = _run_pcb2gcode(job_id, settings, files, out_dir)
            # Ensure merged file exists
            if "all.nc" not in produced:
                parts = [
                    out_dir / n
                    for n in ("isolation.nc", "drill.nc", "outline.nc")
                    if (out_dir / n).exists()
                ]
                if parts:
                    merge_nc_files(parts, out_dir / "all.nc", settings=settings)
                    produced.append("all.nc")
        else:
            produced = _builtin_generate(job_id, settings, files, out_dir)
    except Exception:
        if engine == "pcb2gcode":
            logger.exception("pcb2gcode failed; falling back to builtin generator")
            produced = _builtin_generate(job_id, settings, files, out_dir)
            engine = "builtin"
        else:
            raise

    preview_b64 = render_toolpath_preview(job_id)
    return {
        "job_id": job_id,
        "files": produced,
        "engine": engine,
        "toolpath_preview_png_base64": preview_b64,
    }


def parse_gcode_paths(nc_text: str) -> list[list[tuple[float, float]]]:
    """Extract rapid/feed XY polylines from G-code for plotting."""
    import re

    paths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    for line in nc_text.splitlines():
        s = line.strip().upper()
        if not s or s.startswith("(") or s.startswith(";"):
            continue
        if s.startswith("G0") or s.startswith("G00"):
            if len(current) >= 2:
                paths.append(current)
            current = []
            mx = re.search(r"X([+-]?\d*\.?\d+)", s)
            my = re.search(r"Y([+-]?\d*\.?\d+)", s)
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            current = [(x, y)]
            continue
        if s.startswith("G1") or s.startswith("G01") or "X" in s or "Y" in s:
            if not (s.startswith("G1") or s.startswith("G01") or s.startswith("G0")):
                # bare coordinate words on continuation — treat as feed if we have current
                if not current:
                    continue
            mx = re.search(r"X([+-]?\d*\.?\d+)", s)
            my = re.search(r"Y([+-]?\d*\.?\d+)", s)
            if not mx and not my:
                continue
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            if not current:
                current = [(x, y)]
            else:
                current.append((x, y))
    if len(current) >= 2:
        paths.append(current)
    return paths


def render_toolpath_preview(job_id: str, size: int = 900) -> str:
    """Render a PNG verification graphic of generated NC toolpaths."""
    out_dir = zip_ingest.job_dir(job_id) / "nc"
    if not out_dir.exists():
        raise FileNotFoundError("No generated NC files for this job")

    colors = {
        "isolation.nc": (245, 166, 35, 255),
        "drill.nc": (239, 68, 68, 255),
        "outline.nc": (148, 163, 184, 255),
        "all.nc": (96, 165, 250, 120),
    }

    all_pts: list[tuple[float, float]] = []
    op_paths: dict[str, list[list[tuple[float, float]]]] = {}
    for name in ("isolation.nc", "drill.nc", "outline.nc"):
        path = out_dir / name
        if not path.exists():
            continue
        paths = parse_gcode_paths(path.read_text(errors="replace"))
        op_paths[name] = paths
        for poly in paths:
            all_pts.extend(poly)

    if not all_pts:
        # fallback empty image
        img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 0.05 * max(max_x - min_x, max_y - min_y, 1.0)
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    bw = max_x - min_x
    bh = max_y - min_y
    scale = (size - 40) / max(bw, bh)

    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))
    draw = ImageDraw.Draw(img)

    def to_px(pt: tuple[float, float]) -> tuple[float, float]:
        x = 20 + (pt[0] - min_x) * scale
        y = size - 20 - (pt[1] - min_y) * scale
        return x, y

    for name, paths in op_paths.items():
        color = colors.get(name, (255, 255, 255, 255))
        for poly in paths:
            if len(poly) < 2:
                continue
            px = [to_px(p) for p in poly]
            if name == "drill.nc":
                for p in px:
                    r = 3
                    draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)
            else:
                draw.line(px, fill=color, width=2)

    # Legend
    legend_y = 12
    for name, color in colors.items():
        if name == "all.nc" or name not in op_paths:
            continue
        draw.rectangle((12, legend_y, 28, legend_y + 12), fill=color)
        draw.text((34, legend_y - 1), name.replace(".nc", ""), fill=(226, 232, 240, 255))
        legend_y += 18

    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def list_nc_files(job_id: str) -> list[str]:
    out_dir = zip_ingest.job_dir(job_id) / "nc"
    if not out_dir.exists():
        return []
    return sorted(p.name for p in out_dir.glob("*.nc"))


def nc_file_path(job_id: str, filename: str) -> Path:
    path = (zip_ingest.job_dir(job_id) / "nc" / filename).resolve()
    root = (zip_ingest.job_dir(job_id) / "nc").resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise FileNotFoundError(filename)
    return path
