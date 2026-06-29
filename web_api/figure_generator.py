from __future__ import annotations

from typing import Any

from flask import jsonify
from pydantic import Field

from services.figure_generator import browse_payload, preview_payload, run_payload

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    api_endpoint,
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
    fig_to_b64 = ctx.fig_to_b64
    jobs = ctx.jobs

    def _run_job(job_ctx, body: dict[str, Any]) -> dict[str, Any]:
        job_ctx.set_progress(0.2, "Running figure export")
        return run_payload(body or {})

    @app.route("/api/figure/browse", methods=["POST"])
    @api_endpoint(FigureBrowseRequest)
    def api_figure_browse(data):
        return jsonify(browse_payload(data.get("folder", "")))

    @app.route("/api/figure/plot", methods=["POST"])
    @api_endpoint(FigurePlotRequest)
    def api_figure_plot(data):
        return jsonify(preview_payload(data, fig_to_b64))

    @app.route("/api/figure/run", methods=["POST"])
    @api_endpoint(FigureRunRequest)
    def api_figure_run(data):
        return jsonify(run_payload(data))

    @app.route("/api/figure/run_job", methods=["POST"])
    @api_endpoint(FigureRunRequest)
    def api_figure_run_job(body):
        return submit_json_task(
            jobs,
            "figure.run",
            "Run figure export",
            _run_job,
            body,
            metadata={"endpoint": "/api/figure/run"},
        )
