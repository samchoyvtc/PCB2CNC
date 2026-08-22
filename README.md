# Gerber CNC GUI

Local web app that turns a PCB CAM zip (Gerber + Excellon) into colored layer previews and downloadable CNC `.nc` files.

## Stages

1. **Preview** — drag-drop zip, colored Gerber layers + drill overlay  
2. **Generate** — isolation / drill / outline G-code + verification graphic  
3. **Convert** — combined UI with minimal machine settings and downloads  

## Quick start

```bash
cd /workspace
python3 -m pip install -r backend/requirements.txt
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 and drop `samples/TEST_Gerber.zip`.

Optional: install [pcb2gcode](https://github.com/pcb2gcode/pcb2gcode) on `PATH` for CAM generation; otherwise the built-in contour generator is used.

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/jobs/upload` | Upload CAM `.zip` |
| GET | `/api/jobs/{id}/preview` | Colored layer + drill preview payload |
| POST | `/api/jobs/{id}/generate` | Generate `.nc` + toolpath preview |
| GET | `/api/jobs/{id}/toolpath-preview` | Verification PNG |
| GET | `/api/jobs/{id}/nc/{file}` | Download G-code |

## Tests

```bash
cd /workspace/backend
PYTHONPATH=. python3 -m pytest -q
```
