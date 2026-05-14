"""Workflow-level payload builders for the figure-generator Web API."""

from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .constants import DEFAULT_OUT_NAME, DPI, INT_COLS_CANDIDATES, PEAK_COLS_CANDIDATES
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


def browse_payload(folder: str) -> dict[str, Any]:
    p = Path(folder)
    if not p.is_dir():
        return {"subfolders": []}

    subs = [
        {"name": sub.name, "path": str(sub)}
        for sub in sorted(p.iterdir())
        if sub.is_dir() and list(sub.glob("summary_*.csv"))
    ]
    if list(p.glob("summary_*.csv")):
        subs.insert(0, {"name": "(root)", "path": str(p)})
    return {"subfolders": subs}


def preview_payload(data: dict[str, Any], fig_to_b64: Callable[[Any], str]) -> dict[str, Any]:
    queue = data.get("queue", [])
    if not queue:
        raise ValueError("Queue is empty")

    use_peak, use_integral = _selected_metrics(data)
    series_peak, series_int = _selected_series(queue, use_peak, use_integral)
    lin_ranges, log_ranges = _selected_ranges(data, series_peak, series_int, use_peak, use_integral)
    images = []

    for spec in _metric_specs(use_peak, use_integral, series_peak, series_int):
        series, ylabel, title, prefix = spec
        if lin_ranges:
            xmin, xmax = lin_ranges[0]
            _append_preview(
                images,
                f"{prefix}_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                _plot_linear(series, ylabel, title, xmin, xmax),
                fig_to_b64,
            )
        if log_ranges:
            xmin, xmax = log_ranges[0]
            _append_preview(
                images,
                f"{prefix}_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                _plot_log(series, ylabel, title, xmin, xmax),
                fig_to_b64,
            )

    if not images:
        raise ValueError("No figures generated in current ranges")
    return {
        "images": images,
        "queue_count": len(queue),
        "series_count": {"peak": len(series_peak), "integral": len(series_int)},
    }


def run_payload(data: dict[str, Any]) -> dict[str, Any]:
    queue = data.get("queue", [])
    action = str(data.get("action", "analyze")).strip().lower()
    if not queue:
        raise ValueError("Queue is empty")
    if action not in {"analyze", "normalize", "svg"}:
        raise ValueError("Invalid action")

    use_peak, use_integral = _selected_metrics(data)
    series_peak, series_int = _selected_series(
        queue,
        use_peak,
        use_integral,
        empty_message="No usable peak/integral data in queue",
    )
    lin_ranges, log_ranges = _selected_ranges(data, series_peak, series_int, use_peak, use_integral)
    if not lin_ranges and not log_ranges:
        raise ValueError("Please provide at least one linear or log x-range")

    out_root = _resolve_output_root(data.get("main_folder", ""), queue)
    if out_root is None:
        raise ValueError("Cannot determine output root folder")
    out_name = str(data.get("output_name", "")).strip() or DEFAULT_OUT_NAME
    out_dir = out_root / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[str] = []
    specs = _metric_specs(use_peak, use_integral, series_peak, series_int)
    if action == "analyze":
        _save_pngs(generated, out_dir, lin_ranges, log_ranges, specs)
    elif action == "normalize":
        _save_normalized_outputs(
            generated,
            out_dir,
            queue,
            use_peak,
            use_integral,
            lin_ranges,
            log_ranges,
        )
    elif action == "svg":
        _save_svgs(generated, out_dir, lin_ranges, log_ranges, specs)

    if not generated:
        raise ValueError("No output generated with current settings")
    return {
        "ok": True,
        "action": action,
        "saved_dir": str(out_dir),
        "generated_count": len(generated),
        "generated_files": generated,
    }


def _selected_metrics(data: dict[str, Any]) -> tuple[bool, bool]:
    use_peak, use_integral = _metric_flags(data)
    if not use_peak and not use_integral:
        raise ValueError("Select at least one metric")
    return use_peak, use_integral


def _selected_series(
    queue: list[dict[str, Any]],
    use_peak: bool,
    use_integral: bool,
    *,
    empty_message: str = "No summary CSV data found in queued folders",
):
    series_peak, series_int = _build_series(queue, use_peak, use_integral)
    if (use_peak and not series_peak) and (use_integral and not series_int):
        raise ValueError(empty_message)
    return series_peak, series_int


def _selected_ranges(data, series_peak, series_int, use_peak, use_integral):
    lin_ranges = _parse_ranges(data.get("x_lin_ranges", ""))
    log_ranges = _parse_ranges(data.get("x_log_ranges", ""))
    reference = series_peak if use_peak and series_peak else {}
    if not reference and use_integral and series_int:
        reference = series_int
    if reference:
        if not lin_ranges:
            lin_ranges = _default_linear_range(reference)
        if not log_ranges:
            log_ranges = _default_log_range(reference)
    return lin_ranges, log_ranges


def _metric_specs(use_peak, use_integral, series_peak, series_int):
    specs = []
    if use_peak and series_peak:
        specs.append((series_peak, "Peak (normalized)", "Peak vs Power", "peak"))
    if use_integral and series_int:
        specs.append(
            (
                series_int,
                "Integrated charge (normalized)",
                "Integrated Charge vs Power",
                "integral",
            )
        )
    return specs


def _append_preview(images, name, fig, fig_to_b64):
    if fig is not None:
        images.append({"name": name, "img": fig_to_b64(fig)})


