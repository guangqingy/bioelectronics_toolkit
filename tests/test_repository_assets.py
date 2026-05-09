from __future__ import annotations

import unittest
from pathlib import Path


class RepositoryAssetTests(unittest.TestCase):
    def test_demo_data_files_are_small_and_readable(self) -> None:
        import pandas as pd
        import pyabf
        import tifffile

        root = Path(__file__).resolve().parents[1]
        abf_path = root / "examples" / "sample_patch_clamp.abf"
        csv_path = root / "examples" / "sample_echem_photocurrent.csv"
        tiff_path = root / "examples" / "sample_fluorescence_stack.tif"

        self.assertLess(abf_path.stat().st_size, 1_000_000)
        self.assertGreater(pyabf.ABF(str(abf_path)).sweepCount, 0)

        df = pd.read_csv(csv_path)
        self.assertIn("time_s", df.columns)
        self.assertIn("current_mA", df.columns)

        stack = tifffile.imread(tiff_path)
        self.assertEqual(stack.shape[0], 10)

    def test_readme_visual_assets_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        assets = [
            root / "web_static" / "favicon.ico",
            root / "web_static" / "img" / "screenshot_home.png",
            root / "web_static" / "img" / "screenshot_abf_viewer.png",
            root / "web_static" / "img" / "screenshot_fluorescence_roi.png",
            root / "web_static" / "img" / "social_preview.png",
        ]
        for asset in assets:
            with self.subTest(asset=asset.name):
                self.assertTrue(asset.exists())
                self.assertGreater(asset.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
