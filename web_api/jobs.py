from __future__ import annotations

from typing import Any, Callable

from pydantic import Field

from services.background_jobs import JobContext, JobManager

from .request_validation import RequestModel, api_endpoint
from .response import api_error, api_ok


class JobListRequest(RequestModel):
    limit: int = Field(default=50, ge=1, le=200)
    include_finished: bool = True


class JobIdRequest(RequestModel):
    job_id: str = Field(min_length=1)


def route_response_to_payload(result: Any) -> dict[str, Any]:
    """Convert a Flask route return value into a plain JSON-compatible dict."""
    status_code = 200
    response = result
    if isinstance(result, tuple):
        response = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]
    elif hasattr(result, "status_code"):
        status_code = int(getattr(result, "status_code", 200) or 200)

    if hasattr(response, "get_json"):
        payload = response.get_json(silent=True) or {}
    elif isinstance(response, dict):
        payload = dict(response)
    else:
        payload = {"message": str(response)}

    if not isinstance(payload, dict):
        payload = {"value": payload}
    if status_code >= 400 and not payload.get("error"):
        payload["error"] = f"HTTP {status_code}"
    return payload


def run_json_task_job(
    job_ctx: JobContext,
    task_func: Callable[..., Any],
    body: dict[str, Any] | None,
):
    """Run a body-driven service task without manufacturing a Flask request."""
    job_ctx.set_progress(0.02, "Starting job")
    job_ctx.check_cancelled()
    result = task_func(job_ctx, body or {})
    job_ctx.check_cancelled()
    if result is None:
        result = {}
    if not isinstance(result, dict):
        result = {"value": result}
    if result.get("error"):
        job_ctx.set_progress(1.0, "Failed")
        result.setdefault("ok", False)
        return result
    job_ctx.set_progress(1.0, "Complete")
    return result


def submit_json_task(
    jobs: JobManager | None,
    kind: str,
    title: str,
    task_func: Callable[..., Any],
    body: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Submit a background task that accepts ``(job_ctx, body)``.

    Prefer this for new code: the synchronous route and job route can both call
    the same service function, and the job worker no longer needs Flask's test
    request machinery.
    """
    if jobs is None:
        return api_error("Background job manager is not available", 500)
    job = jobs.submit(
        kind,
        title,
        run_json_task_job,
        task_func,
        body or {},
        metadata=metadata or {},
    )
    return api_ok({"running": True, "job": job, "job_id": job["job_id"]})


def register_job_routes(app, ctx) -> None:
    jobs: JobManager = ctx.jobs

    @app.route("/api/jobs/list", methods=["POST"])
    @api_endpoint(JobListRequest, dump=False)
    def api_jobs_list(payload):
        return api_ok(
            {"jobs": jobs.list(limit=payload.limit, include_finished=payload.include_finished)}
        )

    @app.route("/api/jobs/get", methods=["POST"])
    @api_endpoint(JobIdRequest, dump=False)
    def api_jobs_get(payload):
        job_id = payload.job_id.strip()
        job = jobs.get(job_id)
        if not job:
            return api_error(f"Unknown job: {job_id}", 404)
        return api_ok({"job": job})

    @app.route("/api/jobs/cancel", methods=["POST"])
    @api_endpoint(JobIdRequest, dump=False)
    def api_jobs_cancel(payload):
        job_id = payload.job_id.strip()
        if not jobs.request_cancel(job_id):
            return api_error(f"Unknown job: {job_id}", 404)
        return api_ok({"job": jobs.get(job_id)})

    @app.route("/api/jobs/cleanup", methods=["POST"])
    def api_jobs_cleanup():
        removed = jobs.cleanup()
        return api_ok({"removed": removed})
