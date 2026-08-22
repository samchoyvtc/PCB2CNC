"""FastAPI entrypoint for Gerber → CNC GUI."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.models import (
    GenerateRequest,
    GenerateResponse,
    MachineToolsResponse,
    PathPreviewResponse,
    PreviewProgressResponse,
    PreviewResponse,
    ToolpathPreviewResponse,
    UploadResponse,
)
from app.services import preview, tool_library, toolpath, zip_ingest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

app = FastAPI(title="Gerber CNC GUI", version="0.4.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "pcb2gcode": toolpath._pcb2gcode_available()}


@app.get("/api/machine/tools", response_model=MachineToolsResponse)
def get_machine_tools() -> MachineToolsResponse:
    data = tool_library.load_tool_library()
    return MachineToolsResponse(**data)


@app.post("/api/machine/tools/upload", response_model=MachineToolsResponse)
async def upload_machine_tools(file: UploadFile = File(...)) -> MachineToolsResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty tool library upload")
    try:
        saved = tool_library.save_uploaded_library(data)
        result = tool_library.load_tool_library(saved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool library upload failed")
        raise HTTPException(status_code=400, detail=f"Failed to parse tool library: {exc}") from exc
    return MachineToolsResponse(**result)


@app.post("/api/jobs/upload", response_model=UploadResponse)
async def upload_job(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip CAM export")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        result = zip_ingest.extract_zip(data)
        zip_ingest.save_file_index(result["job_id"], result["files"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload failed")
        raise HTTPException(status_code=400, detail=f"Failed to read zip: {exc}") from exc

    return UploadResponse(
        job_id=result["job_id"],
        files=result["files"],
        message=f"Loaded {len(result['files'])} CAM files",
    )


@app.get("/api/jobs/{job_id}/preview", response_model=PreviewResponse)
def get_preview(job_id: str) -> PreviewResponse:
    try:
        return preview.build_preview(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/jobs/{job_id}/preview/start", response_model=PreviewProgressResponse)
def start_preview(job_id: str) -> PreviewProgressResponse:
    try:
        data = preview.start_preview_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Preview start failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PreviewProgressResponse(**data)


@app.get("/api/jobs/{job_id}/preview/progress", response_model=PreviewProgressResponse)
def preview_progress(job_id: str) -> PreviewProgressResponse:
    try:
        zip_ingest.list_cam_files(job_id)
        data = preview.read_preview_progress(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PreviewProgressResponse(**data)


@app.post("/api/jobs/{job_id}/generate", response_model=GenerateResponse)
def generate(job_id: str, body: GenerateRequest | None = None) -> GenerateResponse:
    body = body or GenerateRequest()
    try:
        result = toolpath.generate_toolpaths(job_id, body.settings, body.plan)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generate failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateResponse(
        job_id=job_id,
        files=result["files"],
        toolpath_preview_png_base64=result.get("toolpath_preview_png_base64"),
        paths=result.get("paths") or [],
        message=f"Generated with {result['engine']}: {', '.join(result['files'])}",
    )


@app.post("/api/jobs/{job_id}/preview-path", response_model=PathPreviewResponse)
def preview_path(job_id: str, body: GenerateRequest | None = None) -> PathPreviewResponse:
    body = body or GenerateRequest()
    try:
        result = toolpath.preview_operation(job_id, body.settings, body.plan)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Path preview failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return PathPreviewResponse(
        job_id=job_id,
        image_png_base64=result["image_png_base64"],
        files=result["files"],
        paths=result.get("paths") or [],
        message=f"Preview: {', '.join(result['files'])}",
    )


@app.get("/api/jobs/{job_id}/toolpath-preview", response_model=ToolpathPreviewResponse)
def toolpath_preview(job_id: str) -> ToolpathPreviewResponse:
    try:
        b64 = toolpath.render_toolpath_preview(job_id)
        ops = toolpath.list_nc_files(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ToolpathPreviewResponse(
        job_id=job_id,
        image_png_base64=b64,
        operations=ops,
    )


@app.get("/api/jobs/{job_id}/nc")
def list_nc(job_id: str) -> dict:
    try:
        files = toolpath.list_nc_files(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"job_id": job_id, "files": files}


@app.get("/api/jobs/{job_id}/nc/{filename}")
def download_nc(job_id: str, filename: str) -> FileResponse:
    try:
        path = toolpath.nc_file_path(job_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="text/plain",
        filename=filename,
    )


# Static frontend last so API routes take precedence
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


if FRONTEND.exists():
    app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND), html=True), name="frontend")
