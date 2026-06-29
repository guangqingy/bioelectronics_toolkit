from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import OptFloat, OptInt, RequestModel


class EchemPhotocurrentBrowseRequest(RequestModel):
    folder: str = ""


class EchemPhotocurrentLoadRequest(RequestModel):
    path: str = Field(min_length=1)


class EchemPhotocurrentTraceDataRequest(EchemPhotocurrentLoadRequest):
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    t0: OptFloat = None
    t1: OptFloat = None


class EchemPhotocurrentDetectRequest(RequestModel):
    path: str = Field(min_length=1)
    t0: OptFloat = None
    t1: OptFloat = None
    pos_min_mA: OptFloat = None
    neg_min_abs_mA: OptFloat = None
    min_delay_ms: OptFloat = 1.0
    max_delay_ms: OptFloat = 15.0
    min_pos_distance_ms: OptFloat = 200.0
    use_all: bool = True
    pos_thresh: OptFloat = None
    neg_thresh: OptFloat = None
    min_dist: OptFloat = None


class EchemPhotocurrentExportRequest(RequestModel):
    path: str = Field(min_length=1)
    pairs: list[Any] = Field(default_factory=list)
    mode: str = "download"
    window: list[Any] = Field(default_factory=list)
    t0: OptFloat = None
    t1: OptFloat = None
    pos_min_mA: OptFloat = None
    neg_min_abs_mA: OptFloat = None
    pair_window_ms: OptFloat = 50.0


class EchemPhotocurrentFigureExportRequest(RequestModel):
    path: str = Field(min_length=1)
    fmt: str = "png"
    pairs: list[Any] = Field(default_factory=list)
    window: list[Any] = Field(default_factory=list)
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    dpi: OptInt = 300
