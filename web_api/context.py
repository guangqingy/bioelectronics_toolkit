from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class WebApiContext:
    """Typed composition context shared by Web route modules.

    Route modules still use mapping-style access during the incremental
    migration, so this class deliberately supports ``ctx["key"]`` and
    ``ctx.get("key")`` while making the available fields explicit.
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
    HAS_ABF: bool
    HAS_RHD: bool
    HAS_SCIPY: bool
    HAS_STATSMODELS: bool
    HAS_TIFF: bool
    HAS_PIL: bool
    HAS_READLIF: bool
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

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value: Any) -> None:
        if not hasattr(self, key):
            raise KeyError(key)
        object.__setattr__(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
