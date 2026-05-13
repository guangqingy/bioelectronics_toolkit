from __future__ import annotations

import json
import logging
import sqlite3
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from web_api.response import infer_outputs

LOG = logging.getLogger(__name__)


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
    def __init__(self, max_jobs: int = 200, persistence_path: Path | str | None = None):
        self.max_jobs = max(20, int(max_jobs))
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        if self.persistence_path:
            self._init_storage()
            self._load_persisted_jobs()

    def _connect(self) -> sqlite3.Connection:
        if not self.persistence_path:
            raise RuntimeError("Job persistence is not configured")
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.persistence_path), timeout=10)

    def _init_storage(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
        except Exception:
            LOG.warning("Could not initialize job persistence at %s", self.persistence_path, exc_info=True)

    def _load_persisted_jobs(self) -> None:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT payload FROM jobs ORDER BY updated_at DESC").fetchall()
        except Exception:
            LOG.warning("Could not load persisted jobs from %s", self.persistence_path, exc_info=True)
            return

        restored: dict[str, dict[str, Any]] = {}
        for (payload,) in rows:
            try:
                job = json.loads(payload)
            except Exception:
                continue
            if not isinstance(job, dict) or not job.get("job_id"):
                continue
            if job.get("status") in {"pending", "running"}:
                job = dict(job)
                job["status"] = "interrupted"
                job["finished_at"] = job.get("finished_at") or _now_iso()
                job["message"] = "Server restarted before this job completed"
                job["error"] = job.get("error") or "Server restarted before job completed"
            restored[str(job["job_id"])] = job
            if len(restored) >= self.max_jobs:
                break
        with self._lock:
            self._jobs.update(restored)
            self._trim_locked()

    def _persist_job_locked(self, job: dict[str, Any]) -> None:
        if not self.persistence_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "REPLACE INTO jobs (job_id, updated_at, payload) VALUES (?, ?, ?)",
                    (
                        job["job_id"],
                        _now_iso(),
                        json.dumps(job, ensure_ascii=False, sort_keys=True),
                    ),
                )
        except Exception:
            LOG.warning("Could not persist job %s", job.get("job_id"), exc_info=True)

    def _delete_job_locked(self, job_id: str) -> None:
        if not self.persistence_path:
            return
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        except Exception:
            LOG.warning("Could not delete persisted job %s", job_id, exc_info=True)

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
            self._persist_job_locked(record)
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
            self._delete_job_locked(job["job_id"])

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            self._persist_job_locked(job)

    def add_warning(self, job_id: str, warning: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.setdefault("warnings", []).append(str(warning))
            self._persist_job_locked(job)

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["cancel_requested"] = True
            if job.get("status") == "pending":
                job["status"] = "cancelled"
                job["finished_at"] = _now_iso()
            self._persist_job_locked(job)
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
                self._delete_job_locked(job_id)
                removed += 1
        return removed
