from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import jsonify, request

from services import run_history as run_history_service

from .jobs import submit_json_task


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
    def api_run_history_record():
        return _json_or_error(run_history_service.record_run, request.json or {}, base_dir)

    @app.route("/api/run_history/list", methods=["POST"])
    def api_run_history_list():
        return _json_or_error(run_history_service.list_runs, request.json or {}, base_dir)

    @app.route("/api/run_history/get", methods=["POST"])
    def api_run_history_get():
        return _json_or_error(run_history_service.get_run_manifest, request.json or {}, base_dir)

    @app.route("/api/run_history/check", methods=["POST"])
    def api_run_history_check():
        return _json_or_error(run_history_service.check_run_manifest, request.json or {}, base_dir)

    @app.route("/api/run_history/report", methods=["POST"])
    def api_run_history_report():
        return _json_or_error(run_history_service.write_run_report, request.json or {}, base_dir)

    def _package_run_history_body(job_ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
        return run_history_service.package_run_manifest(body or {}, base_dir, job_ctx)

    @app.route("/api/run_history/package_job", methods=["POST"])
    def api_run_history_package_job():
        return submit_json_task(
            jobs,
            "run_history.package",
            "Package run manifest",
            _package_run_history_body,
            request.json or {},
            metadata={"endpoint": "/api/run_history/package"},
        )

    @app.route("/api/run_history/package", methods=["POST"])
    def api_run_history_package():
        return _json_or_error(run_history_service.package_run_manifest, request.json or {}, base_dir)
