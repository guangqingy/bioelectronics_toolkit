import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, ScalarFormatter
from flask import jsonify, request

from .jobs import submit_flask_route_job

DEFAULT_OUT_NAME = "plots_quick_analysis"
DPI = 600
EPS = 1e-9

POWER_COL_CANDIDATES = ["power_density", "power_mW_mm2", "power_mW_mm^2", "power_mW"]
PEAK_COLS_CANDIDATES = ["capacitance_peak", "capacitance_peak_norm"]
INT_COLS_CANDIDATES = ["integral_charge", "integral_charge_norm"]


def _find_matching_column(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _parse_ranges(raw):
    if isinstance(raw, list):
        text = ";".join(str(x).strip() for x in raw if str(x).strip())
    else:
        text = str(raw or "").strip()

    out = []
    if not text:
        return out
    for block in text.split(";"):
        block = block.strip()
        if not block or "-" not in block:
            continue
        a, b = block.split("-", 1)
        try:
            xmin = float(a.strip())
            xmax = float(b.strip())
        except Exception:
            continue
        if np.isfinite(xmin) and np.isfinite(xmax) and xmin != xmax:
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            out.append((xmin, xmax))
    return out


def _fmt_range_value(v):
    return f"{float(v):g}"


def _read_all_summaries(folder):
    csvs = sorted(folder.glob("summary_*.csv"))
    if not csvs:
        return None

    frames = []
    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        pcol = _find_matching_column(df, POWER_COL_CANDIDATES)
        if pcol is None:
            continue

        df = df.rename(columns={pcol: "power_density"}).copy()
        cols = ["power_density"]

        peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
        int_col = _find_matching_column(df, INT_COLS_CANDIDATES)
        if peak_col:
            cols.append(peak_col)
        if int_col:
            cols.append(int_col)

        frames.append(df[cols].copy())

    if not frames:
        return None

    out = pd.concat(frames, axis=0, ignore_index=True)
    out["power_density"] = pd.to_numeric(out["power_density"], errors="coerce")
    for c in PEAK_COLS_CANDIDATES + INT_COLS_CANDIDATES:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out.replace([np.inf, -np.inf], np.nan, inplace=True)
    out.dropna(subset=["power_density"], inplace=True)
    return out if not out.empty else None


def _aggregate(df, value_col):
    tmp = df[["power_density", value_col]].dropna()
    if tmp.empty:
        return pd.DataFrame(columns=["power_density", "mean", "sem", "n"])
    g = (
        tmp.groupby("power_density", as_index=False)
        .agg(mean=(value_col, "mean"), std=(value_col, "std"), n=(value_col, "count"))
        .sort_values("power_density", kind="mergesort")
    )
    g["std"] = g["std"].fillna(0.0)
    g["n"] = g["n"].fillna(0).astype(int)
    g["sem"] = 0.0
    m = g["n"].values > 1
    g.loc[m, "sem"] = g.loc[m, "std"].values / np.sqrt(g.loc[m, "n"].values.astype(float))
    return g[["power_density", "mean", "sem", "n"]]


def _raw_max_value(df, value_col):
    if df is None or df.empty or value_col not in df.columns:
        return None
    v = pd.to_numeric(df[value_col], errors="coerce").values
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    m = float(np.max(v))
    if (not np.isfinite(m)) or m == 0:
        return None
    return m


def _scale_group_by_factor(g, factor):
    if g is None or g.empty or "mean" not in g.columns:
        return None
    if (factor is None) or (not np.isfinite(factor)) or factor == 0:
        return None
    gg = g.copy()
    gg["mean"] = gg["mean"] / factor
    if "sem" in gg.columns:
        gg["sem"] = gg["sem"] / factor
    return gg


def _clip_to_range(df, xmin, xmax):
    m = np.isfinite(df["power_density"])
    g = df.loc[m]
    g = g[(g["power_density"] >= xmin) & (g["power_density"] <= xmax)]
    return g


def _min_positive_x(dfs):
    vals = []
    for df in dfs:
        if df is None or df.empty or "power_density" not in df:
            continue
        x = np.asarray(df["power_density"])
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            vals.append(np.min(x))
    return min(vals) if vals else EPS


def _unique_label(existing, label):
    if label not in existing:
        return label
    base = label
    k = 2
    while f"{base} ({k})" in existing:
        k += 1
    return f"{base} ({k})"


def _metric_flags(d):
    use_peak = bool(d.get("use_peak", True))
    use_integral = bool(d.get("use_integral", True))

    metrics = d.get("metrics")
    if isinstance(metrics, dict):
        use_peak = bool(metrics.get("peak", use_peak))
        use_integral = bool(metrics.get("integral", use_integral))
    elif isinstance(metrics, list):
        ms = {str(x).strip().lower() for x in metrics}
        use_peak = "peak" in ms
        use_integral = "integral" in ms

    metric_single = str(d.get("metric", "")).strip().lower()
    if metric_single in {"peak", "capacitance_peak", "capacitance_peak_norm"}:
        use_peak, use_integral = True, False
    elif metric_single in {"integral", "integral_charge", "integral_charge_norm"}:
        use_peak, use_integral = False, True

    return use_peak, use_integral


def _build_series(queue, use_peak, use_integral):
    series_peak = {}
    series_int = {}

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
            g = _aggregate(df, peak_col)
            if g is not None and not g.empty:
                label = _unique_label(series_peak, base_label)
                series_peak[label] = g
        if use_integral and int_col:
            g = _aggregate(df, int_col)
            if g is not None and not g.empty:
                label = _unique_label(series_int, base_label)
                series_int[label] = g

    return series_peak, series_int


def _default_linear_range(groups):
    xmax = 0.0
    for g in groups.values():
        if g is None or g.empty:
            continue
        x = pd.to_numeric(g["power_density"], errors="coerce").to_numpy()
        x = x[np.isfinite(x)]
        if x.size:
            xmax = max(xmax, float(np.max(x)))
    if xmax <= 0:
        xmax = 1.0
    return [(0.0, xmax)]


def _default_log_range(groups):
    all_dfs = [g for g in groups.values() if g is not None and not g.empty]
    minpos = _min_positive_x(all_dfs)
    xmax = minpos
    for g in all_dfs:
        x = pd.to_numeric(g["power_density"], errors="coerce").to_numpy()
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            xmax = max(xmax, float(np.max(x)))
    if xmax <= minpos:
        xmax = minpos * 10.0
    return [(float(minpos), float(xmax))]


def _plot_linear(groups, ylabel, title_txt, xmin, xmax):
    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        plt.close(fig)
        return None
    ax.set_xlabel("Power density (mW/mm^2)")
    ax.set_ylabel(ylabel)
    ax.set_xlim(xmin, xmax)
    ax.set_title(title_txt)
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, borderaxespad=0.0)
    fig.tight_layout(rect=(0.0, 0.0, 0.78, 1.0))
    return fig


