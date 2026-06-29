from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field

from services import abf_batch as abf_batch_service

from .jobs import submit_json_task
from .request_validation import (
    OptFloat,
    OptInt,
    RequestModel,
    api_endpoint,
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
    i_ch: OptInt = 0
    v_ch: OptInt = 1
    analog_ch: OptInt = 2
    segment_mode: str = "auto"
    segment_t0: OptFloat = 0.1
    segment_t1: OptFloat = 0.7
    save_segments: bool = True
    pure_csv: bool = False
    move_files: bool = True
    reindex_seq: bool = False
    dry_run: bool = False


def register_abf_batch_routes(app, ctx):
    browse_files_recursive = ctx.browse_files_recursive
    pyabf_mod = ctx.pyabf
    jobs = ctx.jobs

    def _abf_batch_process_payload(d: dict) -> dict:
        return abf_batch_service.process_payload(
            d,
            pyabf_mod=pyabf_mod,
            root_dir=ROOT_DIR,
        )

    def _abf_batch_process_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Running ABF batch processing")
        return _abf_batch_process_payload(body)

    @app.route("/api/abf_batch/browse", methods=["POST"])
    @api_endpoint(AbfBatchBrowseRequest, dump=False)
    def api_abf_batch_browse(payload):
        return jsonify(abf_batch_service.browse_payload(payload.folder, browse_files_recursive))

    @app.route("/api/abf_batch/scan_tokens", methods=["POST"])
    @api_endpoint(AbfBatchScanTokensRequest, dump=False)
    def api_abf_batch_scan_tokens(payload):
        """Scan filenames to suggest main/treat tokens."""
        return jsonify(abf_batch_service.scan_filename_tokens(payload.files))

    @app.route("/api/abf_batch/process", methods=["POST"])
    @api_endpoint(AbfBatchProcessRequest)
    def api_abf_batch_process(d):
        """Process a batch of ABF files and extract photocurrent peaks."""
        result = _abf_batch_process_payload(d)
        return api_ok(result, outputs=result.get("outputs"), warnings=result.get("warnings"))

    @app.route("/api/abf_batch/process_job", methods=["POST"])
    @api_endpoint(AbfBatchProcessRequest)
    def api_abf_batch_process_job(body):
        return submit_json_task(
            jobs,
            "abf_batch.process",
            "Run ABF batch processing",
            _abf_batch_process_task,
            body,
            metadata={"endpoint": "/api/abf_batch/process"},
        )
