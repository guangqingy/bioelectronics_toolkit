from __future__ import annotations

from typing import Any

from flask import Response, jsonify
from pydantic import Field

from services import echem_lineshape as lineshape_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .request_validation import (
    OptFloat,
    OptInt,
    RequestModel,
    api_endpoint,
)
from .response import api_ok, attachment_content_disposition


class LineshapeBrowseRequest(RequestModel):
    base_dir: str = ""


class LineshapeSourceBrowseRequest(RequestModel):
    folder: str = ""
    kind: str = "photocurrent"


class LineshapeLoadRequest(RequestModel):
    source_path: str = ""
    source_paths: list[Any] = Field(default_factory=list)
    source_folder: str = ""
    base_dir: str = ""
    material: str = ""
    index_k: OptInt = 1
    kind: str = "photocurrent"
    chambers: Any = Field(default_factory=lambda: [1, 2, 3])
    crop_t0: OptFloat = lineshape_service.DEFAULT_CROP_T0
    crop_t1: OptFloat = lineshape_service.DEFAULT_CROP_T1
    x_offset: OptFloat = 0.0
    y_min: OptFloat = None
    y_max: OptFloat = None


class LineshapePlotRequest(RequestModel):
    samples: list[Any] = Field(default_factory=list)
    selected: list[Any] = Field(default_factory=list)
    crop_t0: OptFloat = lineshape_service.DEFAULT_CROP_T0
    crop_t1: OptFloat = lineshape_service.DEFAULT_CROP_T1
    x_offset: OptFloat = 0.0
    y_min: OptFloat = None
    y_max: OptFloat = None
    kind: str = "photocurrent"


class LineshapeExportAvgRequest(RequestModel):
    source_path: str = ""
    source_paths: list[Any] = Field(default_factory=list)
    source_folder: str = ""
    avg_data: dict[str, Any] = Field(default_factory=dict)
    mode: str = "download"
    base_dir: str = ""
    output_dir: str = ""
    material: str = "material"
    index_k: OptInt = 1
    chambers: Any = ""
    kind: str = "photocurrent"
    dpi: OptInt = 300
    crop_t0: OptFloat = lineshape_service.DEFAULT_CROP_T0
    crop_t1: OptFloat = lineshape_service.DEFAULT_CROP_T1
    x_offset: OptFloat = 0.0
    y_min: OptFloat = None
    y_max: OptFloat = None
    selected_count: OptInt = 0
    selected_segments: list[Any] = Field(default_factory=list)


def register_echem_lineshape_routes(app, ctx):
    fig_to_b64 = ctx.fig_to_b64
    jobs = ctx.jobs

    @app.route("/api/echem/lineshape/browse", methods=["POST"])
    @api_endpoint(LineshapeBrowseRequest, dump=False)
    def api_ls_browse(payload):
        return jsonify({"materials": lineshape_service.list_materials(payload.base_dir)})

    @app.route("/api/echem/lineshape/source_browse", methods=["POST"])
    @api_endpoint(LineshapeSourceBrowseRequest, dump=False)
    def api_ls_source_browse(payload):
        return jsonify({"files": lineshape_service.list_source_files(payload.folder, payload.kind)})

    @app.route("/api/echem/lineshape/load", methods=["POST"])
    @api_endpoint(LineshapeLoadRequest)
    def api_ls_load(body):
        return jsonify(lineshape_service.load_samples_payload(body))

    @app.route("/api/echem/lineshape/plot", methods=["POST"])
    @api_endpoint(LineshapePlotRequest)
    def api_ls_plot(body):
        return jsonify(lineshape_service.plot_payload(body, fig_to_b64))

    @app.route("/api/echem/lineshape/trace_data", methods=["POST"])
    @api_endpoint(LineshapePlotRequest)
    def api_ls_trace_data(body):
        return jsonify(lineshape_service.trace_data_payload(body))

    @app.route("/api/echem/lineshape/export_avg", methods=["POST"])
    @api_endpoint(LineshapeExportAvgRequest)
    def api_ls_export_avg(body):
        if mode_is_save(body.get("mode")):
            result = lineshape_service.export_average_files(body)
            return api_ok(result, outputs=result["outputs"])
        csv_payload = lineshape_service.csv_bytes(body.get("avg_data") or {}, body.get("kind"))
        base_name = lineshape_service.export_base_name(
            body.get("material"),
            body.get("index_k"),
            body.get("kind"),
            body.get("source_paths") or body.get("source_path"),
        )
        name = f"{base_name}.csv"
        return Response(
            csv_payload,
            mimetype="text/csv",
            headers={"Content-Disposition": attachment_content_disposition(name)},
        )

    def _export_task(job_ctx, body: dict[str, Any]) -> dict[str, Any]:
        job_ctx.set_progress(0.2, "Exporting averaged lineshape")
        return lineshape_service.export_average_files(body)

    @app.route("/api/echem/lineshape/export_avg_job", methods=["POST"])
    @api_endpoint(LineshapeExportAvgRequest)
    def api_ls_export_avg_job(body):
        return submit_json_task(
            jobs,
            "echem_lineshape.export_avg",
            "Export echem lineshape average",
            _export_task,
            body,
            metadata={"endpoint": "/api/echem/lineshape/export_avg"},
        )
