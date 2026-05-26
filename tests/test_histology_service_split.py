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
                    "sma_channel": "green",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "red",
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

    def test_histology_file_analysis_runs_on_exported_tiff_and_rejects_raw_ets(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology file analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_file_") as tmp:
            root = Path(tmp)
            image = root / "5-CB_channels.tif"
            arr = np.zeros((24, 24, 3), dtype=np.uint8)
            arr[4:18, 4:18, 0] = 220
            arr[8:22, 8:22, 1] = 210
            tifffile.imwrite(image, arr)
            raw_ets = root / "frame_t_0.ets"
            raw_ets.write_bytes(b"SIS\x00")
            rois = [
                {
                    "id": "roi_1",
                    "label": "Lesion",
                    "points": [{"x": 2, "y": 2}, {"x": 21, "y": 2}, {"x": 21, "y": 21}, {"x": 2, "y": 21}],
                }
            ]

            file_preview = histology_ets_analysis.load_histology_file_image_preview(image)
            file_result = histology_ets_analysis.analyze_histology_file_rois(
                image,
                rois,
                {
                    "sma_channel": "green",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "red",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )

            self.assertEqual(file_preview["width"], 24)
            self.assertEqual(file_result["roi_count"], 1)
            self.assertEqual(file_result["kind"], "single_file_histology_analysis")
            self.assertGreater(file_result["results"][0]["macrophage_positive_px"], 0)
            with self.assertRaises(ValueError):
                histology_ets_analysis.load_histology_file_image_preview(raw_ets)

    def test_histology_data_project_indexes_raw_files_and_analyzes_exported_tiffs(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology data project optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_project_") as tmp:
            tmp_root = Path(tmp)
            raw_root = tmp_root / "raw_olympus"
            raw_case = raw_root / "5-CB"
            (raw_case / "_Tray04_Slide01_01_" / "stack1").mkdir(parents=True)
            (raw_case / "_Tray04_Slide01_Overview_" / "stack1").mkdir(parents=True)
            (raw_case / "Tray04_Slide01_01.vsi").write_bytes(b"raw-vsi")
            (raw_case / "Tray04_Slide01_Overview.vsi").write_bytes(b"overview-vsi")
            (raw_case / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets").write_bytes(b"SIS\x00")
            (raw_case / "_Tray04_Slide01_Overview_" / "stack1" / "frame_t.ets").write_bytes(b"SIS\x00")

            exported = tmp_root / "exported_tiff"
            exported.mkdir()
            hoechst = np.zeros((22, 22), dtype=np.uint16)
            fitc = np.zeros((22, 22), dtype=np.uint16)
            cy5 = np.zeros((22, 22), dtype=np.uint16)
            bf = np.full((22, 22), 150, dtype=np.uint16)
            hoechst[2:20, 2:20] = 1200
            fitc[4:16, 4:16] = 50000
            cy5[8:20, 8:20] = 45000
            tifffile.imwrite(exported / "5-CB_Hoechst.tif", hoechst)
            tifffile.imwrite(exported / "5-CB_FITC.tif", fitc)
            tifffile.imwrite(exported / "5-CB_Cy5.tif", cy5)
            tifffile.imwrite(exported / "5-CB_BF.tif", bf)

            project = tmp_root / "project_home" / "study.dphistology"
            analysis_dir = tmp_root / "project_home" / "analysis"
            rois = [
                {
                    "id": "roi_external",
                    "label": "External ROI",
                    "points": [{"x": 2, "y": 2}, {"x": 19, "y": 2}, {"x": 19, "y": 19}, {"x": 2, "y": 19}],
                }
            ]

            scanned = histology.scan_exported_tiff_project(exported, raw_dir=raw_root, analysis_dir=analysis_dir)
            created = histology.create_project_from_exported_tiff(
                project,
                exported,
                raw_dir=raw_root,
                analysis_dir=analysis_dir,
            )
            loaded = histology_ets_analysis.load_histology_data_project(project)
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
                    "sma_channel": "green",
                    "sma_threshold_method": "manual",
                    "sma_threshold": 120,
                    "macrophage_channel": "red",
                    "macrophage_threshold_method": "manual",
                    "macrophage_threshold": 120,
                    "background_mode": "none",
                },
            )

            self.assertEqual(scanned["sample_count"], 1)
            self.assertEqual(scanned["raw_olympus_file_count"], 4)
            self.assertEqual(created["protocol"], "dataprocess-tiff-histology")
            self.assertEqual(created["entry_count"], 1)
            self.assertTrue(Path(created["project_path"]).is_file())
            self.assertTrue(Path(created["raw_olympus_index_path"]).is_file())
            self.assertEqual(loaded["entry_count"], 1)
            self.assertIn("Hoechst", loaded["entries"][0]["image_files"])
            self.assertIn("FITC", loaded["entries"][0]["image_files"])
            self.assertIn("Cy5", loaded["entries"][0]["image_files"])
            self.assertTrue(Path(loaded["entries"][0]["manifest_path"]).is_file())
            self.assertTrue(Path(loaded["entries"][0]["parameters_path"]).is_file())
            self.assertEqual(renamed["renamed_entry"]["image_name"], "5-CB SMA macrophage")
            self.assertEqual(preview["width"], 22)
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertGreater(result["results"][0]["macrophage_positive_px"], 0)
            self.assertTrue(Path(result["analysis_path"]).exists())
            self.assertTrue(Path(result["geojson_path"]).exists())
            self.assertTrue(Path(result["project_path"]).exists())
            self.assertIn("project_home", result["analysis_path"])
            self.assertFalse((raw_root / ".dataprocess_histology").exists())

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
            self.assertEqual(Path(created["cache_dir"]).name, "cache")
            self.assertTrue(Path(created["cache_layout"]["previews"]).is_dir())
            self.assertTrue(Path(created["cache_layout"]["converted"]).is_dir())
            self.assertTrue(Path(created["cache_layout"]["metadata"]).is_dir())


if __name__ == "__main__":
    unittest.main()
