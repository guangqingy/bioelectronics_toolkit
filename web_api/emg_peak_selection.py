import traceback
from typing import Any

from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import emg_peak_selection as emg_peak_selection_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class EmgPeakSelectionBrowseRequest(RequestModel):
    folder: str = ""


class EmgPeakSelectionChannelRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""


class EmgPeakSelectionLoadRequest(RequestModel):
    folder: str = Field(min_length=1)
    subfolder: str = Field(min_length=1)
    channel: str = Field(min_length=1)


class EmgPeakSelectionPlotRequest(RequestModel):
    path: str = Field(min_length=1)
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    invert_signal: Any = False


class EmgPeakSelectionDetectRequest(EmgPeakSelectionPlotRequest):
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


class EmgPeakSelectionGroupedExportRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""
    channel: str = ""
    path: str = ""
    peaks: list[Any] = Field(default_factory=list)
    linked_channels: list[str] = Field(default_factory=list)
    half_ms: Any = 100.0
    invert_signal: Any = False
    mode: str = "download"


class EmgPeakSelectionLoadCsvRequest(RequestModel):
    path: str = Field(min_length=1)


class EmgPeakSelectionDetectPeaksRequest(RequestModel):
    path: str = Field(min_length=1)
    height: Any = None
    prominence: Any = None
    distance: Any = 100
    duration: Any = None


class EmgPeakSelectionExportPeaksRequest(RequestModel):
    path: str = ""
    peaks: list[Any] = Field(default_factory=list)
    mode: str = "download"


def register_emg_peak_selection_routes(app, ctx):
    err = ctx.err
    jobs = ctx.jobs
    service = emg_peak_selection_service.EmgPeakSelectionService(
        find_peaks=ctx.find_peaks,
        peak_widths=ctx.peak_widths,
        fig_to_b64=ctx.fig_to_b64,
        float_or=ctx.float_or,
        line_color=ctx.LINE_COLOR,
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

    @app.route("/api/emg/peak-selection/browse", methods=["POST"])
    @request_schema(EmgPeakSelectionBrowseRequest)
    def api_emg_peak_selection_browse():
        try:
            payload = parse_json_payload(EmgPeakSelectionBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return jsonify(service.browse_payload(payload.folder))

    @app.route("/api/emg/peak-selection/load_channels", methods=["POST"])
    @request_schema(EmgPeakSelectionChannelRequest)
    def api_emg_peak_selection_load_channels():
        try:
            d = parse_json_payload(EmgPeakSelectionChannelRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return jsonify(service.channel_payload(d.folder, d.subfolder))

    @app.route("/api/emg/peak-selection/load", methods=["POST"])
    @request_schema(EmgPeakSelectionLoadRequest)
    def api_emg_peak_selection_load():
        try:
            d = parse_json_payload(EmgPeakSelectionLoadRequest)
            return jsonify(service.load_duration_payload(d.folder, d.subfolder, d.channel))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/plot", methods=["POST"])
    @request_schema(EmgPeakSelectionPlotRequest)
    def api_emg_peak_selection_plot():
        try:
            return jsonify(
                service.plot_payload(parse_json_payload(EmgPeakSelectionPlotRequest).model_dump())
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/trace_data", methods=["POST"])
    @request_schema(EmgPeakSelectionPlotRequest)
    def api_emg_peak_selection_trace_data():
        try:
            return jsonify(
                service.trace_data_payload(
                    parse_json_payload(EmgPeakSelectionPlotRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/detect", methods=["POST"])
    @request_schema(EmgPeakSelectionDetectRequest)
    def api_emg_peak_selection_detect():
        try:
            return jsonify(
                service.detect_payload(
                    parse_json_payload(EmgPeakSelectionDetectRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/export", methods=["POST"])
    @request_schema(EmgPeakSelectionGroupedExportRequest)
    def api_emg_peak_selection_export():
        try:
            return _download_or_save(
                service.grouped_export_payload(
                    parse_json_payload(EmgPeakSelectionGroupedExportRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/export_job", methods=["POST"])
    @request_schema(EmgPeakSelectionGroupedExportRequest)
    def api_emg_peak_selection_export_job():
        try:
            body = parse_json_payload(EmgPeakSelectionGroupedExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "emg_peak_selection.export",
            "Export EMG grouped peaks",
            _emg_grouped_export_task,
            body,
            metadata={"endpoint": "/api/emg/peak-selection/export"},
        )

    @app.route("/api/emg/peak-selection/load_csv", methods=["POST"])
    @request_schema(EmgPeakSelectionLoadCsvRequest)
    def api_emg_peak_selection_load_csv():
        try:
            return jsonify(
                service.load_csv_payload(parse_json_payload(EmgPeakSelectionLoadCsvRequest).path)
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/detect_peaks", methods=["POST"])
    @request_schema(EmgPeakSelectionDetectPeaksRequest)
    def api_emg_peak_selection_detect_peaks():
        try:
            return jsonify(
                service.detect_peaks_payload(
                    parse_json_payload(EmgPeakSelectionDetectPeaksRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/export_peaks", methods=["POST"])
    @request_schema(EmgPeakSelectionExportPeaksRequest)
    def api_emg_peak_selection_export_peaks():
        try:
            return _download_or_save(
                service.export_peaks_payload(
                    parse_json_payload(EmgPeakSelectionExportPeaksRequest).model_dump()
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/peak-selection/export_peaks_job", methods=["POST"])
    @request_schema(EmgPeakSelectionExportPeaksRequest)
    def api_emg_peak_selection_export_peaks_job():
        try:
            body = parse_json_payload(EmgPeakSelectionExportPeaksRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "emg_peak_selection.export_peaks",
            "Export EMG peaks CSV",
            _emg_export_peaks_task,
            body,
            metadata={"endpoint": "/api/emg/peak-selection/export_peaks"},
        )
