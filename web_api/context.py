from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class WebApiContext:
    """Typed composition context shared by Web route modules.

    Route modules use attribute access only, so dataclass fields remain the
    single source of truth for registration dependencies.
    """

    err: Callable[..., Any]
    browse_files: Callable[..., Any]
    browse_files_recursive: Callable[..., Any]
    fig_to_b64: Callable[..., Any]
    float_or: Callable[..., Any]
    int_or: Callable[..., Any]
    request_data: Callable[..., Any]
    apply_axes_limits: Callable[..., Any]
    BASE_DIR: Path
    LINE_COLOR: str
    HAS_RHD: bool
    pyabf: Any = None
    rhd: Any = None
    find_peaks: Any = None
    savgol_filter: Any = None
    peak_widths: Any = None
    f_oneway: Any = None
    MultiComparison: Any = None
    tifflib: Any = None
    Image: Any = None
    ImageDraw: Any = None
    ImageFont: Any = None
    LifFile: Any = None
    jobs: Any = None
