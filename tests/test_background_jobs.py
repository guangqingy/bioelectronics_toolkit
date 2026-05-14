from __future__ import annotations

import threading
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
