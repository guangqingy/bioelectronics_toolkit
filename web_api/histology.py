from __future__ import annotations

import traceback
from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from services import histology as histology_service

from .jobs import route_response_to_payload, submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)

sanitize_name = histology_service.sanitize_name
find_histology_cases = histology_service.find_histology_cases
load_histology_preview_pair = histology_service.load_histology_preview_pair
rename_histology_case = histology_service.rename_histology_case
sync_qupath_names_from_histology_cases = histology_service.sync_qupath_names_from_histology_cases
load_ets_project = histology_service.load_ets_project
load_ets_image_preview = histology_service.load_ets_image_preview
save_ets_rois = histology_service.save_ets_rois
analyze_ets_rois = histology_service.analyze_ets_rois
load_qupath_project = histology_service.load_qupath_project
load_project_image_preview = histology_service.load_project_image_preview
save_project_rois = histology_service.save_project_rois
analyze_project_rois = histology_service.analyze_project_rois
parse_bool = histology_service.parse_bool
normalize_rotate_deg = histology_service.normalize_rotate_deg


class HistologyBrowseRequest(RequestModel):
    folder: str = ""


class HistologyPreviewRequest(RequestModel):
    case_path: str = Field(min_length=1)
    rotate_deg: Any = 0
    do_ocr: Any = False
    ocr_lang: str = "eng"


class HistologyRenameRequest(RequestModel):
    case_path: str = Field(min_length=1)
    new_name: str = ""
    update_server_json: Any = True
    qupath_project: Any = None


class HistologySyncQupathNamesRequest(RequestModel):
    qupath_project: Any = None
    update_server_json: Any = True
    cases: list[Any] | None = None
    folder: str = ""


class HistologyQupathProjectRequest(RequestModel):
    qupath_project: str = Field(min_length=1)


class HistologyEtsProjectRequest(RequestModel):
    folder: str = Field(min_length=1)


class HistologyEtsImagePreviewRequest(RequestModel):
    folder: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    max_side: int = Field(default=1600, ge=256, le=2400)


class HistologyEtsSaveRoisRequest(RequestModel):
    folder: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)


class HistologyEtsAnalyzeRoisRequest(RequestModel):
    folder: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class HistologyQupathImagePreviewRequest(RequestModel):
    qupath_project: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    max_side: int = Field(default=1600, ge=256, le=2400)


class HistologySaveRoisRequest(RequestModel):
    qupath_project: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)


class HistologyAnalyzeRoisRequest(RequestModel):
    qupath_project: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


