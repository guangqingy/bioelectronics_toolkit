from __future__ import annotations

import base64
import json
import os
import struct
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from services import (
    histology,
    histology_discovery,
    histology_project,
)
from services.histology_ets_convert import CONVERTER_VERSION, convert_ets_to_tiff, read_ets_index


def _write_fake_ets(path: Path, value: int = 180) -> None:
    import numpy as np
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    tile = np.zeros((16, 16, 3), dtype=np.uint8)
    tile[..., 0] = value
    tile[..., 1] = 80
    tile[..., 2] = 30
    stream = BytesIO()
    Image.fromarray(tile, "RGB").save(stream, format="JPEG", quality=90)
    blob = stream.getvalue()
    tile_table_offset = 64 + 228
    data_offset = tile_table_offset + 36
    sis = struct.pack(
        "<4sIIIQIIQIIIIII",
        b"SIS\0",
        64,
        2,
        4,
        64,
        228,
        0,
        tile_table_offset,
        1,
        0,
        0,
        0,
        0,
        0,
    )
    ets = struct.pack("<4sIIIIIIIII", b"ETS\0", 0x30001, 2, 3, 4, 2, 90, 16, 16, 1)
    tile_record = struct.pack("<IIIIIQII", 4, 0, 0, 0, 0, data_offset, len(blob), 0)
    with path.open("wb") as handle:
        handle.write(sis)
        handle.write(ets)
        handle.write(b"\0" * (228 - len(ets)))
        handle.write(tile_record)
        handle.write(blob)


def _write_fake_multiz_ets(path: Path) -> None:
    import numpy as np
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    yy, xx = np.indices((16, 16))
    planes = [
        np.full((16, 16), 255, dtype=np.uint8),
        np.where((xx + yy) % 2 == 0, 20, 220).astype(np.uint8),
        np.full((16, 16), 120, dtype=np.uint8),
    ]
    blobs: list[bytes] = []
    for plane in planes:
        stream = BytesIO()
        Image.fromarray(np.stack([plane, plane, plane], axis=-1), "RGB").save(
            stream,
            format="JPEG",
            quality=90,
        )
        blobs.append(stream.getvalue())

    tile_table_offset = 64 + 228
    data_offset = tile_table_offset + 36 * len(blobs)
    sis = struct.pack(
        "<4sIIIQIIQIIIIII",
        b"SIS\0",
        64,
        2,
        4,
        64,
        228,
        0,
        tile_table_offset,
        len(blobs),
        0,
        0,
        0,
        0,
        0,
    )
    ets = struct.pack("<4sIIIIIIIII", b"ETS\0", 0x30001, 2, 3, 4, 2, 90, 16, 16, 1)
    records = []
    offset = data_offset
    for z, blob in enumerate(blobs):
        records.append(struct.pack("<IIIIIQII", 4, 0, 0, z, 0, offset, len(blob), 0))
        offset += len(blob)
    with path.open("wb") as handle:
        handle.write(sis)
        handle.write(ets)
        handle.write(b"\0" * (228 - len(ets)))
        for record in records:
            handle.write(record)
        for blob in blobs:
            handle.write(blob)


