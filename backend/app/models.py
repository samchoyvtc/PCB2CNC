"""Request/response schemas for Gerber CNC jobs."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MachineSettings(BaseModel):
    """Minimal Stage 3 machine / tool settings (Plan.md defaults)."""

    tool_number: int = 2
    board_width_mm: float = Field(100.0, gt=0, le=1000)
    board_length_mm: float = Field(150.0, gt=0, le=1000)
    engraving_depth_mm: float = Field(0.2, gt=0, le=5)
    drill_depth_mm: float = Field(1.7, gt=0, le=20)
    spindle_rpm: int = Field(12000, ge=1000, le=60000)
    feed_mm_min: float = Field(2000, gt=0, le=10000)
    plunge_mm_min: float = Field(200, gt=0, le=3000)
    step_over_percent: float = Field(50.0, gt=0, le=100)
    step_down_mm: float = Field(0.1, gt=0, le=5)
    safe_z_mm: float = Field(15.0, ge=1, le=100)
    retract_z_mm: float = Field(3.0, gt=0, le=100)
    coolant: bool = True


class Bounds(BaseModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width: float
    height: float


class DrillHit(BaseModel):
    x: float
    y: float
    diameter: float
    tool: str
    source: Optional[str] = None


class DrillToolSummary(BaseModel):
    tool: str
    diameter: float
    count: int


class BomItem(BaseModel):
    qty: int
    value: str = ""
    device: str = ""
    package: str = ""
    parts: str = ""
    description: str = ""


class LayerPreview(BaseModel):
    name: str
    kind: str
    color: str
    visible_default: bool
    image_png_base64: Optional[str] = None
    bounds: Optional[Bounds] = None
    error: Optional[str] = None
    drill_tools: list[DrillToolSummary] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    job_id: str
    layers: list[LayerPreview]
    drills: list[DrillHit]
    bom: list[BomItem] = Field(default_factory=list)
    bom_source: Optional[str] = None
    bounds: Optional[Bounds] = None
    files: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class PreviewProgressResponse(BaseModel):
    job_id: str
    state: str
    current: int = 0
    total: int = 0
    percent: int = 0
    message: str = ""
    error: Optional[str] = None
    result: Optional[PreviewResponse] = None


class UploadResponse(BaseModel):
    job_id: str
    files: list[dict[str, Any]]
    message: str


class CopperOp(BaseModel):
    layer: str
    tool_number: int = 2
    isolation_passes: int = Field(1, ge=1, le=12)
    engrave_mode: Literal["contour", "pocket"] = "contour"
    outline_layer: Optional[str] = None
    depth_mm: Optional[float] = Field(default=None, gt=0, le=5)


class DrillSizeMap(BaseModel):
    diameter_mm: float = Field(gt=0)
    tool_number: int = 4
    strategy: Literal["drill", "pocket"] = "drill"


class DrillOp(BaseModel):
    layers: list[str] = Field(default_factory=list)
    size_map: list[DrillSizeMap] = Field(default_factory=list)
    depth_mm: Optional[float] = Field(default=None, gt=0, le=20)


class OutlineOp(BaseModel):
    layer: str
    tool_number: int = 4
    tab_count: int = Field(4, ge=0, le=32)
    tab_width_mm: float = Field(2.0, gt=0, le=20)
    tab_offset: float = Field(0.0, ge=0, le=1)
    depth_mm: Optional[float] = Field(default=None, gt=0, le=20)


class GeneratePlan(BaseModel):
    copper: Optional[CopperOp] = None
    copper_bottom: Optional[CopperOp] = None
    drills: list[DrillOp] = Field(default_factory=list)
    outline: Optional[OutlineOp] = None
    mirror: bool = False


class GenerateRequest(BaseModel):
    settings: MachineSettings = Field(default_factory=MachineSettings)
    plan: Optional[GeneratePlan] = None


class GenerateResponse(BaseModel):
    job_id: str
    files: list[str]
    toolpath_preview_png_base64: Optional[str] = None
    paths: list[dict[str, Any]] = Field(default_factory=list)
    message: str


class ToolpathPreviewResponse(BaseModel):
    job_id: str
    image_png_base64: str
    operations: list[str]


class ToolpathPoly(BaseModel):
    file: str
    kind: str
    tool_number: Optional[int] = None
    diameter_mm: Optional[float] = None
    hole_diameter_mm: Optional[float] = None
    strategy: Optional[str] = None
    points: list[list[float]] = Field(default_factory=list)


class PathPreviewResponse(BaseModel):
    job_id: str
    image_png_base64: str
    files: list[str] = Field(default_factory=list)
    paths: list[ToolpathPoly] = Field(default_factory=list)
    message: str = ""


class MachineToolsResponse(BaseModel):
    source: Optional[str] = None
    path: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""
