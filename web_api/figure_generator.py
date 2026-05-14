from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import jsonify
from pydantic import Field, ValidationError

from services.figure_generator import (
    DEFAULT_OUT_NAME,
    DPI,
    INT_COLS_CANDIDATES,
    PEAK_COLS_CANDIDATES,
    _aggregate,
    _build_series,
    _default_linear_range,
    _default_log_range,
    _find_matching_column,
    _fmt_range_value,
    _legend_svg_only_no_text,
    _metric_flags,
    _parse_ranges,
    _plot_linear,
    _plot_linear_svg_plotonly,
    _plot_log,
    _plot_log_svg_plotonly,
    _raw_max_value,
    _read_all_summaries,
    _resolve_output_root,
    _scale_group_by_factor,
    _unique_label,
)

from .jobs import route_response_to_payload, submit_json_task
from .request_validation import RequestModel, parse_json_payload, request_schema, validation_error_response


class FigureBrowseRequest(RequestModel):
    folder: str = ""


class FigurePlotRequest(RequestModel):
    main_folder: str = ""
    output_name: str = ""
    queue: list[Any] = Field(default_factory=list)
    metrics: Any = None
    metric: str = ""
    use_peak: bool = True
    use_integral: bool = True
    x_lin_ranges: Any = ""
    x_log_ranges: Any = ""


class FigureRunRequest(FigurePlotRequest):
    action: str = "analyze"


