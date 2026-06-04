from __future__ import annotations

import unittest

import numpy as np

from services.trace_decimate import DEFAULT_MAX_POINTS, decimate_xy


class TraceDecimateTests(unittest.TestCase):
    def test_short_trace_returned_unchanged(self) -> None:
        x = np.arange(10, dtype=float)
        y = np.sin(x)
        xd, yd = decimate_xy(x, y, max_points=4000)
        np.testing.assert_array_equal(xd, x)
        np.testing.assert_array_equal(yd, y)

    def test_long_trace_is_capped(self) -> None:
        n = 1_000_000
        x = np.linspace(0, 100, n)
        y = np.sin(x)
        xd, yd = decimate_xy(x, y, max_points=4000)
        self.assertLessEqual(xd.shape[0], 4000)
        self.assertEqual(xd.shape[0], yd.shape[0])
        self.assertGreater(xd.shape[0], 0)

    def test_envelope_is_preserved(self) -> None:
        # A sharp spike that naive stride decimation would likely drop must
        # survive min/max bucketing.
        n = 200_000
        x = np.linspace(0, 1, n)
        y = np.zeros(n)
        spike_idx = 137_777
        y[spike_idx] = 42.0
        xd, yd = decimate_xy(x, y, max_points=2000)
        self.assertAlmostEqual(float(yd.max()), 42.0, places=6)

    def test_x_range_is_bounded_by_input(self) -> None:
        x = np.linspace(5, 9, 50_000)
        y = np.cos(x)
        xd, _ = decimate_xy(x, y, max_points=1000)
        self.assertGreaterEqual(float(xd.min()), 5.0)
        self.assertLessEqual(float(xd.max()), 9.0)

    def test_disabled_when_max_points_tiny(self) -> None:
        x = np.arange(1000, dtype=float)
        y = x.copy()
        xd, yd = decimate_xy(x, y, max_points=0)
        np.testing.assert_array_equal(xd, x)
        np.testing.assert_array_equal(yd, y)

    def test_default_max_points_is_reasonable(self) -> None:
        self.assertGreaterEqual(DEFAULT_MAX_POINTS, 1000)


if __name__ == "__main__":
    unittest.main()
