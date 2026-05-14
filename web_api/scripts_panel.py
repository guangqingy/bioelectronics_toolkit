import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from pipelines.registry import find_pipeline_script, pipeline_catalog, validate_registry
from services import scripts_panel as script_service

from .request_validation import RequestModel, parse_json_payload, request_schema, validation_error_response


class ScriptRunRequest(RequestModel):
    script_id: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    cat: str = ""


class ScriptStatusRequest(RequestModel):
    job_id: str = Field(min_length=1)


class ScriptOpenFolderRequest(RequestModel):
    path: str = Field(min_length=1)


def register_scripts_panel_routes(app, ctx):
    err = ctx["err"]
    base_dir = ctx["BASE_DIR"]
    jobs = ctx.get("jobs")

    def _script_status_payload(job_id, fallback_output_dir=""):
        return script_service.script_status_payload(job_id, jobs, fallback_output_dir)

    @app.route("/api/pipelines/catalog")
    def api_pipeline_catalog():
        catalog = pipeline_catalog(base_dir, include_availability=True)
        return jsonify(
            {
                "ok": True,
                "catalog": catalog,
                "categories": catalog.get("categories", []),
                "errors": validate_registry(catalog),
            }
        )

    @app.route("/api/scripts/run", methods=["POST"])
    @request_schema(ScriptRunRequest)
    def api_scripts_run():
        """
        For analysis scripts: attempt to run the script with modified parameters
        injected via environment variables (DP_BASE_DIR, DP_OUTPUT_DIR, etc.).
        """
        try:
            payload = parse_json_payload(ScriptRunRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        script_id = payload.script_id
        params = payload.params

        script_entry = find_pipeline_script(script_id, base_dir)
        if not script_entry:
            return err(f"Unknown pipeline script: {script_id}", 404)

        script_path = (
            Path(script_entry["resolved_script_path"])
            if script_entry.get("available") and script_entry.get("resolved_script_path")
            else None
        )

        if script_path and script_path.exists():
            out_dir = script_service.resolve_output_dir(script_path, params)
            if not jobs:
                return err("Job manager is not available")
            job = jobs.submit(
                "script",
                f"Pipeline script: {script_id}",
                script_service.run_script_job,
                str(script_path),
                params,
                out_dir,
                metadata={
                    "script_id": script_id,
                    "category": script_entry.get("category") or payload.cat,
                    "script_path": str(script_path),
                    "output_dir": str(out_dir or ""),
                },
            )
            job_id = job["job_id"]
            deadline = time.time() + 15
            result = _script_status_payload(job_id, str(out_dir or "")) or {}
            while time.time() < deadline and not result.get("done"):
                time.sleep(0.15)
                result = _script_status_payload(job_id, str(out_dir or "")) or {}

            if result.get("done"):
                artifacts = result.get("artifacts", [])
                figures = script_service.figures_from_artifacts(artifacts)
                return jsonify(
                    {
                        "message": result.get("stdout", "Done")[:500],
                        "ok": result.get("ok", False),
                        "stderr": result.get("stderr", "")[:1000],
                        "figures": figures,
                        "output_dir": result.get("output_dir", str(out_dir) if out_dir else ""),
                        "artifacts": artifacts,
                        "job_id": job_id,
                    }
                )

            return jsonify(
                {
                    "message": "Script running in background (>15s). Check output folder.",
                    "ok": True,
                    "figures": [],
                    "output_dir": str(out_dir) if out_dir else "",
                    "artifacts": [],
                    "job_id": job_id,
                    "running": True,
                }
            )

        return jsonify(
            {
                "message": (
                    f"Pipeline script '{script_entry.get('name', script_id)}' is registered, "
                    "but the local project script is not available in this checkout."
                ),
                "ok": False,
                "missing": True,
                "script_id": script_id,
                "script_path": script_entry.get("script_path", ""),
                "documentation": script_entry.get("category_documentation", ""),
                "availability_message": script_entry.get("availability_message", ""),
                "config_preview": json.dumps(params, indent=2),
                "figures": [],
                "artifacts": [],
            }
        )

    @app.route("/api/scripts/status", methods=["POST"])
    @request_schema(ScriptStatusRequest)
    def api_scripts_status():
        try:
            payload = parse_json_payload(ScriptStatusRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        job_id = payload.job_id
        result = _script_status_payload(job_id)
        if not result:
            return err(f"Unknown script job: {job_id}", 404)
        artifacts = result.get("artifacts", [])
        return jsonify(
            {
                "job_id": job_id,
                "done": bool(result.get("done")),
                "ok": bool(result.get("ok", False)) if result.get("done") else True,
                "message": (result.get("message") or result.get("stdout", ""))[:500],
                "stderr": result.get("stderr", "")[:1000],
                "output_dir": result.get("output_dir", ""),
                "artifacts": artifacts,
                "figures": script_service.figures_from_artifacts(artifacts) if result.get("done") else [],
                "job_status": result.get("job_status", ""),
                "progress": result.get("progress"),
            }
        )

    @app.route("/api/scripts/open_folder", methods=["POST"])
    @request_schema(ScriptOpenFolderRequest)
    def api_scripts_open_folder():
        try:
            payload = parse_json_payload(ScriptOpenFolderRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        path = payload.path
        p = Path(path)
        if p.is_dir():
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(p)])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(p)])
                else:
                    subprocess.Popen(["explorer", str(p)])
                return jsonify({"ok": True})
            except Exception as e:
                return err(e)
        return err(f"Not a directory: {path}")
