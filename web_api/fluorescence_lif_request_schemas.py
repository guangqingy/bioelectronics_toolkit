from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import OptFloat, OptInt, RequestModel


class LifBrowseRequest(RequestModel):
    folder: str = ""


class LifInfoRequest(RequestModel):
    path: str = Field(min_length=1)
    sort: str = "time"


class LifPreviewRequest(RequestModel):
    path: str = Field(min_length=1)
    image_index: OptInt = 0
    z: OptInt = 0
    t: OptInt = 0
    c: OptInt = 0
    m: OptInt = 0
    requested_dims: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: OptFloat = 1.0
    p_high: OptFloat = 99.0


class LifVolume3dRequest(LifPreviewRequest):
    channel_mode: str = "composite"
    max_points: OptInt = 70000
    max_xy: OptInt = 180
    max_z: OptInt = 80
    threshold_percentile: OptFloat = 98.8


class LifExportVolume3dRequest(LifVolume3dRequest):
    max_points: OptInt = 110000
    max_xy: OptInt = 220
    max_z: OptInt = 120
    threshold_percentile: OptFloat = 98.6
    output_name: str = ""
    output_dir: str = ""
    overwrite: bool = False


class LifExportManifestRequest(RequestModel):
    path: str = Field(min_length=1)
    order_indices: list[Any] = Field(default_factory=list)
    rename_map: Any = Field(default_factory=dict)


class LifExportTiffRequest(RequestModel):
    path: str = Field(min_length=1)
    image_index: OptInt = 0
    output_name: str = ""
    output_dir: str = ""
    overwrite: bool = False


class LifExportTiffBatchRequest(RequestModel):
    path: str = Field(min_length=1)
    order_indices: list[Any] = Field(default_factory=list)
    rename_map: Any = Field(default_factory=dict)
    output_dir: str = ""
    overwrite: bool = False
