from __future__ import annotations

import unittest

from services.background_jobs import JobManager
from web_app import create_app


class WebAppFactoryTests(unittest.TestCase):
    def test_create_app_supports_in_memory_job_manager(self) -> None:
        app = create_app(jobs=JobManager())
        client = app.test_client()

        version = client.get("/api/version")
        self.assertEqual(version.status_code, 200)
        self.assertTrue(version.get_json()["ok"])

    def test_favicon_route_serves_managed_file_response(self) -> None:
        app = create_app(jobs=JobManager())
        response = app.test_client().get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.mimetype, {"image/x-icon", "image/vnd.microsoft.icon"})
        self.assertGreater(len(response.data), 0)

    def test_invalid_host_header_is_rejected(self) -> None:
        app = create_app(jobs=JobManager())
        response = app.test_client().get("/api/version", headers={"Host": "evil.example"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid Host header")

    def test_cross_origin_post_is_rejected(self) -> None:
        app = create_app(jobs=JobManager())
        client = app.test_client()

        blocked = client.post(
            "/api/csv/browse",
            json={"folder": "/definitely/not/a/folder"},
            headers={"Origin": "http://evil.example"},
        )
        allowed = client.post(
            "/api/csv/browse",
            json={"folder": "/definitely/not/a/folder"},
            headers={"Origin": "http://localhost"},
        )

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["error"], "Cross-origin request blocked")
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
