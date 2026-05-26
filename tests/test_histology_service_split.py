from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services import (
    histology,
    histology_analysis,
    histology_discovery,
    histology_ets_analysis,
    histology_qupath,
)


class HistologyServiceSplitTests(unittest.TestCase):
    def test_facade_exports_core_histology_functions(self) -> None:
        self.assertEqual(histology.sanitize_name(" Case 01 / A "), "Case_01_A")
        self.assertTrue(histology.parse_bool("yes"))
        self.assertEqual(histology.normalize_rotate_deg("90"), 90)
        self.assertEqual(histology.normalize_rotate_deg("45"), 0)

    def test_discovery_finds_overview_and_qupath_display_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_discovery_") as tmp:
            root = Path(tmp)
            case = root / "Case_A"
            case.mkdir()
            overview = case / "Sample_Overview.vsi"
            overview.write_bytes(b"")
            server_dir = case / "qupath" / "data" / "1"
            server_dir.mkdir(parents=True)
            (server_dir / "server.json").write_text(
                json.dumps({"metadata": {"name": "QuPath Case A"}}),
                encoding="utf-8",
            )

            cases = histology_discovery.find_histology_cases(root)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["case_name"], "Case_A")
            self.assertEqual(cases[0]["overview_path"], str(overview.resolve()))
            self.assertEqual(cases[0]["qupath_name"], "QuPath Case A")

    def test_qupath_sync_updates_image_names_from_case_dirs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_qupath_") as tmp:
            root = Path(tmp)
            case = (root / "Case_B").resolve()
            case.mkdir()
            project = root / "project.qpproj"
            project.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "entryID": 1,
                                "imageName": "old",
                                "serverBuilder": {"uri": f"file:{case.as_posix()}/scan.vsi"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = histology_qupath.sync_qupath_names_from_histology_cases(
                [{"case_dir": str(case), "case_name": "Case_B"}],
                str(project),
                update_server_json=False,
            )

            self.assertEqual(result["updated_images"], 1)
            updated = json.loads(project.read_text(encoding="utf-8"))
            self.assertEqual(updated["images"][0]["imageName"], "Case_B")

    def test_histology_analysis_saves_rois_and_results_in_project_data(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_analysis_") as tmp:
            root = Path(tmp)
            image = root / "sample.tif"
            arr = np.zeros((24, 24, 3), dtype=np.uint8)
            arr[4:18, 4:18, 0] = 220
            arr[8:22, 8:22, 1] = 210
            tifffile.imwrite(image, arr)
            project = root / "project.qpproj"
            project.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "entryID": 7,
                                "imageName": "sample",
                                "serverBuilder": {"uri": f"file:{image.as_posix()}"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rois = [
                {
                    "id": "roi_1",
                    "label": "Lesion",
                    "points": [{"x": 2, "y": 2}, {"x": 21, "y": 2}, {"x": 21, "y": 21}, {"x": 2, "y": 21}],
                }
            ]

            loaded = histology_analysis.load_qupath_project(project)
            preview = histology_analysis.load_project_image_preview(project, "7")
            result = histology_analysis.analyze_project_rois(
                project,
                "7",
                rois,
                {
                    "sma_channel": "red",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "green",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )

            self.assertEqual(loaded["entry_count"], 1)
            self.assertEqual(preview["width"], 24)
            self.assertEqual(result["roi_count"], 1)
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertGreater(result["results"][0]["macrophage_positive_px"], 0)
            self.assertGreaterEqual(result["results"][0]["sma_object_count"], 1)
            self.assertTrue(Path(result["analysis_path"]).exists())
            self.assertTrue(Path(result["geojson_path"]).exists())
            updated = json.loads(project.read_text(encoding="utf-8"))
            self.assertIn("dataprocessHistologyAnalysis", updated)

    def test_histology_ets_analysis_saves_rois_and_results_in_sidecar_project(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology ETS analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_") as tmp:
            root = Path(tmp)
            case = root / "5-CB"
            stack = case / "_Tray04_Slide01_01_" / "stack1"
            stack.mkdir(parents=True)
            (case / "Tray04_Slide01_Overview.vsi").write_bytes(b"")
            image = stack / "frame_t_0.ets"
            arr = np.zeros((24, 24, 3), dtype=np.uint8)
            arr[4:18, 4:18, 0] = 220
            arr[8:22, 8:22, 1] = 210
            tifffile.imwrite(image, arr)
            rois = [
                {
                    "id": "roi_1",
                    "label": "Lesion",
                    "points": [{"x": 2, "y": 2}, {"x": 21, "y": 2}, {"x": 21, "y": 21}, {"x": 2, "y": 21}],
                }
            ]

            loaded = histology_ets_analysis.load_ets_project(root)
            entry_id = loaded["entries"][0]["entry_id"]
            preview = histology_ets_analysis.load_ets_image_preview(root, entry_id)
            result = histology_ets_analysis.analyze_ets_rois(
                root,
                entry_id,
                rois,
                {
                    "sma_channel": "red",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "green",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )
            file_preview = histology_ets_analysis.load_histology_file_image_preview(image)
            file_result = histology_ets_analysis.analyze_histology_file_rois(
                image,
                rois,
                {
                    "sma_channel": "red",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "green",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )

            self.assertEqual(loaded["entry_count"], 1)
            self.assertEqual(preview["width"], 24)
            self.assertEqual(result["roi_count"], 1)
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertGreater(result["results"][0]["macrophage_positive_px"], 0)
            self.assertTrue(Path(result["analysis_path"]).exists())
            self.assertTrue(Path(result["geojson_path"]).exists())
            index_path = Path(result["project_path"])
            self.assertTrue(index_path.exists())
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index["protocol"], "dataprocess-ets-histology")
            self.assertEqual(index["entry_count"], 1)
            self.assertEqual(file_preview["width"], 24)
            self.assertEqual(file_result["roi_count"], 1)
            self.assertEqual(file_result["kind"], "single_file_histology_analysis")
            self.assertGreater(file_result["results"][0]["macrophage_positive_px"], 0)

    def test_histology_data_project_references_external_images_and_renames_entries(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology data project optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_project_") as tmp:
            tmp_root = Path(tmp)
            source_root = tmp_root / "source"
            stack = source_root / "5-CB" / "_Tray04_Slide01_01_" / "stack1"
            stack.mkdir(parents=True)
            image = stack / "frame_t_0.ets"
            arr = np.zeros((22, 22, 3), dtype=np.uint8)
            arr[4:16, 4:16, 0] = 220
            arr[8:20, 8:20, 1] = 210
            tifffile.imwrite(image, arr)
            project = tmp_root / "project_home" / "study.dphistology"
            rois = [
                {
                    "id": "roi_external",
                    "label": "External ROI",
                    "points": [{"x": 2, "y": 2}, {"x": 19, "y": 2}, {"x": 19, "y": 19}, {"x": 2, "y": 19}],
                }
            ]

            created = histology_ets_analysis.create_histology_data_project(project)
            loaded = histology_ets_analysis.add_histology_data_project_paths(
                created["project_path"],
                [source_root],
            )
            entry_id = loaded["entries"][0]["entry_id"]
            renamed = histology_ets_analysis.rename_histology_data_project_entry(
                project,
                entry_id,
                "5-CB SMA macrophage",
            )
            preview = histology_ets_analysis.load_histology_data_project_image_preview(project, entry_id)
            result = histology_ets_analysis.analyze_histology_data_project_rois(
                project,
                entry_id,
                rois,
                {
                    "sma_channel": "red",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "green",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )

            self.assertEqual(created["entry_count"], 0)
            self.assertEqual(loaded["entry_count"], 1)
            self.assertEqual(loaded["added_count"], 1)
            self.assertEqual(renamed["renamed_entry"]["image_name"], "5-CB SMA macrophage")
            self.assertEqual(preview["width"], 22)
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertTrue(Path(result["analysis_path"]).exists())
            self.assertTrue(Path(result["geojson_path"]).exists())
            self.assertTrue(Path(result["project_path"]).exists())
            self.assertIn("project_home", result["analysis_path"])
            self.assertFalse((source_root / ".dataprocess_histology").exists())

    def test_histology_data_project_folder_path_creates_reusable_local_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_project_file_") as tmp:
            project_folder = Path(tmp) / "study_project"
            project_folder.mkdir()

            created = histology_ets_analysis.create_histology_data_project(project_folder)
            project_path = Path(created["project_path"])
            loaded = histology_ets_analysis.load_histology_data_project(project_folder)

            self.assertEqual(project_path.name, "histology_project.dphistology")
            self.assertTrue(project_path.is_file())
            self.assertEqual(loaded["project_path"], str(project_path))
            self.assertEqual(created["kind"], "dataprocess_histology_project")


if __name__ == "__main__":
    unittest.main()
