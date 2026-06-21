from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from services import histology_line_measure
from services.fluorescence import manual_roi, marker_roi


class DesktopGuiServiceSplitTests(unittest.TestCase):
    def test_fluorescence_manual_roi_analysis_is_service_backed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "1Csd1.tif"
            arr = np.zeros((2, 12, 12), dtype=np.uint16)
            arr[0, 1:5, 1:5] = 10
            arr[0, 8:11, 8:11] = 2
            arr[1, 1:5, 1:5] = 20
            arr[1, 8:11, 8:11] = 5
            tifffile.imwrite(path, arr)

            signal = manual_roi.RoiPolygon(
                label="signal",
                kind="signal",
                points=[(1, 1), (4, 1), (4, 4), (1, 4)],
            )
            background = manual_roi.RoiPolygon(
                label="background",
                kind="background",
                points=[(8, 8), (10, 8), (10, 10), (8, 10)],
            )

            rows = manual_roi.analyze_image(path, [signal, background])
            signal_ch1 = next(
                row
                for row in rows
                if row["roi_label"] == "signal" and row["channel_index"] == "1"
            )

            self.assertEqual(signal_ch1["mouse_id"], "1Csd1")
            self.assertEqual(signal_ch1["area_px"], "16")
            self.assertAlmostEqual(float(signal_ch1["mean_bg_subtracted"]), 8.0)

            summary = manual_roi.summarize_measurements(rows)
            self.assertTrue(summary)
            self.assertTrue(all(row["roi_label"] != "background" for row in summary))

    def test_marker_roi_uses_service_manual_roi_model(self) -> None:
        self.assertIs(marker_roi.RoiPolygon, manual_roi.RoiPolygon)
        self.assertEqual(marker_roi.RoiPolygon.__module__, "services.fluorescence.manual_roi")

    def test_histology_line_measurement_service_exports_calibrated_lengths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "section.png"
            image_path.write_bytes(b"placeholder")
            state = histology_line_measure.ImageMeasurements(path=image_path)
            state.calibration = histology_line_measure.Calibration(
                pixel_length=100,
                real_length=250,
                unit="um",
                source_image=image_path.name,
            )
            state.lines.append(
                histology_line_measure.MeasurementLine.from_points(0, 0, 3, 4, "test")
            )

            rows = histology_line_measure.measurement_rows([state])
            self.assertEqual(rows[0]["pixel_length"], "5")
            self.assertEqual(rows[0]["calibrated_length"], "12.5")
            self.assertEqual(rows[0]["scale_pixels_per_unit"], "0.4")

            output = Path(tmp) / "measurements.csv"
            count = histology_line_measure.write_measurements_csv([state], output)
            self.assertEqual(count, 1)
            with output.open(newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
            self.assertEqual(exported[0]["label"], "test")


if __name__ == "__main__":
    unittest.main()
