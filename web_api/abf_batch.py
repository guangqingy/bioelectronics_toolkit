import traceback
from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from services import abf_batch as abf_batch_service

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok

ROOT_DIR = Path(__file__).resolve().parents[1]


class AbfBatchBrowseRequest(RequestModel):
    folder: str = ""


class AbfBatchScanTokensRequest(RequestModel):
    files: list[Any] = Field(default_factory=list)


class AbfBatchProcessRequest(RequestModel):
    folder: str = Field(min_length=1)
    main: str = ""
    treat: str = ""
    powers: str = ""
    i_ch: Any = 0
    v_ch: Any = 1
    bl_pre0: Any = 0
    bl_pre1: Any = 50
    peak_window: Any = 200
    move_files: bool = True
    reindex_seq: bool = False
    dry_run: bool = False


def register_abf_batch_routes(app, ctx):
    err = ctx["err"]
    browse_files_recursive = ctx["browse_files_recursive"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    has_abf = ctx["HAS_ABF"]
    pyabf_mod = ctx.get("pyabf")
    jobs = ctx.get("jobs")

    def _abf_batch_process_payload(d: dict) -> dict:
        return abf_batch_service.process_payload(
            d,
            has_abf=has_abf,
            pyabf_mod=pyabf_mod,
            float_or=float_or,
            int_or=int_or,
            root_dir=ROOT_DIR,
        )

    def _abf_batch_process_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Running ABF batch processing")
        return _abf_batch_process_payload(body)

    @app.route("/api/abf_batch/browse", methods=["POST"])
    @request_schema(AbfBatchBrowseRequest)
    def api_abf_batch_browse():
        try:
            payload = parse_json_payload(AbfBatchBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        files = browse_files_recursive(payload.folder, {".abf"}, max_files=301)
        truncated = len(files) > 300
        return jsonify({"files": files[:300], "truncated": truncated})

    @app.route("/api/abf_batch/scan_tokens", methods=["POST"])
    @request_schema(AbfBatchScanTokensRequest)
    def api_abf_batch_scan_tokens():
        """Scan filenames to suggest main/treat tokens."""
        try:
            payload = parse_json_payload(AbfBatchScanTokensRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return jsonify(abf_batch_service.scan_filename_tokens(payload.files))

    @app.route("/api/abf_batch/process", methods=["POST"])
    @request_schema(AbfBatchProcessRequest)
    def api_abf_batch_process():
        """Process a batch of ABF files and extract photocurrent peaks."""
        try:
            d = parse_json_payload(AbfBatchProcessRequest).model_dump()
            result = _abf_batch_process_payload(d)
            return api_ok(result, outputs=result.get("outputs"), warnings=result.get("warnings"))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf_batch/process_job", methods=["POST"])
    @request_schema(AbfBatchProcessRequest)
    def api_abf_batch_process_job():
        try:
            body = parse_json_payload(AbfBatchProcessRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "abf_batch.process",
            "Run ABF batch processing",
            _abf_batch_process_task,
            body,
            metadata={"endpoint": "/api/abf_batch/process"},
        )
