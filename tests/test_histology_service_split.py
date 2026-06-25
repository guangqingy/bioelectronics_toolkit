from __future__ import annotations

import base64
import csv
import importlib
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
        self.assertTrue(callable(histology.debug_histology_data_project_roi))

    def test_histology_project_is_split_across_focused_modules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        module_names = [
            "services.histology_project",
            "services.histology_project_preview",
            "services.histology_roi_analysis",
            "services.histology_data_project",
            "services.histology_data_project_rename",
            "services.histology_image_io",
            "services.histology_batch_analysis",
        ]
        for module_name in module_names:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(Path(module.__file__ or "").is_file())

        project_lines = (root / "services" / "histology_project.py").read_text(encoding="utf-8").count("\n") + 1
        preview_lines = (
            root / "services" / "histology_project_preview.py"
        ).read_text(encoding="utf-8").count("\n") + 1
        roi_analysis_lines = (
            root / "services" / "histology_roi_analysis.py"
        ).read_text(encoding="utf-8").count("\n") + 1
        data_project_lines = (
            root / "services" / "histology_data_project.py"
        ).read_text(encoding="utf-8").count("\n") + 1
        data_project_rename_lines = (
            root / "services" / "histology_data_project_rename.py"
        ).read_text(encoding="utf-8").count("\n") + 1
        self.assertLess(project_lines, 1000)
        self.assertLess(preview_lines, 850)
        self.assertLess(roi_analysis_lines, 650)
        self.assertLess(data_project_lines, 1000)
        self.assertLess(data_project_rename_lines, 300)
        self.assertTrue(callable(histology_project.create_histology_data_project))
        self.assertTrue(callable(histology_project.load_histology_data_project_image_preview))
        self.assertTrue(callable(histology_project.analyze_histology_data_project_saved_rois))

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

    def test_histology_project_preview_preserves_single_rgb_tiled_image_color(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image
        except ImportError as exc:
            self.skipTest(f"histology preview optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_rgb_preview_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            yy, xx = np.indices((64, 64))
            arr = np.zeros((64, 64, 3), dtype=np.uint8)
            arr[..., 0] = (xx * 4).astype(np.uint8)
            arr[..., 1] = (yy * 4).astype(np.uint8)
            arr[..., 2] = 40
            tifffile.imwrite(exported / "1-CB.tif", arr, tile=(16, 16), compression="deflate")
            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            entry_id = created["entries"][0]["entry_id"]

            preview = histology_project.load_histology_data_project_image_preview(project, entry_id)

            self.assertEqual(preview["backend"], "composite_preview:tifffile_tiled_preview")
            with Image.open(BytesIO(base64.b64decode(preview["img"]))) as preview_img:
                preview_arr = np.asarray(preview_img.convert("RGB"))
            self.assertTrue(np.any(preview_arr[..., 0] != preview_arr[..., 1]))
            self.assertTrue(np.any(preview_arr[..., 1] != preview_arr[..., 2]))

    def test_histology_project_preview_warns_for_monochrome_rgb_brightfield(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image
        except ImportError as exc:
            self.skipTest(f"histology preview optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_mono_rgb_preview_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            yy, xx = np.indices((64, 64))
            gray = ((xx + yy) * 2).astype(np.uint8)
            arr = np.repeat(gray[..., None], 3, axis=-1)
            tifffile.imwrite(exported / "1-CB_Brightfield.tif", arr, tile=(16, 16), compression="deflate")
            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            entry_id = created["entries"][0]["entry_id"]

            preview = histology_project.load_histology_data_project_image_preview(project, entry_id)

            self.assertEqual(preview["preview_channels"], ["Brightfield"])
            warning_text = " ".join(preview["warnings"])
            self.assertIn("RGB channels are identical", warning_text)
            self.assertIn("only a brightfield/transmitted image is indexed", warning_text)
            self.assertIn("display-only pseudocolor/contrast", warning_text)
            with Image.open(BytesIO(base64.b64decode(preview["img"]))) as preview_img:
                preview_arr = np.asarray(preview_img.convert("RGB"))
            self.assertTrue(np.any(preview_arr[..., 0] != preview_arr[..., 1]))
            self.assertLessEqual(int(preview_arr.max()), 235)

    def test_histology_project_preview_suppresses_expected_rgb_channel_warning(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology preview optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_rgb_channel_warning_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            yy, xx = np.indices((32, 32))
            arr = np.zeros((32, 32, 3), dtype=np.uint8)
            arr[..., 1] = ((xx + yy) * 4).astype(np.uint8)
            tifffile.imwrite(exported / "1-CB_FITC.tif", arr)
            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            entry_id = created["entries"][0]["entry_id"]

            preview = histology_project.load_histology_data_project_image_preview(
                project,
                entry_id,
                selected_channels=["FITC"],
            )

            self.assertEqual(preview["preview_channels"], ["FITC"])
            warning_text = " ".join(preview["warnings"])
            self.assertNotIn("Multi-channel/color image stored in one file", warning_text)
            self.assertNotIn("TIFF is not 16-bit", warning_text)
            self.assertIn("Fluorescence TIFFs are 8-bit", warning_text)

    def test_histology_project_flags_legacy_multiz_brightfield_conversion(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology preview optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_legacy_multiz_") as tmp:
            tmp_root = Path(tmp)
            image = tmp_root / "5-CB_Brightfield.tif"
            tifffile.imwrite(image, np.zeros((12, 12), dtype=np.uint8))
            project = tmp_root / "study.dphistology"
            project.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "protocol": "dataprocess-tiff-histology",
                        "kind": "dataprocess_histology_project",
                        "project_name": "study",
                        "project_path": str(project),
                        "images": [
                            {
                                "entry_id": "sample_5cb",
                                "record_type": "sample",
                                "sample_id": "5-CB",
                                "image_name": "5-CB",
                                "image_path": str(image),
                                "source_path": str(image),
                                "image_files": {"Brightfield": str(image)},
                                "converted_from_ets": [
                                    {
                                        "source_path": str(tmp_root / "frame_t_0.ets"),
                                        "output_path": str(image),
                                        "role": "brightfield",
                                        "status": "skipped_existing",
                                        "z_plane_count": 3,
                                        "selected_z": 2,
                                    }
                                ],
                                "warnings": ["Missing expected fluorescence channel."],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = histology_project.load_histology_data_project(project)
            entry = loaded["entries"][0]
            preview = histology_project.load_histology_data_project_image_preview(project, "sample_5cb")

            self.assertIn("Brightfield", entry["image_files"])
            warning_text = " ".join(entry["warnings"])
            self.assertIn("Legacy ETS conversion collapsed a multi-channel ETS", warning_text)
            self.assertIn("selected z=2", warning_text)
            self.assertIn("Legacy ETS conversion collapsed a multi-channel ETS", " ".join(preview["warnings"]))

    def test_histology_project_analysis_reports_channel_read_failures(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_channel_error_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            tifffile.imwrite(exported / "1-CB_Hoechst.tif", np.zeros((12, 12), dtype=np.uint8))
            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            entry_id = created["entries"][0]["entry_id"]
            rois = [
                {
                    "id": "roi_1",
                    "label": "ROI 1",
                    "points": [{"x": 1, "y": 1}, {"x": 10, "y": 1}, {"x": 10, "y": 10}, {"x": 1, "y": 10}],
                }
            ]

            with mock.patch.dict(os.environ, {"DP_HISTOLOGY_MAX_IMAGE_PIXELS": "10"}):
                with self.assertRaisesRegex(
                    ValueError,
                    "No readable exported image channels.*Hoechst: Image is too large.*DP_HISTOLOGY_MAX_IMAGE_PIXELS",
                ):
                    histology_project.analyze_histology_data_project_rois(project, entry_id, rois)

    def test_histology_project_analysis_uses_native_tiff_region_for_preview_roi(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology region analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_region_analysis_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            arr = np.zeros((64, 64, 3), dtype=np.uint8)
            arr[20:44, 20:44, 0] = 240
            arr[20:44, 20:44, 1] = 230
            tifffile.imwrite(
                exported / "1-CB.tif",
                arr,
                tile=(16, 16),
                compression="deflate",
                resolution=(20000, 20000),
                resolutionunit="CENTIMETER",
            )
            project = tmp_root / "project_home" / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            entry_id = created["entries"][0]["entry_id"]
            rois = [
                {
                    "id": "roi_native",
                    "label": "Native ROI",
                    "coordinate_space": "native",
                    "points": [{"x": 20, "y": 20}, {"x": 43, "y": 20}, {"x": 43, "y": 43}, {"x": 20, "y": 43}],
                }
            ]

            with mock.patch.dict(os.environ, {"DP_HISTOLOGY_MAX_IMAGE_PIXELS": "10"}):
                result = histology_project.analyze_histology_data_project_rois(
                    project,
                    entry_id,
                    rois,
                    {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 200,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 200,
                        "background_mode": "none",
                        "smooth_sigma": 0,
                        "min_positive_area_px": 1,
                    },
                )

            self.assertEqual(result["width"], 64)
            self.assertEqual(result["height"], 64)
            self.assertIn("region", result["backend"])
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertGreater(result["results"][0]["macrophage_positive_px"], 0)
            self.assertLess(result["analysis"]["analysis_region"]["width"], 64)
            self.assertAlmostEqual(result["analysis"]["calibration"]["pixel_width_um"], 0.5)
            self.assertAlmostEqual(
                result["results"][0]["area_um2"],
                result["results"][0]["area_px"] * 0.25,
            )
            self.assertGreater(result["results"][0]["sma_object_density_per_mm2"], 0)

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

    def test_histology_scan_expands_multiz_ets_into_fluorescence_channels(self) -> None:
        try:
            import tifffile  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"histology ETS conversion optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_channels_") as tmp:
            root = Path(tmp) / "04-01-2026"
            case = root / "5-CB"
            case.mkdir(parents=True)
            (case / "Tray04_Slide01_01.vsi").write_text("vsi", encoding="utf-8")
            _write_fake_multiz_ets(case / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets")

            scanned = histology.scan_exported_tiff_project(root)

            image_files = scanned["samples"][0]["image_files"]
            self.assertIn("Hoechst", image_files)
            self.assertIn("FITC", image_files)
            self.assertIn("Cy5", image_files)
            self.assertNotIn("Brightfield", image_files)
            self.assertTrue((case / "5-CB_Hoechst.tif").is_file())
            self.assertTrue((case / "5-CB_FITC.tif").is_file())
            self.assertTrue((case / "5-CB_Cy5.tif").is_file())
            roles = {
                item["role"]
                for item in scanned["ets_conversions"]
                if item["status"] in {"converted", "skipped_existing", "skipped_existing_tiff"}
            }
            self.assertTrue({"Hoechst", "FITC", "Cy5"} <= roles)

    def test_histology_scan_adds_multiz_channels_next_to_legacy_brightfield(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology ETS conversion optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_ets_existing_bf_") as tmp:
            root = Path(tmp) / "04-01-2026"
            case = root / "5-CB"
            case.mkdir(parents=True)
            tifffile.imwrite(case / "5-CB_Brightfield.tif", np.zeros((16, 16), dtype=np.uint8))
            _write_fake_multiz_ets(case / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets")
            project = root / "histology_project.dphistology"

            scanned = histology.scan_exported_tiff_project(root)
            created = histology.create_project_from_exported_tiff(project, root)

            image_files = scanned["samples"][0]["image_files"]
            self.assertIn("Brightfield", image_files)
            self.assertIn("Hoechst", image_files)
            self.assertIn("FITC", image_files)
            self.assertIn("Cy5", image_files)
            self.assertTrue(str(created["entries"][0]["image_path"]).endswith("5-CB_Hoechst.tif"))

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
            self.assertEqual(preview["preview_channels"], ["FITC"])
            self.assertNotIn("analysis uses the indexed fluorescence channels", " ".join(preview["warnings"]))
            multi_preview = histology_project.load_histology_data_project_image_preview(
                project,
                entry_id,
                selected_channels=["Hoechst", "FITC", "Cy5"],
            )
            self.assertEqual(multi_preview["preview_channels"], ["Hoechst", "FITC", "Cy5"])
            with Image.open(BytesIO(base64.b64decode(preview["img"]))) as preview_img:
                preview_arr = np.asarray(preview_img.convert("RGB"))
            self.assertEqual(preview_arr.shape[:2], (22, 22))
            self.assertGreater(result["results"][0]["sma_positive_px"], 0)
            self.assertGreater(result["results"][0]["macrophage_positive_px"], 0)
            self.assertTrue(Path(result["analysis_path"]).exists())
            self.assertTrue(Path(result["geojson_path"]).exists())
            self.assertTrue(Path(result["project_path"]).exists())
            self.assertIn("project_home", result["analysis_path"])
            self.assertFalse((raw_root / ".dataprocess_histology").exists())

    def test_histology_project_saved_roi_batch_outputs_normalized_tables_and_plots(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology saved ROI batch optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_batch_") as tmp:
            tmp_root = Path(tmp)
            exported = tmp_root / "exported"
            exported.mkdir()
            for group in range(1, 4):
                hoechst = np.zeros((32, 32), dtype=np.uint16)
                fitc = np.zeros((32, 32), dtype=np.uint16)
                cy5 = np.zeros((32, 32), dtype=np.uint16)
                hoechst[2:30, 2:30] = 1200
                fitc[4 : 4 + 5 * group, 4:22] = 50000
                cy5[8 : 8 + 4 * group, 8:24] = 45000
                tifffile.imwrite(exported / f"{group}-CB_Hoechst.tif", hoechst)
                tifffile.imwrite(exported / f"{group}-CB_FITC.tif", fitc)
                tifffile.imwrite(exported / f"{group}-CB_Cy5.tif", cy5)

            project = tmp_root / "study.dphistology"
            created = histology.create_project_from_exported_tiff(project, exported)
            rois = [
                {
                    "id": "roi_A",
                    "label": "A",
                    "points": [{"x": 2, "y": 2}, {"x": 29, "y": 2}, {"x": 29, "y": 29}, {"x": 2, "y": 29}],
                },
                {
                    "id": "roi_B",
                    "label": "B",
                    "points": [{"x": 6, "y": 6}, {"x": 25, "y": 6}, {"x": 25, "y": 25}, {"x": 6, "y": 25}],
                },
            ]
            for entry in created["entries"]:
                Path(entry["rois_path"]).write_text(json.dumps(rois), encoding="utf-8")
            loaded_with_external_rois = histology_project.load_histology_data_project(project)
            self.assertTrue(all(entry["roi_count"] == 2 for entry in loaded_with_external_rois["entries"]))

            params = {
                "sma_channel": "green",
                "sma_threshold_method": "manual",
                "sma_threshold": 100,
                "macrophage_channel": "red",
                "macrophage_threshold_method": "manual",
                "macrophage_threshold": 100,
                "background_mode": "none",
                "smooth_sigma": 0,
                "min_positive_area_px": 1,
                "summary_normalize_to_group": "1",
            }
            preview = histology_project.analyze_histology_data_project_saved_rois(
                project,
                params,
                write_outputs=False,
            )

            self.assertFalse(preview["write_outputs"])
            self.assertEqual(preview["roi_count"], 6)
            self.assertEqual(preview["observation_count"], 3)
            self.assertEqual(preview["run_dir"], "")
            self.assertEqual(preview["plots"], [])
            self.assertFalse((project.with_name("study.dataprocess_histology") / "project_analysis").exists())
            first_entry_id = str(created["entries"][0]["entry_id"])
            overridden = histology_project.analyze_histology_data_project_saved_rois(
                project,
                {
                    **params,
                    "roi_parameter_overrides": {
                        f"{first_entry_id}::roi_id::roi_A": {
                            **params,
                            "sma_threshold_method": "manual",
                            "sma_threshold": 999999,
                        }
                    },
                },
                write_outputs=False,
            )

            overridden_roi = next(
                row
                for row in overridden["roi_rows"]
                if row["entry_id"] == first_entry_id and row["roi_id"] == "roi_A"
            )
            sibling_roi = next(
                row
                for row in overridden["roi_rows"]
                if row["entry_id"] == first_entry_id and row["roi_id"] == "roi_B"
            )
            self.assertEqual(overridden["roi_parameter_override_count"], 1)
            self.assertEqual(overridden_roi["roi_parameter_override_key"], f"{first_entry_id}::roi_id::roi_A")
            self.assertEqual(overridden_roi["sma_positive_area_ratio"], 0)
            self.assertGreater(sibling_roi["sma_positive_area_ratio"], 0)

            result = histology_project.analyze_histology_data_project_saved_rois(
                project,
                params,
            )

            self.assertTrue(result["write_outputs"])
            self.assertEqual(result["roi_count"], 6)
            self.assertEqual(result["observation_level"], "image")
            self.assertEqual(result["observation_count"], 3)
            self.assertEqual(result["sample_count"], 3)
            self.assertEqual(result["normalization"]["normalize_to_group"], "1")
            self.assertTrue(Path(result["roi_table_path"]).is_file())
            self.assertTrue(Path(result["image_table_path"]).is_file())
            self.assertTrue(Path(result["summary_table_path"]).is_file())
            self.assertTrue(Path(result["statistics_path"]).is_file())
            self.assertTrue(Path(result["manifest_path"]).is_file())
            self.assertTrue(all(item["roi_source"] != "project" for item in result["analyzed_entries"]))
            self.assertEqual(len(result["plots"]), 4)
            self.assertTrue(all(Path(plot["path"]).is_file() for plot in result["plots"]))
            self.assertTrue(all(Path(plot["svg_path"]).is_file() for plot in result["plots"]))
            with Path(result["roi_table_path"]).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            group_1 = [row for row in rows if row["sample_group"] == "1"]
            self.assertEqual(len(group_1), 2)
            mean_group_1 = sum(float(row["sma_positive_area_ratio_normalized"]) for row in group_1) / len(group_1)
            self.assertAlmostEqual(mean_group_1, 1.0)
            with Path(result["image_table_path"]).open(newline="", encoding="utf-8") as handle:
                image_rows = list(csv.DictReader(handle))
            self.assertEqual(len(image_rows), 3)
            self.assertEqual(len([row for row in image_rows if row["sample_group"] == "1"]), 1)
            self.assertEqual(image_rows[0]["roi_count"], "2")
            self.assertIn("sma", result["statistics"])
            self.assertIn("macrophage", result["statistics"])

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

    def test_analysis_region_scale_keeps_object_area_threshold_in_native_pixels(self) -> None:
        params = {
            "min_positive_area_px": 12,
            "macrophage_min_area_px": 20,
            "macrophage_max_area_px": 800,
        }

        scaled = histology_project._analysis_params_for_region_scale(params, 0.05)

        self.assertEqual(scaled["sma_min_area_px_native"], 12)
        self.assertEqual(scaled["sma_min_area_px"], 1)
        self.assertEqual(scaled["macrophage_min_area_px_native"], 20)
        self.assertEqual(scaled["macrophage_min_area_px"], 1)
        self.assertEqual(scaled["macrophage_max_area_px_native"], 800)
        self.assertEqual(scaled["macrophage_max_area_px"], 2)


if __name__ == "__main__":
    unittest.main()
