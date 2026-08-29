# Changelog

## [1.0.0] - 2026-08-29

First classroom release of Gerber CNC: zip in, CNC paths on the board, G-code out.

### Added

- Two-stage **Generate → Convert** workflow (Board setting is optional).
- Automatic copper, drill, and outline path overlays after a CAM zip is dropped.
- Contour or pocket copper engraving, with three engraving passes by default on contour.
- Per-hole Corn mill pick and Drill/Pocket strategy; outline cut uses the largest selected Corn mill.
- Mirror on for copper bottom (off for top), flipping the whole job around the board center.
- Every downloaded `.nc` ends with **Return Tool** (`T0 M6`) then **Home position** (`G28`) before `M2`. Merged `all.nc` does that once at the end of the job.

### Changed

- Classroom defaults: stock 100 × 150 mm, engrave 0.2 mm, drill and cutout 1.7 mm, Safe Z 15 mm, retract 3 mm.
- Generate layout: zip drop above the settings; file list under the board preview.
- Changing copper, drill, outline, or Mirror clears overlays and rebuilds them.
- Local launcher starts uvicorn with `--reload`.