def register_figure_generator_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    jobs = ctx.get("jobs")

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    @app.route("/api/figure/browse", methods=["POST"])
    @request_schema(FigureBrowseRequest)
    def api_figure_browse():
        try:
            folder = parse_json_payload(FigureBrowseRequest).folder
        except ValidationError as exc:
            return validation_error_response(exc)
        p = Path(folder)
        if not p.is_dir():
            return jsonify({"subfolders": []})

        subs = []
        for sub in sorted(p.iterdir()):
            if sub.is_dir() and list(sub.glob("summary_*.csv")):
                subs.append({"name": sub.name, "path": str(sub)})
        if list(p.glob("summary_*.csv")):
            subs.insert(0, {"name": "(root)", "path": str(p)})
        return jsonify({"subfolders": subs})

    @app.route("/api/figure/plot", methods=["POST"])
    @request_schema(FigurePlotRequest)
    def api_figure_plot():
        try:
            d = parse_json_payload(FigurePlotRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        queue = d.get("queue", [])
        if not queue:
            return err("Queue is empty")

        use_peak, use_integral = _metric_flags(d)
        if not use_peak and not use_integral:
            return err("Select at least one metric")

        try:
            series_peak, series_int = _build_series(queue, use_peak, use_integral)
            if (use_peak and not series_peak) and (use_integral and not series_int):
                return err("No summary CSV data found in queued folders")

            lin_ranges = _parse_ranges(d.get("x_lin_ranges", ""))
            log_ranges = _parse_ranges(d.get("x_log_ranges", ""))

            if use_peak and series_peak:
                if not lin_ranges:
                    lin_ranges = _default_linear_range(series_peak)
                if not log_ranges:
                    log_ranges = _default_log_range(series_peak)
            elif use_integral and series_int:
                if not lin_ranges:
                    lin_ranges = _default_linear_range(series_int)
                if not log_ranges:
                    log_ranges = _default_log_range(series_int)

            images = []

            def _append_preview(fig_name, fig):
                if fig is None:
                    return
                images.append({"name": fig_name, "img": fig_to_b64(fig)})

            if use_peak and series_peak:
                if lin_ranges:
                    xmin, xmax = lin_ranges[0]
                    _append_preview(
                        f"peak_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                        _plot_linear(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax),
                    )
                if log_ranges:
                    xmin, xmax = log_ranges[0]
                    _append_preview(
                        f"peak_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                        _plot_log(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax),
                    )

            if use_integral and series_int:
                if lin_ranges:
                    xmin, xmax = lin_ranges[0]
                    _append_preview(
                        f"integral_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                        _plot_linear(
                            series_int,
                            "Integrated charge (normalized)",
                            "Integrated Charge vs Power",
                            xmin,
                            xmax,
                        ),
                    )
                if log_ranges:
                    xmin, xmax = log_ranges[0]
                    _append_preview(
                        f"integral_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}",
                        _plot_log(
                            series_int,
                            "Integrated charge (normalized)",
                            "Integrated Charge vs Power",
                            xmin,
                            xmax,
                        ),
                    )

            if not images:
                return err("No figures generated in current ranges")

            return jsonify(
                {
                    "images": images,
                    "queue_count": len(queue),
                    "series_count": {
                        "peak": len(series_peak),
                        "integral": len(series_int),
                    },
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/figure/run", methods=["POST"])
    @request_schema(FigureRunRequest)
    def api_figure_run(payload=None):
        try:
            if payload is None:
                d = parse_json_payload(FigureRunRequest).model_dump()
            else:
                d = FigureRunRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        queue = d.get("queue", [])
        action = str(d.get("action", "analyze")).strip().lower()

        if not queue:
            return err("Queue is empty")
        if action not in {"analyze", "normalize", "svg"}:
            return err("Invalid action")

        use_peak, use_integral = _metric_flags(d)
        if not use_peak and not use_integral:
            return err("Select at least one metric")

        lin_ranges = _parse_ranges(d.get("x_lin_ranges", ""))
        log_ranges = _parse_ranges(d.get("x_log_ranges", ""))

        try:
            series_peak, series_int = _build_series(queue, use_peak, use_integral)
            if (use_peak and not series_peak) and (use_integral and not series_int):
                return err("No usable peak/integral data in queue")

            if use_peak and series_peak:
                if not lin_ranges:
                    lin_ranges = _default_linear_range(series_peak)
                if not log_ranges:
                    log_ranges = _default_log_range(series_peak)
            elif use_integral and series_int:
                if not lin_ranges:
                    lin_ranges = _default_linear_range(series_int)
                if not log_ranges:
                    log_ranges = _default_log_range(series_int)

            if not lin_ranges and not log_ranges:
                return err("Please provide at least one linear or log x-range")

            out_root = _resolve_output_root(d.get("main_folder", ""), queue)
            if out_root is None:
                return err("Cannot determine output root folder")

            out_name = str(d.get("output_name", "")).strip() or DEFAULT_OUT_NAME
            out_dir = out_root / out_name
            out_dir.mkdir(parents=True, exist_ok=True)

            generated = []

            if action == "analyze":
                if use_peak and series_peak:
                    for xmin, xmax in lin_ranges:
                        name = f"peak_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        out = out_dir / name
                        fig = _plot_linear(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax)
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = f"peak_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        out = out_dir / name
                        fig = _plot_log(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax)
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))

                if use_integral and series_int:
                    for xmin, xmax in lin_ranges:
                        name = (
                            f"integral_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        )
                        out = out_dir / name
                        fig = _plot_linear(
                            series_int,
                            "Integrated charge (normalized)",
                            "Integrated Charge vs Power",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = f"integral_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        out = out_dir / name
                        fig = _plot_log(
                            series_int,
                            "Integrated charge (normalized)",
                            "Integrated Charge vs Power",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))

            elif action == "normalize":
                series_peak_norm = {}
                series_int_norm = {}
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
                        g_raw = _aggregate(df, peak_col)
                        if g_raw is not None and not g_raw.empty:
                            v = np.abs(g_raw["mean"].values)
                            v = v[np.isfinite(v)]
                            nf = float(np.max(v)) if v.size else None
                        else:
                            nf = None

                        if nf is not None and nf != 0:
                            g_norm = _scale_group_by_factor(g_raw, nf)
                            if g_norm is not None and not g_norm.empty:
                                label = _unique_label(series_peak_norm, base_label)
                                series_peak_norm[label] = g_norm

                                tmp = g_norm.copy()
                                tmp["folder"] = folder.name
                                tmp["series_label"] = base_label
                                tmp["metric"] = "peak"
                                tmp["norm_factor"] = nf
                                tmp["mean_norm"] = tmp["mean"]
                                tmp["sem_norm"] = (
                                    tmp["sem"] if "sem" in tmp.columns else np.nan
                                )
                                tmp["mean_raw"] = (
                                    g_raw["mean"].values
                                    if (g_raw is not None and len(g_raw) == len(tmp))
                                    else np.nan
                                )
                                tmp["sem_raw"] = (
                                    g_raw["sem"].values
                                    if (
                                        g_raw is not None
                                        and "sem" in g_raw.columns
                                        and len(g_raw) == len(tmp)
                                    )
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

                    if use_integral and int_col:
                        nf = _raw_max_value(df, int_col)
                        if nf is not None and nf != 0:
                            g_raw = _aggregate(df, int_col)
                            g_norm = _scale_group_by_factor(g_raw, nf)
                            if g_norm is not None and not g_norm.empty:
                                label = _unique_label(series_int_norm, base_label)
                                series_int_norm[label] = g_norm

                                tmp = g_norm.copy()
                                tmp["folder"] = folder.name
                                tmp["series_label"] = base_label
                                tmp["metric"] = "integral"
                                tmp["norm_factor"] = nf
                                tmp["mean_norm"] = tmp["mean"]
                                tmp["sem_norm"] = (
                                    tmp["sem"] if "sem" in tmp.columns else np.nan
                                )
                                tmp["mean_raw"] = (
                                    g_raw["mean"].values
                                    if (g_raw is not None and len(g_raw) == len(tmp))
                                    else np.nan
                                )
                                tmp["sem_raw"] = (
                                    g_raw["sem"].values
                                    if (
                                        g_raw is not None
                                        and "sem" in g_raw.columns
                                        and len(g_raw) == len(tmp)
                                    )
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

                if (use_peak and not series_peak_norm) and (use_integral and not series_int_norm):
                    return err("No usable data found to normalize")

                if rows:
                    out_df = pd.concat(rows, axis=0, ignore_index=True)
                    out_df["power_density"] = pd.to_numeric(out_df["power_density"], errors="coerce")
                    out_df.sort_values(
                        ["folder", "metric", "power_density"], kind="mergesort", inplace=True
                    )
                    out_csv = out_dir / "normalized_series.csv"
                    out_df.to_csv(out_csv, index=False)
                    generated.append(str(out_csv))

                if use_peak and series_peak_norm:
                    for xmin, xmax in lin_ranges:
                        name = (
                            f"norm_peak_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        )
                        out = out_dir / name
                        fig = _plot_linear(
                            series_peak_norm,
                            "Peak (max raw peak = 1)",
                            "Peak vs Power (normalized)",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = f"norm_peak_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        out = out_dir / name
                        fig = _plot_log(
                            series_peak_norm,
                            "Peak (max raw peak = 1)",
                            "Peak vs Power (normalized)",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))

                if use_integral and series_int_norm:
                    for xmin, xmax in lin_ranges:
                        name = (
                            f"norm_integral_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        )
                        out = out_dir / name
                        fig = _plot_linear(
                            series_int_norm,
                            "Integrated charge (max raw integral = 1)",
                            "Integrated Charge vs Power (normalized)",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = (
                            f"norm_integral_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.png"
                        )
                        out = out_dir / name
                        fig = _plot_log(
                            series_int_norm,
                            "Integrated charge (max raw integral = 1)",
                            "Integrated Charge vs Power (normalized)",
                            xmin,
                            xmax,
                        )
                        if fig is not None:
                            fig.savefig(out, dpi=DPI, bbox_inches="tight")
                            plt.close(fig)
                            generated.append(str(out))

            elif action == "svg":
                if use_peak and series_peak:
                    out = out_dir / "peak_legend.svg"
                    if _legend_svg_only_no_text(series_peak, out):
                        generated.append(str(out))
                    for xmin, xmax in lin_ranges:
                        name = f"peak_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
                        out = out_dir / name
                        if _plot_linear_svg_plotonly(series_peak, xmin, xmax, out):
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = f"peak_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
                        out = out_dir / name
                        if _plot_log_svg_plotonly(series_peak, xmin, xmax, out):
                            generated.append(str(out))

                if use_integral and series_int:
                    out = out_dir / "integral_legend.svg"
                    if _legend_svg_only_no_text(series_int, out):
                        generated.append(str(out))
                    for xmin, xmax in lin_ranges:
                        name = (
                            f"integral_linear_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
                        )
                        out = out_dir / name
                        if _plot_linear_svg_plotonly(series_int, xmin, xmax, out):
                            generated.append(str(out))
                    for xmin, xmax in log_ranges:
                        name = f"integral_log_{_fmt_range_value(xmin)}-{_fmt_range_value(xmax)}.svg"
                        out = out_dir / name
                        if _plot_log_svg_plotonly(series_int, xmin, xmax, out):
                            generated.append(str(out))

            if not generated:
                return err("No output generated with current settings")

            return jsonify(
                {
                    "ok": True,
                    "action": action,
                    "saved_dir": str(out_dir),
                    "generated_count": len(generated),
                    "generated_files": generated,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/figure/run_job", methods=["POST"])
    @request_schema(FigureRunRequest)
    def api_figure_run_job():
        try:
            body = parse_json_payload(FigureRunRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "figure.run",
            "Run figure export",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_figure_run, "Running figure export"
            ),
            body,
            metadata={"endpoint": "/api/figure/run"},
        )
