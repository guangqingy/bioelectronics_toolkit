from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from services.fluorescence import lif_dimensions, lif_export, lif_metadata, lif_records, lif_volume


class LifServiceSplitTests(unittest.TestCase):
    def test_metadata_timestamp_parsing_and_sorting(self) -> None:
        root = ET.fromstring(
            """
            <Element Name="Image">
              <Data><Image/></Data>
              <ATLConfocalSettingDefinition Identifier="AcquisitionDate" Variant="2026-05-14 09:10:11"/>
            </Element>
            """
        )

        stamp = lif_metadata.timestamp_from_element(root)

        self.assertIsNotNone(stamp)
        assert stamp is not None
        self.assertEqual(stamp["display"], "2026-05-14 09:10:11")
        records = [
            {"full_name": "B", "original_order": 1, "sort_value": None},
            {"full_name": "A", "original_order": 2, "sort_value": stamp["sort_value"]},
        ]
        self.assertEqual([r["full_name"] for r in sorted(records, key=lambda r: lif_metadata.record_sort_tuple(r, "time"))], ["A", "B"])

    def test_record_helpers_clone_and_read_planes_by_dimension(self) -> None:
        class Dims:
            x = 3
            y = 2
            z = 2
            t = 1
            m = 1

        class Image:
            dims = Dims()
            channels = 1
            bit_depth = [16]
            scale = [2, 2, 1]
            scale_n = {}
            dims_n = {3: 2}
            settings = {}
            display_dims = [1, 2]
            mosaic_position = []
            info = {"path": "Folder"}
            name = "Folder/Subfile"

            def get_frame(self, z=0, t=0, c=0, m=0):
                return np.full((2, 3), z + c + t + m, dtype=np.uint16)

            def get_plane(self, c=0, requested_dims=None):
                return np.full((2, 3), int((requested_dims or {}).get(3, 0)) + c, dtype=np.uint16)

        class Lif:
            filename = "mock.lif"

            def get_image(self, _index):
                return Image()

        rec = lif_records.record_from_image(Lif(), 0, [{"element": ET.fromstring("<Element/>"), "xml_path": "Folder/Subfile"}])
        self.assertEqual(rec["name"], "Subfile")
        self.assertEqual(rec["dimensions"]["z"], 2)
        cloned = lif_records.clone_records([rec])
        cloned[0]["dimensions"]["z"] = 99
        self.assertEqual(rec["dimensions"]["z"], 2)
        plane = lif_records.get_plane_by_dimensions(Image(), c=0, dimension_values={3: 1})
        self.assertTrue(np.array_equal(plane, np.ones((2, 3), dtype=np.uint16)))

    def test_dimension_orientation_and_calibration_helpers(self) -> None:
        record = {
            "dims_n": {"3": 4, "4": 2},
            "display_dims": [1, 2],
            "dimensions": {"x": 12, "y": 8, "z": 4, "t": 2},
        }

        self.assertEqual([d["label"] for d in lif_dimensions.plane_dimensions_from_record(record)], ["Z", "T"])
        arr = np.arange(6).reshape(2, 3)
        oriented = lif_dimensions.apply_orientation(arr, {"swap_xy": True, "flip_x": True})
        self.assertEqual(oriented.shape, (3, 2))
        self.assertTrue(np.array_equal(oriented[:, 0], np.array([3, 4, 5])))

        calib = lif_dimensions.oriented_calibration(
            {"pixel_width_um": 0.5, "pixel_height_um": 1.0, "x_step_signed_um": 0.5},
            {"swap_xy": True, "flip_x": True},
        )
        self.assertEqual(calib["pixel_width_um"], 1.0)
        self.assertEqual(calib["pixel_height_um"], 0.5)

    def test_export_plan_and_frame_dimension_order(self) -> None:
        record = {
            "channels": 2,
            "dims_n": {"3": 3, "4": 2, "10": 2},
            "display_dims": [1, 2],
            "dimensions": {"x": 5, "y": 4, "z": 3, "t": 2, "m": 2},
        }

        plan = lif_export.export_plan(record)

        self.assertEqual(plan["counts"]["planes"], 24)
        self.assertEqual(plan["imagej_shape"], [4, 3, 2, 4, 5])
        combos = list(lif_export.frame_dimension_combinations(plan["frame_dimensions"]))
        self.assertEqual(len(combos), 4)
        self.assertEqual(combos[0], (0, {4: 0, 10: 0}))
        self.assertEqual(combos[1], (1, {4: 1, 10: 0}))
        rows = lif_export.manifest_rows([{"index": 0, "original_order": 0, "name": "raw", "dimensions": {"x": 5}}], rename_map={"0": "clean"})
        self.assertEqual(rows[0]["display_name"], "clean")
        self.assertEqual(lif_export.sanitize_filename("bad:name.tiff", "fallback"), "bad_name")

        with tempfile.TemporaryDirectory(prefix="dataprocess_lif_export_") as tmp:
            first = Path(tmp) / "stack.tiff"
            first.write_bytes(b"x")
            self.assertEqual(lif_export.unique_output_path(first, overwrite=False).name, "stack_2.tiff")

    def test_volume_point_helpers_are_service_level(self) -> None:
        positions, colors = lif_volume.plane_points(
            np.array([[0, 1], [2, 10]], dtype=np.float32),
            z_index=1,
            c_index=0,
            z_count=3,
            c_count=1,
            xy_step=1,
            per_plane_quota=2,
            threshold_percentile=75,
            calibration={"pixel_width_um": 1.0, "pixel_height_um": 1.0, "z_spacing_um": 2.0},
            lut_rgb_value=lif_volume.lut_rgb("green"),
        )

        self.assertEqual(len(positions) % 3, 0)
        self.assertEqual(len(colors), len(positions))
        self.assertEqual(lif_volume.volume_indices(10, 4), [0, 3, 6, 9])
        html = lif_volume.volume3d_html(
            {
                "title": "<sample>",
                "dimensions": {"z": 2, "c": 1},
                "calibration": {"pixel_width_um": 1.0, "z_spacing_um": 1.0},
                "render": {"positions": [0, 0, 0], "colors": [1, 1, 1], "n_points": 1, "point_size": 1},
            }
        )
        self.assertIn("&lt;sample&gt;", html)
        self.assertIn("three", html)


if __name__ == "__main__":
    unittest.main()
