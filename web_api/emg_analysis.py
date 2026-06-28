import traceback

from flask import Response, jsonify, request
from pydantic import ValidationError

from services import file_renamer
from services import emg_analysis as emg_analysis_service
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    parse_json_payload,
    parse_query_params,
    request_schema,
    validation_error_response,
)
from .response import api_ok
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


def register_emg_analysis_routes(app, ctx):
    err = ctx.err
    browse_files = ctx.browse_files
    browse_files_recursive = ctx.browse_files_recursive
    jobs = ctx.jobs
    service = emg_analysis_service.EmgAnalysisService(
        has_rhd=ctx.HAS_RHD,
        rhd_module=ctx.rhd,
        fig_to_b64=ctx.fig_to_b64,
        float_or=ctx.float_or,
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
            headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
        )

    def _job_payload(job_ctx, body: dict, message: str, handler):
        job_ctx.set_progress(0.2, message)
        return handler(body or {})

    def _rename_job_payload(job_ctx, body: dict):
        if not as_bool((body or {}).get("confirm")):
            raise ValueError("Rename confirmation is required.")
        job_ctx.set_progress(0.05, "Preparing EMG analysis rename plan")
        return file_renamer.apply_payload(body or {}, job_ctx=job_ctx)

    def _json_response(schema, handler):
        try:
            return jsonify(handler(_json(schema)))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    def _export_response(schema, handler):
        try:
            return _download_or_api(handler(_request_body(schema)))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    def _submit(schema, kind: str, label: str, message: str, handler, endpoint: str):
        try:
            body = _json(schema)
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            kind,
            label,
            lambda job_ctx, payload: _job_payload(job_ctx, payload, message, handler),
            body,
            metadata={"endpoint": endpoint},
        )

    @app.route("/api/emg/analysis/browse", methods=["POST"])
    @request_schema(EmgAnalysisBrowseRequest)
    def api_emg_analysis_browse():
        try:
            folder = parse_json_payload(EmgAnalysisBrowseRequest).folder
            return jsonify(emg_analysis_service.browse_payload(folder, browse_files))
        except ValidationError as exc:
            return validation_error_response(exc)

    @app.route("/api/emg/analysis/browse_recursive", methods=["POST"])
    @request_schema(EmgAnalysisBrowseRequest)
    def api_emg_analysis_browse_recursive():
        try:
            folder = parse_json_payload(EmgAnalysisBrowseRequest).folder
            return jsonify(
                emg_analysis_service.browse_recursive_payload(folder, browse_files_recursive)
            )
        except ValidationError as exc:
            return validation_error_response(exc)

    @app.route("/api/emg/analysis/load", methods=["POST"])
    @request_schema(EmgAnalysisLoadRequest)
    def api_emg_analysis_load():
        return _json_response(EmgAnalysisLoadRequest, service.metadata_payload)

    @app.route("/api/emg/analysis/plot", methods=["POST"])
    @request_schema(EmgAnalysisViewRequest)
    def api_emg_analysis_plot():
        return _json_response(EmgAnalysisViewRequest, service.plot_payload)

    @app.route("/api/emg/analysis/process", methods=["POST"])
    @request_schema(EmgAnalysisProcessingRequest)
    def api_emg_analysis_process():
        return _json_response(EmgAnalysisProcessingRequest, service.processing_payload)

    @app.route("/api/emg/analysis/export_channel", methods=["GET", "POST"])
    @request_schema(EmgAnalysisExportChannelRequest)
    def api_emg_analysis_export_channel():
        return _export_response(EmgAnalysisExportChannelRequest, service.export_channel_payload)

    @app.route("/api/emg/analysis/export_processing", methods=["GET", "POST"])
    @request_schema(EmgAnalysisProcessingRequest)
    def api_emg_analysis_export_processing():
        return _export_response(EmgAnalysisProcessingRequest, service.export_processing_payload)

    @app.route("/api/emg/analysis/export_processing_job", methods=["POST"])
    @request_schema(EmgAnalysisProcessingRequest)
    def api_emg_analysis_export_processing_job():
        return _submit(
            EmgAnalysisProcessingRequest,
            "emg_analysis.export_processing",
            "Export EMG analysis processing output",
            "Exporting EMG analysis processing output",
            service.export_processing_job_payload,
            "/api/emg/analysis/export_processing",
        )

    @app.route("/api/emg/analysis/export_all", methods=["POST"])
    @request_schema(EmgAnalysisExportAllRequest)
    def api_emg_analysis_export_all():
        try:
            result = service.export_all_payload(_json(EmgAnalysisExportAllRequest))
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"], warnings=data.get("warnings"))
            return _download_or_api(result)
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/analysis/export_all_job", methods=["POST"])
    @request_schema(EmgAnalysisExportAllRequest)
    def api_emg_analysis_export_all_job():
        return _submit(
            EmgAnalysisExportAllRequest,
            "emg_analysis.export_all",
            "Export all EMG analysis channels",
            "Exporting all EMG analysis channels",
            service.export_all_job_payload,
            "/api/emg/analysis/export_all",
        )

    @app.route("/api/emg/analysis/export_queue", methods=["POST"])
    @request_schema(EmgAnalysisExportQueueRequest)
    def api_emg_analysis_export_queue():
        try:
            result = service.export_queue_payload(_json(EmgAnalysisExportQueueRequest))
            return api_ok(result, outputs=result["outputs"], warnings=result.get("warnings"))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))

    @app.route("/api/emg/analysis/export_queue_job", methods=["POST"])
    @request_schema(EmgAnalysisExportQueueRequest)
    def api_emg_analysis_export_queue_job():
        return _submit(
            EmgAnalysisExportQueueRequest,
            "emg_analysis.export_queue",
            "Export EMG analysis queue",
            "Exporting EMG analysis queue",
            service.export_queue_payload,
            "/api/emg/analysis/export_queue",
        )

    @app.route("/api/emg/analysis/rename/preview", methods=["POST"])
    @request_schema(EmgAnalysisRenamePreviewRequest)
    def api_emg_analysis_rename_preview():
        try:
            return api_ok(file_renamer.preview_payload(_json(EmgAnalysisRenamePreviewRequest)))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/analysis/rename/apply_job", methods=["POST"])
    @request_schema(EmgAnalysisRenameApplyRequest)
    def api_emg_analysis_rename_apply_job():
        try:
            body = _json(EmgAnalysisRenameApplyRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "emg_analysis.rename",
            "Rename EMG analysis recording",
            lambda job_ctx, payload: _rename_job_payload(job_ctx, payload),
            body,
            metadata={"endpoint": "/api/emg/analysis/rename/apply"},
        )
