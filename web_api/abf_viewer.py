import traceback
from typing import Any

from flask import Response, jsonify, request
from pydantic import Field, ValidationError

from services import abf_viewer as abf_viewer_service
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    RequestModel,
    parse_json_payload,
    parse_query_params,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class AbfBrowseRequest(RequestModel):
    folder: str = ""


class AbfPathRequest(RequestModel):
    path: str = Field(min_length=1)


class AbfLegacyTraceExportRequest(AbfPathRequest):
    mode: Any = "download"


class AbfPlotRequest(AbfPathRequest):
    sweep: Any = 0
    channel: Any = 0
    i_ch: Any = 0
    v_ch: Any = 1
    r_norm: Any = False
    bl_pre0: Any = None
    bl_pre1: Any = None
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    dsf: Any = 1


class AbfDetectRequest(AbfPlotRequest):
    t0: Any = None
    t1: Any = None
    use_all: Any = False
    polarity: str = "positive"
    height: Any = None
    prominence: Any = None
    distance: Any = 2.0


class AbfExportPeaksRequest(AbfPathRequest):
    mode: Any = "download"
    peaks: list[Any] = Field(default_factory=list)
    sweep: Any = 0
    channel: Any = 0
    i_ch: Any = 0
    v_ch: Any = 1
    r_norm: Any = False
    bl_pre0: Any = None
    bl_pre1: Any = None
    export_window_ms: Any = 50.0
    polarity: str = "POS"
    window: list[Any] = Field(default_factory=list)


class AbfExportRequest(AbfPlotRequest):
    fmt: str = "png"
    mode: Any = "download"
    signal_only: Any = False


def register_abf_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    jobs = ctx.get("jobs")

    service = abf_viewer_service.AbfViewerService(
        has_abf=ctx["HAS_ABF"],
        has_scipy=ctx["HAS_SCIPY"],
        pyabf_mod=ctx.get("pyabf"),
        find_peaks=ctx.get("find_peaks"),
        fig_to_b64=ctx["fig_to_b64"],
        float_or=ctx["float_or"],
        int_or=ctx["int_or"],
        as_bool=as_bool,
        mode_is_save=mode_is_save,
        apply_axes_limits=ctx["apply_axes_limits"],
        clean_trace_svg=clean_trace_svg,
        next_numbered_path=next_numbered_path,
        line_color=ctx["LINE_COLOR"],
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            outputs = data.get("outputs")
            return api_ok(data, outputs=outputs) if outputs else jsonify(data)
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
        )

    def _abf_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_peaks_payload(save_body)["data"]

    def _abf_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF trace")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_payload(save_body)["data"]

    @app.route("/api/abf/browse", methods=["POST"])
    @request_schema(AbfBrowseRequest)
    def api_abf_browse():
        try:
            folder = parse_json_payload(AbfBrowseRequest).folder
        except ValidationError as exc:
            return validation_error_response(exc)
        files_data = browse_files(folder, {".abf"})
        return jsonify({"files": files_data, "folder": folder})

    @app.route("/api/abf/browse/tree", methods=["POST"])
    @request_schema(AbfBrowseRequest)
    def api_abf_browse_tree():
        try:
            folder = parse_json_payload(AbfBrowseRequest).folder
            return jsonify(service.browse_tree_payload(folder))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/info", methods=["POST"])
    @request_schema(AbfPathRequest)
    def api_abf_info():
        try:
            path = parse_json_payload(AbfPathRequest).path
            return jsonify(service.info_payload(path))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            if "pyabf" in str(exc):
                return err("pyabf not installed. Run: pip install pyabf")
            return err(str(exc))
        except Exception as exc:
            return err(exc)

    @app.route("/api/abf/plot", methods=["POST"])
    @request_schema(AbfPlotRequest)
    def api_abf_plot():
        try:
            return jsonify(service.plot_payload(parse_json_payload(AbfPlotRequest).model_dump()))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/detect", methods=["POST"])
    @request_schema(AbfDetectRequest)
    def api_abf_detect_compat():
        try:
            return jsonify(service.detect_payload(parse_json_payload(AbfDetectRequest).model_dump()))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks", methods=["GET", "POST"])
    @request_schema(AbfExportPeaksRequest)
    def api_abf_export_peaks_compat():
        try:
            if request.method == "GET":
                query = parse_query_params(AbfLegacyTraceExportRequest)
                result = service.legacy_trace_export_payload(
                    query.path,
                    query.mode,
                )
            else:
                result = service.export_peaks_payload(parse_json_payload(AbfExportPeaksRequest).model_dump())
            return _download_or_save(result)
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks_job", methods=["POST"])
    @request_schema(AbfExportPeaksRequest)
    def api_abf_export_peaks_job():
        try:
            body = parse_json_payload(AbfExportPeaksRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "abf.export_peaks",
            "Export ABF peaks",
            _abf_export_peaks_task,
            body,
            metadata={"endpoint": "/api/abf/export_peaks"},
        )

    @app.route("/api/abf/export", methods=["GET", "POST"])
    @request_schema(AbfExportRequest)
    def api_abf_export():
        try:
            if request.method == "GET":
                body = parse_query_params(AbfExportRequest).model_dump()
            else:
                body = parse_json_payload(AbfExportRequest).model_dump()
            return _download_or_save(service.export_payload(body))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(exc)

    @app.route("/api/abf/export_job", methods=["POST"])
    @request_schema(AbfExportRequest)
    def api_abf_export_job():
        try:
            body = parse_json_payload(AbfExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "abf.export",
            "Export ABF trace",
            _abf_export_task,
            body,
            metadata={"endpoint": "/api/abf/export"},
        )
