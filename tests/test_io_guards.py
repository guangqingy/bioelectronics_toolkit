from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from services.io_guards import (
    InputTooLargeError,
    assert_file_size_within_limit,
    assert_tiff_within_limits,
)


class IoGuardTests(unittest.TestCase):
    def test_tiff_guard_rejects_configured_byte_limit(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"tifffile not available: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_io_guard_") as tmp:
            source = Path(tmp) / "stack.tif"
            tifffile.imwrite(source, np.zeros((2, 8, 8), dtype=np.uint16))
            with self.assertRaises(InputTooLargeError):
                assert_tiff_within_limits(source, tifffile, max_bytes=1)

    def test_csv_guard_rejects_configured_file_size_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_io_guard_") as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text("time,value\n0,1\n", encoding="utf-8")
            with self.assertRaises(InputTooLargeError):
                assert_file_size_within_limit(path, max_bytes=1, label="CSV")


if __name__ == "__main__":
    unittest.main()