def register_histology_routes(app, ctx):
    err = ctx["err"]
    jobs = ctx.get("jobs")

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    @app.route("/api/histology/browse", methods=["POST"])
    @request_schema(HistologyBrowseRequest)
    def api_histology_browse():
        try:
            root = parse_json_payload(HistologyBrowseRequest).folder
            cases = find_histology_cases(root)
            return jsonify({"cases": cases})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/preview", methods=["POST"])
    @request_schema(HistologyPreviewRequest)
    def api_histology_preview():
        try:
            d = parse_json_payload(HistologyPreviewRequest).model_dump()
            case_path = d.get("case_path", "")
            rotate_deg = normalize_rotate_deg(d.get("rotate_deg", 0))
            do_ocr = parse_bool(d.get("do_ocr", False), default=False)
            ocr_lang = str(d.get("ocr_lang", "eng") or "eng")
            payload = load_histology_preview_pair(
                case_path, rotate_deg=rotate_deg, do_ocr=do_ocr, ocr_lang=ocr_lang
            )
            if payload.get("error"):
                return err(payload["error"])
            return jsonify(payload)
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/rename", methods=["POST"])
    @request_schema(HistologyRenameRequest)
    def api_histology_rename(payload=None):
        try:
            if payload is None:
                d = parse_json_payload(HistologyRenameRequest).model_dump()
            else:
                d = HistologyRenameRequest.model_validate(payload).model_dump()
            case_path = d.get("case_path", "")
            new_name = d.get("new_name", "")
            update_server_json = parse_bool(d.get("update_server_json", True), default=True)
            qupath_project = d.get("qupath_project", None)
            result = rename_histology_case(
                case_path,
                new_name,
                update_server_json=update_server_json,
                qupath_project=qupath_project,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/rename_job", methods=["POST"])
    @request_schema(HistologyRenameRequest)
    def api_histology_rename_job():
        try:
            body = parse_json_payload(HistologyRenameRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.rename",
            "Rename histology case",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_rename, "Renaming histology case"
            ),
            body,
            metadata={"endpoint": "/api/histology/rename"},
        )

    @app.route("/api/histology/sync_qupath_names", methods=["POST"])
    @request_schema(HistologySyncQupathNamesRequest)
    def api_histology_sync_qupath_names(payload=None):
        try:
            if payload is None:
                d = parse_json_payload(HistologySyncQupathNamesRequest).model_dump()
            else:
                d = HistologySyncQupathNamesRequest.model_validate(payload).model_dump()
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
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/sync_qupath_names_job", methods=["POST"])
    @request_schema(HistologySyncQupathNamesRequest)
    def api_histology_sync_qupath_names_job():
        try:
            body = parse_json_payload(HistologySyncQupathNamesRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.sync_qupath_names",
            "Sync histology QuPath names",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_sync_qupath_names, "Syncing histology QuPath names"
            ),
            body,
            metadata={"endpoint": "/api/histology/sync_qupath_names"},
        )

    @app.route("/api/histology/ets_project", methods=["POST"])
    @request_schema(HistologyEtsProjectRequest)
    def api_histology_ets_project():
        try:
            payload = parse_json_payload(HistologyEtsProjectRequest)
            return jsonify(load_ets_project(payload.folder))
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/ets_image_preview", methods=["POST"])
    @request_schema(HistologyEtsImagePreviewRequest)
    def api_histology_ets_image_preview():
        try:
            payload = parse_json_payload(HistologyEtsImagePreviewRequest)
            result = load_ets_image_preview(
                payload.folder,
                payload.entry_id,
                max_side=payload.max_side,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/ets_analysis/save_rois", methods=["POST"])
    @request_schema(HistologyEtsSaveRoisRequest)
    def api_histology_ets_analysis_save_rois(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyEtsSaveRoisRequest).model_dump()
            else:
                body = HistologyEtsSaveRoisRequest.model_validate(payload).model_dump()
            result = save_ets_rois(
                body["folder"],
                body["entry_id"],
                body.get("rois", []),
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/ets_analysis/run", methods=["POST"])
    @request_schema(HistologyEtsAnalyzeRoisRequest)
    def api_histology_ets_analysis_run(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyEtsAnalyzeRoisRequest).model_dump()
            else:
                body = HistologyEtsAnalyzeRoisRequest.model_validate(payload).model_dump()
            result = analyze_ets_rois(
                body["folder"],
                body["entry_id"],
                body.get("rois", []),
                parameters=body.get("parameters", {}),
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/ets_analysis/run_job", methods=["POST"])
    @request_schema(HistologyEtsAnalyzeRoisRequest)
    def api_histology_ets_analysis_run_job():
        try:
            body = parse_json_payload(HistologyEtsAnalyzeRoisRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.ets_analysis",
            "Analyze histology ETS ROIs",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_ets_analysis_run, "Analyzing histology ETS ROIs"
            ),
            body,
            metadata={"endpoint": "/api/histology/ets_analysis/run"},
        )

    @app.route("/api/histology/qupath_project", methods=["POST"])
    @request_schema(HistologyQupathProjectRequest)
    def api_histology_qupath_project():
        try:
            payload = parse_json_payload(HistologyQupathProjectRequest)
            return jsonify({"ok": True, **load_qupath_project(payload.qupath_project)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/qupath_image_preview", methods=["POST"])
    @request_schema(HistologyQupathImagePreviewRequest)
    def api_histology_qupath_image_preview():
        try:
            payload = parse_json_payload(HistologyQupathImagePreviewRequest)
            result = load_project_image_preview(
                payload.qupath_project,
                payload.entry_id,
                max_side=payload.max_side,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/analysis/save_rois", methods=["POST"])
    @request_schema(HistologySaveRoisRequest)
    def api_histology_analysis_save_rois(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologySaveRoisRequest).model_dump()
            else:
                body = HistologySaveRoisRequest.model_validate(payload).model_dump()
            result = save_project_rois(
                body["qupath_project"],
                body["entry_id"],
                body.get("rois", []),
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/analysis/run", methods=["POST"])
    @request_schema(HistologyAnalyzeRoisRequest)
    def api_histology_analysis_run(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyAnalyzeRoisRequest).model_dump()
            else:
                body = HistologyAnalyzeRoisRequest.model_validate(payload).model_dump()
            result = analyze_project_rois(
                body["qupath_project"],
                body["entry_id"],
                body.get("rois", []),
                parameters=body.get("parameters", {}),
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/analysis/run_job", methods=["POST"])
    @request_schema(HistologyAnalyzeRoisRequest)
    def api_histology_analysis_run_job():
        try:
            body = parse_json_payload(HistologyAnalyzeRoisRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.analysis",
            "Analyze histology ROIs",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_histology_analysis_run, "Analyzing histology ROIs"
            ),
            body,
            metadata={"endpoint": "/api/histology/analysis/run"},
        )
