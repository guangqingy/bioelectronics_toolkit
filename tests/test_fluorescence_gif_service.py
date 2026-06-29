from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import numpy as np

from services import output_naming
from services.fluorescence import gif as fl_gif
from services.fluorescence import (
    gif_kymograph,
    gif_kymograph_export,
    gif_roi_context,
    route_helpers,
)


def _float_or(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    def test_kymograph_payload_builds_plot_and_csv_tables(self) -> None:
        try:
            import tifffile
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            self.skipTest(f"kymograph dependencies unavailable: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_kymograph_service_") as tmp:
            folder = Path(tmp)
            source = folder / "stack.tif"
            frame0 = np.arange(100, dtype=np.uint16).reshape(10, 10) + 20
            stack = np.stack([frame0, frame0 + 5, frame0 + 10])
            tifffile.imwrite(source, stack, photometric="minisblack")

            helpers = {
                "fig_to_b64": lambda _fig: "plot-payload",
                "float_or": _float_or,
                "int_or": _int_or,
                "_fl_bool": route_helpers.parse_bool,
                "_fl_parse_slice_spec": fl_gif.parse_slice_spec,
                "_fl_read_selected_gif_planes": lambda path, indices: fl_gif.read_selected_planes(
                    path, indices, tifffile
                ),
                "_fl_tiff_gif_frame_count": lambda path: fl_gif.tiff_frame_count(path, tifffile),
            }
            helpers.update(
                gif_roi_context.build_gif_roi_context(
                    image_mod=Image,
                    image_draw_mod=ImageDraw,
                    image_font_mod=ImageFont,
                    fig_to_b64=lambda _fig: "plot-payload",
                )
            )

            result = gif_kymograph.build_gif_roi_kymograph_payload(
                {
                    "tiff_paths": [str(source)],
                    "slice_specs": ["1-3"],
                    "roi": {
                        "label": "Cell A",
                        "points": [
                            {"x": 2, "y": 2},
                            {"x": 7, "y": 2},
                            {"x": 7, "y": 7},
                            {"x": 2, "y": 7},
                        ],
                    },
                    "bins": 12,
                    "overlay_peak": True,
                    "overlay_mean": True,
                    "overlay_percentiles": [50, 90],
                    "overlay_top_means": [10],
                    "threshold_lines": "0, 0.1",
                    "smooth_time_frames": 0.5,
                },
                helpers=helpers,
                resolve_output_dir=lambda source_path, output_dir, suffix: (
                    output_naming.resolve_output_dir(
                        source_path, output_dir, default_suffix=suffix, project_root=folder
                    )
                ),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["img"], "plot-payload")
            self.assertEqual(result["n_frames"], 3)
            self.assertEqual(result["selected_slices"], 3)
            self.assertEqual(result["roi_label"], "Cell A")
            self.assertIn("smoothed_pixel_fraction_pct", result["heatmap_csv"])
            self.assertIn("display_peak_bin_intensity", result["summary_csv"])

    def test_kymograph_export_writes_selected_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_kymograph_export_") as tmp:
            folder = Path(tmp)
            source = folder / "stack.tif"
            source.write_bytes(b"placeholder")
            raw_png = b"png-bytes"

            result = gif_kymograph_export.save_gif_roi_kymograph_outputs(
                {
                    "tiff_paths": [str(source)],
                    "output_dir": "exports",
                    "prefix": "bad/name",
                    "heatmap_csv": "bin,pct\n1,50\n",
                    "summary_csv": "frame,mean\n1,2\n",
                    "plot_png_b64": base64.b64encode(raw_png).decode(),
                },
                bool_value=route_helpers.parse_bool,
                sanitize_prefix=route_helpers.sanitize_prefix,
                decode_base64_payload=route_helpers.decode_base64_payload,
                resolve_output_dir=lambda source_path, output_dir, suffix: (
                    output_naming.resolve_output_dir(
                        source_path, output_dir, default_suffix=suffix, project_root=folder
                    )
                ),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["saved_paths"]), 3)
            self.assertTrue((folder / "exports" / "bad_name_heatmap.csv").exists())
            self.assertEqual((folder / "exports" / "bad_name.png").read_bytes(), raw_png)

            with self.assertRaisesRegex(ValueError, "No kymograph outputs"):
                gif_kymograph_export.save_gif_roi_kymograph_outputs(
                    {"output_dir": str(folder / "empty"), "save_plot": False},
                    bool_value=route_helpers.parse_bool,
                    sanitize_prefix=route_helpers.sanitize_prefix,
                    decode_base64_payload=route_helpers.decode_base64_payload,
                    resolve_output_dir=lambda source_path, output_dir, suffix: (
                        output_naming.resolve_output_dir(
                            source_path, output_dir, default_suffix=suffix, project_root=folder
                        )
                    ),
                )


if __name__ == "__main__":
    unittest.main()
