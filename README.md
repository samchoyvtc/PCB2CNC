# Gerber CNC GUI

Local web app that turns a PCB CAM zip (Gerber + Excellon) into colored layer previews and downloadable CNC `.nc` files.

## Stages

1. **Preview** — drag-drop zip, colored Gerber layers + drill overlay  
2. **Machine** — pick a PAEN tool; set copper engrave depth, drill depth, and safe Z  
3. **Generate** — per-process copper (contour/pocket), drill (plunge/pocket), and outline G-code; path overlay on the board  
4. **Convert** — inspect G-code + downloads  

## Quick start

```bash
python3 -m pip install -r backend/requirements.txt
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 and drop a CAM zip from `samples/`.

Place `PAEN_TOOLS.tlslibrary` in the repo root (or upload it on the Machine step) so the six machine tools load. Cutting feeds come from each tool’s PCB material row.

Optional: install [pcb2gcode](https://github.com/pcb2gcode/pcb2gcode) on `PATH` for CAM generation; otherwise the built-in contour generator is used.

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/jobs/upload` | Upload CAM `.zip` |
| GET | `/api/jobs/{id}/preview` | Colored layer + drill preview payload |
| POST | `/api/jobs/{id}/generate` | Generate `.nc` + toolpath preview |
| POST | `/api/jobs/{id}/preview-path` | Overlay one process path on the board |
| GET | `/api/jobs/{id}/toolpath-preview` | Verification PNG |
| GET | `/api/jobs/{id}/nc/{file}` | Download G-code |

## Tests

```bash
cd /workspace/backend
PYTHONPATH=. python3 -m pytest -q
```
