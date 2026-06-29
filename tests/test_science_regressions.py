from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_widths

from services import echem, emg
from services.abf_viewer import AbfViewerService
from services.fluorescence import roi as fl_roi
from services.fluorescence import roi_primitives, roi_radial
from web_api.common import apply_axes_limits, mode_is_save

ROOT = Path(__file__).resolve().parents[1]


class EchemRegressionTests(unittest.TestCase):
    def test_photovoltage_detrend_and_pulse_detection(self) -> None:
        t = np.linspace(0, 0.099, 100)
        baseline = 0.2 + 0.03 * t
        signal = baseline.copy()
        signal[50] += 0.75

        detrended = signal - baseline
        pulses = echem.detect_positive_pulses(
            t,
            detrended,
            t0=0.0,
            t1=0.099,
            peak_min_v=0.4,
            min_width_ms=0.5,
            min_spacing_ms=10.0,
            find_peaks_func=find_peaks,
            peak_widths_func=peak_widths,
        )

        self.assertEqual(len(pulses), 1)
        self.assertEqual(pulses[0]["idx"], 50)
        self.assertGreater(pulses[0]["amp_det_v"], 0.7)

    def test_photovoltage_export_payload_versions_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_pv_export_") as tmp:
            path = Path(tmp) / "pv.tsv"
            path.write_text("time_s\tvoltage_V\n0\t0\n0.001\t1\n0.002\t0\n", encoding="utf-8")
            result = echem.photovoltage_export_payload(
                {
                    "path": str(path),
                    "mode": "save",
                    "pulses": [{"idx": 1, "original_index": 1, "width_ms": 1.0}],
                    "pulse_window_ms": 2.0,
                }
            )

            self.assertTrue(result["data"]["ok"])
            self.assertEqual(result["data"]["saved_count"], 1)
            self.assertTrue(Path(result["data"]["summary_path"]).exists())
            self.assertTrue(
                any(item["role"] == "photovoltage_pulse_summary" for item in result["data"]["outputs"])
            )


class EmgRegressionTests(unittest.TestCase):
    def test_detect_with_polarity_keeps_largest_nearby_peak(self) -> None:
        fs = 1000.0
        sig = np.zeros(80, dtype=float)
        sig[20] = 1.0
        sig[23] = -2.0
        sig[60] = 1.5
        params = {
            "min_peak_distance_ms": 10.0,
            "min_width_ms": None,
            "wlen_ms": None,
            "min_prominence_uV": 0.5,
            "min_height_uV": None,
            "use_adaptive_sigma": False,
            "sigma_for_prom": None,
            "sigma_for_height": None,
        }

        peaks, widths, signs = emg.detect_with_polarity(
            sig,
            fs,
            params,
            "both",
            find_peaks,
            peak_widths,
        )

        self.assertEqual(peaks.tolist(), [23, 60])
        self.assertEqual(signs.tolist(), [-1, 1])
        self.assertEqual(len(widths), 2)

    def test_numeric_signal_and_sampling_rate_ignore_bad_rows(self) -> None:
        df = pd.DataFrame({"time_s": ["0", "0.001", "bad", "0.002"], "value_uV": [0, 1, 2, 3]})
        t_raw, v_raw, valid = emg.numeric_signal(df, "time_s", "value_uV")
        self.assertEqual(valid.tolist(), [True, True, False, True])
        self.assertAlmostEqual(emg.infer_sampling_rate(t_raw[valid]), 1000.0)
        self.assertEqual(emg.channel_label_from_source(Path("cell/cell_channel A.csv")), "channel_A")


