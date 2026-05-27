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

load_histology_data_project = histology_service.load_histology_data_project
rename_histology_data_project_entry = histology_service.rename_histology_data_project_entry
load_histology_data_project_image_preview = histology_service.load_histology_data_project_image_preview
load_histology_data_project_image_region_preview = (
    histology_service.load_histology_data_project_image_region_preview
)
save_histology_data_project_rois = histology_service.save_histology_data_project_rois
analyze_histology_data_project_rois = histology_service.analyze_histology_data_project_rois
load_histology_file_image_preview = histology_service.load_histology_file_image_preview
load_histology_file_image_region_preview = histology_service.load_histology_file_image_region_preview
analyze_histology_file_rois = histology_service.analyze_histology_file_rois
scan_exported_tiff_project = histology_service.scan_exported_tiff_project
create_project_from_exported_tiff = histology_service.create_project_from_exported_tiff
load_histology_preview_pair = histology_service.load_histology_preview_pair


class HistologyTiffProjectScanRequest(RequestModel):
    exported_dir: str = Field(min_length=1)
    raw_dir: str = ""
    analysis_dir: str = ""
    convert_ets: bool = True


class HistologyTiffProjectCreateRequest(RequestModel):
    project_path: str = Field(min_length=1)
    exported_dir: str = Field(min_length=1)
    raw_dir: str = ""
    analysis_dir: str = ""
    name: str = ""
    convert_ets: bool = True


class HistologyDataProjectLoadRequest(RequestModel):
    project_path: str = Field(min_length=1)


class HistologyDataProjectRenameEntryRequest(RequestModel):
    project_path: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class HistologyDataProjectImagePreviewRequest(RequestModel):
    project_path: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    max_side: int = Field(default=1600, ge=256, le=2400)


class HistologyDataProjectImageRegionPreviewRequest(RequestModel):
    project_path: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    max_side: int = Field(default=1800, ge=256, le=2600)


class HistologyDataProjectSaveRoisRequest(RequestModel):
    project_path: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)


class HistologyDataProjectAnalyzeRoisRequest(RequestModel):
    project_path: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class HistologyFileImagePreviewRequest(RequestModel):
    image_path: str = Field(min_length=1)
    max_side: int = Field(default=1600, ge=256, le=2400)


