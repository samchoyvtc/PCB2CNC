# Gerber CNC GUI

**Version 0.4.1** — Stages 1–4 of the local CAM workflow (preview, board setting, generate, convert).

Local web app that turns a PCB CAM zip (Gerber + Excellon) into colored layer previews and downloadable CNC `.nc` files.

## Stages

1. **Preview** — drag-drop zip, colored Gerber layers, drill overlay, board size ruler  
2. **Board setting** — stock size, copper/drill depths, clearance and retract heights; pick a PAEN tool  
3. **Generate** — copper (contour or pocket), drill (plunge or pocket), and outline with holding tabs; overlay paths on the board  
4. **Convert** — write `.nc` files; mill-order table or G-code inspect; download  

On Generate and Convert, **Hide rapids** (on by default) hides G0 travel so only cuts and plunges show.

## Quick start

```bash
python3 -m pip install -r backend/requirements.txt
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 and drop a CAM zip from `samples/`.

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
