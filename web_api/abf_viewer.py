import io
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify, request

from services import abf as abf_service

from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .response import api_ok


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

    def _abf_baseline_apply(y, t, pre0_ms=None, pre1_ms=None, use_default=False):
        """Return baseline-subtracted signal and baseline value."""
        return abf_service.baseline_apply(y, t, pre0_ms, pre1_ms, use_default)

    def _abf_baseline_subtract(y, t, pre0_ms, pre1_ms):
        """Subtract mean of the [pre0_ms, pre1_ms] baseline window."""
        return abf_service.baseline_subtract(y, t, pre0_ms, pre1_ms)

    def _abf_estimate_r(i_trace, v_trace, dt):
        """Estimate resistance from V-step edges via dV/dI."""
        return abf_service.estimate_resistance(i_trace, v_trace, dt)

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
            ax.margins(x=0)
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

            try:
                peaks, window_bounds = abf_service.detect_peaks(
                    t_full,
                    y_full,
                    t0,
                    t1,
                    use_all,
                    polarity,
                    distance_ms,
                    find_peaks,
                    height=height,
                    prominence=prominence,
                )
            except ValueError as exc:
                return err(str(exc))
            t0_plot, t1_plot = window_bounds
            pol_out = peaks[0]["polarity"] if peaks else str(polarity or "positive").upper()

            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(t_full, y_full, color=line_color, lw=0.7)
            ax.margins(x=0)

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

    def _abf_output(path: str | Path, role: str) -> dict:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else (p.suffix.lower().lstrip(".") or "file"),
            "role": role,
        }

    def _abf_export_peaks_payload(d: dict) -> dict:
        if not has_abf or pyabf_mod is None:
            raise ValueError("pyabf not installed")

        path = d.get("path", "")
        mode = d.get("mode", "download")
        peaks = d.get("peaks", [])
        if not peaks:
            raise ValueError("No peaks selected")

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
        segment_paths = []
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
            segment_paths.append(str(seg_path))

        if _mode_is_save(mode):
            outputs = [_abf_output(out_folder, "abf_peak_folder")]
            outputs.append(_abf_output(summary_path, "abf_peak_summary"))
            outputs.extend(_abf_output(path, "abf_peak_segment") for path in segment_paths)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_folder),
                    "summary_path": str(summary_path),
                    "saved_count": saved,
                    "saved_paths": [str(summary_path)] + segment_paths,
                    "outputs": outputs,
                },
            }

        return {
            "kind": "download",
            "payload": summary_path.read_bytes(),
            "mimetype": "text/csv",
            "download_name": f"{src.stem}_peaks_summary.csv",
        }

    def _abf_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _abf_export_peaks_payload(save_body)["data"]

    def _abf_export_payload(d: dict) -> dict:
        if not has_abf or pyabf_mod is None:
            raise ValueError("pyabf not installed")
        path = d.get("path", "")
        fmt = str(d.get("fmt", "png") or "png").lower()
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
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "outputs": [_abf_output(out_path, "abf_trace_csv")],
                    },
                }
            return {
                "kind": "download",
                "payload": payload,
                "mimetype": "text/csv",
                "download_name": "abf_export.csv",
            }

        if fmt == "svg":
            payload = clean_trace_svg(t, y, y_min=y_min, y_max=y_max, line_color=line_color)
            base_name = f"{src.stem}_preview_signal.svg" if signal_only else f"{src.stem}_s{sweep}_ch{ch}.svg"
            if _mode_is_save(mode):
                base_path = src.with_name(base_name)
                out_path = next_numbered_path(base_path)
                out_path.write_bytes(payload)
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "outputs": [_abf_output(out_path, "abf_trace_export")],
                    },
                }
            return {
                "kind": "download",
                "payload": payload,
                "mimetype": "image/svg+xml",
                "download_name": base_name,
            }

        fig, ax = plt.subplots(figsize=(9, 4))
        ax.plot(t, y, color=line_color, lw=0.7)
        ax.margins(x=0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(y_unit)
        apply_axes_limits(ax, None, None, y_min, y_max)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        buf = io.BytesIO()
        save_kw = {"format": fmt, "bbox_inches": "tight"}
        if fmt == "png":
            save_kw["dpi"] = 300
        fig.savefig(buf, **save_kw)
        plt.close(fig)
        buf.seek(0)
        payload = buf.getvalue()
        if _mode_is_save(mode):
            out_path = next_numbered_path(src.with_name(f"{src.stem}_s{sweep}_ch{ch}.{fmt}"))
            out_path.write_bytes(payload)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_path),
                    "outputs": [_abf_output(out_path, "abf_trace_export")],
                },
            }
        return {
            "kind": "download",
            "payload": payload,
            "mimetype": "image/png" if fmt == "png" else "image/svg+xml",
            "download_name": f"abf_export.{fmt}",
        }

    def _abf_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF trace")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _abf_export_payload(save_body)["data"]

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
        try:
            result = _abf_export_peaks_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks_job", methods=["POST"])
    def api_abf_export_peaks_job():
        return submit_json_task(
            jobs,
            "abf.export_peaks",
            "Export ABF peaks",
            _abf_export_peaks_task,
            request.json or {},
            metadata={"endpoint": "/api/abf/export_peaks"},
        )

    @app.route("/api/abf/export", methods=["GET", "POST"])
    def api_abf_export():
        d = request_data()
        try:
            result = _abf_export_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception as e:
            return err(e)

    @app.route("/api/abf/export_job", methods=["POST"])
    def api_abf_export_job():
        return submit_json_task(
            jobs,
            "abf.export",
            "Export ABF trace",
            _abf_export_task,
            request.json or {},
            metadata={"endpoint": "/api/abf/export"},
        )
