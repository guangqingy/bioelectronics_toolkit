from __future__ import annotations

import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any

from flask import jsonify, request

from services import run_history as run_history_service

from .jobs import submit_json_task

_run_lock = run_history_service._run_lock
_MAX_HISTORY = run_history_service._MAX_HISTORY
_now_iso = run_history_service._now_iso
_as_path = run_history_service._as_path
_abs_path = run_history_service._abs_path
_resolve_root = run_history_service._resolve_root
_history_path = run_history_service._history_path
_manifest_dir = run_history_service._manifest_dir
_sanitize_run_id = run_history_service._sanitize_run_id
_default_history = run_history_service._default_history
_read_json = run_history_service._read_json
_write_json = run_history_service._write_json
_json_hash = run_history_service._json_hash
_server_context = run_history_service._server_context
_normalize_file_records = run_history_service._normalize_file_records
_as_string_list = run_history_service._as_string_list
_summary_from_manifest = run_history_service._summary_from_manifest
_load_manifest_from_request = run_history_service._load_manifest_from_request
_check_manifest_files = run_history_service._check_manifest_files
_manifest_markdown = run_history_service._manifest_markdown
_add_records_to_zip = run_history_service._add_records_to_zip


def register_run_history_routes(app, ctx):
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])
    jobs = ctx.get("jobs")

    @app.route("/api/run_history/record", methods=["POST"])
    def api_run_history_record():
        try:
            body = request.json or {}
            if not isinstance(body, dict):
                return err("Missing run history object")

            project_root = _resolve_root(body, base_dir)
            view = str(body.get("view") or "unknown").strip() or "unknown"
            run_id = _sanitize_run_id(body.get("run_id") or f"{view}_{uuid.uuid4().hex[:10]}")
            now = _now_iso()
            manifest_path = _manifest_dir(project_root) / f"{run_id}.json"
            history_path = _history_path(project_root)

            parameters = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            manifest = {
                "version": 2,
                "run_id": run_id,
                "view": view,
                "title": str(body.get("title") or view),
                "status": str(body.get("status") or "ok"),
                "project_root": str(project_root),
                "started_at": str(body.get("started_at") or ""),
                "completed_at": str(body.get("completed_at") or now),
                "profile_name": str(body.get("profile_name") or ""),
                "parameters": parameters,
                "input_files": _normalize_file_records(body.get("input_files"), project_root),
                "outputs": _normalize_file_records(body.get("outputs"), project_root),
                "warnings": _as_string_list(body.get("warnings")),
                "errors": _as_string_list(body.get("errors")),
                "metadata": metadata,
                "source": body.get("source") if isinstance(body.get("source"), dict) else {},
                "recorded_by": _server_context(base_dir),
            }
            manifest["hashes"] = {
                "parameters_sha256": _json_hash(manifest["parameters"]),
                "inputs_sha256": _json_hash(manifest["input_files"]),
                "outputs_sha256": _json_hash(manifest["outputs"]),
            }

            with _run_lock:
                _write_json(manifest_path, manifest)
                history = _read_json(history_path, _default_history(project_root))
                history.setdefault("version", 1)
                history["project_root"] = str(project_root)
                history["updated_at"] = now
                runs = history.get("runs")
                if not isinstance(runs, list):
                    runs = []
                summary = _summary_from_manifest(manifest, manifest_path)
                runs = [r for r in runs if isinstance(r, dict) and r.get("run_id") != run_id]
                history["runs"] = [summary] + runs[: _MAX_HISTORY - 1]
                _write_json(history_path, history)

            return jsonify(
                {
                    "ok": True,
                    "manifest": manifest,
                    "manifest_path": str(manifest_path),
                    "history_path": str(history_path),
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/list", methods=["POST"])
    def api_run_history_list():
        try:
            body = request.json or {}
            project_root = _resolve_root(body, base_dir)
            view = str(body.get("view") or "").strip()
            limit = int(body.get("limit") or 100)
            history_path = _history_path(project_root)
            with _run_lock:
                history = _read_json(history_path, _default_history(project_root))
            runs = history.get("runs") if isinstance(history.get("runs"), list) else []
            if view:
                runs = [r for r in runs if isinstance(r, dict) and r.get("view") == view]
            return jsonify(
                {
                    "ok": True,
                    "history_path": str(history_path),
                    "project_root": str(project_root),
                    "runs": runs[: max(1, min(limit, _MAX_HISTORY))],
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/get", methods=["POST"])
    def api_run_history_get():
        try:
            body = request.json or {}
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            return jsonify({"ok": True, "manifest_path": str(manifest_path or ""), "manifest": manifest})
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/check", methods=["POST"])
    def api_run_history_check():
        try:
            body = request.json or {}
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            check = _check_manifest_files(manifest)
            return jsonify(
                {
                    "ok": True,
                    "manifest_path": str(manifest_path or ""),
                    "run_id": manifest.get("run_id", ""),
                    "check": check,
                }
            )
        except Exception as exc:
            return err(exc)

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

    @app.route("/api/run_history/report", methods=["POST"])
    def api_run_history_report():
        try:
            body = request.json or {}
            include_check = body.get("include_check") is not False
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            check = _check_manifest_files(manifest) if include_check else None
            report_text = _manifest_markdown(manifest, manifest_path, check)
            report_path = None
            if manifest_path:
                report_path = manifest_path.with_suffix(".md")
                report_path.write_text(report_text, encoding="utf-8")
            return jsonify(
                {
                    "ok": True,
                    "manifest_path": str(manifest_path or ""),
                    "report_path": str(report_path or ""),
                    "report": report_text,
                    "check": check,
                }
            )
        except Exception as exc:
            return err(exc)

    def _package_run_history_body(job_ctx, body: dict[str, Any]) -> dict[str, Any]:
        include_inputs = bool(body.get("include_inputs"))
        include_outputs = body.get("include_outputs") is not False
        if job_ctx is not None:
            job_ctx.check_cancelled()
            job_ctx.set_progress(0.08, "Loading run manifest")
        with _run_lock:
            manifest, manifest_path = _load_manifest_from_request(body, base_dir)
        if not manifest:
            return {"ok": False, "error": f"Run manifest not found: {manifest_path or ''}"}

        run_id = _sanitize_run_id(manifest.get("run_id") or "run")
        if manifest_path:
            package_path = manifest_path.with_suffix(".zip")
        else:
            project_root = _abs_path(_as_path(manifest.get("project_root")) or base_dir)
            package_path = _manifest_dir(project_root) / f"{run_id}.zip"
        package_path.parent.mkdir(parents=True, exist_ok=True)

        if job_ctx is not None:
            job_ctx.check_cancelled()
            job_ctx.set_progress(0.22, "Checking files")
        check = _check_manifest_files(manifest)
        report_text = _manifest_markdown(manifest, manifest_path, check)
        index: dict[str, Any] = {
            "run_id": run_id,
            "created_at": _now_iso(),
            "include_inputs": include_inputs,
            "include_outputs": include_outputs,
            "manifest_path": str(manifest_path or ""),
            "included": [],
            "missing": [],
        }
        used = {"manifest.json", "report.md", "package_index.json"}
        tmp = package_path.with_suffix(package_path.suffix + ".tmp")
        if job_ctx is not None:
            job_ctx.check_cancelled()
            job_ctx.set_progress(0.4, "Writing package")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            zf.writestr("report.md", report_text)
            if include_outputs:
                included, missing = _add_records_to_zip(zf, manifest.get("outputs"), "outputs", used)
                index["included"].extend(included)
                index["missing"].extend(missing)
            if include_inputs:
                included, missing = _add_records_to_zip(zf, manifest.get("input_files"), "inputs", used)
                index["included"].extend(included)
                index["missing"].extend(missing)
            zf.writestr("package_index.json", json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, package_path)

        return {
            "ok": True,
            "manifest_path": str(manifest_path or ""),
            "package_path": str(package_path),
            "included_count": len(index["included"]),
            "missing_count": len(index["missing"]),
            "index": index,
            "check": check,
        }

    @app.route("/api/run_history/package", methods=["POST"])
    def api_run_history_package():
        try:
            return jsonify(_package_run_history_body(None, request.json or {}))
        except Exception as exc:
            return err(exc)