class RoiRegressionTests(unittest.TestCase):
    def test_rectangular_and_background_metrics_are_stable(self) -> None:
        img = np.arange(100, dtype=float).reshape(10, 10)
        roi = {"x1": 2, "y1": 3, "x2": 5, "y2": 7}

        metrics = roi_primitives.metrics_2d(img, roi)
        self.assertEqual(metrics["area_px"], 12)
        self.assertAlmostEqual(metrics["mean"], float(np.mean(img[3:7, 2:5])))
        self.assertAlmostEqual(
            roi_primitives.background_mean(img, "roi", {"x1": 0, "y1": 0, "x2": 2, "y2": 2}),
            5.5,
        )
        self.assertAlmostEqual(
            roi_primitives.apply_metric_mode(20.0, 4, "sum", 2.0, "bg_subtracted"),
            12.0,
        )

    def test_concentric_radial_rows_have_expected_ratio(self) -> None:
        yy, xx = np.ogrid[:9, :9]
        img1 = ((xx - 4) ** 2 + (yy - 4) ** 2).astype(float)
        img2 = img1 + 1.0
        roi = {
            "label": "stim",
            "key": "stim",
            "type": "concentric",
            "cx": 4,
            "cy": 4,
            "radius": 4,
            "ring_count": 2,
        }

        rows = roi_radial.radial_pair_rows(
            img1,
            img2,
            roi,
            "mean",
            np.nan,
            np.nan,
            "absolute",
            0.5,
            "sample_001",
            1.0,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["roi_label"], "stim")
        self.assertLess(rows[0]["ratio"], 1.0)
        self.assertAlmostEqual(rows[0]["difference"], -1.0)

    def test_compute_stack_roi_streams_frames(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"tifffile not available: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_roi_stack_") as tmp:
            path = Path(tmp) / "stack.tif"
            stack = np.stack(
                [
                    np.full((4, 4), 1, dtype=np.uint16),
                    np.full((4, 4), 3, dtype=np.uint16),
                    np.full((4, 4), 5, dtype=np.uint16),
                ],
                axis=0,
            )
            tifffile.imwrite(path, stack)
            values, n_frames = fl_roi.compute_stack_roi(
                str(path),
                [{"label": "roi", "x1": 1, "y1": 1, "x2": 3, "y2": 3}],
                "mean",
                tifffile,
            )

            self.assertEqual(n_frames, 3)
            self.assertEqual(values["roi"], [1.0, 3.0, 5.0])


class AbfViewerRegressionTests(unittest.TestCase):
    def _service(self) -> AbfViewerService:
        try:
            import pyabf
        except ImportError as exc:
            self.skipTest(f"pyabf not available: {exc}")

        def fake_fig_to_b64(fig):
            fig.clear()
            return "plot"

        return AbfViewerService(
            pyabf_mod=pyabf,
            find_peaks=find_peaks,
            fig_to_b64=fake_fig_to_b64,
            mode_is_save=mode_is_save,
            apply_axes_limits=apply_axes_limits,
            clean_trace_svg=lambda *_args, **_kwargs: b"<svg/>",
            next_numbered_path=lambda path: path,
            line_color="#3E6AE1",
        )

    def test_abf_info_plot_and_detection_on_bundled_example(self) -> None:
        path = ROOT / "examples" / "sample_patch_clamp.abf"
        service = self._service()

        info = service.info_payload(str(path))
        self.assertEqual(info["num_sweeps"], 2)
        self.assertEqual(info["channel_count"], 1)

        plot = service.plot_payload({"path": str(path), "sweep": 0, "channel": 0, "dsf": 10})
        self.assertEqual(plot["img"], "plot")

        trace = service.trace_data_payload(
            {"path": str(path), "sweep": 0, "channel": 0, "dsf": 10},
            max_points=100,
        )
        self.assertEqual(trace["x_label"], "Time (s)")
        self.assertEqual(trace["y_label"], "pA")
        self.assertGreater(trace["n_full"], trace["n_points"])
        self.assertLessEqual(trace["n_points"], 100)

        detected = service.detect_payload(
            {
                "path": str(path),
                "sweep": 0,
                "channel": 0,
                "use_all": True,
                "polarity": "positive",
                "height": 20,
                "distance": 10,
            }
        )
        self.assertGreaterEqual(len(detected["peaks"]), 1)
        self.assertEqual(detected["meta"]["polarity"], "POS")


if __name__ == "__main__":
    unittest.main()
