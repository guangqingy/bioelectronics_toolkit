from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask

from web_api import system as system_api
from web_api.jobs import JobManager
from web_api.response import api_error, make_envelope


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

    def test_explicit_output_records_take_precedence_over_legacy_paths(self) -> None:
        envelope = make_envelope(
            {
                "ok": True,
                "saved_path": "/tmp/result.csv",
                "outputs": [{"path": "/tmp/result.csv", "type": "csv", "role": "full_csv"}],
            }
        )

        self.assertEqual(
            envelope["outputs"],
            [{"path": "/tmp/result.csv", "type": "csv", "role": "full_csv"}],
        )

    def test_traceback_errors_are_redacted_outside_debug(self) -> None:
        app = Flask(__name__)
        app.config["DEBUG"] = False

        with app.app_context():
            response, code = api_error("Traceback (most recent call last):\n  File x.py", 400)

        payload = response.get_json()
        self.assertEqual(code, 500)
        self.assertEqual(payload["error"], "Internal error")
        self.assertRegex(payload["id"], r"^[0-9a-f]{8}$")


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

    def test_job_records_persist_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_jobs_") as tmp:
            db_path = Path(tmp) / "jobs.sqlite"
            manager = JobManager(persistence_path=db_path)

            submitted = manager.submit(
                "test",
                "Persisted job",
                lambda _ctx: {"ok": True, "message": "done"},
            )
            job = self._wait_for_job(manager, submitted["job_id"])
            self.assertEqual(job["status"], "succeeded")

            restored = JobManager(persistence_path=db_path)
            restored_job = restored.get(submitted["job_id"])
            self.assertIsNotNone(restored_job)
            self.assertEqual(restored_job["status"], "succeeded")

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
        "/fluorescence/timecourse",
        "/fluorescence/kymograph",
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

    def test_templates_do_not_expose_developer_absolute_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for folder in (root / "web_templates", root / "web_static"):
            for source in folder.rglob("*"):
                if source.suffix not in {".html", ".js", ".css"}:
                    continue
                text = source.read_text(encoding="utf-8")
                if "/" + "Users/" + "guangqing" in text or "Desktop" + "/" + "UChicago" in text:
                    offenders.append(str(source.relative_to(root)))
        self.assertEqual([], offenders)

    def test_rendered_pages_do_not_expose_developer_absolute_paths(self) -> None:
        needles = ("/" + "Users/" + "guangqing", "Desktop" + "/" + "UChicago")
        for route in ("/", "/scripts", "/abf/viewer", "/fluorescence/roi?demo=fluorescence"):
            with self.subTest(route=route):
                response = self.client.get(route)
                html = response.data.decode("utf-8")
                for needle in needles:
                    self.assertNotIn(needle, html)

    def test_abf_viewer_does_not_auto_scan_empty_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "abf_viewer.html").read_text(encoding="utf-8")
        self.assertIn('if (folderInput.value.trim())', template)
        self.assertIn('setStatusBar("Choose an ABF folder to begin.", "")', template)
        self.assertIn('data-rnorm-state="checked"', template)
        self.assertIn("function updateAbfParameterGroups()", template)
        self.assertIn('dpBindToggleGroups("rNorm", "data-rnorm-state")', template)
        self.assertNotIn('DEFAULT_DATA_DIR + "/examples"', template)

    def test_nav_exposes_domain_groups_and_version(self) -> None:
        response = self.client.get("/")
        html = response.data.decode("utf-8")
        self.assertIn("Fluorescence", html)
        self.assertIn("Timecourse", html)
        self.assertIn("Kymograph", html)
        self.assertIn("Photocurrent", html)
        self.assertIn("Intan RHD Viewer", html)
        self.assertIn("Command Palette", html)
        self.assertIn("commandPalette", html)
        self.assertIn("v0.4.0", html)
        self.assertNotIn("unknown", html.lower())

    def test_rhd_viewer_exposes_preview_merge_downsample_and_view_first_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "rhd_viewer.html").read_text(encoding="utf-8")

        self.assertIn('id="previewDownsample"', template)
        self.assertIn('id="previewMergePair"', template)
        self.assertIn("function reloadCurrentRhdFile()", template)
        self.assertIn("function renderRhdFileList(options)", template)
        self.assertIn("Auto merge folder recording", template)
        self.assertIn("Merge folder recording", template)
        self.assertIn("split(/[\\\\/]/)", template)
        self.assertIn('id="filterType"', template)
        self.assertIn('id="processType"', template)
        self.assertIn('data-filter-mode="notch"', template)
        self.assertIn('data-process-mode="smooth"', template)
        self.assertIn("function updateRhdParameterGroups()", template)
        self.assertIn("dpBindParamGroups('processType', 'data-process-mode')", template)
        self.assertIn('id="envelopeSmoothMs"', template)
        self.assertIn('id="smoothMethod"', template)
        self.assertIn('id="fftWindow"', template)
        self.assertIn('id="fftMaxHz"', template)
        self.assertIn('id="stftOverlapPct"', template)
        self.assertIn('id="figWidthIn"', template)
        self.assertIn('id="traceLineWidth"', template)
        self.assertIn("function currentFigureParams()", template)
        self.assertIn('id="processArea"', template)
        self.assertIn("/api/rhd/process", template)
        self.assertIn("let _fileLoadSeq", template)
        self.assertIn("let _plotSeq", template)
        self.assertIn("merge_pair: previewMergeEnabled()", template)
        self.assertLess(template.index("View Window"), template.index("Export Options"))
        self.assertLess(template.index("Export Current"), template.index("Batch Export Queue"))

    def test_rhd_preview_plot_accepts_merge_and_downsample(self) -> None:
        import numpy as np

        import web_app

        if not web_app.HAS_RHD:
            self.skipTest("Intan RHD parser is not available")

        def fake_recording_metadata(path, _rhd_module, do_merge):
            n = 120_000 if do_merge else 60_000
            return {
                "channels": ["A-000", "A-001"],
                "channels_meta": [
                    {
                        "idx": 0,
                        "name": "A-000",
                        "native_name": "native-000",
                        "label": "A-000",
                        "type": "amplifier",
                    },
                    {
                        "idx": 1,
                        "name": "A-001",
                        "native_name": "native-001",
                        "label": "A-001",
                        "type": "amplifier",
                    },
                ],
                "sample_rate": 1000.0,
                "sampling_rate": 1000.0,
                "n_samples": n,
                "duration_s": n / 1000.0,
                "duration": n / 1000.0,
                "num_amplifiers": 2,
                "merged_pair": bool(do_merge),
                "merged_folder": bool(do_merge),
                "base_stem": "record_0001",
                "source_path": str(path),
                "source_paths": [str(path)],
                "segment_count": 2 if do_merge else 1,
            }

        def fake_load_channel_with_merge_option(path, _rhd_module, ch_in, do_merge):
            n = 120_000 if do_merge else 60_000
            t = np.arange(n, dtype=float) / 1000.0
            y = np.cos(t) if ch_in == "A-001" else np.sin(t)
            ch = 1 if ch_in == "A-001" else 0
            ch_name = "A-001" if ch == 1 else "A-000"
            return t, 1000.0, ["A-000", "A-001"], y, ch, ch_name, "record_0001", bool(do_merge), 2

        with (
            mock.patch(
                "web_api.rhd_viewer.rhd_service.recording_metadata_with_merge_option",
                side_effect=fake_recording_metadata,
            ),
            mock.patch(
                "web_api.rhd_viewer.rhd_service.load_channel_with_merge_option",
                side_effect=fake_load_channel_with_merge_option,
            ),
        ):
            loaded = self.client.post(
                "/api/rhd/load",
                json={"path": "/tmp/record_0100.rhd", "merge_pair": True},
            )
            loaded_payload = loaded.get_json()
            loaded_data = loaded_payload.get("data", loaded_payload)
            self.assertEqual(loaded.status_code, 200)
            self.assertTrue(loaded_data["merged_pair"])
            self.assertEqual(loaded_data["n_samples"], 120_000)

            plot = self.client.post(
                "/api/rhd/plot",
                json={
                    "path": "/tmp/record_0100.rhd",
                    "channel": "A-001",
                    "merge_pair": True,
                    "downsample": 10,
                },
            )
            plot_payload = plot.get_json()
            plot_data = plot_payload.get("data", plot_payload)
            self.assertEqual(plot.status_code, 200)
            self.assertTrue(plot_data["img"])
            self.assertEqual(plot_data["downsample"], 10)
            self.assertEqual(plot_data["plotted_points"], 12_000)

            processed = self.client.post(
                "/api/rhd/process",
                json={
                    "path": "/tmp/record_0100.rhd",
                    "channel": "A-001",
                    "merge_pair": True,
                    "x_min": 0,
                    "x_max": 1,
                    "filter_type": "highpass",
                    "filter_low_hz": 1,
                    "process_type": "fft",
                    "fft_window": "hamming",
                    "fft_max_hz": 100,
                    "fft_log": True,
                    "fig_width_in": 6,
                    "fig_height_in": 2.5,
                    "trace_line_width": 1.5,
                    "show_grid": False,
                },
            )
            processed_payload = processed.get_json()
            processed_data = processed_payload.get("data", processed_payload)
            self.assertEqual(processed.status_code, 200)
            self.assertTrue(processed_data["img"])
            self.assertEqual(processed_data["process_type"], "fft")
            self.assertEqual(processed_data["fft_window"], "hamming")
            self.assertLessEqual(processed_data["frequency_max"], 100)

    def test_rhd_svg_export_is_clean_and_numbered(self) -> None:
        import numpy as np

        import web_app

        if not web_app.HAS_RHD:
            self.skipTest("Intan RHD parser is not available")

        def fake_load_channel_with_merge_option(path, _rhd_module, ch_in, do_merge):
            n = 1000
            t = np.arange(n, dtype=float) / 1000.0
            y = np.sin(2 * np.pi * 20 * t)
            return t, 1000.0, ["A-000"], y, 0, "A-000", Path(path).stem, bool(do_merge), 1

        with tempfile.TemporaryDirectory(prefix="dataprocess_rhd_svg_") as tmp:
            src = Path(tmp) / "record_0000.rhd"
            src.write_bytes(b"placeholder")
            with mock.patch(
                "web_api.rhd_viewer.rhd_service.load_channel_with_merge_option",
                side_effect=fake_load_channel_with_merge_option,
            ):
                first = self.client.get(
                    "/api/rhd/export_channel",
                    query_string={
                        "path": str(src),
                        "channel": "A-000",
                        "fmt": "svg",
                        "mode": "save",
                        "x_min": 0,
                        "x_max": 0.25,
                        "filter_type": "notch",
                        "filter_notch_hz": 60,
                        "fig_width_in": 5,
                        "fig_height_in": 3,
                        "trace_line_width": 2.5,
                        "trace_color": "#ff0000",
                    },
                )
                second = self.client.get(
                    "/api/rhd/export_channel",
                    query_string={
                        "path": str(src),
                        "channel": "A-000",
                        "fmt": "svg",
                        "mode": "save",
                        "x_min": 0,
                        "x_max": 0.25,
                    },
                )

            first_payload = first.get_json()
            second_payload = second.get_json()
            first_data = first_payload.get("data", first_payload)
            second_data = second_payload.get("data", second_payload)
            first_path = Path(first_data["saved_path"])
            second_path = Path(second_data["saved_path"])
            self.assertTrue(first_path.name.endswith("_1.svg"))
            self.assertTrue(second_path.name.endswith("_2.svg"))
            svg = first_path.read_text(encoding="utf-8")
            self.assertIn('width="360"', svg)
            self.assertIn('height="216"', svg)
            self.assertIn('stroke="#ff0000"', svg)
            self.assertIn('stroke-width="2.5"', svg)
            self.assertIn("<polyline", svg)
            self.assertNotIn("<g", svg)
            self.assertNotIn("<rect", svg)
            self.assertNotIn("grid", svg.lower())

    def test_csv_svg_export_uses_clean_numbered_trace_svg(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_csv_svg_") as tmp:
            source = Path(tmp) / "trace.csv"
            source.write_text("time,value\n0,1\n1,2\n2,0\n", encoding="utf-8")
            saved_paths = []

            for _ in range(2):
                started = self.client.post(
                    "/api/csv/export_job",
                    json={
                        "path": str(source),
                        "x_col": "time",
                        "y_col": "value",
                        "fmt": "svg",
                        "mode": "save",
                    },
                )
                started_payload = started.get_json()
                self.assertEqual(started.status_code, 200)
                self.assertTrue(started_payload["ok"])
                job = self._wait_for_api_job(started_payload["job_id"])
                self.assertEqual(job["status"], "succeeded")
                saved_paths.append(Path(job["outputs"][0]["path"]))

            self.assertTrue(saved_paths[0].name.endswith("_1.svg"))
            self.assertTrue(saved_paths[1].name.endswith("_2.svg"))
            svg = saved_paths[0].read_text(encoding="utf-8")
            self.assertIn("<polyline", svg)
            self.assertNotIn("<g", svg)
            self.assertNotIn("<rect", svg)
            self.assertNotIn("grid", svg.lower())

    def test_version_api_omits_unknown_commit_from_display_label(self) -> None:
        response = self.client.get("/api/version")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], "0.4.0")
        self.assertTrue(payload["label"].startswith("v0.4.0"))
        self.assertNotIn("unknown", payload["label"].lower())

    def test_abf_batch_dry_run_reports_plan_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_dry_run_") as tmp:
            root = Path(tmp)
            source = root / "ctrl_T1_sample_1_A_0001.abf"
            source.write_bytes(b"not a real abf")

            response = self.client.post(
                "/api/abf_batch/process",
                json={
                    "folder": str(root),
                    "main": "ctrl",
                    "treat": "T1",
                    "powers": "0, 1",
                    "move_files": True,
                    "reindex_seq": True,
                    "dry_run": True,
                },
            )
            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["data"]["dry_run"])
            self.assertGreaterEqual(payload["data"]["planned_count"], 1)
            self.assertTrue(source.exists())
            self.assertTrue(Path(payload["data"]["operation_log_path"]).exists())

    def test_fluorescence_refactor_keeps_route_contracts(self) -> None:
        routes = {str(rule.rule) for rule in self.client.application.url_map.iter_rules()}
        expected = {
            "/api/system/select_folder",
            "/api/system/select_file",
            "/api/system/logout",
            "/api/version",
            "/api/fluorescence/browse",
            "/api/fluorescence/stack_export",
            "/api/fluorescence/stack_export_job",
            "/api/fluorescence/3d/volume",
            "/api/fluorescence/3d/export_volume_job",
            "/api/fluorescence/3d/rotation_gif_preview",
            "/api/fluorescence/3d/export_rotation_gif_job",
            "/api/fluorescence/3d/intensity_distribution",
            "/api/fluorescence/gif_preview",
            "/api/fluorescence/make_gif_job",
            "/api/fluorescence/gif_roi/kymograph_export_job",
            "/api/fluorescence/roi/analyze_sequence",
            "/api/fluorescence/roi/export_sequence_gif_job",
        }
        self.assertTrue(expected.issubset(routes), sorted(expected - routes))

    def test_windows_picker_failure_returns_error_without_tk_fallback(self) -> None:
        with (
            mock.patch.object(system_api.sys, "platform", "win32"),
            mock.patch.object(
                system_api,
                "_choose_windows_folder",
                side_effect=system_api._windows_picker_error("folder", "picker unavailable"),
            ),
            mock.patch.object(system_api, "_choose_tk_folder") as tk_fallback,
        ):
            response = self.client.post("/api/system/select_folder", json={"start": ""})

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Folder picker unavailable; please paste path manually.", payload["error"])
        tk_fallback.assert_not_called()

    def test_extracted_page_assets_are_served(self) -> None:
        assets = (
            "/static/css/fluorescence_gif.css",
            "/static/js/pages/fluorescence_gif.js",
            "/static/js/pages/fluorescence_roi.js",
        )
        for route in assets:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 100)
                response.close()

    def test_core_js_exposes_command_palette_and_file_list_filter_contracts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        core_js = (root / "web_static" / "js" / "dp_core.js").read_text(encoding="utf-8")
        style_css = (root / "web_static" / "style.css").read_text(encoding="utf-8")

        self.assertIn("function openCommandPalette()", core_js)
        self.assertIn("function closeCommandPalette()", core_js)
        self.assertIn("metaKey || ev.ctrlKey", core_js)
        self.assertIn("ev.key.toLowerCase() === 'k'", core_js)
        self.assertIn("function filterFileList(listId)", core_js)
        self.assertIn("function installFileListFilters()", core_js)
        self.assertIn("function dpApplyParamGroups(selectId, attr)", core_js)
        self.assertIn("function dpApplyToggleGroups(controlId, attr)", core_js)
        self.assertIn("[hidden] { display: none !important; }", style_css)
        self.assertNotIn("btn.click();\n        btn.click();", core_js)

    def test_settings_modal_uses_tabs_instead_of_one_long_panel(self) -> None:
        root = Path(__file__).resolve().parents[1]
        modal = (
            root / "web_templates" / "partials" / "preferences_modal.html"
        ).read_text(encoding="utf-8")
        settings_js = (root / "web_static" / "js" / "dp_settings.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('data-prefs-tab="defaults"', modal)
        self.assertIn('data-prefs-tab="history"', modal)
        self.assertIn('data-prefs-tab-panel="jobs"', modal)
        self.assertIn('data-prefs-tab-panel="json"', modal)
        self.assertIn("function openPrefsTab(tab)", settings_js)

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
            tifffile.imwrite(source, arr, photometric="minisblack", metadata={"axes": "ZYX"})

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

    def test_fluorescence_3d_rotation_gif_and_distribution_routes(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"fluorescence optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_fl_3d_test_") as tmp:
            source = Path(tmp) / "tiny_3d_stack.tif"
            arr = np.zeros((4, 16, 18), dtype=np.uint16)
            for z in range(arr.shape[0]):
                arr[z, 4 + z : 8 + z, 5 + z : 9 + z] = 100 + z * 50
            tifffile.imwrite(source, arr, photometric="minisblack", metadata={"axes": "ZYX"})

            preview = self.client.post(
                "/api/fluorescence/3d/rotation_gif_preview",
                json={
                    "path": str(source),
                    "channel_mode": "current",
                    "rotation_axis": "0.2x+0.8y",
                    "gif_frames": 8,
                    "gif_size": 280,
                    "gif_points": 1000,
                    "max_points": 1000,
                    "max_xy": 64,
                    "max_z": 4,
                    "threshold_percentile": 80,
                    "show_scale_bar": True,
                    "scale_bar_um": 5,
                },
            )
            preview_payload = preview.get_json()
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertTrue(preview_payload["gif_b64"])
            self.assertIn("x", preview_payload["axis"])
            self.assertIn("y", preview_payload["axis"])

            volume = self.client.post(
                "/api/fluorescence/3d/volume",
                json={
                    "path": str(source),
                    "channel_mode": "current",
                    "max_points": 4000,
                    "max_xy": 64,
                    "max_z": 4,
                    "threshold_percentile": 80,
                    "interlayer_level": "high",
                    "density_mode": "low",
                    "density_radius_um": 3,
                    "density_min_neighbors": 2,
                },
            )
            volume_payload = volume.get_json()
            self.assertEqual(volume.status_code, 200)
            self.assertTrue(volume_payload["ok"])
            render = volume_payload["volume"]["render"]
            self.assertGreater(render["n_points"], 0)
            self.assertEqual(render["interlayer_level"], "high")
            self.assertEqual(render["interlayer_steps"], 3)
            self.assertEqual(render["density_filter"]["mode"], "low")

            distribution = self.client.post(
                "/api/fluorescence/3d/intensity_distribution",
                json={
                    "path": str(source),
                    "distribution_channel": 0,
                    "distribution_axis": "z",
                    "distribution_metric": "mean",
                    "output_name": "tiny",
                },
            )
            distribution_payload = distribution.get_json()
            self.assertEqual(distribution.status_code, 200)
            self.assertTrue(distribution_payload["ok"])
            self.assertEqual(len(distribution_payload["rows"]), 4)
            self.assertTrue(distribution_payload["plot"])
            self.assertTrue(Path(distribution_payload["csv_path"]).exists())

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
            self.assertEqual(job["outputs"][0]["role"], "full_csv")

    def test_job_routes_do_not_wrap_flask_routes(self) -> None:
        web_api = Path(__file__).resolve().parents[1] / "web_api"
        offenders = []
        for source in web_api.glob("*.py"):
            text = source.read_text(encoding="utf-8")
            if "submit_flask_route_job" in text or "test_request_context" in text:
                offenders.append(source.name)
        self.assertEqual([], offenders)

    def test_web_app_keeps_page_and_system_routes_out_of_composition_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        web_app = (root / "web_app.py").read_text(encoding="utf-8")
        self.assertNotIn('@app.route("/")', web_app)
        self.assertNotIn("/api/system/select_folder", web_app)
        self.assertIn("register_page_routes", web_app)
        self.assertIn("register_system_routes", web_app)

    def test_run_history_package_job_uses_service_task_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_run_history_test_") as tmp:
            recorded = self.client.post(
                "/api/run_history/record",
                json={
                    "project_root": tmp,
                    "view": "test_view",
                    "title": "Test Run",
                    "status": "ok",
                    "parameters": {"alpha": 1},
                    "input_files": [],
                    "outputs": [],
                },
            )
            recorded_payload = recorded.get_json()
            self.assertEqual(recorded.status_code, 200)
            self.assertTrue(recorded_payload["ok"])

            started = self.client.post(
                "/api/run_history/package_job",
                json={"manifest_path": recorded_payload["manifest_path"]},
            )
            started_payload = started.get_json()
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started_payload["ok"])

            job = self._wait_for_api_job(started_payload["job_id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(Path(job["data"]["package_path"]).exists())

    def test_openapi_json_is_available(self) -> None:
        response = self.client.get("/api/openapi.json")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("PickerRequest", payload["components"]["schemas"])

    def test_schema_validation_returns_422(self) -> None:
        response = self.client.post("/api/jobs/list", json={"limit": 9999})
        payload = response.get_json()

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid request payload")

    def test_telemetry_is_disabled_by_default(self) -> None:
        with mock.patch("web_api.telemetry._telemetry_enabled", return_value=False):
            response = self.client.post(
                "/api/telemetry/event",
                json={"event": "page_open", "view": "index"},
            )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["recorded"])

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
