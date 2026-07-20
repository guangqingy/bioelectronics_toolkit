# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move PV plotting/export payload assembly into echem service helpers and track
# the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import traceback
from pathlib import Path

import numpy as np
from flask import Response, jsonify
from pydantic import ValidationError

from services import echem as echem_service
from services import echem_tokens
from services.matplotlib_utils import close_figure, new_subplots
from web_api.common import as_bool, float_or, int_or

from .echem_photovoltage_request_schemas import (
    EchemPhotovoltageBrowseRequest,
    EchemPhotovoltageDetectRequest,
    EchemPhotovoltageExportRequest,
    EchemPhotovoltageFigureExportRequest,
    EchemPhotovoltageLoadRequest,
    EchemPhotovoltageTraceDataRequest,
)
from .jobs import submit_json_task
from .request_validation import (
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok, attachment_content_disposition


def register_echem_photovoltage_routes(app, ctx):
    err = ctx.err
    browse_files = ctx.browse_files
    fig_to_b64 = ctx.fig_to_b64
    line_color = ctx.LINE_COLOR
    jobs = ctx.jobs
    find_peaks = ctx.find_peaks
    peak_widths = ctx.peak_widths
    savgol_filter = ctx.savgol_filter

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

    def _detect_positive_pulses_in_window(
        t, e_det, t0, t1, peak_min_v, min_width_ms, min_spacing_ms
    ):
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

    def _detect_negative_pulses_in_window(
        t, e_det, t0, t1, peak_min_v, min_width_ms, min_spacing_ms
    ):
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

    def _echem_photovoltage_export_payload(body: dict) -> dict:
        return echem_service.photovoltage_export_payload(body, savgol_filter_func=savgol_filter)

    def _echem_photovoltage_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting photovoltage pulses")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _echem_photovoltage_export_payload(save_body)["data"]

    def _figure_window(body: dict, t) -> tuple[float, float]:
        window = body.get("window", [])
        x0 = float_or(body.get("x_min"), None)
        x1 = float_or(body.get("x_max"), None)
        if (x0 is None or x1 is None) and isinstance(window, list) and len(window) >= 2:
            x0 = float_or(window[0], None)
            x1 = float_or(window[1], None)
        if x0 is None or x1 is None or x1 <= x0:
            x0, x1 = float(t[0]), float(t[-1])
        if x1 <= x0:
            x1 = x0 + 1.0
        return float(x0), float(x1)

    def _marker_index_from_pulse(pulse: dict, t) -> int | None:
        try:
            idx = int(pulse.get("idx", -1))
            if 0 <= idx < len(t):
                return idx
        except Exception:
            pass
        marker_t = float_or(pulse.get("t", pulse.get("time")), None)
        if marker_t is None:
            return None
        return int(np.argmin(np.abs(t - marker_t)))

    def _echem_photovoltage_figure_export_payload(body: dict) -> dict:
        src = Path(body.get("path", ""))
        if not src.exists():
            raise ValueError(f"File not found: {src}")
        fmt = str(body.get("fmt", "png") or "png").lower()
        if fmt not in {"png", "svg"}:
            raise ValueError("Figure format must be png or svg")

        t, v_raw, _t_col, v_col = _load_echem(str(src))
        if len(t) == 0:
            raise ValueError("No data points found in file")
        params = body.get("params", {}) if isinstance(body.get("params"), dict) else {}
        show_detrended = _as_bool(
            body.get("show_detrended", params.get("show_detrended", False)),
            False,
        )
        if show_detrended:
            baseline_method = _normalize_method(
                body.get("baseline_method") or params.get("baseline_method") or "median"
            )
            baseline_win_ms = float_or(
                body.get("baseline_win_ms", params.get("baseline_win_ms", 50.0)),
                50.0,
            )
            sg_window_ms = float_or(body.get("sg_window_ms", params.get("sg_window_ms", 51.0)), 51.0)
            sg_poly = int_or(body.get("sg_poly", params.get("sg_poly", 3)), 3)
            y = _detrend_signal(t, v_raw, baseline_method, baseline_win_ms, sg_window_ms, sg_poly)
            y_label = f"Detrended {v_col}"
        else:
            y = v_raw
            y_label = v_col

        x0, x1 = _figure_window(body, t)
        mask = (t >= x0) & (t <= x1)
        if not np.any(mask):
            raise ValueError("No points in the current preview window")
        y_min = float_or(body.get("y_min"), None)
        y_max = float_or(body.get("y_max"), None)
        if y_min is None or y_max is None or y_max <= y_min:
            y_view = y[mask]
            pad = float(np.ptp(y_view)) * 0.08 if len(y_view) else 0.0
            if pad <= 0:
                pad = 1.0
            y_min = float(np.nanmin(y_view) - pad)
            y_max = float(np.nanmax(y_view) + pad)

        out_path = src.with_name(
            f"{src.stem}_preview.png" if fmt == "png" else f"{src.stem}_preview_signal.svg"
        )
        fig, ax = new_subplots(figsize=(9, 4.8) if fmt == "png" else (8, 3), dpi=100)
        try:
            if fmt == "svg":
                ax.plot(t[mask], y[mask], color=line_color, lw=1.0)
                ax.set_xlim(x0, x1)
                ax.set_ylim(y_min, y_max)
                ax.set_position([0, 0, 1, 1])
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_visible(False)
                ax.set_frame_on(False)
                ax.axis("off")
                fig.savefig(
                    out_path,
                    format="svg",
                    bbox_inches="tight",
                    pad_inches=0,
                    transparent=True,
                    facecolor="none",
                )
            else:
                ax.plot(t, y, color=line_color, lw=1.0)
                ax.set_xlabel("Time (s)")
                ax.set_ylabel(y_label)
                ax.set_xlim(x0, x1)
                ax.set_ylim(y_min, y_max)
                marker_t = []
                marker_y = []
                for pulse in body.get("pulses", []):
                    idx = _marker_index_from_pulse(pulse, t)
                    if idx is not None and x0 <= float(t[idx]) <= x1:
                        marker_t.append(float(t[idx]))
                        marker_y.append(float(y[idx]))
                if marker_t:
                    ax.scatter(marker_t, marker_y, s=50, marker="^", color="red", zorder=5)
                dpi = int(float_or(body.get("dpi"), 300) or 300)
                fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="white")
        finally:
            close_figure(fig)

        role = f"photovoltage_preview_{fmt}"
        return {
            "saved_path": str(out_path),
            "fmt": fmt,
            "outputs": [{"path": str(out_path), "type": fmt, "role": role}],
        }

    def _echem_photovoltage_figure_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting photovoltage preview figure")
        return _echem_photovoltage_figure_export_payload(dict(body or {}))

    @app.route("/api/echem/photovoltage/browse", methods=["POST"])
    @request_schema(EchemPhotovoltageBrowseRequest)
    def api_echem_photovoltage_browse():
        try:
            body = parse_json_payload(EchemPhotovoltageBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        files = [
            record
            for record in browse_files(body.folder, {".txt", ".csv"})
            if echem_tokens.recording_matches_techniques(record["path"], {"CP"})
        ]
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/echem/photovoltage/load", methods=["POST"])
    @request_schema(EchemPhotovoltageLoadRequest)
    def api_echem_photovoltage_load():
        try:
            path = parse_json_payload(EchemPhotovoltageLoadRequest).path
            t, v, t_col, v_col = _load_echem(path)
            if len(t) == 0:
                return err("No data points found in file")

            fig, ax = new_subplots(figsize=(9, 3.5))
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
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/photovoltage/trace_data", methods=["POST"])
    @request_schema(EchemPhotovoltageTraceDataRequest)
    def api_echem_photovoltage_trace_data():
        try:
            body = parse_json_payload(EchemPhotovoltageTraceDataRequest).model_dump()
            return jsonify(echem_service.photovoltage_trace_data_payload(body))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/photovoltage/detect", methods=["POST"])
    @request_schema(EchemPhotovoltageDetectRequest)
    def api_echem_photovoltage_detect():
        if find_peaks is None or peak_widths is None:
            return err("scipy not installed")
        try:
            body = parse_json_payload(EchemPhotovoltageDetectRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        path = body.get("path", "")
        baseline_method = _normalize_method(
            body.get("baseline_method", body.get("detrend_method", "median"))
        )
        baseline_win_ms = float_or(
            body.get(
                "baseline_win_ms",
                body.get("bl_win_ms", body.get("detrend_win_ms", body.get("detrend_win", 50.0))),
            ),
            50.0,
        )
        sg_window_ms = float_or(body.get("sg_window_ms", body.get("sg_win_ms", 51.0)), 51.0)
        sg_poly = int_or(body.get("sg_poly", 3), 3)

        t0 = float_or(body.get("t0"), None)
        t1 = float_or(body.get("t1"), None)
        peak_min_v = float_or(body.get("peak_min_v", body.get("peak_min_V", body.get("pv_height"))), None)
        if peak_min_v is None:
            peak_min_v = 0.01
        min_width_ms = float_or(body.get("min_width_ms", 5.0), 5.0)
        min_spacing_ms = float_or(
            body.get("min_spacing_ms", body.get("pv_dist", body.get("min_dist", 10.0))), 10.0
        )

        polarity = str(body.get("polarity", "")).strip().lower()
        if not polarity:
            det_pos = _as_bool(body.get("det_pos"), True)
            det_neg = _as_bool(body.get("det_neg"), False)
            if det_pos and det_neg:
                polarity = "both"
            elif det_neg:
                polarity = "negative"
            else:
                polarity = "positive"
        if polarity not in {"positive", "negative", "both"}:
            polarity = "positive"

        use_all = _as_bool(body.get("use_all"), False)
        show_detrended = _as_bool(body.get("show_detrended"), False)

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
                    t,
                    e_det,
                    float(t0),
                    float(t1),
                    float(peak_min_v),
                    float(min_width_ms),
                    float(min_spacing_ms),
                )
                signed = 1
            elif polarity == "negative":
                raw = _detect_negative_pulses_in_window(
                    t,
                    e_det,
                    float(t0),
                    float(t1),
                    float(peak_min_v),
                    float(min_width_ms),
                    float(min_spacing_ms),
                )
                signed = -1
            else:
                raw = _detect_positive_pulses_in_window(
                    t,
                    e_det,
                    float(t0),
                    float(t1),
                    float(peak_min_v),
                    float(min_width_ms),
                    float(min_spacing_ms),
                )
                raw_neg = _detect_negative_pulses_in_window(
                    t,
                    e_det,
                    float(t0),
                    float(t1),
                    float(peak_min_v),
                    float(min_width_ms),
                    float(min_spacing_ms),
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
                pol = int(
                    p.get(
                        "_polarity",
                        signed if signed != 0 else (1 if float(p["amp_det_v"]) >= 0 else -1),
                    )
                )
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
            y_label = f"Detrended {v_col}" if show_detrended else v_col

            fig, ax = new_subplots(figsize=(10, 3.8))
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
            ax.set_title(
                f"{Path(path).name} - {len(pulses)} pulses detected", fontsize=10, color="#5C5E62"
            )
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
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/photovoltage/export", methods=["POST"])
    @request_schema(EchemPhotovoltageExportRequest)
    def api_echem_photovoltage_export():
        try:
            body = parse_json_payload(EchemPhotovoltageExportRequest).model_dump()
            result = _echem_photovoltage_export_payload(body)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={
                    "Content-Disposition": attachment_content_disposition(result["download_name"])
                },
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/photovoltage/export_job", methods=["POST"])
    @request_schema(EchemPhotovoltageExportRequest)
    def api_echem_photovoltage_export_job():
        try:
            body = parse_json_payload(EchemPhotovoltageExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_photovoltage.export",
            "Export echem photovoltage pulses",
            _echem_photovoltage_export_task,
            body,
            metadata={"endpoint": "/api/echem/photovoltage/export"},
        )

    @app.route("/api/echem/photovoltage/export_figure", methods=["POST"])
    @request_schema(EchemPhotovoltageFigureExportRequest)
    def api_echem_photovoltage_export_figure():
        try:
            body = parse_json_payload(EchemPhotovoltageFigureExportRequest).model_dump()
            result = _echem_photovoltage_figure_export_payload(body)
            return api_ok(result, outputs=result["outputs"])
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/photovoltage/export_figure_job", methods=["POST"])
    @request_schema(EchemPhotovoltageFigureExportRequest)
    def api_echem_photovoltage_export_figure_job():
        try:
            body = parse_json_payload(EchemPhotovoltageFigureExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_photovoltage.export_figure",
            "Export echem photovoltage preview figure",
            _echem_photovoltage_figure_export_task,
            body,
            metadata={"endpoint": "/api/echem/photovoltage/export_figure"},
        )
