"""Request/response schemas for Gerber CNC jobs."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MachineSettings(BaseModel):
    """Minimal Stage 3 machine / tool settings (Plan.md defaults)."""

    tool_number: int = 2
    engraving_depth_mm: float = Field(0.15, gt=0, le=5)
    spindle_rpm: int = Field(12000, ge=1000, le=60000)
    feed_mm_min: float = Field(2000, gt=0, le=10000)
    plunge_mm_min: float = Field(200, gt=0, le=3000)
    safe_z_mm: float = Field(15.0, ge=1, le=100)
    stock_thickness_mm: float = Field(1.5, gt=0, le=20)
    coolant: bool = True


class Bounds(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width: float
    height: float


class LayerPreview(BaseModel):
    name: str
    kind: str
    color: str
    visible_default: bool
    image_png_base64: Optional[str] = None
    bounds: Optional[Bounds] = None
    error: Optional[str] = None


class DrillHit(BaseModel):
    x: float
    y: float
    diameter: float
    tool: str


class PreviewResponse(BaseModel):
    job_id: str
    layers: list[LayerPreview]
    drills: list[DrillHit]
    bounds: Optional[Bounds] = None
    files: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    job_id: str
    files: list[dict[str, Any]]
    message: str


class GenerateRequest(BaseModel):
    settings: MachineSettings = Field(default_factory=MachineSettings)


class GenerateResponse(BaseModel):
    job_id: str
    files: list[str]
    toolpath_preview_png_base64: Optional[str] = None
    message: str


class ToolpathPreviewResponse(BaseModel):
    job_id: str
    image_png_base64: str
    operations: list[str]
