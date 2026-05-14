"""Figure-generator service helpers and workflow payload builders."""

from .constants import (
    DEFAULT_OUT_NAME,
    DPI,
    EPS,
    INT_COLS_CANDIDATES,
    PEAK_COLS_CANDIDATES,
    POWER_COL_CANDIDATES,
)
from .plots import (
    _legend_svg_only_no_text,
    _plot_linear,
    _plot_linear_svg_plotonly,
    _plot_log,
    _plot_log_svg_plotonly,
)
from .summary import (
    _aggregate,
    _build_series,
    _default_linear_range,
    _default_log_range,
    _find_matching_column,
    _fmt_range_value,
    _metric_flags,
    _parse_ranges,
    _raw_max_value,
    _read_all_summaries,
    _resolve_output_root,
    _scale_group_by_factor,
    _unique_label,
)
from .workflows import browse_payload, preview_payload, run_payload

__all__ = [
    "DEFAULT_OUT_NAME",
    "DPI",
    "EPS",
    "INT_COLS_CANDIDATES",
    "PEAK_COLS_CANDIDATES",
    "POWER_COL_CANDIDATES",
    "_aggregate",
    "_build_series",
    "_default_linear_range",
    "_default_log_range",
    "_find_matching_column",
    "_fmt_range_value",
    "_legend_svg_only_no_text",
    "_metric_flags",
    "_parse_ranges",
    "_plot_linear",
    "_plot_linear_svg_plotonly",
    "_plot_log",
    "_plot_log_svg_plotonly",
    "_raw_max_value",
    "_read_all_summaries",
    "_resolve_output_root",
    "_scale_group_by_factor",
    "_unique_label",
    "browse_payload",
    "preview_payload",
    "run_payload",
]
