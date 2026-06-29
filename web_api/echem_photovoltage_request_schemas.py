from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import OptFloat, OptInt, RequestModel


class EchemPhotovoltageBrowseRequest(RequestModel):
    folder: str = ""


class EchemPhotovoltageLoadRequest(RequestModel):
    path: str = Field(min_length=1)


class EchemPhotovoltageTraceDataRequest(EchemPhotovoltageLoadRequest):
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    t0: OptFloat = None
    t1: OptFloat = None


class EchemPhotovoltageDetectRequest(RequestModel):
    path: str = Field(min_length=1)
    t0: OptFloat = None
    t1: OptFloat = None
    baseline_method: str = "median"
    detrend_method: str = ""
    baseline_win_ms: OptFloat = 50.0
    bl_win_ms: OptFloat = None
    detrend_win_ms: OptFloat = None
    detrend_win: OptFloat = None
    sg_window_ms: OptFloat = 51.0
    sg_win_ms: OptFloat = None
    sg_poly: OptInt = 3
    peak_min_v: OptFloat = None
    peak_min_V: OptFloat = None
    pv_height: OptFloat = None
    min_width_ms: OptFloat = 5.0
    min_spacing_ms: OptFloat = 10.0
    pv_dist: OptFloat = None
    min_dist: OptFloat = None
    polarity: str = ""
    det_pos: bool = True
    det_neg: bool = False
    use_all: bool = False
    show_detrended: bool = False


class EchemPhotovoltageExportRequest(RequestModel):
    path: str = Field(min_length=1)
    pulses: list[Any] = Field(default_factory=list)
    mode: str = "download"
    window: list[Any] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    baseline_method: str = ""
    detrend_method: str = ""
    baseline_win_ms: OptFloat = None
    bl_win_ms: OptFloat = None
    sg_window_ms: OptFloat = None
    sg_poly: OptInt = None
    peak_min_v: OptFloat = None
    peak_min_V: OptFloat = None
    min_width_ms: OptFloat = None
    min_spacing_ms: OptFloat = None
    pulse_window_ms: OptFloat = 50.0


class EchemPhotovoltageFigureExportRequest(RequestModel):
    path: str = Field(min_length=1)
    fmt: str = "png"
    pulses: list[Any] = Field(default_factory=list)
    window: list[Any] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    show_detrended: bool | None = None
    baseline_method: str = ""
    baseline_win_ms: OptFloat = None
    sg_window_ms: OptFloat = None
    sg_poly: OptInt = None
    dpi: OptInt = 300
