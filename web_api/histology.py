from __future__ import annotations

import traceback

from flask import jsonify, request

from services import histology as histology_service

from .jobs import route_response_to_payload, submit_json_task

sanitize_name = histology_service.sanitize_name
find_histology_cases = histology_service.find_histology_cases
load_histology_preview_pair = histology_service.load_histology_preview_pair
rename_histology_case = histology_service.rename_histology_case
sync_qupath_names_from_histology_cases = histology_service.sync_qupath_names_from_histology_cases
parse_bool = histology_service.parse_bool
normalize_rotate_deg = histology_service.normalize_rotate_deg


def register_histology_routes(app, ctx):
    err = ctx["err"]
    jobs = ctx.get("jobs")

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    @app.route("/api/histology/browse", methods=["POST"])
    def api_histology_browse():
        try:
            d = request.json or {}
            root = d.get("folder", "")
            cases = find_histology_cases(root)
            return jsonify({"cases": cases})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/preview", methods=["POST"])
    def api_histology_preview():
        try:
            d = request.json or {}
            case_path = d.get("case_path", "")
            if not case_path:
                return err("case_path is required")
            rotate_deg = normalize_rotate_deg(d.get("rotate_deg", 0))
            do_ocr = parse_bool(d.get("do_ocr", False), default=False)
            ocr_lang = str(d.get("ocr_lang", "eng") or "eng")
            payload = load_histology_preview_pair(case_path, rotate_deg=rotate_deg, do_ocr=do_ocr, ocr_lang=ocr_lang)
            if payload.get("error"):
                return err(payload["error"])
            return jsonify(payload)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/rename", methods=["POST"])
    def api_histology_rename(payload=None):
        try:
            d = (request.json or {}) if payload is None else payload
            case_path = d.get("case_path", "")
            new_name = d.get("new_name", "")
            update_server_json = parse_bool(d.get("update_server_json", True), default=True)
            qupath_project = d.get("qupath_project", None)
            if not case_path:
                return err("case_path is required")
            result = rename_histology_case(
                case_path,
                new_name,
                update_server_json=update_server_json,
                qupath_project=qupath_project,
            )
            return jsonify({"ok": True, **result})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/rename_job", methods=["POST"])
    def api_histology_rename_job():
        return submit_json_task(
            jobs,
            "histology.rename",
            "Rename histology case",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_rename, "Renaming histology case"
            ),
            request.json or {},
            metadata={"endpoint": "/api/histology/rename"},
        )

    @app.route("/api/histology/sync_qupath_names", methods=["POST"])
    def api_histology_sync_qupath_names(payload=None):
        try:
            d = (request.json or {}) if payload is None else payload
            qupath_project = d.get("qupath_project", None)
            update_server_json = parse_bool(d.get("update_server_json", True), default=True)
            cases = d.get("cases")
            if not isinstance(cases, list) or not cases:
                folder = str(d.get("folder", "") or "").strip()
                if not folder:
                    return err("cases (or folder) is required")
                cases = find_histology_cases(folder)
            if not qupath_project:
                return err("qupath_project is required")
            result = sync_qupath_names_from_histology_cases(
                cases,
                qupath_project,
                update_server_json=update_server_json,
            )
            return jsonify({"ok": True, **result})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/sync_qupath_names_job", methods=["POST"])
    def api_histology_sync_qupath_names_job():
        return submit_json_task(
            jobs,
            "histology.sync_qupath_names",
            "Sync histology QuPath names",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_sync_qupath_names, "Syncing histology QuPath names"
            ),
            request.json or {},
            metadata={"endpoint": "/api/histology/sync_qupath_names"},
        )
