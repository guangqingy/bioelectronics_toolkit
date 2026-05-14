import traceback

from flask import Response, jsonify, request
from pydantic import ValidationError

from services.rhd_viewer import RhdViewerService
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import parse_json_payload, parse_query_params, request_schema, validation_error_response
from .response import api_ok
from .rhd_request_schemas import (
    RhdBrowseRequest,
    RhdExportAllRequest,
    RhdExportChannelRequest,
    RhdExportQueueRequest,
    RhdLoadRequest,
    RhdProcessingRequest,
    RhdViewRequest,
)


def register_rhd_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    browse_files_recursive = ctx["browse_files_recursive"]
    jobs = ctx.get("jobs")
    service = RhdViewerService(
        has_rhd=ctx["HAS_RHD"],
        rhd_module=ctx.get("rhd"),
        fig_to_b64=ctx["fig_to_b64"],
        float_or=ctx["float_or"],
        bool_value=as_bool,
        mode_is_save=mode_is_save,
        clean_trace_svg=clean_trace_svg,
        next_numbered_path=next_numbered_path,
        line_color=ctx["LINE_COLOR"],
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
    @app.route("/api/rhd/browse", methods=["POST"])
    @request_schema(RhdBrowseRequest)
    def api_rhd_browse():
        try:
            folder = parse_json_payload(RhdBrowseRequest).folder
            files = browse_files(folder, {".rhd"})
            return jsonify({"files": [f["path"] for f in files], "file_meta": files})
        except ValidationError as exc:
            return validation_error_response(exc)

    @app.route("/api/rhd/browse_recursive", methods=["POST"])
    @request_schema(RhdBrowseRequest)
    def api_rhd_browse_recursive():
        try:
            folder = parse_json_payload(RhdBrowseRequest).folder
            files = browse_files_recursive(folder, {".rhd"})
            return jsonify({"files": [f["path"] for f in files], "file_meta": files})
        except ValidationError as exc:
            return validation_error_response(exc)

    @app.route("/api/rhd/load", methods=["POST"])
    @request_schema(RhdLoadRequest)
    def api_rhd_load():
        return _json_response(RhdLoadRequest, service.metadata_payload)

    @app.route("/api/rhd/plot", methods=["POST"])
    @request_schema(RhdViewRequest)
    def api_rhd_plot():
        return _json_response(RhdViewRequest, service.plot_payload)

    @app.route("/api/rhd/process", methods=["POST"])
    @request_schema(RhdProcessingRequest)
    def api_rhd_process():
        return _json_response(RhdProcessingRequest, service.processing_payload)

    @app.route("/api/rhd/export_channel", methods=["GET", "POST"])
    @request_schema(RhdExportChannelRequest)
    def api_rhd_export_channel():
        return _export_response(RhdExportChannelRequest, service.export_channel_payload)

    @app.route("/api/rhd/export_processing", methods=["GET", "POST"])
    @request_schema(RhdProcessingRequest)
    def api_rhd_export_processing():
        return _export_response(RhdProcessingRequest, service.export_processing_payload)

    @app.route("/api/rhd/export_processing_job", methods=["POST"])
    @request_schema(RhdProcessingRequest)
    def api_rhd_export_processing_job():
        return _submit(
            RhdProcessingRequest,
            "rhd.export_processing",
            "Export RHD processing output",
            "Exporting RHD processing output",
            service.export_processing_job_payload,
            "/api/rhd/export_processing",
        )

    @app.route("/api/rhd/export_all", methods=["POST"])
    @request_schema(RhdExportAllRequest)
    def api_rhd_export_all():
        try:
            result = service.export_all_payload(_json(RhdExportAllRequest))
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

    @app.route("/api/rhd/export_all_job", methods=["POST"])
    @request_schema(RhdExportAllRequest)
    def api_rhd_export_all_job():
        return _submit(
            RhdExportAllRequest,
            "rhd.export_all",
            "Export all RHD channels",
            "Exporting all RHD channels",
            service.export_all_job_payload,
            "/api/rhd/export_all",
        )

    @app.route("/api/rhd/export_queue", methods=["POST"])
    @request_schema(RhdExportQueueRequest)
    def api_rhd_export_queue():
        try:
            result = service.export_queue_payload(_json(RhdExportQueueRequest))
            return api_ok(result, outputs=result["outputs"], warnings=result.get("warnings"))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))

    @app.route("/api/rhd/export_queue_job", methods=["POST"])
    @request_schema(RhdExportQueueRequest)
    def api_rhd_export_queue_job():
        return _submit(
            RhdExportQueueRequest,
            "rhd.export_queue",
            "Export RHD queue",
            "Exporting RHD queue",
            service.export_queue_payload,
            "/api/rhd/export_queue",
        )