class HistologyFileImageRegionPreviewRequest(RequestModel):
    image_path: str = Field(min_length=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    max_side: int = Field(default=1800, ge=256, le=2600)


class HistologyLabelPreviewRequest(RequestModel):
    overview_path: str = Field(min_length=1)
    rotate_deg: int = Field(default=0, ge=0, le=270)
    do_ocr: bool = True
    ocr_lang: str = "eng"


class HistologyFileAnalyzeRoisRequest(RequestModel):
    image_path: str = Field(min_length=1)
    rois: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


def register_histology_routes(app, ctx):
    err = ctx["err"]
    jobs = ctx.get("jobs")

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    def _job_progress(job_ctx, low: float = 0.05, high: float = 0.92):
        def progress(fraction: float, message: str) -> None:
            job_ctx.check_cancelled()
            span = max(0.0, high - low)
            job_ctx.set_progress(low + span * max(0.0, min(1.0, float(fraction))), message)

        return progress

    @app.route("/api/histology/project/scan_tiff", methods=["POST"])
    @request_schema(HistologyTiffProjectScanRequest)
    def api_histology_project_scan_tiff():
        try:
            payload = parse_json_payload(HistologyTiffProjectScanRequest)
            return jsonify(
                scan_exported_tiff_project(
                    payload.exported_dir,
                    raw_dir=payload.raw_dir,
                    analysis_dir=payload.analysis_dir,
                    convert_ets=payload.convert_ets,
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/project/scan_tiff_job", methods=["POST"])
    @request_schema(HistologyTiffProjectScanRequest)
    def api_histology_project_scan_tiff_job():
        try:
            body = parse_json_payload(HistologyTiffProjectScanRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)

        def task(job_ctx, body: dict) -> dict:
            job_ctx.set_progress(0.02, "Scanning histology source")
            return scan_exported_tiff_project(
                body["exported_dir"],
                raw_dir=body.get("raw_dir", ""),
                analysis_dir=body.get("analysis_dir", ""),
                convert_ets=bool(body.get("convert_ets", True)),
                progress=_job_progress(job_ctx),
            )

        return submit_json_task(
            jobs,
            "histology.project_scan",
            "Scan histology source",
            task,
            body,
            metadata={"endpoint": "/api/histology/project/scan_tiff"},
        )

    @app.route("/api/histology/project/create_from_tiff", methods=["POST"])
    @request_schema(HistologyTiffProjectCreateRequest)
    def api_histology_project_create_from_tiff():
        try:
            payload = parse_json_payload(HistologyTiffProjectCreateRequest)
            return jsonify(
                create_project_from_exported_tiff(
                    payload.project_path,
                    payload.exported_dir,
                    raw_dir=payload.raw_dir,
                    analysis_dir=payload.analysis_dir,
                    name=payload.name,
                    convert_ets=payload.convert_ets,
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/project/create_from_tiff_job", methods=["POST"])
    @request_schema(HistologyTiffProjectCreateRequest)
    def api_histology_project_create_from_tiff_job():
        try:
            body = parse_json_payload(HistologyTiffProjectCreateRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)

        def task(job_ctx, body: dict) -> dict:
            job_ctx.set_progress(0.02, "Creating histology project")
            return create_project_from_exported_tiff(
                body["project_path"],
                body["exported_dir"],
                raw_dir=body.get("raw_dir", ""),
                analysis_dir=body.get("analysis_dir", ""),
                name=body.get("name", ""),
                convert_ets=bool(body.get("convert_ets", True)),
                progress=_job_progress(job_ctx),
            )

        return submit_json_task(
            jobs,
            "histology.project_create",
            "Create histology project",
            task,
            body,
            metadata={"endpoint": "/api/histology/project/create_from_tiff"},
        )

    @app.route("/api/histology/project/load", methods=["POST"])
    @request_schema(HistologyDataProjectLoadRequest)
    def api_histology_project_load():
        try:
            payload = parse_json_payload(HistologyDataProjectLoadRequest)
            return jsonify(load_histology_data_project(payload.project_path))
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/project/rename_entry", methods=["POST"])
    @request_schema(HistologyDataProjectRenameEntryRequest)
    def api_histology_project_rename_entry():
        try:
            payload = parse_json_payload(HistologyDataProjectRenameEntryRequest)
            return jsonify(
                rename_histology_data_project_entry(
                    payload.project_path,
                    payload.entry_id,
                    payload.display_name,
                )
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/project/image_preview", methods=["POST"])
    @request_schema(HistologyDataProjectImagePreviewRequest)
    def api_histology_project_image_preview():
        try:
            payload = parse_json_payload(HistologyDataProjectImagePreviewRequest)
            result = load_histology_data_project_image_preview(
                payload.project_path,
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

    @app.route("/api/histology/project/image_region_preview", methods=["POST"])
    @request_schema(HistologyDataProjectImageRegionPreviewRequest)
    def api_histology_project_image_region_preview():
        try:
            payload = parse_json_payload(HistologyDataProjectImageRegionPreviewRequest)
            result = load_histology_data_project_image_region_preview(
                payload.project_path,
                payload.entry_id,
                payload.x,
                payload.y,
                payload.width,
                payload.height,
                max_side=payload.max_side,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/project/analysis/save_rois", methods=["POST"])
    @request_schema(HistologyDataProjectSaveRoisRequest)
    def api_histology_project_analysis_save_rois(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyDataProjectSaveRoisRequest).model_dump()
            else:
                body = HistologyDataProjectSaveRoisRequest.model_validate(payload).model_dump()
            result = save_histology_data_project_rois(
                body["project_path"],
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

    @app.route("/api/histology/project/analysis/run", methods=["POST"])
    @request_schema(HistologyDataProjectAnalyzeRoisRequest)
    def api_histology_project_analysis_run(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyDataProjectAnalyzeRoisRequest).model_dump()
            else:
                body = HistologyDataProjectAnalyzeRoisRequest.model_validate(payload).model_dump()
            result = analyze_histology_data_project_rois(
                body["project_path"],
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

    @app.route("/api/histology/project/analysis/run_job", methods=["POST"])
    @request_schema(HistologyDataProjectAnalyzeRoisRequest)
    def api_histology_project_analysis_run_job():
        try:
            body = parse_json_payload(HistologyDataProjectAnalyzeRoisRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.project_analysis",
            "Analyze histology project ROIs",
            lambda job_ctx, body: _response_task(
                job_ctx,
                body,
                api_histology_project_analysis_run,
                "Analyzing histology project ROIs",
            ),
            body,
            metadata={"endpoint": "/api/histology/project/analysis/run"},
        )

    @app.route("/api/histology/file/image_preview", methods=["POST"])
    @request_schema(HistologyFileImagePreviewRequest)
    def api_histology_file_image_preview():
        try:
            payload = parse_json_payload(HistologyFileImagePreviewRequest)
            result = load_histology_file_image_preview(
                payload.image_path,
                max_side=payload.max_side,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/file/image_region_preview", methods=["POST"])
    @request_schema(HistologyFileImageRegionPreviewRequest)
    def api_histology_file_image_region_preview():
        try:
            payload = parse_json_payload(HistologyFileImageRegionPreviewRequest)
            result = load_histology_file_image_region_preview(
                payload.image_path,
                payload.x,
                payload.y,
                payload.width,
                payload.height,
                max_side=payload.max_side,
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/label_preview", methods=["POST"])
    @request_schema(HistologyLabelPreviewRequest)
    def api_histology_label_preview():
        try:
            payload = parse_json_payload(HistologyLabelPreviewRequest)
            result = load_histology_preview_pair(
                payload.overview_path,
                rotate_deg=payload.rotate_deg,
                do_ocr=payload.do_ocr,
                ocr_lang=payload.ocr_lang,
            )
            return jsonify({"ok": not bool(result.get("error")), **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/histology/file/analysis/run", methods=["POST"])
    @request_schema(HistologyFileAnalyzeRoisRequest)
    def api_histology_file_analysis_run(payload=None):
        try:
            if payload is None:
                body = parse_json_payload(HistologyFileAnalyzeRoisRequest).model_dump()
            else:
                body = HistologyFileAnalyzeRoisRequest.model_validate(payload).model_dump()
            result = analyze_histology_file_rois(
                body["image_path"],
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

    @app.route("/api/histology/file/analysis/run_job", methods=["POST"])
    @request_schema(HistologyFileAnalyzeRoisRequest)
    def api_histology_file_analysis_run_job():
        try:
            body = parse_json_payload(HistologyFileAnalyzeRoisRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "histology.file_analysis",
            "Analyze histology file ROIs",
            lambda job_ctx, body: _response_task(
                job_ctx,
                body,
                api_histology_file_analysis_run,
                "Analyzing histology file ROIs",
            ),
            body,
            metadata={"endpoint": "/api/histology/file/analysis/run"},
        )
