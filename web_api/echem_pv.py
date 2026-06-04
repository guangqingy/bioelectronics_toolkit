# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move PV plotting/export payload assembly into echem service helpers and track
# the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import traceback
from pathlib import Path
from typing import Any

from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import echem as echem_service
from services.matplotlib_utils import new_subplots
from web_api.common import as_bool

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class EchemPvBrowseRequest(RequestModel):
    folder: str = ""


class EchemPvLoadRequest(RequestModel):
    path: str = Field(min_length=1)


class EchemPvTraceDataRequest(EchemPvLoadRequest):
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    t0: Any = None
    t1: Any = None


class EchemPvDetectRequest(RequestModel):
    path: str = Field(min_length=1)
    t0: Any = None
    t1: Any = None
    baseline_method: str = "median"
    detrend_method: str = ""
    baseline_win_ms: Any = 50.0
    bl_win_ms: Any = None
    detrend_win_ms: Any = None
    detrend_win: Any = None
    sg_window_ms: Any = 51.0
    sg_win_ms: Any = None
    sg_poly: Any = 3
    peak_min_v: Any = None
    peak_min_V: Any = None
    pv_height: Any = None
    min_width_ms: Any = 5.0
    min_spacing_ms: Any = 10.0
    pv_dist: Any = None
    min_dist: Any = None
    polarity: str = ""
    det_pos: Any = True
    det_neg: Any = False
    use_all: Any = False
    show_detrended: Any = False


class EchemPvExportRequest(RequestModel):
    path: str = Field(min_length=1)
    pulses: list[Any] = Field(default_factory=list)
    mode: str = "download"
    window: list[Any] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    baseline_method: str = ""
    detrend_method: str = ""
    baseline_win_ms: Any = None
    bl_win_ms: Any = None
    sg_window_ms: Any = None
    sg_poly: Any = None
    peak_min_v: Any = None
    peak_min_V: Any = None
    min_width_ms: Any = None
    min_spacing_ms: Any = None
    pulse_window_ms: Any = 50.0


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

    def _echem_pv_export_payload(d: dict) -> dict:
        return echem_service.photovoltage_export_payload(d, savgol_filter_func=savgol_filter)

    def _echem_pv_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting photovoltage pulses")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _echem_pv_export_payload(save_body)["data"]

    @app.route("/api/echem_pv/browse", methods=["POST"])
    @request_schema(EchemPvBrowseRequest)
    def api_echem_pv_browse():
        try:
            payload = parse_json_payload(EchemPvBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        files = browse_files(payload.folder, {".txt", ".csv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/echem_pv/load", methods=["POST"])
    @request_schema(EchemPvLoadRequest)
    def api_echem_pv_load():
        try:
            path = parse_json_payload(EchemPvLoadRequest).path
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
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/trace_data", methods=["POST"])
    @request_schema(EchemPvTraceDataRequest)
    def api_echem_pv_trace_data():
        try:
            d = parse_json_payload(EchemPvTraceDataRequest).model_dump()
            return jsonify(echem_service.photovoltage_trace_data_payload(d))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/detect", methods=["POST"])
    @request_schema(EchemPvDetectRequest)
    def api_echem_pv_detect():
        if not has_scipy or find_peaks is None or peak_widths is None:
            return err("scipy not installed")

        try:
            d = parse_json_payload(EchemPvDetectRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        path = d.get("path", "")
        baseline_method = _normalize_method(
            d.get("baseline_method", d.get("detrend_method", "median"))
        )
        baseline_win_ms = float_or(
            d.get(
                "baseline_win_ms",
                d.get("bl_win_ms", d.get("detrend_win_ms", d.get("detrend_win", 50.0))),
            ),
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
        min_spacing_ms = float_or(
            d.get("min_spacing_ms", d.get("pv_dist", d.get("min_dist", 10.0))), 10.0
        )

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
            y_label = "Detrended Voltage (V)" if show_detrended else v_col

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
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/export", methods=["POST"])
    @request_schema(EchemPvExportRequest)
    def api_echem_pv_export():
        try:
            d = parse_json_payload(EchemPvExportRequest).model_dump()
            result = _echem_pv_export_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem_pv/export_job", methods=["POST"])
    @request_schema(EchemPvExportRequest)
    def api_echem_pv_export_job():
        try:
            body = parse_json_payload(EchemPvExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_pv.export",
            "Export echem photovoltage pulses",
            _echem_pv_export_task,
            body,
            metadata={"endpoint": "/api/echem_pv/export"},
        )
