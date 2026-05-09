from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from web_api.jobs import JobManager
from web_api.response import make_envelope


class ApiEnvelopeTests(unittest.TestCase):
    def test_legacy_saved_path_is_exposed_as_output(self) -> None:
        envelope = make_envelope({"ok": True, "saved_path": "/tmp/result.csv", "rows": 3})

        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["saved_path"], "/tmp/result.csv")
        self.assertEqual(envelope["data"]["saved_path"], "/tmp/result.csv")
        self.assertEqual(envelope["outputs"], [{"path": "/tmp/result.csv", "type": "csv"}])

    def test_batch_output_records_are_inferred_without_losing_legacy_shape(self) -> None:
        payload = {
            "ok": True,
            "outputs": [
                {
                    "input": "/tmp/source.tif",
                    "combined_tiff": "/tmp/source_selected_stacks.tif",
                    "stack_files": ["/tmp/source_stack1_blue.tif", "/tmp/source_stack2_red.tif"],
                    "json": "/tmp/source_display_settings.json",
                }
            ],
        }

        envelope = make_envelope(payload)
        output_paths = {item["path"] for item in envelope["outputs"]}

        self.assertIn("/tmp/source_selected_stacks.tif", output_paths)
        self.assertIn("/tmp/source_stack1_blue.tif", output_paths)
        self.assertIn("/tmp/source_stack2_red.tif", output_paths)
        self.assertIn("/tmp/source_display_settings.json", output_paths)
        self.assertEqual(envelope["data"]["outputs"][0]["input"], "/tmp/source.tif")

    def test_source_metadata_path_is_not_inferred_as_generated_output(self) -> None:
        envelope = make_envelope(
            {
                "ok": True,
                "output_path": "/tmp/movie.gif",
                "metadata_path": "/tmp/source_display_settings.json",
            }
        )

        self.assertEqual(envelope["outputs"], [{"path": "/tmp/movie.gif", "type": "gif"}])


class JobManagerContractTests(unittest.TestCase):
    def test_job_record_gets_inferred_outputs(self) -> None:
        manager = JobManager()

        def target(_ctx):
            return {
                "ok": True,
                "outputs": [
                    {
                        "input": "/tmp/source.tif",
                        "combined_tiff": "/tmp/source_selected_stacks.tif",
                        "stack_files": ["/tmp/source_stack1_blue.tif"],
                    }
                ],
            }

        submitted = manager.submit("test", "Batch export", target)
        job = self._wait_for_job(manager, submitted["job_id"])

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["data"]["outputs"][0]["input"], "/tmp/source.tif")
        self.assertEqual(
            {item["path"] for item in job["outputs"]},
            {"/tmp/source_selected_stacks.tif", "/tmp/source_stack1_blue.tif"},
        )

    @staticmethod
    def _wait_for_job(manager: JobManager, job_id: str) -> dict:
        for _ in range(50):
            job = manager.get(job_id) or {}
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for job {job_id}")


class WebAppSmokeTests(unittest.TestCase):
    PAGE_ROUTES = (
        "/",
        "/csv",
        "/abf/viewer",
        "/abf/peaks",
        "/abf/batch",
        "/abf/figure",
        "/emg/rhd",
        "/emg/peaks",
        "/echem/photocurrent",
        "/echem/photovoltage",
        "/echem/lineshape",
        "/fluorescence",
        "/fluorescence/3d-stacking",
        "/fluorescence/roi",
        "/fluorescence/gif",
        "/fluorescence/lif",
        "/histology",
        "/runs",
        "/scripts",
    )

    @classmethod
    def setUpClass(cls) -> None:
        from web_app import app

        cls.client = app.test_client()

    def test_all_web_pages_render(self) -> None:
        for route in self.PAGE_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_fluorescence_refactor_keeps_route_contracts(self) -> None:
        routes = {str(rule.rule) for rule in self.client.application.url_map.iter_rules()}
        expected = {
            "/api/fluorescence/browse",
            "/api/fluorescence/stack_export",
            "/api/fluorescence/stack_export_job",
            "/api/fluorescence/3d/volume",
            "/api/fluorescence/3d/export_volume_job",
            "/api/fluorescence/gif_preview",
            "/api/fluorescence/make_gif_job",
            "/api/fluorescence/gif_roi/kymograph_export_job",
            "/api/fluorescence/roi/analyze_sequence",
            "/api/fluorescence/roi/export_sequence_gif_job",
        }
        self.assertTrue(expected.issubset(routes), sorted(expected - routes))

    def test_fluorescence_split_routes_handle_small_tiff(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"fluorescence optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_fl_test_") as tmp:
            source = Path(tmp) / "tiny_stack.tif"
            arr = np.arange(2 * 12 * 14, dtype=np.uint16).reshape(2, 12, 14)
            tifffile.imwrite(source, arr)

            info = self.client.post("/api/fluorescence/info", json={"path": str(source)})
            info_payload = info.get_json()
            self.assertEqual(info.status_code, 200)
            self.assertTrue(info_payload["ok"])
            self.assertEqual(info_payload["data"]["n_frames"], 2)

            preview = self.client.post(
                "/api/fluorescence/preview_frame",
                json={"path": str(source), "frame": 1, "lut": "Gray"},
            )
            preview_payload = preview.get_json()
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertTrue(preview_payload["data"]["img"])

            roi = self.client.post(
                "/api/fluorescence/roi/load_stack",
                json={"stack_path": str(source), "frame": 0, "lut": "Gray"},
            )
            roi_payload = roi.get_json()
            self.assertEqual(roi.status_code, 200)
            self.assertTrue(roi_payload["ok"])
            self.assertEqual(roi_payload["data"]["n_frames"], 2)

    def test_csv_export_and_job_contracts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_web_test_") as tmp:
            source = Path(tmp) / "trace.csv"
            source.write_text("time,value\n0,1\n1,2\n", encoding="utf-8")

            direct = self.client.get(
                "/api/csv/export_csv",
                query_string={"path": str(source), "mode": "save"},
            )
            direct_payload = direct.get_json()
            self.assertEqual(direct.status_code, 200)
            self.assertTrue(direct_payload["ok"])
            self.assertTrue(Path(direct_payload["outputs"][0]["path"]).exists())

            started = self.client.post("/api/csv/export_csv_job", json={"path": str(source)})
            started_payload = started.get_json()
            self.assertTrue(started_payload["ok"])

            job = self._wait_for_api_job(started_payload["job_id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(Path(job["outputs"][0]["path"]).exists())
            self.assertEqual(job["data"]["saved_path"], job["outputs"][0]["path"])

    def _wait_for_api_job(self, job_id: str) -> dict:
        for _ in range(80):
            response = self.client.post("/api/jobs/get", json={"job_id": job_id})
            job = response.get_json()["job"]
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for API job {job_id}")


if __name__ == "__main__":
    unittest.main()
