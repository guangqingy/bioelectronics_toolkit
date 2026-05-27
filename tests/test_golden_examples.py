from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pyabf
import tifffile
from scipy.signal import find_peaks

from services import abf, echem


class GoldenExampleRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def test_patch_clamp_example_peak_detection_is_stable(self) -> None:
        recording = pyabf.ABF(str(self.root / "examples" / "sample_patch_clamp.abf"))
        recording.setSweep(0)
        corrected, baseline = abf.baseline_apply(
            recording.sweepY,
            recording.sweepX,
            use_default=True,
        )
        peaks, window = abf.detect_peaks(
            recording.sweepX,
            corrected,
            t0=None,
            t1=None,
            use_all=True,
            polarity="positive",
            distance_ms=10,
            find_peaks_func=find_peaks,
            height=30,
        )

        self.assertEqual(recording.sweepCount, 2)
        self.assertEqual(recording.sweepPointCount, 5000)
        self.assertAlmostEqual(baseline, -7.65283203125)
        self.assertEqual(window, [0.0, 0.9998])
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["global_index"], 1502)
        self.assertAlmostEqual(peaks[0]["time"], 0.3004)
        self.assertAlmostEqual(peaks[0]["amplitude"], 38.6129150390625)

    def test_echem_example_numeric_trace_is_stable(self) -> None:
        t, signal, t_col, v_col = echem.load_photocurrent(
            self.root / "examples" / "sample_echem_photocurrent.csv"
        )

        self.assertEqual((t_col, v_col), ("time_s", "current_mA"))
        self.assertEqual(len(t), 600)
        self.assertAlmostEqual(float(t[0]), 0.0)
        self.assertAlmostEqual(float(t[-1]), 3.0)
        self.assertEqual(int(np.argmax(signal)), 469)
        self.assertEqual(int(np.argmin(signal)), 478)
        self.assertAlmostEqual(float(signal[469]), 0.0463818255506895)
        self.assertAlmostEqual(float(signal[478]), -0.0387498023197497)
        self.assertAlmostEqual(float(np.sum(signal)), 0.024609168686022177)

    def test_fluorescence_tiff_example_pixels_are_stable(self) -> None:
        arr = tifffile.imread(str(self.root / "examples" / "sample_fluorescence_stack.tif"))

        self.assertEqual(arr.shape, (10, 64, 64))
        self.assertEqual(str(arr.dtype), "uint16")
        self.assertEqual(int(arr.min()), 2376)
        self.assertEqual(int(arr.max()), 58840)
        self.assertEqual(int(arr.sum()), 253060708)
        self.assertAlmostEqual(float(arr.mean()), 6178.23994140625)


if __name__ == "__main__":
    unittest.main()