class HistologyServiceSplitTests(unittest.TestCase):
    def test_facade_exports_core_histology_functions(self) -> None:
        self.assertEqual(histology.sanitize_name(" Case 01 / A "), "Case_01_A")
        self.assertTrue(histology.parse_bool("yes"))
        self.assertEqual(histology.normalize_rotate_deg("90"), 90)
        self.assertEqual(histology.normalize_rotate_deg("45"), 0)

    def test_discovery_finds_overview_cases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_discovery_") as tmp:
            root = Path(tmp)
            case = root / "Case_A"
            case.mkdir()
            overview = case / "Sample_Overview.vsi"
            overview.write_bytes(b"")

            cases = histology_discovery.find_histology_cases(root)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["case_name"], "Case_A")
            self.assertEqual(cases[0]["overview_path"], str(overview.resolve()))

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

            file_preview = histology_project.load_histology_file_image_preview(image)
            file_result = histology_project.analyze_histology_file_rois(
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
                histology_project.load_histology_file_image_preview(raw_ets)

    def test_histology_scan_converts_ets_to_case_folder_tiff(self) -> None:
        try:
            import tifffile  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"histology ETS conversion optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_scan_") as tmp:
            root = Path(tmp) / "04-01-2026"
            case = root / "5-CB"
            (case / "Tray04_Slide01_01.vsi").parent.mkdir(parents=True)
            (case / "Tray04_Slide01_01.vsi").write_text("vsi", encoding="utf-8")
            (case / "Tray04_Slide01_Overview.vsi").write_text("overview-vsi", encoding="utf-8")
            _write_fake_ets(case / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets")
            _write_fake_ets(case / "_Tray04_Slide01_Overview_" / "stack1" / "frame_t.ets", value=90)
            _write_fake_ets(case / "_Tray04_Slide01_Overview_" / "stack10000" / "frame_t.ets", value=70)

            scanned = histology.scan_exported_tiff_project(root)

            converted = case / "5-CB_Brightfield.tif"
            overview = case / "5-CB_Overview.tif"
            self.assertEqual(scanned["sample_count"], 1)
            self.assertEqual(scanned["image_count"], 1)
            self.assertEqual(scanned["ets_converted_file_count"], 2)
            self.assertTrue(converted.is_file())
            self.assertTrue(overview.is_file())
            self.assertFalse(list(case.glob("*stack10000*.tif")))
            self.assertEqual(scanned["samples"][0]["sample_id"], "5-CB")
            self.assertEqual(scanned["samples"][0]["metadata"]["case_dir"], str(case.resolve()))
            self.assertEqual(
                scanned["samples"][0]["image_files"],
                {"Brightfield": str(converted.resolve())},
            )
            self.assertEqual(
                scanned["samples"][0]["metadata"]["overview_vsi_path"],
                str((case / "Tray04_Slide01_Overview.vsi").resolve()),
            )
            self.assertIn(
                "skipped_duplicate_role",
                {item["status"] for item in scanned["ets_conversions"]},
            )

    def test_histology_scan_reuses_existing_case_tiff_and_ignores_stack_derivatives(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology TIFF optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_existing_tif_") as tmp:
            root = Path(tmp) / "04-01-2026"
            case = root / "2-Hy"
            case.mkdir(parents=True)
            (case / "Tray04_Slide02_01.vsi").write_text("vsi", encoding="utf-8")
            _write_fake_ets(case / "_Tray04_Slide02_01_" / "stack1" / "frame_t_0.ets")
            existing = case / "2-Hy_Brightfield.tif"
            derivative = case / "2-Hy_Tray04_Slide02_02_stack1_Brightfield.tif"
            tifffile.imwrite(existing, np.ones((12, 12), dtype=np.uint8) * 33)
            tifffile.imwrite(derivative, np.ones((12, 12), dtype=np.uint8) * 99)

            scanned = histology.scan_exported_tiff_project(root)

            self.assertEqual(scanned["sample_count"], 1)
            self.assertEqual(scanned["image_count"], 1)
            self.assertEqual(scanned["samples"][0]["sample_id"], "2-Hy")
            self.assertEqual(scanned["samples"][0]["image_files"], {"Brightfield": str(existing.resolve())})
            self.assertIn("skipped_existing_tiff", {item["status"] for item in scanned["ets_conversions"]})

    def test_histology_project_rename_updates_converted_tiff_and_case_folder(self) -> None:
        try:
            import tifffile  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"histology ETS conversion optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_rename_") as tmp:
            tmp_root = Path(tmp)
            root = tmp_root / "04-01-2026"
            case = root / "5-CB"
            (case / "Tray04_Slide01_01.vsi").parent.mkdir(parents=True)
            (case / "Tray04_Slide01_01.vsi").write_text("vsi", encoding="utf-8")
            _write_fake_ets(case / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets")

            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, root)
            entry_id = created["entries"][0]["entry_id"]
            renamed = histology_project.rename_histology_data_project_entry(
                project,
                entry_id,
                "6-CB",
            )

            new_case = root / "6-CB"
            new_tiff = new_case / "6-CB_Brightfield.tif"
            self.assertTrue(new_case.is_dir())
            self.assertFalse(case.exists())
            self.assertTrue(new_tiff.is_file())
            self.assertEqual(renamed["renamed_entry"]["image_name"], "6-CB")
            self.assertEqual(renamed["renamed_entry"]["sample_id"], "6-CB")
            self.assertEqual(renamed["renamed_entry"]["image_path"], str(new_tiff.resolve()))
            self.assertTrue(renamed["physical_rename"]["renamed"])

    def test_histology_image_loading_respects_memory_guard(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology image guard optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_guard_") as tmp:
            image = Path(tmp) / "large.tif"
            tifffile.imwrite(image, np.zeros((12, 12), dtype=np.uint8))

            with mock.patch.dict(os.environ, {"DP_HISTOLOGY_MAX_IMAGE_PIXELS": "10"}):
                with self.assertRaisesRegex(ValueError, "too large to load safely"):
                    histology.load_image_for_analysis(image)

    def test_histology_preview_streams_tiled_tiff_when_full_load_guard_would_fail(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology preview optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_preview_guard_") as tmp:
            image = Path(tmp) / "preview.tif"
            arr = np.zeros((512, 512, 3), dtype=np.uint8)
            ramp = np.arange(512, dtype=np.uint16)
            arr[..., 0] = (ramp[None, :] // 2).astype(np.uint8)
            arr[..., 1] = (ramp[:, None] // 2).astype(np.uint8)
            tifffile.imwrite(image, arr, tile=(16, 16), compression="deflate")

            with mock.patch.dict(os.environ, {"DP_HISTOLOGY_MAX_IMAGE_PIXELS": "10"}):
                preview = histology_project.load_histology_file_image_preview(image, max_side=32)

            self.assertEqual(preview["backend"], "tifffile_tiled_preview")
            self.assertEqual(preview["width"], 512)
            self.assertEqual(preview["height"], 512)
            self.assertEqual(preview["preview_width"], 256)
            self.assertEqual(preview["preview_height"], 256)
            self.assertTrue(preview["img"])

    def test_ets_index_respects_tile_memory_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_guard_") as tmp:
            ets = Path(tmp) / "5-CB" / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets"
            _write_fake_ets(ets)

            with mock.patch.dict(os.environ, {"DP_HISTOLOGY_MAX_ETS_TILE_PIXELS": "128"}):
                with self.assertRaisesRegex(MemoryError, "ETS tile size"):
                    read_ets_index(ets)

    def test_ets_conversion_selects_textured_plane_for_multiz_ets(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology ETS conversion optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_multiz_") as tmp:
            root = Path(tmp)
            ets = root / "5-CB" / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets"
            output = root / "5-CB" / "5-CB_Brightfield.tif"
            _write_fake_multiz_ets(ets)

            result = convert_ets_to_tiff(ets, output)
            arr = tifffile.imread(output)
            sidecar = output.with_suffix(output.suffix + ".dataprocess_ets.json")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))

            self.assertEqual(result.z_plane_count, 3)
            self.assertEqual(result.selected_z, 1)
            self.assertGreater(float(arr.std()), 40.0)
            self.assertEqual(payload["converter_version"], CONVERTER_VERSION)
            self.assertEqual(payload["index"]["selected_z"], 1)

    def test_histology_data_project_indexes_raw_files_and_analyzes_exported_tiffs(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image
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
            loaded = histology_project.load_histology_data_project(project)
            entry_id = loaded["entries"][0]["entry_id"]
            renamed = histology_project.rename_histology_data_project_entry(
                project,
                entry_id,
                "5-CB SMA macrophage",
            )
            preview = histology_project.load_histology_data_project_image_preview(project, entry_id)
            result = histology_project.analyze_histology_data_project_rois(
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
            self.assertIn("FITC", preview["preview_channels"])
            with Image.open(BytesIO(base64.b64decode(preview["img"]))) as preview_img:
                preview_arr = np.asarray(preview_img.convert("RGB"))
            self.assertTrue(np.any(preview_arr[..., 0] != preview_arr[..., 1]))
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

            created = histology_project.create_histology_data_project(project_folder)
            project_path = Path(created["project_path"])
            loaded = histology_project.load_histology_data_project(project_folder)

            self.assertEqual(project_path.name, "histology_project.dphistology")
            self.assertTrue(project_path.is_file())
            self.assertEqual(loaded["project_path"], str(project_path))
            self.assertEqual(created["kind"], "dataprocess_histology_project")
            self.assertEqual(Path(created["cache_dir"]).name, "cache")
            self.assertTrue(Path(created["cache_layout"]["previews"]).is_dir())
            self.assertTrue(Path(created["cache_layout"]["converted"]).is_dir())
            self.assertTrue(Path(created["cache_layout"]["metadata"]).is_dir())

    def test_histology_data_project_rejects_image_or_binary_project_path_cleanly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_bad_project_") as tmp:
            root = Path(tmp)
            image = root / "1-CB_Brightfield.tif"
            image.write_bytes(b"\xee\x00not-json")
            broken_project = root / "broken.dphistology"
            broken_project.write_bytes(b"\xee\x00not-json")

            with self.assertRaisesRegex(ValueError, "not a DataProcess histology project"):
                histology_project.load_histology_data_project(image)
            with self.assertRaisesRegex(ValueError, "not valid UTF-8 JSON"):
                histology_project.load_histology_data_project(broken_project)


if __name__ == "__main__":
    unittest.main()
