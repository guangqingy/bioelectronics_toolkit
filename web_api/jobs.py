from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from flask import request

from .response import api_error, api_ok, infer_outputs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(RuntimeError):
    pass


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str):
        self._manager = manager
        self.job_id = job_id

    def set_progress(self, progress: float | None = None, message: str = "") -> None:
        self._manager.update(self.job_id, progress=progress, message=message)

    def add_warning(self, warning: Any) -> None:
        self._manager.add_warning(self.job_id, warning)

    def check_cancelled(self) -> None:
        if self._manager.cancel_requested(self.job_id):
            raise JobCancelled("Job cancelled")


class JobManager:
    def __init__(self, max_jobs: int = 200):
        self.max_jobs = max(20, int(max_jobs))
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def submit(
        self,
        kind: str,
        title: str,
        target: Callable[..., Any],
        *args,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        record = {
            "job_id": job_id,
            "kind": str(kind or "job"),
            "title": str(title or kind or "Job"),
            "status": "pending",
            "progress": None,
            "message": "",
            "created_at": _now_iso(),
            "started_at": "",
            "finished_at": "",
            "data": {},
            "outputs": [],
            "warnings": [],
            "error": None,
            "metadata": metadata or {},
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[job_id] = record
            self._trim_locked()
        thread = threading.Thread(target=self._run, args=(job_id, target, args, kwargs), daemon=True)
        thread.start()
        return self.get(job_id) or record

    def _run(self, job_id: str, target: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.update(job_id, status="running", started_at=_now_iso(), message="Running")
        ctx = JobContext(self, job_id)
        try:
            result = target(ctx, *args, **kwargs)
            if result is None:
                result = {}
            if not isinstance(result, dict):
                result = {"value": result}
            result_ok = result.get("ok", True) is not False
            outputs = infer_outputs(result) if result_ok else []
            data = result.get("data") if isinstance(result.get("data"), dict) else result
            if isinstance(data, dict) and outputs and not isinstance(data.get("outputs"), list):
                data = dict(data)
                data["outputs"] = outputs
            current_job = self.get(job_id) or {}
            self.update(
                job_id,
                status="succeeded" if result_ok else "failed",
                progress=1,
                message=result.get("message") or "Complete",
                finished_at=_now_iso(),
                data=data,
                outputs=outputs,
                warnings=result.get("warnings") if isinstance(result.get("warnings"), list) else current_job.get("warnings", []),
                error=None if result_ok else (result.get("error") or result.get("stderr") or "Job failed"),
            )
        except JobCancelled as exc:
            self.update(job_id, status="cancelled", finished_at=_now_iso(), message=str(exc), error=str(exc))
        except Exception as exc:
            self.update(
                job_id,
                status="failed",
                finished_at=_now_iso(),
                message=str(exc),
                error=traceback.format_exc(),
            )

    def _trim_locked(self) -> None:
        if len(self._jobs) <= self.max_jobs:
            return
        ordered = sorted(self._jobs.values(), key=lambda j: j.get("created_at", ""))
        for job in ordered[: max(0, len(self._jobs) - self.max_jobs)]:
            self._jobs.pop(job["job_id"], None)

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(fields)

    def add_warning(self, job_id: str, warning: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.setdefault("warnings", []).append(str(warning))

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["cancel_requested"] = True
            if job.get("status") == "pending":
                job["status"] = "cancelled"
                job["finished_at"] = _now_iso()
            return True

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return bool(self._jobs.get(job_id, {}).get("cancel_requested"))

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit: int = 50, include_finished: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        if not include_finished:
            jobs = [j for j in jobs if j.get("status") in {"pending", "running"}]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return [dict(j) for j in jobs[: max(1, min(int(limit or 50), self.max_jobs))]]

    def cleanup(self, keep_running: bool = True) -> int:
        removed = 0
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if keep_running and job.get("status") in {"pending", "running"}:
                    continue
                self._jobs.pop(job_id, None)
                removed += 1
        return removed


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


def unwrap_api_envelope(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": payload}
    envelope_keys = {"ok", "data", "outputs", "warnings", "error"}
    if envelope_keys.issubset(payload.keys()) and isinstance(payload.get("data"), dict):
        merged = dict(payload["data"])
        for key, value in payload.items():
            if key != "data":
                merged[key] = value
        return merged
    return payload


def run_flask_route_job(
    job_ctx: JobContext,
    app,
    endpoint: str,
    route_func: Callable[..., Any],
    body: dict[str, Any] | None,
    method: str = "POST",
    query_string: dict[str, Any] | None = None,
):
    job_ctx.set_progress(0.02, "Starting job")
    request_kwargs: dict[str, Any] = {"method": method or "POST"}
    if query_string:
        request_kwargs["query_string"] = query_string
    elif (method or "POST").upper() != "GET":
        request_kwargs["json"] = body or {}
    with app.test_request_context(endpoint, **request_kwargs):
        payload = unwrap_api_envelope(route_response_to_payload(route_func()))
    if payload.get("error"):
        job_ctx.set_progress(1.0, "Failed")
        payload.setdefault("ok", False)
        return payload
    job_ctx.set_progress(1.0, "Complete")
    return payload


def run_json_task_job(
    job_ctx: JobContext,
    task_func: Callable[..., Any],
    body: dict[str, Any] | None,
):
    """Run a body-driven service task without manufacturing a Flask request."""
    job_ctx.set_progress(0.02, "Starting job")
    result = task_func(job_ctx, body or {})
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


def submit_flask_route_job(
    app,
    jobs: JobManager | None,
    endpoint: str,
    kind: str,
    title: str,
    route_func: Callable[..., Any],
    body: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    method: str = "POST",
    query_string: dict[str, Any] | None = None,
):
    if jobs is None:
        return api_error("Background job manager is not available", 500)
    meta = {"endpoint": endpoint}
    meta.update(metadata or {})
    job = jobs.submit(
        kind,
        title,
        run_flask_route_job,
        app,
        endpoint,
        route_func,
        body or {},
        method,
        query_string,
        metadata=meta,
    )
    return api_ok({"running": True, "job": job, "job_id": job["job_id"]})


def register_job_routes(app, ctx) -> None:
    jobs: JobManager = ctx["jobs"]

    @app.route("/api/jobs/list", methods=["POST"])
    def api_jobs_list():
        body = request.json or {}
        limit = int(body.get("limit") or 50) if isinstance(body, dict) else 50
        include_finished = bool(body.get("include_finished", True)) if isinstance(body, dict) else True
        return api_ok({"jobs": jobs.list(limit=limit, include_finished=include_finished)})

    @app.route("/api/jobs/get", methods=["POST"])
    def api_jobs_get():
        body = request.json or {}
        job_id = str((body or {}).get("job_id") or "").strip()
        job = jobs.get(job_id)
        if not job:
            return api_error(f"Unknown job: {job_id}", 404)
        return api_ok({"job": job})

    @app.route("/api/jobs/cancel", methods=["POST"])
    def api_jobs_cancel():
        body = request.json or {}
        job_id = str((body or {}).get("job_id") or "").strip()
        if not jobs.request_cancel(job_id):
            return api_error(f"Unknown job: {job_id}", 404)
        return api_ok({"job": jobs.get(job_id)})

    @app.route("/api/jobs/cleanup", methods=["POST"])
    def api_jobs_cleanup():
        removed = jobs.cleanup()
        return api_ok({"removed": removed})
