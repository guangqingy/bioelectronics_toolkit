from __future__ import annotations

import sqlite3
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from services.background_jobs import JobManager


class BackgroundJobCancelTests(unittest.TestCase):
    def test_cancel_stops_worker_that_checks_token(self) -> None:
        jobs = JobManager()
        started = threading.Event()

        def slow_task(job_ctx):
            started.set()
            for _ in range(100):
                time.sleep(0.01)
                job_ctx.check_cancelled()
            return {"ok": True}

        job = jobs.submit("test.cancel", "Cancelable test", slow_task)
        self.assertTrue(started.wait(1.0))
        self.assertTrue(jobs.request_cancel(job["job_id"]))

        deadline = time.time() + 2.0
        final = jobs.get(job["job_id"]) or {}
        while (
            final.get("status") not in {"cancelled", "failed", "succeeded"}
            and time.time() < deadline
        ):
            time.sleep(0.02)
            final = jobs.get(job["job_id"]) or {}

        self.assertEqual(final.get("status"), "cancelled")
        self.assertTrue(final.get("cancel_requested"))

    def test_max_workers_bounds_concurrent_jobs(self) -> None:
        jobs = JobManager(max_workers=1)
        entered = 0
        max_seen = 0
        lock = threading.Lock()

        def short_task(_job_ctx):
            nonlocal entered, max_seen
            with lock:
                entered += 1
                max_seen = max(max_seen, entered)
            time.sleep(0.05)
            with lock:
                entered -= 1
            return {"ok": True}

        submitted = [jobs.submit("test.queue", "Queued test", short_task) for _ in range(3)]
        deadline = time.time() + 2.0
        while time.time() < deadline:
            states = [jobs.get(job["job_id"]) for job in submitted]
            if all(state and state.get("status") == "succeeded" for state in states):
                break
            time.sleep(0.02)

        self.assertEqual(max_seen, 1)

    def test_persistence_failure_logs_once_and_degrades_to_memory(self) -> None:
        with (
            mock.patch.object(
                JobManager,
                "_connect",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            self.assertLogs("services.background_jobs", level="WARNING") as logs,
        ):
            jobs = JobManager(persistence_path=Path("/tmp/dataprocess-unwritable/jobs.sqlite"))
            first = jobs.submit("test.persist", "Persist fallback", lambda _ctx: {"ok": True})
            second = jobs.submit("test.persist", "Persist fallback", lambda _ctx: {"ok": True})

        self.assertTrue(jobs._persistence_disabled)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("continuing with in-memory jobs only", logs.records[0].getMessage())
        self.assertIn("job_id", first)
        self.assertIn("job_id", second)


if __name__ == "__main__":
    unittest.main()
