from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import OptFloat, OptInt, RequestModel


class EmgAnalysisBrowseRequest(RequestModel):
    folder: str = ""


class EmgAnalysisLoadRequest(RequestModel):
    path: str = Field(min_length=1)
    merge_pair: bool = False
    preview_merge_pair: bool | None = None


class EmgAnalysisViewRequest(EmgAnalysisLoadRequest):
    channel: Any = 0
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    invert_y: bool = False
    downsample: Any = "auto"
    dsf: Any = None
    filter_type: str = "none"
    filter_low_hz: OptFloat = None
    filter_high_hz: OptFloat = None
    filter_notch_hz: OptFloat = None
    filter_order: OptInt = None
    filter_notch_q: OptFloat = None
    fig_width_in: OptFloat = None
    fig_height_in: OptFloat = None
    fig_dpi: OptInt = None
    trace_line_width: OptFloat = None
    trace_color: str = "#3E6AE1"
    show_grid: bool = False
    show_title: bool = False


class EmgAnalysisProcessingRequest(EmgAnalysisViewRequest):
    process_type: str = "envelope"
    smooth_ms: OptFloat = None
    envelope_smooth_ms: OptFloat = None
    smooth_method: str = "moving"
    sg_poly: OptInt = None
    fit_degree: OptInt = None
    fit_show_raw: bool = False
    fft_window: str = "hann"
    fft_max_hz: OptFloat = None
    fft_log: bool = False
    stft_ms: OptFloat = None
    stft_overlap_pct: OptFloat = None
    stft_max_hz: OptFloat = None
    stft_cmap: str = "viridis"
    stft_log: bool = False
    fmt: str = "csv"
    mode: str = "download"


class EmgAnalysisExportChannelRequest(EmgAnalysisViewRequest):
    fmt: str = "csv"
    mode: str = "download"


class EmgAnalysisExportAllRequest(RequestModel):
    path: str = Field(min_length=1)
    mode: str = "download"
    merge_pair: bool = True
    preview_merge_pair: bool | None = None
    wide_csv: bool = False


class EmgAnalysisExportQueueRequest(RequestModel):
    paths: list[str] = Field(default_factory=list)
    merge_pair: bool = True
    preview_merge_pair: bool | None = None
    wide_csv: bool = False


class EmgAnalysisRenamePreviewRequest(RequestModel):
    root: str = Field(min_length=1)
    find: str = ""
    replace: str = ""
    prefix: str = ""
    suffix: str = ""
    recursive: bool = True
    include_root: bool = True
    include_files: bool = True
    include_dirs: bool = True
    use_regex: bool = False
    case_sensitive: bool = True
    preserve_extension: bool = True
    skip_hidden: bool = True
    extensions: Any = ".rhd,.xml,.csv,.txt,.tsv,.json,.png,.svg"
    max_items: int = Field(default=5000, ge=1, le=50000)


class EmgAnalysisRenameApplyRequest(EmgAnalysisRenamePreviewRequest):
    confirm: bool = False
