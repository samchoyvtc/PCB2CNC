# PCB Gerber-to-G-code MVP Plan

**Version 0.4.2** — One copper side per job (top or bottom Layer); pocket uses the profile Gerber; the canvas follows the selected Layer.

## Goal

Create a local web-based workflow (inspired by [Carbide Copper](https://copper.carbide3d.com/)) to convert single-sided PCB files into CNC milling/drilling G-code with straightforward machine/tool configuration.

## Delivery stages

### Stage 1 — Colored Gerber + drill preview
- Drag-drop a CAM `.zip` (Gerber RS-274X + Excellon).
- Classify layers (copper, profile, mask, silk, drill).
- Render multi-layer canvas preview with distinct colors and toggles.
- Overlay Excellon drill hits (not finished machine G-code).
- Show board extents (width × length) on the canvas. Hide-rapids is not shown on this stage.

### Stage 2 — Board setting
- Heading **Board setting** (not Machine settings).
- Stock size: width default `100 mm`, length default `150 mm`.
- Job-wide depths: copper engrave `0.15 mm`, drill `1.6 mm`.
- **Safe Position**: Clearance Height (Safe Z, default `15 mm`) and Retract Height (default `3 mm`).
- Load `PAEN_TOOLS.tlslibrary` and show a 5-column tool list (Number, Name, Type, Diameter, Tip Diameter). Remaining geometry and PCB cutting values appear in **Tool properties** when a row is selected.
- Feeds, spindle, step-over, step-down, and coolant come from the selected tool’s PCB row, not the settings form.

### Stage 3 — Generate CNC paths
- Right-hand settings column; the Stage 1 board preview stays on the left (~60% / 40%).
- **Preview** on each process card overlays that job’s toolpaths on the board (toggle on/off).
- Changing **Layer** on copper engraving shows that Gerber on the canvas (other copper/silk/mask layers hide; profile stays on).
- **Mirror** switch at the top of the panel (off by default) flips copper, drill, and outline left-to-right around the profile center. Gerber preview, overlays, and Convert G-code all follow it. Use after turning the board over.
- **Hide rapids** switch appears here (and on Convert) once paths exist; on by default so G0 travel is hidden.
- Feeds, spindle, step-over, and step-down come from each selected tool’s PCB row.
- Isolation follows copper **outer** contours and slots cut into large pours (Euclidean offset by tip radius). Pad drill holes are not cut as paths. Long outlines are not thinned to a 400-point cap, so chords do not cut through pads.
- Convert (Stage 4) writes the downloadable `.nc` files from this plan.

**1 · Copper trace engraving**
- Strategy: **contour** (isolation around copper) or **pocket** (clear unused copper inside a selected board outline, leaving traces).
- Fields, top to bottom: Strategy, Layer, Board outline (pocket only), Tool (default T2), Engraving depth (default `0.15 mm`), Isolation passes (contour only).
- Extra contour passes step farther out using the tool’s step-over.
- Layer can be copper top or copper bottom (one side per job). Same contour/pocket process in board XY; turn on **Mirror** to X-flip the whole job. Pocket always uses the profile Gerber as the board outline, even if that field is omitted.

**2 · PCB drilling**
- Drill depth (default `1.6 mm`) and one or more Excellon files.
- Table: hole Ø, count, tool (nearest tip by default), strategy **drill** or **pocket**.
- Drill: single plunge. Pocket: mill concentric circles so a smaller corn mill can open a hole larger than the tool, one Step Down layer at a time (from the selected tool’s PCB row). Falls back to a plunge if the hole is not larger than the tip.
- Preview colours follow the assigned tool; hole size is shown in the legend.

**3 · Board outline cut**
- Outside compensation: tool center is offset by the cutter radius so the cut edge follows the outline.
- Fields: Outline layer, Tool (default T4), Cutout depth (default `1.6 mm`), holding-tab count and width.
- **Tab offset** (0–100% around the perimeter) rotates the evenly spaced tabs. Preview pan/zoom only — no drag handles on the board.
- Each step-down pass rapids back to the segment start before plunging, so open tab segments do not chord across a corner.

### Stage 4 — Convert
- **Next step · Convert** writes `isolation.nc`, `drill.nc`, `outline.nc`, and merged `all.nc` from the Stage 3 plan.
- Three columns: CNC path preview (50%), mill-order / G-code inspect (30%), file list (20%).
- Middle pane switches between **Job list** and **G-code** for the selected file.
- Job list is a table (`#`, Job, Tool). A tool change is its own mill-order step. Rows use a dark-blue background; no orange highlight bar.
- File list download buttons are labeled **Download** (not “Download all.nc”).
- Hide rapids remains available on this stage.

## Scope (current)

- One copper side per job: pick copper top or copper bottom in **1 · PCB copper trace engraving**.
- Inputs:
  - Gerber RS-274X signal layer (from zip).
  - Excellon drill file.
  - Optional board outline Gerber.
  - PAEN tool library (`PAEN_TOOLS.tlslibrary`) for machine tools.
- Job-wide machining parameters:
  - Board width / length: `100 mm` / `150 mm` (defaults).
  - Copper engrave depth: `0.15 mm` (default).
  - Drill depth: `1.6 mm` (default; also used for outline through-cut).
  - Clearance Height (Safe Z): `15 mm` (default).
  - Retract Height: `3 mm` (default).
- Per-tool PCB properties (from the library, PCB material only):
  - Spindle speed, step over, step down, feed rate, plunge rate, coolant.
- Default selected tool: `#2` `0.2mm*30° Engraving (Metal)`.
- Outputs:
  - One merged `.nc` file (`all.nc`).
  - Optional separate files by operation (`isolation.nc`, `drill.nc`, `outline.nc`).

## Machine tools (PAEN library, PCB material)

| Number | Name | Type | Tip | PCB spindle | Step over | Step down | Feed | Plunge | Coolant |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.3mm*30° Solder Mask Removal | Engraving | 0.3 | 6000 | 66.667 | 0.2 | 400 | 200 | Y |
| 2 | 0.2mm*30° Engraving (Metal) | Engraving | 0.2 | 12000 | 50 | 0.1 | 2000 | 200 | Y |
| 3 | 0.8mm Corn | Flat End | 0.8 | 12000 | 60 | 0.3 | 500 | 300 | N |
| 4 | 1mm Corn | Flat End | 1.0 | 12000 | 60 | 0.3 | 500 | 300 | N |
| 5 | 1.5mm Corn | Flat End | 1.5 | 12000 | 60 | 0.3 | 500 | 300 | N |
| 6 | 2mm Corn | Flat End | 2.0 | 12000 | 60 | 0.3 | 500 | 300 | N |

Other library materials (copper, aluminum, wood, etc.) are ignored.

## User Workflow

1. Double-click `Start PCB2CNC.command` (Mac) or `Start PCB2CNC.bat` (Windows), or start uvicorn manually.
2. Drag-drop PCB CAM zip (Gerber + drill).
3. Render and preview layers on canvas (zoom, pan, fit-to-view, color toggles).
4. Open Board setting: confirm stock size, depths, and Safe Position.
5. Pick a tool from the 5-column list; remaining values appear in **Tool properties**.
6. Generate: pick copper Layer (top or bottom), tool, and strategy; the canvas shows that Gerber. Turn on **Mirror** if the board is flipped. Preview the CNC path (Hide rapids on if travel cluttered).
7. Convert: inspect mill order or G-code, then download `.nc`.

## Proposed Architecture

```mermaid
flowchart LR
  user[UserBrowser] --> ui[WebUI]
  ui --> api[FastAPIService]
  api --> parser[GerberExcellonParser]
  parser --> geom[GeometryEngine]
  geom --> toolpath[ToolpathPlanner]
  toolpath --> post[GRBLPostProcessor]
  post --> files[GcodeArtifacts]
  files --> ui
  api --> tools[PAENToolLibrary]
  tools --> ui
```

## Technical Approach

- Backend: Python + FastAPI (`backend/app`).
- Frontend: lightweight HTML/JS (`frontend/`).
- Preview: **pygerber** raster layers + Excellon hole overlay.
- CAM: **pcb2gcode** CLI when present; otherwise builtin OpenCV contour toolpaths.
- Tool library: parse PAEN binary `.tlslibrary` (UTF-16-BE length-prefixed records) and keep PCB cutting params only.
- Geometry/processing pipeline:
  1. Parse Gerber into preview rasters + bounds.
  2. Parse Excellon into drill hits.
  3. Generate isolation (contour or pocket), drill (plunge or hole-pocket), and outside outline with holding tabs.
  4. Post-process to GRBL-compatible G-code with clearance/retract, selected-tool spindle/coolant, and tool changes.
- Preview requirements:
  - Gerber layers visible immediately after upload.
  - Drill overlay toggleable.
  - Coordinate extents shown before generation.
  - Per-process toolpath overlay on the board canvas during Generate (Preview on).
  - Rapid (G0) moves hidden by default on Generate/Convert; not shown as a control on Stage 1.
  - Convert inspects mill order and G-code instead of a bottom overview image.

## Project Structure

- `backend/`
  - `app/main.py` (FastAPI entry)
  - `app/models.py` (request/response schemas)
  - `app/services/zip_ingest.py`
  - `app/services/parser.py`
  - `app/services/preview.py`
  - `app/services/toolpath.py`
  - `app/services/postprocess.py`
  - `app/services/tool_library.py`
  - `tests/`
- `frontend/`
  - `index.html`
  - `src/upload.js`
  - `src/preview.js`
  - `src/settings.js`
  - `src/tools.js`
  - `src/generate.js`
  - `src/output.js`
  - `src/app.js`
- `Start PCB2CNC.command` — double-click to run on Mac
- `Start PCB2CNC.bat` — double-click to run on Windows
- `scripts/start_server.py` — shared launcher (venv, packages, uvicorn, browser)
- `PAEN_TOOLS.tlslibrary` — machine tool library (geometry + PCB properties)
- `samples/` — Gerber zip fixtures
- `data/jobs/` — runtime upload workspace

## CNC Safety & Reliability Guardrails

- Enforce configurable clearance and retract between operations.
- Copper engrave depth must not exceed drill depth.
- Clamp feed/plunge/RPM from the selected tool to machine-safe ranges.
- Emit coolant from the selected tool’s PCB property (`Y`/`N`).
- Outline step-down must re-enter at the current segment start (no G1 across an open tab gap).

## Risks and Mitigations

- **Gerber format edge cases**: start with RS-274X via pygerber + clear error messages.
- **pcb2gcode missing**: automatic fallback to builtin generator.
- **Preview mismatch risk**: same job CAM files feed preview and generation.
- **PAEN library format**: scan GUID-prefixed tool records and skip 3D mesh blobs.

## Success Criteria

- Stage 1: Dropping a sample Gerber zip shows copper + profile + drills with colors.
- Stage 2: Board setting lists the six PAEN tools; selecting one shows PCB properties.
- Stage 3: Layer switches the Gerber on the canvas; Preview overlays isolation (copper-following or pocket inside the profile), drill/pocket holes, and outside outline with tabs; Hide rapids works on this stage only after paths exist.
- Stage 4: Convert writes `isolation.nc`, `drill.nc`, `outline.nc`, and `all.nc`; mill order and G-code are inspectable; files download.
