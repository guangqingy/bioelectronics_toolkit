from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from services import run_history as run_history_service

from .jobs import submit_json_task
from .request_validation import RequestModel, parse_json_payload, request_schema, validation_error_response


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
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])
    jobs = ctx.get("jobs")

    def _json_or_error(func, *args):
        try:
            return jsonify(func(*args))
        except FileNotFoundError as exc:
            return err(str(exc), 404)
        except ValueError as exc:
            return err(str(exc), 400)
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/record", methods=["POST"])
    @request_schema(RunHistoryRecordRequest)
    def api_run_history_record():
        try:
            payload = parse_json_payload(RunHistoryRecordRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.record_run, payload.model_dump(), base_dir)

    @app.route("/api/run_history/list", methods=["POST"])
    @request_schema(RunHistoryListRequest)
    def api_run_history_list():
        try:
            payload = parse_json_payload(RunHistoryListRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.list_runs, payload.model_dump(), base_dir)

    @app.route("/api/run_history/get", methods=["POST"])
    @request_schema(RunManifestRequest)
    def api_run_history_get():
        try:
            payload = parse_json_payload(RunManifestRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.get_run_manifest, payload.model_dump(), base_dir)

    @app.route("/api/run_history/check", methods=["POST"])
    @request_schema(RunManifestRequest)
    def api_run_history_check():
        try:
            payload = parse_json_payload(RunManifestRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.check_run_manifest, payload.model_dump(), base_dir)

    @app.route("/api/run_history/report", methods=["POST"])
    @request_schema(RunReportRequest)
    def api_run_history_report():
        try:
            payload = parse_json_payload(RunReportRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.write_run_report, payload.model_dump(), base_dir)

    def _package_run_history_body(job_ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        return run_history_service.package_run_manifest(body or {}, base_dir, job_ctx)

    @app.route("/api/run_history/package_job", methods=["POST"])
    @request_schema(RunPackageRequest)
    def api_run_history_package_job():
        try:
            payload = parse_json_payload(RunPackageRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "run_history.package",
            "Package run manifest",
            _package_run_history_body,
            payload.model_dump(),
            metadata={"endpoint": "/api/run_history/package"},
        )

    @app.route("/api/run_history/package", methods=["POST"])
    @request_schema(RunPackageRequest)
    def api_run_history_package():
        try:
            payload = parse_json_payload(RunPackageRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(run_history_service.package_run_manifest, payload.model_dump(), base_dir)