def _save_pngs(generated, out_dir, lin_ranges, log_ranges, specs):
    for series, ylabel, title, prefix in specs:
        for xmin, xmax in lin_ranges:
            out = out_dir / f"{prefix}_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
            _save_fig(generated, out, _plot_linear(series, ylabel, title, xmin, xmax))
        for xmin, xmax in log_ranges:
            out = out_dir / f"{prefix}_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
            _save_fig(generated, out, _plot_log(series, ylabel, title, xmin, xmax))


def _save_svgs(generated, out_dir, lin_ranges, log_ranges, specs):
    for series, _ylabel, _title, prefix in specs:
        legend_out = out_dir / f"{prefix}_legend.svg"
        if _legend_svg_only_no_text(series, legend_out):
            generated.append(str(legend_out))
        for xmin, xmax in lin_ranges:
            out = out_dir / f"{prefix}_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
            if _plot_linear_svg_plotonly(series, xmin, xmax, out):
                generated.append(str(out))
        for xmin, xmax in log_ranges:
            out = out_dir / f"{prefix}_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
            if _plot_log_svg_plotonly(series, xmin, xmax, out):
                generated.append(str(out))


def _save_fig(generated, out_path, fig):
    if fig is None:
        return
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    generated.append(str(out_path))


def _save_normalized_outputs(
    generated,
    out_dir,
    queue,
    use_peak,
    use_integral,
    lin_ranges,
    log_ranges,
):
    series_peak_norm, series_int_norm, rows = _normalized_series(queue, use_peak, use_integral)
    if (use_peak and not series_peak_norm) and (use_integral and not series_int_norm):
        raise ValueError("No usable data found to normalize")

    if rows:
        out_df = pd.concat(rows, axis=0, ignore_index=True)
        out_df["power_density"] = pd.to_numeric(out_df["power_density"], errors="coerce")
        out_df.sort_values(["folder", "metric", "power_density"], kind="mergesort", inplace=True)
        out_csv = out_dir / "normalized_series.csv"
        out_df.to_csv(out_csv, index=False)
        generated.append(str(out_csv))

    peak_specs = [
        (
            series_peak_norm,
            "Peak (max raw peak = 1)",
            "Peak vs Power (normalized)",
            "norm_peak",
        )
    ]
    int_specs = [
        (
            series_int_norm,
            "Integrated charge (max raw integral = 1)",
            "Integrated Charge vs Power (normalized)",
            "norm_integral",
        )
    ]
    _save_pngs(generated, out_dir, lin_ranges, log_ranges, peak_specs if use_peak else [])
    _save_pngs(generated, out_dir, lin_ranges, log_ranges, int_specs if use_integral else [])


def _normalized_series(queue, use_peak, use_integral):
    series_peak_norm: dict[str, Any] = {}
    series_int_norm: dict[str, Any] = {}
    rows = []

    for item in queue:
        folder = Path(item.get("path", ""))
        if not folder.is_dir():
            continue
        base_label = str(item.get("label") or folder.name)
        df = _read_all_summaries(folder)
        if df is None:
            continue

        peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
        int_col = _find_matching_column(df, INT_COLS_CANDIDATES)
        if use_peak and peak_col:
            _append_normalized_peak(rows, series_peak_norm, folder, base_label, df, peak_col)
        if use_integral and int_col:
            _append_normalized_integral(rows, series_int_norm, folder, base_label, df, int_col)

    return series_peak_norm, series_int_norm, rows


def _append_normalized_peak(rows, series_peak_norm, folder, base_label, df, peak_col):
    g_raw = _aggregate(df, peak_col)
    if g_raw is None or g_raw.empty:
        return
    v = np.abs(g_raw["mean"].values)
    v = v[np.isfinite(v)]
    nf = float(np.max(v)) if v.size else None
    _append_normalized_group(rows, series_peak_norm, folder, base_label, "peak", g_raw, nf)


def _append_normalized_integral(rows, series_int_norm, folder, base_label, df, int_col):
    nf = _raw_max_value(df, int_col)
    if nf is None or nf == 0:
        return
    g_raw = _aggregate(df, int_col)
    _append_normalized_group(rows, series_int_norm, folder, base_label, "integral", g_raw, nf)


def _append_normalized_group(rows, target, folder, base_label, metric, g_raw, norm_factor):
    g_norm = _scale_group_by_factor(g_raw, norm_factor)
    if g_norm is None or g_norm.empty:
        return
    label = _unique_label(target, base_label)
    target[label] = g_norm

    tmp = g_norm.copy()
    tmp["folder"] = folder.name
    tmp["series_label"] = base_label
    tmp["metric"] = metric
    tmp["norm_factor"] = norm_factor
    tmp["mean_norm"] = tmp["mean"]
    tmp["sem_norm"] = tmp["sem"] if "sem" in tmp.columns else np.nan
    tmp["mean_raw"] = (
        g_raw["mean"].values if (g_raw is not None and len(g_raw) == len(tmp)) else np.nan
    )
    tmp["sem_raw"] = (
        g_raw["sem"].values
        if (g_raw is not None and "sem" in g_raw.columns and len(g_raw) == len(tmp))
        else np.nan
    )
    rows.append(
        tmp[
            [
                "folder",
                "series_label",
                "metric",
                "power_density",
                "mean_raw",
                "sem_raw",
                "mean_norm",
                "sem_norm",
                "norm_factor",
            ]
        ]
    )
