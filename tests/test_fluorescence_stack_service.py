from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from services.fluorescence import stack as fl_stack
from services.fluorescence import stack_export, stack_io, stack_processing


class FluorescenceStackServiceTests(unittest.TestCase):
    def test_stack_facade_exports_split_services(self) -> None:
        self.assertIs(fl_stack.read_tiff_as_pages, stack_io.read_tiff_as_pages)
        self.assertIs(fl_stack.compute_default_min_max, stack_processing.compute_default_min_max)
        self.assertIs(fl_stack.export_with_settings, stack_export.export_with_settings)
        self.assertIs(fl_stack.build_fiji_macro, stack_export.build_fiji_macro)

    def test_default_settings_and_export_outputs(self) -> None:
        try:
            import tifffile
        except ImportError as exc:
            self.skipTest(f"tifffile not available: {exc}")

        pages = [
            np.arange(25, dtype=np.uint16).reshape(5, 5),
            np.arange(25, 50, dtype=np.uint16).reshape(5, 5),
        ]

        with tempfile.TemporaryDirectory(prefix="dataprocess_stack_service_") as tmp:
            source = Path(tmp) / "sample.tif"
            tifffile.imwrite(source, np.stack(pages, axis=0))

            loaded_pages = fl_stack.read_tiff_as_pages(source, tifffile)
            info = fl_stack.tiff_stack_info(source, tifffile)
            frame, display_info = fl_stack.select_display_frame_from_tiff(
                source, 1, "single", None, None, tifffile
            )
            settings = fl_stack.build_default_settings_for_pages(loaded_pages)

            self.assertEqual(info["n_frames"], 2)
            self.assertEqual(info["shape"], [2, 5, 5])
            self.assertTrue(np.array_equal(frame, pages[1]))
            self.assertEqual(display_info["frame"], 1)
            self.assertEqual(len(settings), 2)
            self.assertEqual(settings[0]["lut"], "Red")
            self.assertEqual(settings[1]["lut"], "Blue")

            settings[0]["include"] = True
            settings[1]["include"] = True
            outputs = fl_stack.export_with_settings(source, loaded_pages, settings, tifffile)

            for key in ("combined_tiff", "macro", "json"):
                self.assertTrue(Path(outputs[key]).exists(), key)
            for path in outputs["stack_files"]:
                self.assertTrue(Path(path).exists(), path)

            self.assertTrue(fl_stack.is_generated_tiff(Path(outputs["combined_tiff"])))


if __name__ == "__main__":
    unittest.main()
