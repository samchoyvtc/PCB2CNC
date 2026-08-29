# Gerber CNC GUI

**Version 1.0.0** — Classroom app: drop a CAM zip, preview CNC paths, convert to G-code.

Local web app that turns a PCB CAM zip (Gerber RS-274X + Excellon) into a board preview and downloadable GRBL `.nc` files.

To recreate this project from a blank tree, follow **[Plan.md](Plan.md)**.

## Stages

1. **Generate** — drop a CAM zip above the settings. CNC path preview is on the left (file list under the board). Isolation, drill/pocket, and outline are on the right. Paths overlay automatically.
2. **Convert** — write `.nc` files; mill-order table or G-code inspect; download.

**Board setting** (stock size, copper tool and depths, Safe Clearance / Retract Height, tool library) is optional from a link on Generate. **Apply** saves and returns to Generate; **Cancel** discards edits and returns.

On Generate, students pick **copper top or bottom**, **Contour or Pocket** (engraving passes default **3** on contour), **Corn** mills and Drill/Pocket per hole size, the **profile** outline, and holding tabs. Outline cut uses the **largest Corn mill selected for drilling**. Choosing **bottom** turns **Mirror** on (off for top), which flips copper, drill, and outline left-to-right around the board center. Changing Layer shows that Gerber on the canvas (profile stays on). Changing copper, drill, outline, or Mirror clears the overlays and rebuilds them.

Board-setting defaults: stock `100 × 150 mm`, engraving `0.2 mm`, drilling and cutout `1.7 mm`, Safe Z `15 mm`, retract `3 mm`.

On Generate and Convert, **Hide rapids** (on by default) hides G0 travel so only cuts and plunges show.

Every downloaded `.nc` ends with **Return Tool** (`T0 M6`) then **Home position** (`G28`) after the last cut, before `M2`. Merged `all.nc` does that once at the end of the whole job.

## Quick start

**One click**

- **Mac:** double-click `Start PCB2CNC.command` in Finder. The first time, macOS may ask you to confirm opening it (Right-click → Open).
- **Windows:** double-click `Start PCB2CNC.bat`.

The launcher creates a local `.venv` if needed, installs Python packages on first run, starts the server with reload, and opens http://127.0.0.1:8000. Leave that window open while you use the app; press Ctrl+C to stop. If the server is already running, it just opens the browser.

Python 3 must already be installed.

**Manual**

```bash
python3 -m pip install -r backend/requirements.txt
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open http://127.0.0.1:8000 and drop a CAM zip from `samples/` (`TEST_Gerber_Simple.zip` or `TEST_Gerber_Complex.zip`).

Place `PAEN_TOOLS.tlslibrary` in `samples/` or the repo root (or upload it on Board setting) so the six machine tools load. Cutting feeds come from each tool’s PCB material row.

Optional: install [pcb2gcode](https://github.com/pcb2gcode/pcb2gcode) on `PATH` for CAM generation when no generate plan is sent; the classroom UI always sends a plan and uses the built-in contour generator.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness |
| GET | `/api/machine/tools` | PAEN tool library |
| POST | `/api/machine/tools/upload` | Replace the tool library |
| POST | `/api/jobs/upload` | Upload CAM `.zip` |
| GET | `/api/jobs/{id}/preview` | Colored layer + drill preview payload |
| POST | `/api/jobs/{id}/preview/start` | Start async preview |
| GET | `/api/jobs/{id}/preview/progress` | Preview progress |
| POST | `/api/jobs/{id}/preview-path` | Overlay one process path on the board |
| POST | `/api/jobs/{id}/generate` | Write `.nc` files + toolpaths |
| GET | `/api/jobs/{id}/nc` | List G-code files |
| GET | `/api/jobs/{id}/nc/{file}` | Download G-code |
| GET | `/api/jobs/{id}/toolpath-preview` | Verification PNG (optional) |

## Tests

```bash
cd backend
PYTHONPATH=. python3 -m pytest -q
```
