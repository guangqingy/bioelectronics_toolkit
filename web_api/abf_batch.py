import traceback
from pathlib import Path

from flask import jsonify, request

from services import abf_batch as abf_batch_service

from .jobs import submit_json_task
from .response import api_ok

ROOT_DIR = Path(__file__).resolve().parents[1]


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
    def api_abf_batch_browse():
        d = request.json or {}
        files = browse_files_recursive(d.get("folder", ""), {".abf"})
        return jsonify({"files": files})

    @app.route("/api/abf_batch/scan_tokens", methods=["POST"])
    def api_abf_batch_scan_tokens():
        """Scan filenames to suggest main/treat tokens."""
        d = request.json or {}
        return jsonify(abf_batch_service.scan_filename_tokens(d.get("files", [])))

    @app.route("/api/abf_batch/process", methods=["POST"])
    def api_abf_batch_process():
        """Process a batch of ABF files and extract photocurrent peaks."""
        d = request.json or {}
        try:
            result = _abf_batch_process_payload(d)
            return api_ok(result, outputs=result.get("outputs"), warnings=result.get("warnings"))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf_batch/process_job", methods=["POST"])
    def api_abf_batch_process_job():
        return submit_json_task(
            jobs,
            "abf_batch.process",
            "Run ABF batch processing",
            _abf_batch_process_task,
            request.json or {},
            metadata={"endpoint": "/api/abf_batch/process"},
        )
