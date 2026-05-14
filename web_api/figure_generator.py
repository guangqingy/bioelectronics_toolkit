from __future__ import annotations

import traceback
from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from services.figure_generator import browse_payload, preview_payload, run_payload

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)


class FigureBrowseRequest(RequestModel):
    folder: str = ""


class FigurePlotRequest(RequestModel):
    main_folder: str = ""
    output_name: str = ""
    queue: list[Any] = Field(default_factory=list)
    metrics: Any = None
    metric: str = ""
    use_peak: bool = True
    use_integral: bool = True
    x_lin_ranges: Any = ""
    x_log_ranges: Any = ""


class FigureRunRequest(FigurePlotRequest):
    action: str = "analyze"


def register_figure_generator_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    jobs = ctx.get("jobs")

    def _json(schema):
        return parse_json_payload(schema).model_dump()

    def _json_response(schema, handler):
        try:
            return jsonify(handler(_json(schema)))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    def _run_job(job_ctx, body: dict[str, Any]) -> dict[str, Any]:
        job_ctx.set_progress(0.2, "Running figure export")
        return run_payload(body or {})

    @app.route("/api/figure/browse", methods=["POST"])
    @request_schema(FigureBrowseRequest)
    def api_figure_browse():
        return _json_response(
            FigureBrowseRequest,
            lambda data: browse_payload(data.get("folder", "")),
        )

    @app.route("/api/figure/plot", methods=["POST"])
    @request_schema(FigurePlotRequest)
    def api_figure_plot():
        return _json_response(FigurePlotRequest, lambda data: preview_payload(data, fig_to_b64))

    @app.route("/api/figure/run", methods=["POST"])
    @request_schema(FigureRunRequest)
    def api_figure_run():
        return _json_response(FigureRunRequest, run_payload)

    @app.route("/api/figure/run_job", methods=["POST"])
    @request_schema(FigureRunRequest)
    def api_figure_run_job():
        try:
            body = _json(FigureRunRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "figure.run",
            "Run figure export",
            _run_job,
            body,
            metadata={"endpoint": "/api/figure/run"},
        )
