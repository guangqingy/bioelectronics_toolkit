import io
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify, request


from web_api.common import as_bool, mode_is_save
from .jobs import submit_flask_route_job


def register_abf_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    request_data = ctx["request_data"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    jobs = ctx.get("jobs")

    has_abf = ctx["HAS_ABF"]
    has_scipy = ctx["HAS_SCIPY"]
    pyabf_mod = ctx.get("pyabf")
    find_peaks = ctx.get("find_peaks")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _default_baseline_indices(n):
        i0, i1 = 19000, 20000
        if i1 > n or (i1 - i0) < 5:
            i0 = int(0.38 * n)
            i1 = int(0.40 * n)
        i0 = max(0, min(i0, n - 1))
        i1 = max(i0 + 1, min(i1, n))
        return i0, i1

    def _abf_baseline_apply(y, t, pre0_ms=None, pre1_ms=None, use_default=False):
        """Return baseline-subtracted signal and baseline value."""
        baseline = 0.0
        n = len(y)

        if pre0_ms is not None and pre1_ms is not None and len(t) > 1:
            dt = t[1] - t[0]
            if dt > 0:
                i0 = max(0, int(pre0_ms / 1000.0 / dt))
                i1 = min(n, int(pre1_ms / 1000.0 / dt))
                if i1 > i0:
                    baseline = float(np.mean(y[i0:i1]))
                    return y - baseline, baseline

        if use_default and n > 5:
            i0, i1 = _default_baseline_indices(n)
            baseline = float(np.mean(y[i0:i1]))
            return y - baseline, baseline

        return y, baseline

    def _abf_baseline_subtract(y, t, pre0_ms, pre1_ms):
        """Subtract mean of the [pre0_ms, pre1_ms] baseline window."""
        if pre0_ms is None or pre1_ms is None:
            return y
        dt = t[1] - t[0]
        i0 = max(0, int(pre0_ms / 1000 / dt))
        i1 = min(len(t), int(pre1_ms / 1000 / dt))
        if i1 > i0:
            baseline = np.mean(y[i0:i1])
            return y - baseline
        return y

    def _abf_estimate_r(i_trace, v_trace, dt):
        """Estimate resistance from V-step edges via dV/dI."""
        try:
            dv = np.diff(v_trace)
            thresh = np.std(dv) * 3
            edges = np.where(np.abs(dv) > thresh)[0]
            if len(edges) >= 2:
                e = edges[0]
                win = max(10, int(0.002 / dt))
                v_pre = np.mean(v_trace[max(0, e - win) : e])
                v_post = np.mean(v_trace[e + 1 : e + 1 + win])
                i_pre = np.mean(i_trace[max(0, e - win) : e])
                i_post = np.mean(i_trace[e + 1 : e + 1 + win])
                d_v = v_post - v_pre
                d_i = i_post - i_pre
                if abs(d_i) > 1e-6:
                    return abs(d_v / d_i)
        except Exception:
            pass
        return None

    @app.route("/api/abf/browse", methods=["POST"])
    def api_abf_browse():
        """List .abf files in a folder."""
        d = request.json or {}
        folder = d.get("folder", "")
        files_data = browse_files(folder, {".abf"})
        return jsonify({"files": files_data, "folder": folder})

    @app.route("/api/abf/browse/tree", methods=["POST"])
    def api_abf_browse_tree():
        """Get subdirectories at a path for tree browsing."""
        d = request.json or {}
        folder = d.get("folder", "")
        p = Path(folder)
        if not p.is_dir():
            return err(f"Not a directory: {folder}")

        subdirs = []
        try:
            for item in sorted(p.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    has_abf_files = any(f.suffix.lower() == ".abf" for f in item.iterdir())
                    subdirs.append({"name": item.name, "path": str(item), "has_abf": has_abf_files})
        except Exception:
            pass

        return jsonify({"subdirs": subdirs})

    @app.route("/api/abf/info", methods=["POST"])
    def api_abf_info():
        """Load ABF file metadata."""
        if not has_abf or pyabf_mod is None:
            return err("pyabf not installed. Run: pip install pyabf")
        path = (request.json or {}).get("path", "")
        try:
            abf = pyabf_mod.ABF(path)
            channels = []
            for i in range(abf.channelCount):
                ch_name = abf.adcNames[i] if i < len(abf.adcNames) else f"Ch{i}"
                ch_unit = abf.adcUnits[i] if i < len(abf.adcUnits) else ""
                channels.append(
                    {
                        "index": i,
                        "label": f"{i}: {ch_name} [{ch_unit}]",
                        "name": ch_name,
                        "unit": ch_unit,
                    }
                )
            return jsonify(
                {
                    "num_sweeps": abf.sweepCount,
                    "channels": channels,
                    "channel_count": abf.channelCount,
                    "sample_rate": abf.dataRate,
                    "duration_s": round(abf.sweepLengthSec, 3),
                }
            )
        except Exception as e:
            return err(e)

    @app.route("/api/abf/plot", methods=["POST"])
    def api_abf_plot():
        if not has_abf or pyabf_mod is None:
            return err("pyabf not installed")
        d = request.json or {}
        path = d.get("path", "")
        sweep = int_or(d.get("sweep", 0), 0)
        ch = int_or(d.get("channel", 0), 0)
        i_ch = int_or(d.get("i_ch", 0), 0)
        v_ch = int_or(d.get("v_ch", 1), 1)
        r_norm = bool(d.get("r_norm", False))
        bl0 = float_or(d.get("bl_pre0"), None)
        bl1 = float_or(d.get("bl_pre1"), None)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        dsf = int_or(d.get("dsf", 1), 1)
        try:
            abf = pyabf_mod.ABF(path)
            abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(ch, abf.channelCount - 1))
            t = abf.sweepX[::dsf]
            y = abf.sweepY[::dsf].copy()
            y_unit = abf.adcUnits[ch] if ch < abf.channelCount else ""

            r_val = None
            if r_norm and abf.channelCount >= 2:
                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=i_ch)
                i_full = abf.sweepY
                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=v_ch)
                v_full = abf.sweepY
                dt = abf.sweepX[1] - abf.sweepX[0]
                r_val = _abf_estimate_r(i_full, v_full, dt)

                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(ch, abf.channelCount - 1))
                y = abf.sweepY[::dsf].copy()
                if r_val is not None:
                    y = y * (r_val * 1e3)
                    y_unit = "mV (xR)"

            y = _abf_baseline_subtract(y, t, bl0, bl1)

            if x_min is not None:
                mask = t >= x_min
                t, y = t[mask], y[mask]
            if x_max is not None:
                mask = t <= x_max
                t, y = t[mask], y[mask]

            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(t, y, color=line_color, lw=0.7)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel(y_unit)
            ch_name = abf.adcNames[ch] if ch < abf.channelCount else f"Ch{ch}"
            ax.set_title(f"{Path(path).name} · sweep {sweep} · {ch_name}", fontsize=10, color="#5C5E62")
            if r_val:
                ax.set_title(ax.get_title() + f" · R={r_val * 1e3:.1f} MOhm", fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.4)
            apply_axes_limits(ax, None, None, y_min, y_max)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig), "r_val": r_val})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/detect", methods=["POST"])
    def api_abf_detect_compat():
        if not has_abf or pyabf_mod is None:
            return err("pyabf not installed")
        if not has_scipy or find_peaks is None:
            return err("scipy not installed")
        d = request.json or {}
        path = d.get("path", "")
        sweep = int_or(d.get("sweep", 0), 0)
        ch = int_or(d.get("channel", 0), 0)
        i_ch = int_or(d.get("i_ch", 0), 0)
        v_ch = int_or(d.get("v_ch", 1), 1)
        r_norm = _as_bool(d.get("r_norm", False))
        bl0 = float_or(d.get("bl_pre0"), None)
        bl1 = float_or(d.get("bl_pre1"), None)
        t0 = float_or(d.get("t0"), None)
        t1 = float_or(d.get("t1"), None)
        use_all = _as_bool(d.get("use_all", False))
        polarity = d.get("polarity", "positive")
        height = float_or(d.get("height"), None)
        prominence = float_or(d.get("prominence"), None)
        distance_ms = float_or(d.get("distance", 2.0), 2.0)

        if height is not None and height <= 0:
            height = None
        if prominence is not None and prominence <= 0:
            prominence = None

        try:
            abf = pyabf_mod.ABF(path)
            sw = min(sweep, abf.sweepCount - 1)
            ch = min(ch, abf.channelCount - 1)
            abf.setSweep(sw, channel=ch)
            t_full = abf.sweepX.copy()
            y_full = abf.sweepY.copy()
            y_unit = abf.adcUnits[ch] if ch < abf.channelCount else ""

            r_val = None
            r_method = ""
            baseline_raw_i = np.nan

            if r_norm and abf.channelCount >= 2:
                i_ch = min(i_ch, abf.channelCount - 1)
                v_ch = min(v_ch, abf.channelCount - 1)
                abf.setSweep(sw, channel=i_ch)
                i_full = abf.sweepY.copy()
                abf.setSweep(sw, channel=v_ch)
                v_full = abf.sweepY.copy()
                dt_full = abf.sweepX[1] - abf.sweepX[0] if len(abf.sweepX) > 1 else 1e-4
                r_val = _abf_estimate_r(i_full, v_full, dt_full)
                r_method = "dV/dI edge" if r_val is not None else "unavailable"

                i_norm, baseline_raw_i = _abf_baseline_apply(
                    i_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
                )
                if ch == i_ch:
                    y_full = i_norm
                    if r_val is not None:
                        y_full = y_full * (r_val * 1e3)
                        y_unit = "I_norm (pA·MΩ)"
                    else:
                        y_unit = "I_baseline_sub (pA)"
                else:
                    y_full, _ = _abf_baseline_apply(
                        y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=False
                    )
            else:
                y_full, baseline_raw_i = _abf_baseline_apply(
                    y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
                )

            if use_all:
                t_use = t_full
                y_use = y_full
                idx_base = np.arange(len(t_full))
                t0_plot = float(t_full[0]) if len(t_full) else 0.0
                t1_plot = float(t_full[-1]) if len(t_full) else 0.0
            else:
                if t0 is None or t1 is None:
                    return err("Set analysis window t0/t1 or enable use_all")
                if t1 < t0:
                    t0, t1 = t1, t0
                m = (t_full >= t0) & (t_full <= t1)
                if not np.any(m):
                    return err("Selected time window has no points")
                idx_base = np.where(m)[0]
                t_use = t_full[m]
                y_use = y_full[m]
                t0_plot = float(t0)
                t1_plot = float(t1)

            if len(t_use) < 3:
                return err("Not enough points in selected window")

            dt = max(1e-9, t_use[1] - t_use[0])
            dist_pts = max(1, int(distance_ms / 1000 / dt))
            kw = {"distance": dist_pts}
            if height is not None:
                kw["height"] = height
            if prominence is not None:
                kw["prominence"] = prominence

            pol = str(polarity or "positive").strip().lower()
            if pol in {"neg", "negative"}:
                idx_local, props = find_peaks(-y_use, **kw)
                amps = y_use[idx_local]
                pol_out = "NEG"
            elif pol == "abs_max":
                idx_local, props = find_peaks(np.abs(y_use), **kw)
                amps = np.abs(y_use[idx_local])
                pol_out = "ABS"
            else:
                idx_local, props = find_peaks(y_use, **kw)
                amps = y_use[idx_local]
                pol_out = "POS"

            peaks = []
            for i, p_local in enumerate(idx_local):
                gi = int(idx_base[int(p_local)])
                peaks.append(
                    {
                        "idx": gi,
                        "global_index": gi,
                        "time": float(t_full[gi]),
                        "amplitude": float(amps[i]),
                        "width": float(props.get("widths", [np.nan] * len(idx_local))[i]) if "widths" in props else None,
                        "prominence": (
                            float(props.get("prominences", [np.nan] * len(idx_local))[i])
                            if "prominences" in props
                            else None
                        ),
                        "polarity": pol_out,
                    }
                )

            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(t_full, y_full, color=line_color, lw=0.7)

            if not use_all:
                ax.axvspan(t0_plot, t1_plot, alpha=0.12, color="gray")
                ax.axvline(t0_plot, ls="--", lw=0.8, color="gray")
                ax.axvline(t1_plot, ls="--", lw=0.8, color="gray")

            if peaks:
                p_idx = np.array([int(p["idx"]) for p in peaks], dtype=int)
                ax.scatter(t_full[p_idx], y_full[p_idx], color="#d62728", marker="^", s=22, zorder=5)

            ax.set_xlabel("Time (s)")
            ax.set_ylabel(y_unit)
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "peaks": peaks,
                    "window": [t0_plot, t1_plot],
                    "meta": {
                        "polarity": pol_out,
                        "R_MOhm": (float(r_val * 1e3) if r_val is not None else None),
                        "R_method": r_method,
                        "baseline_raw_i_pA": float(baseline_raw_i),
                        "y_unit": y_unit,
                    },
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks", methods=["GET", "POST"])
    def api_abf_export_peaks_compat():
        if not has_abf or pyabf_mod is None:
            return err("pyabf not installed")
        if request.method == "GET":
            path = request.args.get("path", "")
            mode = request.args.get("mode", "download")
            try:
                abf = pyabf_mod.ABF(path)
                abf.setSweep(0, channel=0)
                buf = io.BytesIO()
                pd.DataFrame({"time_s": abf.sweepX, "value": abf.sweepY}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    src = Path(path)
                    out_path = src.with_name(f"{src.stem}_trace.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={Path(path).stem}_trace.csv"},
                )
            except Exception:
                return err(traceback.format_exc())

        d = request.json or {}
        path = d.get("path", "")
        mode = d.get("mode", "download")
        peaks = d.get("peaks", [])
        if not peaks:
            return err("No peaks selected")

        sweep = int_or(d.get("sweep", 0), 0)
        ch = int_or(d.get("channel", 0), 0)
        i_ch = int_or(d.get("i_ch", 0), 0)
        v_ch = int_or(d.get("v_ch", 1), 1)
        r_norm = _as_bool(d.get("r_norm", False))
        bl0 = float_or(d.get("bl_pre0"), None)
        bl1 = float_or(d.get("bl_pre1"), None)
        export_window_ms = float_or(d.get("export_window_ms"), 50.0)
        if export_window_ms is None or export_window_ms <= 0:
            export_window_ms = 50.0

        polarity = str(d.get("polarity", "POS")).upper()
        window = d.get("window", [])
        win_t0 = float_or(window[0], np.nan) if isinstance(window, list) and len(window) > 0 else np.nan
        win_t1 = float_or(window[1], np.nan) if isinstance(window, list) and len(window) > 1 else np.nan

        try:
            src = Path(path)
            abf = pyabf_mod.ABF(path)
            sw = min(sweep, abf.sweepCount - 1)
            ch = min(ch, abf.channelCount - 1)

            abf.setSweep(sw, channel=ch)
            t_full = abf.sweepX.copy()
            y_full = abf.sweepY.copy()

            r_val = None
            r_method = ""
            baseline_raw_i = np.nan

            if r_norm and abf.channelCount >= 2:
                i_ch = min(i_ch, abf.channelCount - 1)
                v_ch = min(v_ch, abf.channelCount - 1)
                abf.setSweep(sw, channel=i_ch)
                i_full = abf.sweepY.copy()
                abf.setSweep(sw, channel=v_ch)
                v_full = abf.sweepY.copy()
                dt_full = abf.sweepX[1] - abf.sweepX[0] if len(abf.sweepX) > 1 else 1e-4
                r_val = _abf_estimate_r(i_full, v_full, dt_full)
                r_method = "dV/dI edge" if r_val is not None else "unavailable"

                i_norm, baseline_raw_i = _abf_baseline_apply(
                    i_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
                )
                if ch == i_ch:
                    y_full = i_norm
                    if r_val is not None:
                        y_full = y_full * (r_val * 1e3)
                else:
                    y_full, _ = _abf_baseline_apply(
                        y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=False
                    )
            else:
                y_full, baseline_raw_i = _abf_baseline_apply(
                    y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
                )

            out_folder = src.parent / src.stem
            out_folder.mkdir(parents=True, exist_ok=True)

            rows = []
            selected = []
            for export_idx, p in enumerate(peaks, start=1):
                gi = int_or(p.get("idx", p.get("global_index")), -1)
                if gi < 0 or gi >= len(t_full):
                    tp = float_or(p.get("time", p.get("t")), None)
                    if tp is None:
                        continue
                    gi = int(np.argmin(np.abs(t_full - tp)))

                selected.append((export_idx, gi))
                rows.append(
                    {
                        "export_index": export_idx,
                        "global_index": gi,
                        "t_s": float(t_full[gi]),
                        "y_norm": float(y_full[gi]),
                        "polarity": polarity,
                        "R_MOhm": (float(r_val * 1e3) if r_val is not None else np.nan),
                        "R_method": r_method,
                        "baseline_raw_i_pA": float(baseline_raw_i),
                        "window_start_s": win_t0,
                        "window_end_s": win_t1,
                    }
                )

            summary_path = out_folder / f"{src.stem}_peaks_summary.csv"
            pd.DataFrame(rows).to_csv(summary_path, index=False, encoding="utf-8-sig")

            half = float(export_window_ms) / 1000.0
            saved = 0
            for export_idx, gi in selected:
                tp = float(t_full[gi])
                mask = (t_full >= tp - half) & (t_full <= tp + half)
                if not np.any(mask):
                    continue
                seg_path = out_folder / f"{src.stem}_peak_{export_idx:03d}.csv"
                pd.DataFrame({"time_s": t_full[mask], "I_norm": y_full[mask]}).to_csv(
                    seg_path, index=False
                )
                saved += 1

            if _mode_is_save(mode):
                return jsonify(
                    {
                        "ok": True,
                        "saved_path": str(out_folder),
                        "summary_path": str(summary_path),
                        "saved_count": saved,
                    }
                )

            payload = summary_path.read_bytes()
            return Response(
                payload,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename={src.stem}_peaks_summary.csv"
                },
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks_job", methods=["POST"])
    def api_abf_export_peaks_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/abf/export_peaks",
            "abf.export_peaks",
            "Export ABF peaks",
            api_abf_export_peaks_compat,
            request.json or {},
        )

    @app.route("/api/abf/export", methods=["GET", "POST"])
    def api_abf_export():
        if not has_abf or pyabf_mod is None:
            return err("pyabf not installed")
        d = request_data()
        path = d.get("path", "")
        fmt = d.get("fmt", "png")
        mode = d.get("mode", "download")
        sweep = int_or(d.get("sweep", 0), 0)
        ch = int_or(d.get("channel", 0), 0)
        i_ch = int_or(d.get("i_ch", 0), 0)
        v_ch = int_or(d.get("v_ch", 1), 1)
        r_norm = _as_bool(d.get("r_norm", False))
        bl0 = float_or(d.get("bl_pre0"), None)
        bl1 = float_or(d.get("bl_pre1"), None)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        dsf = max(1, int_or(d.get("dsf", 1), 1))
        signal_only = _as_bool(d.get("signal_only", False))
        try:
            src = Path(path)
            abf = pyabf_mod.ABF(path)
            abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(ch, abf.channelCount - 1))
            t = abf.sweepX[::dsf]
            y = abf.sweepY.copy()
            y = y[::dsf]
            y_unit = abf.adcUnits[ch] if ch < abf.channelCount else ""

            if r_norm and abf.channelCount >= 2:
                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(i_ch, abf.channelCount - 1))
                i_full = abf.sweepY
                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(v_ch, abf.channelCount - 1))
                v_full = abf.sweepY
                dt_full = abf.sweepX[1] - abf.sweepX[0]
                r_val = _abf_estimate_r(i_full, v_full, dt_full)

                abf.setSweep(min(sweep, abf.sweepCount - 1), channel=min(ch, abf.channelCount - 1))
                y = abf.sweepY[::dsf].copy()
                if r_val is not None:
                    y = y * (r_val * 1e3)
                    y_unit = "mV (xR)"

            y = _abf_baseline_subtract(y, t, bl0, bl1)

            if x_min is not None:
                mask = t >= x_min
                t, y = t[mask], y[mask]
            if x_max is not None:
                mask = t <= x_max
                t, y = t[mask], y[mask]

            if fmt == "csv":
                buf = io.BytesIO()
                pd.DataFrame({"time_s": t, "value": y}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    out_path = src.with_name(f"{src.stem}_s{sweep}_ch{ch}.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=abf_export.csv"},
                )
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(t, y, color=line_color, lw=0.7)
            if signal_only and str(fmt).lower() == "svg":
                if len(t):
                    ax.set_xlim(float(t[0]), float(t[-1]))
                if y_min is not None or y_max is not None:
                    cur = ax.get_ylim()
                    ax.set_ylim(y_min if y_min is not None else cur[0], y_max if y_max is not None else cur[1])
                ax.set_position([0, 0, 1, 1])
                ax.set_title("")
                ax.set_xlabel("")
                ax.set_ylabel("")
                ax.set_xticks([])
                ax.set_yticks([])
                for sp in ax.spines.values():
                    sp.set_visible(False)
                ax.set_frame_on(False)
                ax.axis("off")
            else:
                ax.set_xlabel("Time (s)")
                ax.set_ylabel(y_unit)
                apply_axes_limits(ax, None, None, y_min, y_max)
                ax.grid(True, alpha=0.4)
            fig.tight_layout()
            buf = io.BytesIO()
            save_kw = {"format": fmt, "bbox_inches": "tight"}
            if fmt == "png":
                save_kw["dpi"] = 300
            if signal_only and str(fmt).lower() == "svg":
                save_kw["pad_inches"] = 0
                save_kw["transparent"] = True
                save_kw["facecolor"] = "none"
            fig.savefig(buf, **save_kw)
            plt.close(fig)
            buf.seek(0)
            payload = buf.getvalue()
            if _mode_is_save(mode):
                if signal_only and str(fmt).lower() == "svg":
                    out_path = src.with_name(f"{src.stem}_preview_signal.svg")
                else:
                    out_path = src.with_name(f"{src.stem}_s{sweep}_ch{ch}.{fmt}")
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            mt = "image/png" if fmt == "png" else "image/svg+xml"
            return Response(
                payload,
                mimetype=mt,
                headers={"Content-Disposition": f"attachment; filename=abf_export.{fmt}"},
            )
        except Exception as e:
            return err(e)

    @app.route("/api/abf/export_job", methods=["POST"])
    def api_abf_export_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/abf/export",
            "abf.export",
            "Export ABF trace",
            api_abf_export,
            request.json or {},
        )
