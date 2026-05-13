import io
import traceback
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import signal
from flask import Response, jsonify, request

from services import rhd as rhd_service

from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .response import api_ok


def register_rhd_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    browse_files_recursive = ctx["browse_files_recursive"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    has_rhd = ctx["HAS_RHD"]
    rhd = ctx.get("rhd")
    request_data = ctx["request_data"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _load_rhd_with_merge_option(path, do_merge):
        return rhd_service.load_with_merge_option(path, rhd, do_merge)

    def _load_rhd_metadata_with_merge_option(path, do_merge):
        return rhd_service.recording_metadata_with_merge_option(path, rhd, do_merge)

    def _load_rhd_channel_with_merge_option(path, channel, do_merge):
        return rhd_service.load_channel_with_merge_option(path, rhd, channel, do_merge)

    def _df_all_channels_wide(time_s, ch_names, amp):
        return rhd_service.all_channels_wide_frame(time_s, ch_names, amp)

    def _rhd_output(path: str | Path, role: str = "rhd_export") -> dict:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else "csv",
            "role": role,
        }

    def _rhd_recording_key(path: Path, do_merge: bool) -> tuple[str, ...]:
        if not do_merge:
            return (str(path),)
        return tuple(str(p) for p in rhd_service.recording_files_for_path(path, True))

    def _rhd_downsample_factor(value, n_points: int) -> int:
        raw = str(value if value is not None else "auto").strip().lower()
        if raw in {"", "auto", "adaptive"}:
            return max(1, int((max(0, int(n_points)) + 49999) // 50000))
        return max(1, min(5000, int_or(value, 1)))

    def _rhd_apply_time_window(t, y, x_min, x_max):
        if x_min is not None:
            mask = t >= x_min
            t, y = t[mask], y[mask]
        if x_max is not None:
            mask = t <= x_max
            t, y = t[mask], y[mask]
        return t, y

    def _rhd_filter_params(d: dict) -> dict:
        return {
            "type": str(d.get("filter_type", d.get("filter", "none")) or "none").strip().lower(),
            "low_hz": float_or(d.get("filter_low_hz"), None),
            "high_hz": float_or(d.get("filter_high_hz"), None),
            "notch_hz": float_or(d.get("filter_notch_hz"), 60.0),
            "notch_q": max(1.0, float_or(d.get("filter_notch_q"), 30.0) or 30.0),
            "order": max(1, min(12, int_or(d.get("filter_order"), 4))),
        }

    def _rhd_apply_filter(y, fs: float, params: dict):
        kind = str(params.get("type") or "none").lower()
        y = np.asarray(y, dtype=float)
        if kind in {"", "none", "off"} or y.size < 8 or fs <= 0:
            return y

        nyq = fs / 2.0
        try:
            if kind == "notch":
                f0 = float(params.get("notch_hz") or 0.0)
                if not (0 < f0 < nyq):
                    return y
                b, a = signal.iirnotch(f0, float(params.get("notch_q") or 30.0), fs=fs)
                return signal.filtfilt(b, a, y)

            order = int(params.get("order") or 4)
            low = params.get("low_hz")
            high = params.get("high_hz")
            if kind == "highpass":
                if low is None or not (0 < low < nyq):
                    return y
                sos = signal.butter(order, low, btype="highpass", fs=fs, output="sos")
            elif kind == "lowpass":
                if high is None or not (0 < high < nyq):
                    return y
                sos = signal.butter(order, high, btype="lowpass", fs=fs, output="sos")
            elif kind in {"bandpass", "iir", "iir_bandpass"}:
                if low is None or high is None or not (0 < low < high < nyq):
                    return y
                sos = signal.butter(order, [low, high], btype="bandpass", fs=fs, output="sos")
            else:
                return y
            return signal.sosfiltfilt(sos, y)
        except ValueError:
            return y

    def _rhd_finish_axis(ax, t, y_min, y_max, grid: bool = True):
        if len(t):
            x0 = float(t[0])
            x1 = float(t[-1])
            if x1 == x0:
                x1 = x0 + 1e-9
            ax.set_xlim(x0, x1)
        ax.margins(x=0)
        apply_axes_limits(ax, None, None, y_min, y_max)
        if grid:
            ax.grid(True, alpha=0.4)
        else:
            ax.grid(False)

    def _rhd_process_trace(t, y, fs: float, d: dict) -> tuple[plt.Figure, dict]:
        mode = str(d.get("process_type") or "envelope").strip().lower()
        t = np.asarray(t, dtype=float)
        y = np.asarray(y, dtype=float)
        fig, ax = plt.subplots(figsize=(10, 3.5))
        meta = {"process_type": mode}
        if t.size == 0 or y.size == 0:
            ax.text(0.5, 0.5, "No data in current window", ha="center", va="center")
            ax.axis("off")
            fig.tight_layout()
            return fig, meta

        if mode == "envelope":
            processed = np.abs(signal.hilbert(y - np.nanmean(y)))
            dsf = _rhd_downsample_factor(d.get("downsample", "auto"), len(t))
            ax.plot(t[::dsf], processed[::dsf], color=line_color, lw=0.8)
            ax.set_ylabel("Envelope (uV)")
            _rhd_finish_axis(ax, t, None, None)
            meta["points"] = int(len(processed[::dsf]))
        elif mode == "smooth":
            win_ms = max(0.01, float_or(d.get("smooth_ms"), 5.0) or 5.0)
            win = max(1, int(round(fs * win_ms / 1000.0))) if fs > 0 else 1
            kernel = np.ones(win, dtype=float) / win
            processed = np.convolve(y, kernel, mode="same")
            dsf = _rhd_downsample_factor(d.get("downsample", "auto"), len(t))
            ax.plot(t[::dsf], processed[::dsf], color=line_color, lw=0.8)
            ax.set_ylabel("Smoothed (uV)")
            _rhd_finish_axis(ax, t, None, None)
            meta.update({"smooth_ms": win_ms, "points": int(len(processed[::dsf]))})
        elif mode == "fitting":
            degree = max(1, min(5, int_or(d.get("fit_degree"), 1)))
            t0 = t - float(t[0])
            coeff = np.polyfit(t0, y, degree)
            fitted = np.polyval(coeff, t0)
            dsf = _rhd_downsample_factor(d.get("downsample", "auto"), len(t))
            ax.plot(t[::dsf], y[::dsf], color="#A5AFBF", lw=0.5)
            ax.plot(t[::dsf], fitted[::dsf], color=line_color, lw=1.1)
            ax.set_ylabel("Fit (uV)")
            _rhd_finish_axis(ax, t, None, None)
            meta.update({"fit_degree": degree, "coefficients": [float(c) for c in coeff]})
        elif mode == "fft":
            dt = 1.0 / fs if fs > 0 else float(np.median(np.diff(t))) if t.size > 1 else 1.0
            centered = y - np.nanmean(y)
            window = np.hanning(centered.size) if centered.size > 1 else np.ones_like(centered)
            freq = np.fft.rfftfreq(centered.size, d=dt)
            amp = np.abs(np.fft.rfft(centered * window))
            scale = np.sum(window) if np.sum(window) else centered.size
            amp = 2.0 * amp / scale
            ax.plot(freq, amp, color=line_color, lw=0.8)
            ax.set_xlabel("Frequency (Hz)")
            ax.set_ylabel("Amplitude (uV)")
            ax.margins(x=0)
            ax.grid(True, alpha=0.4)
            meta.update({"points": int(freq.size), "frequency_max": float(freq[-1]) if freq.size else 0.0})
        elif mode == "stft":
            win_ms = max(1.0, float_or(d.get("stft_ms"), 100.0) or 100.0)
            nperseg = max(16, int(round(fs * win_ms / 1000.0))) if fs > 0 else 256
            nperseg = min(nperseg, y.size)
            noverlap = max(0, min(nperseg - 1, nperseg // 2))
            freq, tt, zxx = signal.stft(y - np.nanmean(y), fs=fs if fs > 0 else 1.0, nperseg=nperseg, noverlap=noverlap)
            mag = np.abs(zxx)
            im = ax.pcolormesh((tt + t[0]), freq, mag, shading="auto", cmap="viridis")
            fig.colorbar(im, ax=ax, label="Magnitude")
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Frequency (Hz)")
            if tt.size:
                ax.set_xlim(float(t[0]), float(t[0] + tt[-1]))
            meta.update({"stft_ms": win_ms, "frequency_bins": int(freq.size), "time_bins": int(tt.size)})
        else:
            dsf = _rhd_downsample_factor(d.get("downsample", "auto"), len(t))
            ax.plot(t[::dsf], y[::dsf], color=line_color, lw=0.8)
            ax.set_ylabel("Amplitude (uV)")
            _rhd_finish_axis(ax, t, None, None)
            meta["process_type"] = "trace"

        if mode not in {"fft", "stft"}:
            ax.set_xlabel("Time (s)")
        fig.tight_layout()
        return fig, meta

    def _rhd_export_all_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("Intan RHD parser is not available")

        path = d.get("path", "")
        mode = d.get("mode", "download")
        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)
        src = Path(path)
        if not src.is_file():
            raise ValueError(f"RHD file not found: {path}")
        t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(src, do_merge)

        if _mode_is_save(mode):
            base_dir = src.parent
            if wide_csv:
                out_path = base_dir / f"{base_stem}.csv"
                dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                dfw.to_csv(out_path, index=False, sep="\t")
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "saved_paths": [str(out_path)],
                        "outputs": [_rhd_output(out_path, "rhd_all_channels_wide")],
                    },
                }

            target_dir = base_dir / base_stem
            target_dir.mkdir(parents=True, exist_ok=True)
            saved = 0
            saved_paths = []
            for i, name in enumerate(ch_all):
                out_path = target_dir / f"{base_stem}_{name}.csv"
                pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(out_path, index=False)
                saved += 1
                saved_paths.append(str(out_path))
            outputs = [_rhd_output(target_dir, "rhd_channel_folder")]
            outputs.extend(_rhd_output(path, "rhd_channel_csv") for path in saved_paths)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(target_dir),
                    "saved_count": saved,
                    "saved_paths": saved_paths,
                    "outputs": outputs,
                },
            }

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, name in enumerate(ch_all):
                csv_bytes = pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(
                    index=False
                ).encode("utf-8")
                zf.writestr(f"{base_stem}_{name}.csv", csv_bytes)
        out.seek(0)
        return {
            "kind": "download",
            "payload": out.getvalue(),
            "mimetype": "application/zip",
            "download_name": f"{base_stem}_all_channels.zip",
        }

    def _rhd_export_all_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting all RHD channels")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _rhd_export_all_payload(save_body)["data"]

    def _rhd_export_queue_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("Intan RHD parser is not available")

        paths = d.get("paths", [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("Queue is empty.")

        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)

        total = 0
        ok = 0
        warnings = []
        saved_paths = []
        processed_recordings = set()

        for raw in paths:
            total += 1
            p = Path(str(raw))
            try:
                recording_key = _rhd_recording_key(p, do_merge)
                if do_merge and recording_key in processed_recordings:
                    continue

                t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(p, do_merge)

                base_dir = p.parent
                if wide_csv:
                    out_path = base_dir / f"{base_stem}.csv"
                    dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                    dfw.to_csv(out_path, index=False, sep="\t")
                    saved_paths.append(str(out_path))
                else:
                    target_dir = base_dir / base_stem
                    target_dir.mkdir(parents=True, exist_ok=True)
                    for i, name in enumerate(ch_all):
                        out_path = target_dir / f"{base_stem}_{name}.csv"
                        pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(
                            out_path, index=False
                        )
                    saved_paths.append(str(target_dir))

                if do_merge:
                    processed_recordings.add(recording_key)
                ok += 1
            except Exception as e:
                warnings.append(f"{p}: {e}")

        return {
            "ok": True,
            "saved_count": ok,
            "total": total,
            "saved_paths": saved_paths,
            "warnings": warnings,
            "outputs": [_rhd_output(path, "rhd_queue_export") for path in saved_paths],
        }

    def _rhd_export_queue_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting RHD queue")
        return _rhd_export_queue_payload(body)

    @app.route("/api/rhd/browse", methods=["POST"])
    def api_rhd_browse():
        d = request.json or {}
        files = browse_files(d.get("folder", ""), {".rhd"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/rhd/browse_recursive", methods=["POST"])
    def api_rhd_browse_recursive():
        d = request.json or {}
        files = browse_files_recursive(d.get("folder", ""), {".rhd"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/rhd/load", methods=["POST"])
    def api_rhd_load():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        path = d.get("path", "")
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        try:
            return jsonify(_load_rhd_metadata_with_merge_option(Path(path), do_merge))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/plot", methods=["POST"])
    def api_rhd_plot():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        path = d.get("path", "")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        downsample = d.get("downsample", d.get("dsf", "auto"))
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = _rhd_filter_params(d)

        try:
            t, fs, _ch_names, y, ch, ch_label, base_stem, used_pair, _segment_count = (
                _load_rhd_channel_with_merge_option(Path(path), ch_in, do_merge)
            )
            t, y = _rhd_apply_time_window(t, y, x_min, x_max)
            y = _rhd_apply_filter(y, fs, filter_params)

            dsf = _rhd_downsample_factor(downsample, len(t))
            t_d = t[::dsf]
            y_d = y[::dsf]

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t_d, y_d, color=line_color, lw=0.6)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            title_name = f"{base_stem} (merged)" if used_pair else Path(path).name
            ax.set_title(
                f"{title_name} - Ch {ch}: {ch_label}",
                fontsize=10,
                color="#5C5E62",
            )
            _rhd_finish_axis(ax, t_d, y_min, y_max)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig), "downsample": dsf, "plotted_points": int(len(t_d))})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/process", methods=["POST"])
    def api_rhd_process():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        path = d.get("path", "")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = _rhd_filter_params(d)

        try:
            t, fs, _ch_names, y, _ch, _ch_label, _base_stem, _used_pair, _segment_count = (
                _load_rhd_channel_with_merge_option(Path(path), ch_in, do_merge)
            )
            t, y = _rhd_apply_time_window(t, y, x_min, x_max)
            y = _rhd_apply_filter(y, fs, filter_params)
            fig, meta = _rhd_process_trace(t, y, fs, d)
            return jsonify({"img": fig_to_b64(fig), **meta})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_channel", methods=["GET", "POST"])
    def api_rhd_export_channel():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request_data()
        path = d.get("path", "")
        fmt = d.get("fmt", "csv")
        mode = d.get("mode", "download")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        downsample = d.get("downsample", d.get("dsf", "auto"))
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = _rhd_filter_params(d)

        try:
            src = Path(path)
            t, fs, _ch_names, y, _ch, ch_name, base_stem, used_pair, _segment_count = (
                _load_rhd_channel_with_merge_option(src, ch_in, do_merge)
            )
            out_stem = base_stem if used_pair else src.stem

            if fmt == "csv":
                buf = io.BytesIO()
                pd.DataFrame({"time_s": t, "value_uV": y}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    out_path = src.with_name(f"{out_stem}_{ch_name}.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.csv"},
                )

            t_view, y_view = _rhd_apply_time_window(t, y, x_min, x_max)
            y_view = _rhd_apply_filter(y_view, fs, filter_params)
            dsf = _rhd_downsample_factor(downsample, len(t_view))
            t_view = t_view[::dsf]
            y_view = y_view[::dsf]

            if str(fmt).lower() == "svg":
                payload = clean_trace_svg(t_view, y_view, y_min=y_min, y_max=y_max, line_color=line_color)
                if _mode_is_save(mode):
                    base_path = src.with_name(f"{out_stem}_{ch_name}.svg")
                    out_path = next_numbered_path(base_path)
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="image/svg+xml",
                    headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.svg"},
                )

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t_view, y_view, color=line_color, lw=0.6)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            _rhd_finish_axis(ax, t_view, y_min, y_max)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, dpi=300 if fmt == "png" else None, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            payload = buf.getvalue()
            if _mode_is_save(mode):
                out_path = next_numbered_path(src.with_name(f"{out_stem}_{ch_name}.{fmt}"))
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            mt = "image/png" if fmt == "png" else "image/svg+xml"
            return Response(
                payload,
                mimetype=mt,
                headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.{fmt}"},
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_all", methods=["POST"])
    def api_rhd_export_all():
        d = request.json or {}
        try:
            result = _rhd_export_all_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"], warnings=data.get("warnings"))
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_all_job", methods=["POST"])
    def api_rhd_export_all_job():
        return submit_json_task(
            jobs,
            "rhd.export_all",
            "Export all RHD channels",
            _rhd_export_all_task,
            request.json or {},
            metadata={"endpoint": "/api/rhd/export_all"},
        )

    @app.route("/api/rhd/export_queue", methods=["POST"])
    def api_rhd_export_queue():
        d = request.json or {}
        try:
            result = _rhd_export_queue_payload(d)
            return api_ok(result, outputs=result["outputs"], warnings=result.get("warnings"))
        except ValueError as exc:
            return err(str(exc))

    @app.route("/api/rhd/export_queue_job", methods=["POST"])
    def api_rhd_export_queue_job():
        return submit_json_task(
            jobs,
            "rhd.export_queue",
            "Export RHD queue",
            _rhd_export_queue_task,
            request.json or {},
            metadata={"endpoint": "/api/rhd/export_queue"},
        )
