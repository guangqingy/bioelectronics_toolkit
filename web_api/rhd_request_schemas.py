from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import RequestModel


class RhdBrowseRequest(RequestModel):
    folder: str = ""


class RhdLoadRequest(RequestModel):
    path: str = Field(min_length=1)
    merge_pair: Any = False
    preview_merge_pair: Any = None


class RhdViewRequest(RhdLoadRequest):
    channel: Any = 0
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    invert_y: Any = False
    downsample: Any = "auto"
    dsf: Any = None
    filter_type: str = "none"
    filter_low_hz: Any = None
    filter_high_hz: Any = None
    filter_notch_hz: Any = None
    filter_order: Any = None
    filter_notch_q: Any = None
    fig_width_in: Any = None
    fig_height_in: Any = None
    fig_dpi: Any = None
    trace_line_width: Any = None
    trace_color: str = "#3E6AE1"
    show_grid: Any = False
    show_title: Any = False


class RhdProcessingRequest(RhdViewRequest):
    process_type: str = "envelope"
    smooth_ms: Any = None
    envelope_smooth_ms: Any = None
    smooth_method: str = "moving"
    sg_poly: Any = None
    fit_degree: Any = None
    fit_show_raw: Any = False
    fft_window: str = "hann"
    fft_max_hz: Any = None
    fft_log: Any = False
    stft_ms: Any = None
    stft_overlap_pct: Any = None
    stft_max_hz: Any = None
    stft_cmap: str = "viridis"
    stft_log: Any = False
    fmt: str = "csv"
    mode: Any = "download"


class RhdExportChannelRequest(RhdViewRequest):
    fmt: str = "csv"
    mode: Any = "download"


class RhdExportAllRequest(RequestModel):
    path: str = Field(min_length=1)
    mode: Any = "download"
    merge_pair: Any = True
    preview_merge_pair: Any = None
    wide_csv: Any = False


class RhdExportQueueRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)
    merge_pair: Any = True
    preview_merge_pair: Any = None
    wide_csv: Any = False


class RhdRenamePreviewRequest(RequestModel):
    root: str = Field(min_length=1)
    find: str = ""
    replace: str = ""
    prefix: str = ""
    suffix: str = ""
    recursive: Any = True
    include_root: Any = True
    include_files: Any = True
    include_dirs: Any = True
    use_regex: Any = False
    case_sensitive: Any = True
    preserve_extension: Any = True
    skip_hidden: Any = True
    extensions: Any = ".rhd,.csv,.txt,.tsv,.json,.png,.svg"
    max_items: int = Field(default=5000, ge=1, le=50000)


class RhdRenameApplyRequest(RhdRenamePreviewRequest):
    confirm: Any = False
