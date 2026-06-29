from pathlib import Path
from typing import Any

from flask import Response, jsonify
from pydantic import Field

from services import csv_tools, csv_viewer
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .path_policy import ensure_output_parent
from .plot_export import clean_trace_svg, next_numbered_path
from .request_validation import (
    OptFloat,
    OptInt,
    RequestModel,
    api_endpoint,
)
from .response import api_ok, attachment_content_disposition


class CsvBrowseRequest(RequestModel):
    folder: str = ""


class CsvColumnsRequest(RequestModel):
    path: str = Field(min_length=1)


class CsvPlotRequest(RequestModel):
    path: str = Field(min_length=1)
    x_col: str = ""
    y_col: str = ""
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    dsf: OptInt = 1


class CsvMergeRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)
    x_col: str = ""
    y_col: str = ""
    x_min: OptFloat = None
    x_max: OptFloat = None
    drop_first_subsequent: bool = True
    mode: str = "download"


class CsvExportRequest(CsvPlotRequest):
    fmt: str = "png"
    mode: str = "download"


class CsvFullExportRequest(RequestModel):
    path: str = Field(min_length=1)
    mode: str = "download"


def register_csv_viewer_routes(app, ctx):
    err = ctx.err
    browse_files = ctx.browse_files
    fig_to_b64 = ctx.fig_to_b64
    apply_axes_limits = ctx.apply_axes_limits
    line_color = ctx.LINE_COLOR
    jobs = ctx.jobs

    _mode_is_save = mode_is_save
    viewer_service = csv_viewer.CsvViewerService(
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

    def _submit_csv_job(kind: str, label: str, task, body: dict, endpoint: str):
        return submit_json_task(jobs, kind, label, task, body, metadata={"endpoint": endpoint})

    @app.route("/api/csv/browse", methods=["POST"])
    @api_endpoint(CsvBrowseRequest)
    def api_csv_browse(d):
        files = browse_files(d.get("folder", ""), {".csv", ".txt", ".tsv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/csv/columns", methods=["POST"])
    @api_endpoint(CsvColumnsRequest, dump=False)
    def api_csv_columns(d):
        return jsonify({"columns": csv_tools.read_columns(d.path)})

    @app.route("/api/csv/plot", methods=["POST"])
    @api_endpoint(CsvPlotRequest)
    def api_csv_plot(d):
        return jsonify(viewer_service.plot_preview_payload(d))

    @app.route("/api/csv/trace_data", methods=["POST"])
    @api_endpoint(CsvPlotRequest)
    def api_csv_trace_data(d):
        # Decimated numeric trace for client-side interactive (uPlot) rendering.
        # No server-side matplotlib render; the browser handles zoom/pan locally.
        return jsonify(viewer_service.trace_data_payload(d))

    @app.route("/api/csv/merge", methods=["POST"])
    @api_endpoint(CsvMergeRequest)
    def api_csv_merge(d):
        return api_ok(viewer_service.merge_preview_payload(d))

    @app.route("/api/csv/merge_job", methods=["POST"])
    @api_endpoint(CsvMergeRequest)
    def api_csv_merge_job(body):
        return _submit_csv_job(
            "csv.merge_preview",
            "Merge CSV preview",
            _csv_merge_task,
            body,
            "/api/csv/merge",
        )

    @app.route("/api/csv/export_merge", methods=["POST"])
    @api_endpoint(CsvMergeRequest)
    def api_csv_export_merge(d):
        mode = d.get("mode", "download")
        export = viewer_service.merge_export_payload(d)
        if _mode_is_save(mode):
            result = _write_payload_result(export, "merged_csv")
            return api_ok(result, outputs=result["outputs"])

        return Response(
            export["payload"],
            mimetype="text/csv",
            headers={"Content-Disposition": attachment_content_disposition(export["out_name"])},
        )

    @app.route("/api/csv/export_merge_job", methods=["POST"])
    @api_endpoint(CsvMergeRequest)
    def api_csv_export_merge_job(body):
        return _submit_csv_job(
            "csv.export_merge",
            "Export merged CSV",
            _csv_export_merge_task,
            body,
            "/api/csv/export_merge",
        )

    @app.route("/api/csv/export")
    @api_endpoint(CsvExportRequest, source="query")
    def api_csv_export(d):
        mode = d.get("mode", "download")
        if _mode_is_save(mode):
            return err("Saving CSV plot exports requires POST /api/csv/export_job.", 405)
        export = viewer_service.plot_export_payload(d)
        return Response(
            export["payload"],
            mimetype=export["mimetype"],
            headers={"Content-Disposition": attachment_content_disposition(export["download_name"])},
        )

    @app.route("/api/csv/export_job", methods=["POST"])
    @api_endpoint(CsvExportRequest)
    def api_csv_export_job(body):
        return _submit_csv_job(
            "csv.export_plot",
            "Export CSV plot",
            _csv_export_plot_task,
            body,
            "/api/csv/export",
        )

    @app.route("/api/csv/export_csv")
    @api_endpoint(CsvFullExportRequest, source="query")
    def api_csv_export_csv(d):
        mode = d.get("mode", "download")
        if _mode_is_save(mode):
            return err("Saving full CSV exports requires POST /api/csv/export_csv_job.", 405)
        export = viewer_service.full_csv_export_payload(d)
        return Response(
            export["payload"],
            mimetype="text/csv",
            headers={"Content-Disposition": attachment_content_disposition(export["download_name"])},
        )

    @app.route("/api/csv/export_csv_job", methods=["POST"])
    @api_endpoint(CsvFullExportRequest)
    def api_csv_export_csv_job(body):
        return _submit_csv_job(
            "csv.export_full",
            "Export full CSV",
            _csv_export_full_task,
            body,
            "/api/csv/export_csv",
        )
