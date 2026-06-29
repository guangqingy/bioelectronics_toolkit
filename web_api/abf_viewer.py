# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move ABF schemas or remaining export wrappers out so this route file returns
# to the route-only budget; see docs/loc_budget_issue_drafts.md.
from typing import Any

from flask import Response, jsonify, request
from pydantic import ConfigDict, Field, ValidationError

from services import abf_viewer as abf_viewer_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    OptFloat,
    OptInt,
    RequestModel,
    api_endpoint,
    parse_json_payload,
    parse_query_params,
    request_schema,
    validation_error_response,
)
from .response import api_ok, attachment_content_disposition


class AbfBrowseRequest(RequestModel):
    folder: str = ""


class AbfPathRequest(RequestModel):
    path: str = Field(min_length=1)


class AbfPlotRequest(AbfPathRequest):
    sweep: OptInt = 0
    channel: OptInt = 0
    i_ch: OptInt = 0
    v_ch: OptInt = 1
    r_norm: bool = False
    bl_pre0: OptFloat = None
    bl_pre1: OptFloat = None
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    dsf: OptInt = 1


class AbfDetectRequest(AbfPlotRequest):
    t0: OptFloat = None
    t1: OptFloat = None
    use_all: bool = False
    polarity: str = "positive"
    height: OptFloat = None
    prominence: OptFloat = None
    distance: OptFloat = 2.0


class AbfPeakEntry(RequestModel):
    model_config = ConfigDict(extra="allow")

    idx: OptInt = None
    global_index: OptInt = None
    time: OptFloat = None
    t: OptFloat = None
    amplitude: OptFloat = None
    height: OptFloat = None
    polarity: str | None = None


class AbfExportPeaksRequest(AbfPathRequest):
    mode: str = "download"
    peaks: list[AbfPeakEntry] = Field(default_factory=list)
    sweep: OptInt = 0
    channel: OptInt = 0
    i_ch: OptInt = 0
    v_ch: OptInt = 1
    r_norm: bool = False
    bl_pre0: OptFloat = None
    bl_pre1: OptFloat = None
    export_window_ms: OptFloat = 50.0
    polarity: str = "POS"
    window: list[Any] = Field(default_factory=list)


class AbfExportRequest(AbfPlotRequest):
    fmt: str = "png"
    mode: str = "download"
    signal_only: bool = False


def register_abf_viewer_routes(app, ctx):
    err = ctx.err
    browse_files = ctx.browse_files
    jobs = ctx.jobs

    service = abf_viewer_service.AbfViewerService(
        pyabf_mod=ctx.pyabf,
        find_peaks=ctx.find_peaks,
        fig_to_b64=ctx.fig_to_b64,
        mode_is_save=mode_is_save,
        apply_axes_limits=ctx.apply_axes_limits,
        clean_trace_svg=clean_trace_svg,
        next_numbered_path=next_numbered_path,
        line_color=ctx.LINE_COLOR,
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            outputs = data.get("outputs")
            return api_ok(data, outputs=outputs) if outputs else jsonify(data)
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={
                "Content-Disposition": attachment_content_disposition(result["download_name"])
            },
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
    @api_endpoint(AbfBrowseRequest, dump=False)
    def api_abf_browse(payload):
        folder = payload.folder
        files_data = browse_files(folder, {".abf"})
        return jsonify({"files": files_data, "folder": folder})

    @app.route("/api/abf/browse/tree", methods=["POST"])
    @api_endpoint(AbfBrowseRequest, dump=False)
    def api_abf_browse_tree(payload):
        return jsonify(service.browse_tree_payload(payload.folder))

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
    @api_endpoint(AbfPlotRequest)
    def api_abf_plot(body):
        return jsonify(service.plot_payload(body))

    @app.route("/api/abf/trace_data", methods=["POST"])
    @api_endpoint(AbfPlotRequest)
    def api_abf_trace_data(body):
        return jsonify(service.trace_data_payload(body))

    @app.route("/api/abf/detect", methods=["POST"])
    @api_endpoint(AbfDetectRequest)
    def api_abf_detect(body):
        return jsonify(service.detect_payload(body))

    @app.route("/api/abf/export_peaks", methods=["POST"])
    @api_endpoint(AbfExportPeaksRequest)
    def api_abf_export_peaks(body):
        return _download_or_save(service.export_peaks_payload(body))

    @app.route("/api/abf/export_peaks_job", methods=["POST"])
    @api_endpoint(AbfExportPeaksRequest)
    def api_abf_export_peaks_job(body):
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
            if request.method == "GET" and mode_is_save(body.get("mode")):
                return err("Saving ABF trace exports requires POST /api/abf/export_job.", 405)
            return _download_or_save(service.export_payload(body))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(exc)

    @app.route("/api/abf/export_job", methods=["POST"])
    @api_endpoint(AbfExportRequest)
    def api_abf_export_job(body):
        return submit_json_task(
            jobs,
            "abf.export",
            "Export ABF trace",
            _abf_export_task,
            body,
            metadata={"endpoint": "/api/abf/export"},
        )
