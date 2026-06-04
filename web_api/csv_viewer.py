# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move remaining CSV export/download response assembly into services/csv_viewer
# and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import traceback
from pathlib import Path
from typing import Any

from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import csv_tools, csv_viewer
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .path_policy import ensure_output_parent
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    RequestModel,
    parse_json_payload,
    parse_query_params,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class CsvBrowseRequest(RequestModel):
    folder: str = ""


class CsvColumnsRequest(RequestModel):
    path: str = Field(min_length=1)


class CsvPlotRequest(RequestModel):
    path: str = Field(min_length=1)
    x_col: str = ""
    y_col: str = ""
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    dsf: Any = 1


class CsvMergeRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)
    x_col: str = ""
    y_col: str = ""
    x_min: Any = None
    x_max: Any = None
    drop_first_subsequent: bool = True
    mode: str = "download"


class CsvExportRequest(CsvPlotRequest):
    fmt: str = "png"
    mode: str = "download"


class CsvFullExportRequest(RequestModel):
    path: str = Field(min_length=1)
    mode: str = "download"


def register_csv_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save
    viewer_service = csv_viewer.CsvViewerService(
        float_or=float_or,
        int_or=int_or,
        apply_axes_limits=apply_axes_limits,
        fig_to_b64=fig_to_b64,
        clean_trace_svg=clean_trace_svg,
        line_color=line_color,
    )

    def _csv_output(path: Path, role: str = "csv") -> dict:
        return {"path": str(path), "type": "csv", "role": role}

    def _write_payload_result(export: dict, role: str) -> dict:
        out_path = ensure_output_parent(Path(export["out_path"]))
        if out_path.suffix.lower() in {".png", ".svg"}:
            out_path = next_numbered_path(out_path)
        out_path.write_bytes(export["payload"])
        result = {"ok": True, "saved_path": str(out_path)}
        if "rows" in export:
            result["rows"] = export["rows"]
        result["outputs"] = [_csv_output(out_path, role)]
        return result

    def _csv_merge_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Rendering CSV merge preview")
        return viewer_service.merge_preview_payload(body)

    def _csv_export_merge_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Building merged CSV")
        return _write_payload_result(viewer_service.merge_export_payload(body), "merged_csv")

    def _csv_export_plot_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Rendering CSV export")
        export = viewer_service.plot_export_payload(body)
        result = _write_payload_result(export, export["role"])
        result["outputs"][0]["type"] = export["output_type"]
        return result

    def _csv_export_full_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting full CSV")
        return _write_payload_result(viewer_service.full_csv_export_payload(body), "full_csv")

    @app.route("/api/csv/browse", methods=["POST"])
    @request_schema(CsvBrowseRequest)
    def api_csv_browse():
        try:
            d = parse_json_payload(CsvBrowseRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        files = browse_files(d.get("folder", ""), {".csv", ".txt", ".tsv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/csv/columns", methods=["POST"])
    @request_schema(CsvColumnsRequest)
    def api_csv_columns():
        try:
            path = parse_json_payload(CsvColumnsRequest).path
            return jsonify({"columns": csv_tools.read_columns(path)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception as e:
            return err(e)

    @app.route("/api/csv/plot", methods=["POST"])
    @request_schema(CsvPlotRequest)
    def api_csv_plot():
        try:
            d = parse_json_payload(CsvPlotRequest).model_dump()
            return jsonify(viewer_service.plot_preview_payload(d))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/trace_data", methods=["POST"])
    @request_schema(CsvPlotRequest)
    def api_csv_trace_data():
        # Decimated numeric trace for client-side interactive (uPlot) rendering.
        # No server-side matplotlib render; the browser handles zoom/pan locally.
        try:
            d = parse_json_payload(CsvPlotRequest).model_dump()
            return jsonify(viewer_service.trace_data_payload(d))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/merge", methods=["POST"])
    @request_schema(CsvMergeRequest)
    def api_csv_merge():
        try:
            d = parse_json_payload(CsvMergeRequest).model_dump()
            return api_ok(viewer_service.merge_preview_payload(d))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/merge_job", methods=["POST"])
    @request_schema(CsvMergeRequest)
    def api_csv_merge_job():
        try:
            body = parse_json_payload(CsvMergeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "csv.merge_preview",
            "Merge CSV preview",
            _csv_merge_task,
            body,
            metadata={"endpoint": "/api/csv/merge"},
        )

    @app.route("/api/csv/export_merge", methods=["POST"])
    @request_schema(CsvMergeRequest)
    def api_csv_export_merge():
        try:
            d = parse_json_payload(CsvMergeRequest).model_dump()
            mode = d.get("mode", "download")
            export = viewer_service.merge_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, "merged_csv")
                return api_ok(result, outputs=result["outputs"])

            return Response(
                export["payload"],
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={export['out_name']}"},
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/export_merge_job", methods=["POST"])
    @request_schema(CsvMergeRequest)
    def api_csv_export_merge_job():
        try:
            body = parse_json_payload(CsvMergeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "csv.export_merge",
            "Export merged CSV",
            _csv_export_merge_task,
            body,
            metadata={"endpoint": "/api/csv/export_merge"},
        )

    @app.route("/api/csv/export")
    def api_csv_export():
        try:
            d = parse_query_params(CsvExportRequest).model_dump()
            mode = d.get("mode", "download")
            export = viewer_service.plot_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, export["role"])
                result["outputs"][0]["type"] = export["output_type"]
                return api_ok(result, outputs=result["outputs"])
            return Response(
                export["payload"],
                mimetype=export["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={export['download_name']}"},
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_job", methods=["POST"])
    @request_schema(CsvExportRequest)
    def api_csv_export_job():
        try:
            body = parse_json_payload(CsvExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "csv.export_plot",
            "Export CSV plot",
            _csv_export_plot_task,
            body,
            metadata={"endpoint": "/api/csv/export"},
        )

    @app.route("/api/csv/export_csv")
    def api_csv_export_csv_compat():
        try:
            d = parse_query_params(CsvFullExportRequest).model_dump()
            mode = d.get("mode", "download")
            export = viewer_service.full_csv_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, "full_csv")
                return api_ok(result, outputs=result["outputs"])
            return Response(
                export["payload"],
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={export['download_name']}"},
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_csv_job", methods=["POST"])
    @request_schema(CsvFullExportRequest)
    def api_csv_export_csv_job():
        try:
            body = parse_json_payload(CsvFullExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "csv.export_full",
            "Export full CSV",
            _csv_export_full_task,
            body,
            metadata={"endpoint": "/api/csv/export_csv"},
        )