def _plot_log(groups, ylabel, title_txt, xmin, xmax):
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        plt.close(fig)
        return None

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)
    ax.set_xlabel("Power density (mW/mm^2, log scale)")
    ax.set_ylabel(ylabel)
    ax.set_title(title_txt + "  (log x)")
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, borderaxespad=0.0)
    fig.tight_layout(rect=(0.0, 0.0, 0.78, 1.0))
    return fig


def _plot_linear_svg_plotonly(groups, xmin, xmax, out_path):
    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for _, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.set_xlim(xmin, xmax)
    ax.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)
    return True


def _plot_log_svg_plotonly(groups, xmin, xmax, out_path):
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for _, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return False

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)
    return True


def _legend_svg_only_no_text(groups, out_path):
    labels = list(groups.keys())
    if not labels:
        return False

    colors = plt.rcParams["axes.prop_cycle"].by_key().get("color", [])
    if not colors:
        colors = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]

    handles = []
    for i, _lab in enumerate(labels):
        c = colors[i % len(colors)]
        handles.append(Line2D([], [], color=c, marker="o", linestyle="-", linewidth=1.2, markersize=3.5))

    fig = plt.figure(figsize=(1.1, 0.35 * max(1, len(handles))), dpi=DPI)
    ax = plt.gca()
    ax.legend(
        handles=handles,
        labels=[""] * len(handles),
        loc="center",
        frameon=False,
        handlelength=1.6,
        handletextpad=0.0,
        borderaxespad=0.0,
        labelspacing=0.35,
    )
    ax.axis("off")
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)
    return True


def _resolve_output_root(main_folder, queue):
    p = Path(str(main_folder or "").strip()) if str(main_folder or "").strip() else None
    if p is not None and p.is_dir():
        return p
    if queue:
        q0 = Path(queue[0].get("path", ""))
        if q0.exists():
            return q0.parent if q0.is_dir() else q0.parent
    return None


def register_figure_generator_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    jobs = ctx.get("jobs")

    @app.route("/api/figure/browse", methods=["POST"])
    def api_figure_browse():
        folder = (request.json or {}).get("folder", "")
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
    def api_figure_plot():
        d = request.json or {}
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
    def api_figure_run():
        d = request.json or {}
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
    def api_figure_run_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/figure/run",
            "figure.run",
            "Run figure export",
            api_figure_run,
            request.json or {},
        )
