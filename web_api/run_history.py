from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field

from services import run_history as run_history_service

from .jobs import submit_json_task
from .request_validation import RequestModel, api_endpoint


class RunHistoryRecordRequest(RequestModel):
    project_root: str = ""
    run_id: str = ""
    view: str = ""
    title: str = ""
    status: str = "ok"
    started_at: str = ""
    completed_at: str = ""
    profile_name: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_files: list[Any] = Field(default_factory=list)
    outputs: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    errors: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)


class RunHistoryListRequest(RequestModel):
    project_root: str = ""
    view: str = ""
    limit: int = Field(default=100, ge=1, le=1000)


class RunManifestRequest(RequestModel):
    manifest: dict[str, Any] | None = None
    manifest_path: str = ""
    project_root: str = ""
    run_id: str = ""


class RunReportRequest(RunManifestRequest):
    include_check: bool = True


class RunPackageRequest(RunManifestRequest):
    include_inputs: bool = False
    include_outputs: bool = True


def register_run_history_routes(app, ctx):
    err = ctx.err
    base_dir = Path(ctx.BASE_DIR)
    jobs = ctx.jobs

    def _json_or_error(func, *args):
        try:
            return jsonify(func(*args))
        except FileNotFoundError as exc:
            return err(str(exc), 404)
        except ValueError as exc:
            return err(str(exc), 400)

    @app.route("/api/run_history/record", methods=["POST"])
    @api_endpoint(RunHistoryRecordRequest)
    def api_run_history_record(body):
        return _json_or_error(run_history_service.record_run, body, base_dir)

    @app.route("/api/run_history/list", methods=["POST"])
    @api_endpoint(RunHistoryListRequest)
    def api_run_history_list(body):
        return _json_or_error(run_history_service.list_runs, body, base_dir)

    @app.route("/api/run_history/get", methods=["POST"])
    @api_endpoint(RunManifestRequest)
    def api_run_history_get(body):
        return _json_or_error(run_history_service.get_run_manifest, body, base_dir)

    @app.route("/api/run_history/check", methods=["POST"])
    @api_endpoint(RunManifestRequest)
    def api_run_history_check(body):
        return _json_or_error(run_history_service.check_run_manifest, body, base_dir)

    @app.route("/api/run_history/report", methods=["POST"])
    @api_endpoint(RunReportRequest)
    def api_run_history_report(body):
        return _json_or_error(run_history_service.write_run_report, body, base_dir)

    def _package_run_history_body(job_ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        return run_history_service.package_run_manifest(body or {}, base_dir, job_ctx)

    @app.route("/api/run_history/package_job", methods=["POST"])
    @api_endpoint(RunPackageRequest)
    def api_run_history_package_job(body):
        return submit_json_task(
            jobs,
            "run_history.package",
            "Package run manifest",
            _package_run_history_body,
            body,
            metadata={"endpoint": "/api/run_history/package"},
        )

    @app.route("/api/run_history/package", methods=["POST"])
    @api_endpoint(RunPackageRequest)
    def api_run_history_package(body):
        return _json_or_error(run_history_service.package_run_manifest, body, base_dir)
