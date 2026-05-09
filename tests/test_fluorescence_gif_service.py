from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from services.fluorescence import gif as fl_gif


class FluorescenceGifServiceTests(unittest.TestCase):
    def test_slice_spec_is_one_based_and_supports_steps(self) -> None:
        self.assertEqual(fl_gif.parse_slice_spec("1-3:2,4", 5), [0, 2, 3])
        self.assertEqual(fl_gif.parse_slice_spec("4-2", 5), [3, 2, 1])
        with self.assertRaises(ValueError):
            fl_gif.parse_slice_spec("6", 5)

    def test_make_gif_writes_gif_and_preview(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"tifffile not available: {exc}")
        try:
            import PIL  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"Pillow not available: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_gif_service_") as tmp:
            folder = Path(tmp)
            source = folder / "stack.tif"
            output = folder / "out.gif"
            arr = np.arange(3 * 12 * 12, dtype=np.uint16).reshape(3, 12, 12)
            tifffile.imwrite(source, arr, photometric="minisblack")

            result = fl_gif.make_gif(
                source,
                output_path=output,
                fps=4.0,
                lut="Green",
                scale_bar_um=0.0,
                add_timestamp=False,
                slice_spec="1,3",
                tifflib_module=tifffile,
            )

            self.assertEqual(result["n_frames"], 2)
            self.assertEqual(result["selected_slices"], 2)
            self.assertTrue(output.exists())
            self.assertTrue(Path(result["preview_path"]).exists())


if __name__ == "__main__":
    unittest.main()
