import traceback

from flask import Response, jsonify, request
from pydantic import ValidationError

from services import emg_analysis as emg_analysis_service
from services import file_renamer
from web_api.common import as_bool, mode_is_save

from .emg_analysis_request_schemas import (
    EmgAnalysisBrowseRequest,
    EmgAnalysisExportAllRequest,
    EmgAnalysisExportChannelRequest,
    EmgAnalysisExportQueueRequest,
    EmgAnalysisLoadRequest,
    EmgAnalysisProcessingRequest,
    EmgAnalysisRenameApplyRequest,
    EmgAnalysisRenamePreviewRequest,
    EmgAnalysisViewRequest,
)
from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    api_endpoint,
    parse_json_payload,
    parse_query_params,
    request_schema,
    validation_error_response,
)
from .response import api_ok, attachment_content_disposition


def register_emg_analysis_routes(app, ctx):
    err = ctx.err
    browse_files = ctx.browse_files
    browse_files_recursive = ctx.browse_files_recursive
    jobs = ctx.jobs
    service = emg_analysis_service.EmgAnalysisService(
        has_rhd=ctx.HAS_RHD,
        rhd_module=ctx.rhd,
        fig_to_b64=ctx.fig_to_b64,
        bool_value=as_bool,
        mode_is_save=mode_is_save,
        clean_trace_svg=clean_trace_svg,
        next_numbered_path=next_numbered_path,
        line_color=ctx.LINE_COLOR,
    )

    def _json(schema):
        return parse_json_payload(schema).model_dump()

    def _request_body(schema):
        if request.method == "GET":
            return parse_query_params(schema).model_dump()
        return _json(schema)

    def _download_or_api(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            return api_ok(data, outputs=result.get("outputs") or data.get("outputs"))
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": attachment_content_disposition(result["download_name"])},
        )

    def _job_payload(job_ctx, body: dict, message: str, handler):
        job_ctx.set_progress(0.2, message)
        return handler(body or {})

    def _rename_job_payload(job_ctx, body: dict):
        if not as_bool((body or {}).get("confirm")):
            raise ValueError("Rename confirmation is required.")
        job_ctx.set_progress(0.05, "Preparing EMG analysis rename plan")
        return file_renamer.apply_payload(body or {}, job_ctx=job_ctx)

    def _export_response(schema, handler, save_endpoint: str):
        try:
            body = _request_body(schema)
            if request.method == "GET" and mode_is_save(body.get("mode")):
                return err(f"Saving exports requires POST {save_endpoint}.", 405)
            return _download_or_api(handler(body))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    def _submit_body(body: dict, kind: str, label: str, message: str, handler, endpoint: str):
        return submit_json_task(
            jobs,
            kind,
            label,
            lambda job_ctx, payload: _job_payload(job_ctx, payload, message, handler),
            body,
            metadata={"endpoint": endpoint},
        )

    @app.route("/api/emg/analysis/browse", methods=["POST"])
    @api_endpoint(EmgAnalysisBrowseRequest, dump=False)
    def api_emg_analysis_browse(body):
        return jsonify(emg_analysis_service.browse_payload(body.folder, browse_files))

    @app.route("/api/emg/analysis/browse_recursive", methods=["POST"])
    @api_endpoint(EmgAnalysisBrowseRequest, dump=False)
    def api_emg_analysis_browse_recursive(body):
        return jsonify(
            emg_analysis_service.browse_recursive_payload(body.folder, browse_files_recursive)
        )

    @app.route("/api/emg/analysis/load", methods=["POST"])
    @api_endpoint(EmgAnalysisLoadRequest)
    def api_emg_analysis_load(body):
        return jsonify(service.metadata_payload(body))

    @app.route("/api/emg/analysis/plot", methods=["POST"])
    @api_endpoint(EmgAnalysisViewRequest)
    def api_emg_analysis_plot(body):
        return jsonify(service.plot_payload(body))

    @app.route("/api/emg/analysis/process", methods=["POST"])
    @api_endpoint(EmgAnalysisProcessingRequest)
    def api_emg_analysis_process(body):
        return jsonify(service.processing_payload(body))

    @app.route("/api/emg/analysis/export_channel", methods=["GET", "POST"])
    @request_schema(EmgAnalysisExportChannelRequest)
    def api_emg_analysis_export_channel():
        return _export_response(
            EmgAnalysisExportChannelRequest,
            service.export_channel_payload,
            "/api/emg/analysis/export_channel_job",
        )

    @app.route("/api/emg/analysis/export_channel_job", methods=["POST"])
    @api_endpoint(EmgAnalysisExportChannelRequest)
    def api_emg_analysis_export_channel_job(body):
        return _submit_body(
            body,
            "emg_analysis.export_channel",
            "Export EMG analysis channel",
            "Exporting EMG analysis channel",
            service.export_channel_job_payload,
            "/api/emg/analysis/export_channel",
        )

    @app.route("/api/emg/analysis/export_processing", methods=["GET", "POST"])
    @request_schema(EmgAnalysisProcessingRequest)
    def api_emg_analysis_export_processing():
        return _export_response(
            EmgAnalysisProcessingRequest,
            service.export_processing_payload,
            "/api/emg/analysis/export_processing_job",
        )

    @app.route("/api/emg/analysis/export_processing_job", methods=["POST"])
    @api_endpoint(EmgAnalysisProcessingRequest)
    def api_emg_analysis_export_processing_job(body):
        return _submit_body(
            body,
            "emg_analysis.export_processing",
            "Export EMG analysis processing output",
            "Exporting EMG analysis processing output",
            service.export_processing_job_payload,
            "/api/emg/analysis/export_processing",
        )

    @app.route("/api/emg/analysis/export_all", methods=["POST"])
    @api_endpoint(EmgAnalysisExportAllRequest)
    def api_emg_analysis_export_all(body):
        result = service.export_all_payload(body)
        if result["kind"] == "save":
            data = result["data"]
            return api_ok(data, outputs=data["outputs"], warnings=data.get("warnings"))
        return _download_or_api(result)

    @app.route("/api/emg/analysis/export_all_job", methods=["POST"])
    @api_endpoint(EmgAnalysisExportAllRequest)
    def api_emg_analysis_export_all_job(body):
        return _submit_body(
            body,
            "emg_analysis.export_all",
            "Export all EMG analysis channels",
            "Exporting all EMG analysis channels",
            service.export_all_job_payload,
            "/api/emg/analysis/export_all",
        )

    @app.route("/api/emg/analysis/export_queue", methods=["POST"])
    @api_endpoint(EmgAnalysisExportQueueRequest)
    def api_emg_analysis_export_queue(body):
        result = service.export_queue_payload(body)
        return api_ok(result, outputs=result["outputs"], warnings=result.get("warnings"))

    @app.route("/api/emg/analysis/export_queue_job", methods=["POST"])
    @api_endpoint(EmgAnalysisExportQueueRequest)
    def api_emg_analysis_export_queue_job(body):
        return _submit_body(
            body,
            "emg_analysis.export_queue",
            "Export EMG analysis queue",
            "Exporting EMG analysis queue",
            service.export_queue_payload,
            "/api/emg/analysis/export_queue",
        )

    @app.route("/api/emg/analysis/rename/preview", methods=["POST"])
    @api_endpoint(EmgAnalysisRenamePreviewRequest)
    def api_emg_analysis_rename_preview(body):
        return api_ok(file_renamer.preview_payload(body))

    @app.route("/api/emg/analysis/rename/apply_job", methods=["POST"])
    @api_endpoint(EmgAnalysisRenameApplyRequest)
    def api_emg_analysis_rename_apply_job(body):
        return submit_json_task(
            jobs,
            "emg_analysis.rename",
            "Rename EMG analysis recording",
            lambda job_ctx, payload: _rename_job_payload(job_ctx, payload),
            body,
            metadata={"endpoint": "/api/emg/analysis/rename/apply"},
        )
