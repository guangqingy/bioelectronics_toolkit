from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from services.fluorescence import roi as fl_roi


class FluorescenceRoiServiceTests(unittest.TestCase):
    def test_collect_pairs_can_skip_unpaired_raw_tiffs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_roi_pairs_") as tmp:
            folder = Path(tmp)
            (folder / "sample_stack1_red.tif").touch()
            (folder / "sample_stack2_blue.tif").touch()
            (folder / "raw_source.tif").touch()

            web_records = fl_roi.collect_pairs(folder, include_unpaired=True)
            desktop_records = fl_roi.collect_pairs(folder, include_unpaired=False)

            self.assertEqual({record["base"] for record in web_records}, {"sample", "raw_source"})
            self.assertEqual([record["base"] for record in desktop_records], ["sample"])

    def test_rectangular_roi_metrics_and_background(self) -> None:
        img = np.arange(100, dtype=np.float32).reshape(10, 10)

        metrics = fl_roi.metrics_2d(img, (2, 3, 5, 7))

        expected = img[3:7, 2:5]
        self.assertEqual(metrics["area_px"], expected.size)
        self.assertAlmostEqual(metrics["mean"], float(np.mean(expected)))
        self.assertAlmostEqual(metrics["sum"], float(np.sum(expected)))

        bg = fl_roi.background_mean(img, "corner_tl")
        self.assertTrue(np.isfinite(bg))

    def test_concentric_roi_radial_rows(self) -> None:
        img = np.ones((20, 20), dtype=np.float32)
        roi = {
            "type": "concentric",
            "label": "ROI1",
            "key": "roi1",
            "cx": 10,
            "cy": 10,
            "radius": 6,
            "ring_count": 3,
        }

        rows = fl_roi.radial_metrics_2d(img, roi, "mean", np.nan, "absolute", pixel_size_um=0.5)

        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["area_px"] > 0 for row in rows))
        self.assertTrue(all(abs(row["value"] - 1.0) < 1e-12 for row in rows))

    def test_stack_roi_reads_pages_without_loading_whole_gui(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"tifffile not available: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_roi_service_") as tmp:
            path = Path(tmp) / "roi_stack.tif"
            arr = np.arange(2 * 6 * 6, dtype=np.uint16).reshape(2, 6, 6)
            tifffile.imwrite(path, arr)

            results, n_frames = fl_roi.compute_stack_roi(
                str(path),
                [{"label": "ROI1", "x1": 1, "y1": 1, "x2": 4, "y2": 4}],
                "mean",
                tifffile,
            )

            self.assertEqual(n_frames, 2)
            self.assertEqual(len(results["ROI1"]), 2)
            self.assertAlmostEqual(results["ROI1"][0], float(np.mean(arr[0, 1:4, 1:4])))


if __name__ == "__main__":
    unittest.main()
