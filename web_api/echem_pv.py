import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify, request

from services import echem as echem_service
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .response import api_ok


def register_echem_pv_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    line_color = ctx["LINE_COLOR"]
    jobs = ctx.get("jobs")
    has_scipy = ctx["HAS_SCIPY"]
    find_peaks = ctx.get("find_peaks")
    peak_widths = ctx.get("peak_widths")
    savgol_filter = ctx.get("savgol_filter")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _normalize_method(method):
        return echem_service.normalize_baseline_method(method)

    def _load_echem(path):
        """Load time/voltage data from a .txt or .csv echem file."""
        return echem_service.load_photovoltage(path)

    def _detrend_signal(t, v, method="median", window_ms=50.0, sg_window_ms=51.0, sg_poly=3):
        return echem_service.detrend_signal(
            t,
            v,
            method=method,
            window_ms=window_ms,
            sg_window_ms=sg_window_ms,
            sg_poly=sg_poly,
            savgol_filter_func=savgol_filter,
        )

    def _detect_positive_pulses_in_window(t, e_det, t0, t1, peak_min_v, min_width_ms, min_spacing_ms):
        return echem_service.detect_positive_pulses(
            t,
            e_det,
            t0,
            t1,
            peak_min_v,
            min_width_ms,
            min_spacing_ms,
            find_peaks,
            peak_widths,
        )

    def _detect_negative_pulses_in_window(t, e_det, t0, t1, peak_min_v, min_width_ms, min_spacing_ms):
        return echem_service.detect_negative_pulses(
            t,
            e_det,
            t0,
            t1,
            peak_min_v,
            min_width_ms,
            min_spacing_ms,
            find_peaks,
            peak_widths,
        )

    def _pv_outputs(output_folder: Path, summary_path: Path, saved_paths: list[str]) -> list[dict]:
        outputs = [{"path": str(output_folder), "type": "directory", "role": "photovoltage_pulse_folder"}]
        outputs.append({"path": str(summary_path), "type": "csv", "role": "photovoltage_pulse_summary"})
        outputs.extend(
            {"path": path, "type": "csv", "role": "photovoltage_pulse_window"}
            for path in saved_paths
            if path != str(summary_path)
        )
        return outputs

    def _echem_pv_export_payload(d: dict) -> dict:
        pulses = d.get("pulses", [])
        path = d.get("path", "")
        mode = d.get("mode", "download")

        if not pulses:
            raise ValueError("No pulses to export")

        stem = Path(path).stem if path else "pulses"
        if _mode_is_save(mode):
            if not path:
                raise ValueError("Missing source file path")

            src = Path(path)
            t, v, _t_col, _v_col = _load_echem(path)
            if len(t) == 0:
                raise ValueError("No data points found in file")

            params = d.get("params", {}) if isinstance(d.get("params", {}), dict) else {}
            baseline_method = _normalize_method(
                d.get("baseline_method", params.get("baseline_method", d.get("detrend_method", "median")))
            )
            baseline_win_ms = float_or(
                d.get("baseline_win_ms", params.get("baseline_win_ms", d.get("bl_win_ms", 50.0))),
                50.0,
            )
            sg_window_ms = float_or(d.get("sg_window_ms", params.get("sg_window_ms", 51.0)), 51.0)
            sg_poly = int_or(d.get("sg_poly", params.get("sg_poly", 3)), 3)

            e_det = _detrend_signal(
                t,
                v,
                method=baseline_method,
                window_ms=float(baseline_win_ms),
                sg_window_ms=float(sg_window_ms),
                sg_poly=int(sg_poly),
            )

            window = d.get("window", [])
            if isinstance(window, list) and len(window) >= 2:
                win_t0 = float_or(window[0], np.nan)
                win_t1 = float_or(window[1], np.nan)
            else:
                win_t0 = float_or(d.get("t0"), np.nan)
                win_t1 = float_or(d.get("t1"), np.nan)

            peak_min_v = float_or(d.get("peak_min_v", d.get("peak_min_V", params.get("peak_min_V"))), np.nan)
            min_width_ms = float_or(d.get("min_width_ms", params.get("min_width_ms")), np.nan)
            min_spacing_ms = float_or(d.get("min_spacing_ms", params.get("min_spacing_ms")), np.nan)

            output_folder = src.with_name(src.stem)
            output_folder.mkdir(parents=True, exist_ok=True)

            summary_path = output_folder / f"{src.stem}_pulses_summary.csv"
            rows = []
            pulse_indices = []
            saved_paths = [str(summary_path)]
            export_idx = 1
            for p in pulses:
                gi = int(p.get("idx", -1)) if p.get("idx", None) is not None else -1
                if gi < 0 or gi >= len(t):
                    tp = float_or(p.get("t", p.get("time")), None)
                    if tp is None:
                        continue
                    gi = int(np.argmin(np.abs(t - tp)))

                rows.append(
                    [
                        export_idx,
                        int(p.get("original_index", export_idx)),
                        float(t[gi]),
                        float(v[gi]),
                        float(e_det[gi]),
                        float_or(p.get("width_ms", p.get("duration")), np.nan),
                        win_t0,
                        win_t1,
                        peak_min_v,
                        min_width_ms,
                        min_spacing_ms,
                        baseline_method,
                        float(baseline_win_ms),
                        float(sg_window_ms),
                        int(sg_poly),
                    ]
                )
                pulse_indices.append((export_idx, gi))
                export_idx += 1

            header = (
                "export_index,original_index,peak_t_s,peak_V_raw,peak_V_detrended,halfwidth_ms,"
                "window_start_s,window_end_s,peak_min_V,min_width_ms,min_spacing_ms,"
                "baseline_method,baseline_win_ms,sg_window_ms,sg_poly"
            )
            with summary_path.open("w", encoding="utf-8") as f:
                f.write(header + "\n")
                for r in rows:
                    f.write(
                        ",".join(f"{v:.9g}" if isinstance(v, (float, int)) else str(v) for v in r)
                        + "\n"
                    )

            pulse_window_ms = float_or(d.get("pulse_window_ms"), 50.0)
            if pulse_window_ms is None or pulse_window_ms <= 0:
                pulse_window_ms = 50.0

            saved_count = 0
            for export_idx, gi in pulse_indices:
                tp = float(t[gi])
                t_start = tp - (pulse_window_ms / 1000.0)
                t_end = tp + (pulse_window_ms / 1000.0)
                mask = (t >= t_start) & (t <= t_end)
                if not np.any(mask):
                    continue

                pulse_path = output_folder / f"{src.stem}_pulse_{export_idx:03d}.csv"
                with pulse_path.open("w", encoding="utf-8") as f:
                    f.write("time_s,voltage_V\n")
                    for t_val, v_val in zip(t[mask], e_det[mask]):
                        f.write(f"{float(t_val):.9g},{float(v_val):.9g}\n")
                saved_count += 1
                saved_paths.append(str(pulse_path))

            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(output_folder),
                    "summary_path": str(summary_path),
                    "saved_count": saved_count,
                    "saved_paths": saved_paths,
                    "outputs": _pv_outputs(output_folder, summary_path, saved_paths),
                },
            }

        df = pd.DataFrame(pulses)
        return {
            "kind": "download",
            "payload": df.to_csv(index=False).encode("utf-8"),
            "mimetype": "text/csv",
            "download_name": f"{stem}_pulses.csv",
        }

    def _echem_pv_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting photovoltage pulses")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _echem_pv_export_payload(save_body)["data"]

    @app.route("/api/echem_pv/browse", methods=["POST"])
    def api_echem_pv_browse():
        d = request.json or {}
        files = browse_files(d.get("folder", ""), {".txt", ".csv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/echem_pv/load", methods=["POST"])
    def api_echem_pv_load():
        path = (request.json or {}).get("path", "")
        try:
            t, v, t_col, v_col = _load_echem(path)
            if len(t) == 0:
                return err("No data points found in file")

            fig, ax = plt.subplots(figsize=(9, 3.5))
            ax.plot(t, v, color=line_color, lw=0.7)
            ax.set_xlabel(t_col)
            ax.set_ylabel(v_col)
            ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "t_range": [float(t[0]), float(t[-1])],
                    "duration": round(float(t[-1] - t[0]), 3) if len(t) else 0,
                    "n_points": len(t),
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/detect", methods=["POST"])
    def api_echem_pv_detect():
        if not has_scipy or find_peaks is None or peak_widths is None:
            return err("scipy not installed")

        d = request.json or {}
        path = d.get("path", "")
        baseline_method = _normalize_method(d.get("baseline_method", d.get("detrend_method", "median")))
        baseline_win_ms = float_or(
            d.get("baseline_win_ms", d.get("bl_win_ms", d.get("detrend_win_ms", d.get("detrend_win", 50.0)))),
            50.0,
        )
        sg_window_ms = float_or(d.get("sg_window_ms", d.get("sg_win_ms", 51.0)), 51.0)
        sg_poly = int_or(d.get("sg_poly", 3), 3)

        t0 = float_or(d.get("t0"), None)
        t1 = float_or(d.get("t1"), None)
        peak_min_v = float_or(d.get("peak_min_v", d.get("peak_min_V", d.get("pv_height"))), None)
        if peak_min_v is None:
            peak_min_v = 0.01
        min_width_ms = float_or(d.get("min_width_ms", 5.0), 5.0)
        min_spacing_ms = float_or(d.get("min_spacing_ms", d.get("pv_dist", d.get("min_dist", 10.0))), 10.0)

        polarity = str(d.get("polarity", "")).strip().lower()
        if not polarity:
            det_pos = _as_bool(d.get("det_pos"), True)
            det_neg = _as_bool(d.get("det_neg"), False)
            if det_pos and det_neg:
                polarity = "both"
            elif det_neg:
                polarity = "negative"
            else:
                polarity = "positive"
        if polarity not in {"positive", "negative", "both"}:
            polarity = "positive"

        use_all = _as_bool(d.get("use_all"), False)
        show_detrended = _as_bool(d.get("show_detrended"), False)

        try:
            t, v, t_col, v_col = _load_echem(path)
            if len(t) < 2:
                return err("Not enough data points for detection")
            e_det = _detrend_signal(
                t,
                v,
                method=baseline_method,
                window_ms=float(baseline_win_ms),
                sg_window_ms=float(sg_window_ms),
                sg_poly=int(sg_poly),
            )

            if use_all:
                t0 = float(t[0])
                t1 = float(t[-1])
            else:
                if t0 is None or t1 is None:
                    return err("Set an analysis window first (t0/t1).")
                if t1 < t0:
                    t0, t1 = t1, t0

            if polarity == "positive":
                raw = _detect_positive_pulses_in_window(
                    t, e_det, float(t0), float(t1), float(peak_min_v), float(min_width_ms), float(min_spacing_ms)
                )
                signed = 1
            elif polarity == "negative":
                raw = _detect_negative_pulses_in_window(
                    t, e_det, float(t0), float(t1), float(peak_min_v), float(min_width_ms), float(min_spacing_ms)
                )
                signed = -1
            else:
                raw = _detect_positive_pulses_in_window(
                    t, e_det, float(t0), float(t1), float(peak_min_v), float(min_width_ms), float(min_spacing_ms)
                )
                raw_neg = _detect_negative_pulses_in_window(
                    t, e_det, float(t0), float(t1), float(peak_min_v), float(min_width_ms), float(min_spacing_ms)
                )
                for it in raw:
                    it["_polarity"] = 1
                for it in raw_neg:
                    it["_polarity"] = -1
                raw = raw + raw_neg
                raw.sort(key=lambda x: x["t"])
                signed = 0

            pulses = []
            for i, p in enumerate(raw, start=1):
                gi = int(p["idx"])
                pol = int(p.get("_polarity", signed if signed != 0 else (1 if float(p["amp_det_v"]) >= 0 else -1)))
                pulses.append(
                    {
                        "idx": gi,
                        "original_index": i,
                        "t": round(float(t[gi]), 6),
                        "time": round(float(t[gi]), 6),
                        "amp_det_V": round(float(e_det[gi]), 8),
                        "amplitude": round(float(e_det[gi]), 8),
                        "height": round(float(e_det[gi]), 8),
                        "width_ms": round(float(p["width_ms"]), 4),
                        "duration": round(float(p["width_ms"]), 4),
                        "polarity": pol,
                        "polarity_label": "+" if pol >= 0 else "-",
                    }
                )

            y_plot = e_det if show_detrended else v
            y_label = "Detrended Voltage (V)" if show_detrended else v_col

            fig, ax = plt.subplots(figsize=(10, 3.8))
            ax.plot(t, y_plot, color=line_color, lw=0.8)

            ax.axvspan(float(t0), float(t1), alpha=0.12, color="gray")
            ax.axvline(float(t0), ls="--", lw=0.8, color="gray")
            ax.axvline(float(t1), ls="--", lw=0.8, color="gray")

            for p in pulses:
                gi = int(p["idx"])
                yy = float(y_plot[gi])
                color = "#d62728" if int(p["polarity"]) >= 0 else "#1f77b4"
                ax.scatter([float(p["t"])], [yy], s=28, marker="^", color=color, zorder=5)
                ax.annotate(
                    str(p["original_index"]),
                    xy=(float(p["t"]), yy),
                    xytext=(3, 3),
                    textcoords="offset points",
                    fontsize=8,
                    color=color,
                )

            ax.set_xlabel(t_col)
            ax.set_ylabel(y_label)
            ax.set_title(f"{Path(path).name} - {len(pulses)} pulses detected", fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.35)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "pulses": pulses,
                    "window": [float(t0), float(t1)],
                    "params": {
                        "polarity": polarity,
                        "peak_min_V": float(peak_min_v),
                        "min_width_ms": float(min_width_ms),
                        "min_spacing_ms": float(min_spacing_ms),
                        "baseline_method": baseline_method,
                        "baseline_win_ms": float(baseline_win_ms),
                        "sg_window_ms": float(sg_window_ms),
                        "sg_poly": int(sg_poly),
                        "show_detrended": bool(show_detrended),
                    },
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/export", methods=["POST"])
    def api_echem_pv_export():
        d = request.json or {}
        try:
            result = _echem_pv_export_payload(d)
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

    @app.route("/api/echem_pv/export_job", methods=["POST"])
    def api_echem_pv_export_job():
        return submit_json_task(
            jobs,
            "echem_pv.export",
            "Export echem photovoltage pulses",
            _echem_pv_export_task,
            request.json or {},
            metadata={"endpoint": "/api/echem_pv/export"},
        )
