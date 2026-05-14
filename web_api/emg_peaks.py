# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move EMG schemas or legacy compatibility wrappers out so this route file
# returns to budget; see docs/loc_budget_issue_drafts.md.
import traceback
from typing import Any

from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import emg_peaks as emg_peaks_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class EmgBrowseRequest(RequestModel):
    folder: str = ""


class EmgChannelRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""


class EmgLoadRequest(EmgChannelRequest):
    channel: str = ""


class EmgPlotRequest(RequestModel):
    path: str = Field(min_length=1)
    x_min: Any = None
    x_max: Any = None


class EmgDetectRequest(EmgPlotRequest):
    pk_height: Any = None
    pk_prom: Any = None
    pk_dist: Any = 100
    pk_minw: Any = None
    pk_wlen: Any = None
    pk_dur: Any = None
    polarity: str = "both"
    adaptive_sigma: Any = False
    sigma_prom: Any = 1.0
    sigma_height: Any = 1.0


class EmgGroupedExportRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""
    channel: str = ""
    path: str = ""
    peaks: list[Any] = Field(default_factory=list)
    half_ms: Any = 100.0
    mode: str = "download"


class EmgLoadCsvRequest(RequestModel):
    path: str = Field(min_length=1)


class EmgDetectPeaksRequest(RequestModel):
    path: str = Field(min_length=1)
    height: Any = None
    prominence: Any = None
    distance: Any = 100
    duration: Any = None


class EmgExportPeaksRequest(RequestModel):
    path: str = ""
    peaks: list[Any] = Field(default_factory=list)
    mode: str = "download"


def register_emg_peaks_routes(app, ctx):
    err = ctx["err"]
    jobs = ctx.get("jobs")
    service = emg_peaks_service.EmgPeaksService(
        has_scipy=ctx["HAS_SCIPY"],
        find_peaks=ctx.get("find_peaks"),
        peak_widths=ctx.get("peak_widths"),
        fig_to_b64=ctx["fig_to_b64"],
        float_or=ctx["float_or"],
        line_color=ctx["LINE_COLOR"],
        mode_is_save=mode_is_save,
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            return api_ok(data, outputs=data["outputs"])
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
        )

    def _emg_grouped_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG grouped peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.grouped_export_payload(save_body)["data"]

    def _emg_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG peaks CSV")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_peaks_payload(save_body)["data"]

    @app.route("/api/emg/browse", methods=["POST"])
    @request_schema(EmgBrowseRequest)
    def api_emg_browse():
        try:
            payload = parse_json_payload(EmgBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return jsonify(service.browse_payload(payload.folder))

    @app.route("/api/emg/load_channels", methods=["POST"])
    @request_schema(EmgChannelRequest)
    def api_emg_load_channels_compat():
        try:
            d = parse_json_payload(EmgChannelRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return jsonify(service.channel_payload(d.folder, d.subfolder))

    @app.route("/api/emg/load", methods=["POST"])
    @request_schema(EmgLoadRequest)
    def api_emg_load_compat():
        try:
            d = parse_json_payload(EmgLoadRequest)
            return jsonify(service.load_duration_payload(d.folder, d.subfolder, d.channel))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/plot", methods=["POST"])
    @request_schema(EmgPlotRequest)
    def api_emg_plot_compat():
        try:
            return jsonify(service.plot_payload(parse_json_payload(EmgPlotRequest).model_dump()))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect", methods=["POST"])
    @request_schema(EmgDetectRequest)
    def api_emg_detect_compat():
        try:
            return jsonify(
                service.detect_payload(parse_json_payload(EmgDetectRequest).model_dump())
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export", methods=["POST"])
    @request_schema(EmgGroupedExportRequest)
    def api_emg_export_compat():
        try:
            return _download_or_save(
                service.grouped_export_payload(
                    parse_json_payload(EmgGroupedExportRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_job", methods=["POST"])
    @request_schema(EmgGroupedExportRequest)
    def api_emg_export_job():
        try:
            body = parse_json_payload(EmgGroupedExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "emg.export",
            "Export EMG grouped peaks",
            _emg_grouped_export_task,
            body,
            metadata={"endpoint": "/api/emg/export"},
        )

    @app.route("/api/emg/load_csv", methods=["POST"])
    @request_schema(EmgLoadCsvRequest)
    def api_emg_load_csv():
        try:
            return jsonify(service.load_csv_payload(parse_json_payload(EmgLoadCsvRequest).path))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect_peaks", methods=["POST"])
    @request_schema(EmgDetectPeaksRequest)
    def api_emg_detect_peaks():
        try:
            return jsonify(
                service.detect_peaks_payload(parse_json_payload(EmgDetectPeaksRequest).model_dump())
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_peaks", methods=["POST"])
    @request_schema(EmgExportPeaksRequest)
    def api_emg_export_peaks():
        try:
            return _download_or_save(
                service.export_peaks_payload(parse_json_payload(EmgExportPeaksRequest).model_dump())
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_peaks_job", methods=["POST"])
    @request_schema(EmgExportPeaksRequest)
    def api_emg_export_peaks_job():
        try:
            body = parse_json_payload(EmgExportPeaksRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "emg.export_peaks",
            "Export EMG peaks CSV",
            _emg_export_peaks_task,
            body,
            metadata={"endpoint": "/api/emg/export_peaks"},
        )
