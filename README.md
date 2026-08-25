# Gerber CNC GUI

**Version 0.4.3** — One copper side per job (top or bottom), pocket bounded by the board profile, Generate Layer switches the canvas Gerber; G-code travel optimizations (retract between hops, nearest-neighbor order, single-pass copper).

Local web app that turns a PCB CAM zip (Gerber + Excellon) into colored layer previews and downloadable CNC `.nc` files.

## Stages

1. **Preview** — drag-drop zip, colored Gerber layers, drill overlay, board size ruler  
2. **Board setting** — stock size, copper/drill depths, clearance and retract heights; pick a PAEN tool  
3. **Generate** — copper (contour or pocket), drill (plunge or pocket), and outline with holding tabs; overlay paths on the board  
4. **Convert** — write `.nc` files; mill-order table or G-code inspect; download  

On Generate, **Layer** in copper engraving can be `copper_top` or `copper_bottom` (one side per job). Changing Layer shows that Gerber on the canvas (profile stays on for the board shape). **Pocket** always mills inside the profile / board-outline Gerber. **Mirror** (off by default) flips copper, drill, and outline left-to-right around the board center. On Generate and Convert, **Hide rapids** (on by default) hides G0 travel so only cuts and plunges show.

## Quick start

**One click**

- **Mac:** double-click `Start PCB2CNC.command` in Finder. The first time, macOS may ask you to confirm opening it (Right-click → Open).
- **Windows:** double-click `Start PCB2CNC.bat`.

The launcher creates a local `.venv` if needed, installs Python packages on first run, starts the server, and opens http://127.0.0.1:8000. Leave that window open while you use the app; press Ctrl+C to stop. If the server is already running, it just opens the browser.

Python 3 must already be installed.

**Manual**

```bash
python3 -m pip install -r backend/requirements.txt
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open http://127.0.0.1:8000 and drop a CAM zip from `samples/`.

Place `PAEN_TOOLS.tlslibrary` in the repo root (or upload it on Board setting) so the six machine tools load. Cutting feeds come from each tool’s PCB material row.

Optional: install [pcb2gcode](https://github.com/pcb2gcode/pcb2gcode) on `PATH` for CAM generation; otherwise the built-in contour generator is used.

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
