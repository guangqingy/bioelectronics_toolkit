from __future__ import annotations

import traceback
from typing import Any

from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import echem_lineshape as lineshape_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class LineshapeBrowseRequest(RequestModel):
    base_dir: str = ""


class LineshapeLoadRequest(RequestModel):
    source_path: str = ""
    base_dir: str = ""
    material: str = ""
    index_k: Any = 1
    kind: str = "photocurrent"
    chambers: Any = Field(default_factory=lambda: [1, 2, 3])
    crop_t0: Any = lineshape_service.DEFAULT_CROP_T0
    crop_t1: Any = lineshape_service.DEFAULT_CROP_T1
    x_offset: Any = 0.0
    y_min: Any = None
    y_max: Any = None


class LineshapePlotRequest(RequestModel):
    samples: list[Any] = Field(default_factory=list)
    selected: list[Any] = Field(default_factory=list)
    crop_t0: Any = lineshape_service.DEFAULT_CROP_T0
    crop_t1: Any = lineshape_service.DEFAULT_CROP_T1
    x_offset: Any = 0.0
    y_min: Any = None
    y_max: Any = None
    kind: str = "photocurrent"


class LineshapeExportAvgRequest(RequestModel):
    source_path: str = ""
    avg_data: dict[str, Any] = Field(default_factory=dict)
    mode: str = "download"
    base_dir: str = ""
    output_dir: str = ""
    material: str = "material"
    index_k: Any = 1
    chambers: Any = ""
    kind: str = "photocurrent"
    dpi: Any = 300
    crop_t0: Any = lineshape_service.DEFAULT_CROP_T0
    crop_t1: Any = lineshape_service.DEFAULT_CROP_T1
    x_offset: Any = 0.0
    y_min: Any = None
    y_max: Any = None
    selected_count: Any = 0


def register_echem_lineshape_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    jobs = ctx.get("jobs")

    @app.route("/api/echem/lineshape/browse", methods=["POST"])
    @request_schema(LineshapeBrowseRequest)
    def api_ls_browse():
        try:
            payload = parse_json_payload(LineshapeBrowseRequest)
            return jsonify({"materials": lineshape_service.list_materials(payload.base_dir)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/lineshape/load", methods=["POST"])
    @request_schema(LineshapeLoadRequest)
    def api_ls_load():
        try:
            body = parse_json_payload(LineshapeLoadRequest).model_dump()
            return jsonify(lineshape_service.load_samples_payload(body))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/lineshape/plot", methods=["POST"])
    @request_schema(LineshapePlotRequest)
    def api_ls_plot():
        try:
            body = parse_json_payload(LineshapePlotRequest).model_dump()
            return jsonify(lineshape_service.plot_payload(body, fig_to_b64))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/lineshape/export_avg", methods=["POST"])
    @request_schema(LineshapeExportAvgRequest)
    def api_ls_export_avg():
        try:
            body = parse_json_payload(LineshapeExportAvgRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        try:
            if mode_is_save(body.get("mode")):
                result = lineshape_service.export_average_files(body)
                return api_ok(result, outputs=result["outputs"])
            csv_payload = lineshape_service.csv_bytes(body.get("avg_data") or {}, body.get("kind"))
            base_name = lineshape_service.export_base_name(
                body.get("material"),
                body.get("index_k"),
                body.get("kind"),
                body.get("source_path"),
            )
            name = f"{base_name}.csv"
            return Response(
                csv_payload,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={name}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    def _export_task(job_ctx, body: dict[str, Any]) -> dict[str, Any]:
        job_ctx.set_progress(0.2, "Exporting averaged lineshape")
        return lineshape_service.export_average_files(body)

    @app.route("/api/echem/lineshape/export_avg_job", methods=["POST"])
    @request_schema(LineshapeExportAvgRequest)
    def api_ls_export_avg_job():
        try:
            body = parse_json_payload(LineshapeExportAvgRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_lineshape.export_avg",
            "Export echem lineshape average",
            _export_task,
            body,
            metadata={"endpoint": "/api/echem/lineshape/export_avg"},
        )
