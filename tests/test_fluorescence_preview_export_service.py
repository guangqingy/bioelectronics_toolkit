from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from services.fluorescence.preview_export import (
    ChannelBC,
    composite_preview_image,
    find_tiff_files,
    load_tiff_channels,
    parse_fluorescence_name,
    rotate_image_for_preview,
    single_channel_preview_image,
)


class FluorescencePreviewExportServiceTests(unittest.TestCase):
    def test_load_tiff_channels_normalizes_channel_last_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_fl_preview_") as tmp:
            path = Path(tmp) / "sample.tif"
            arr = np.zeros((12, 5, 3), dtype=np.uint16)
            arr[..., 0] = 10
            arr[..., 1] = 20
            arr[..., 2] = 30
            tifffile.imwrite(path, arr)

            channels = load_tiff_channels(path)

        self.assertEqual(channels.shape, (3, 12, 5))
        self.assertTrue(np.all(channels[0] == 10))
        self.assertTrue(np.all(channels[2] == 30))

    def test_find_tiff_files_deduplicates_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_fl_preview_") as tmp:
            root = Path(tmp)
            path = root / "image01.tif"
            tifffile.imwrite(path, np.zeros((2, 2), dtype=np.uint8))

            found = find_tiff_files([root, path])

        self.assertEqual(found, [path.resolve()])

    def test_preview_images_are_rgb_and_preserve_size(self) -> None:
        channels = np.stack(
            [
                np.arange(12, dtype=np.uint16).reshape(3, 4),
                np.arange(12, dtype=np.uint16).reshape(3, 4) * 2,
            ]
        )
        settings = [ChannelBC(color="#ff0000"), ChannelBC(color="#00ff00")]

        composite = composite_preview_image(channels, settings)
        single = single_channel_preview_image(channels, 1, settings)

        self.assertEqual(composite.mode, "RGB")
        self.assertEqual(single.mode, "RGB")
        self.assertEqual(composite.size, (4, 3))
        self.assertEqual(single.size, (4, 3))

    def test_rotation_returns_geometry_for_inverse_mapping(self) -> None:
        image = composite_preview_image(np.ones((1, 3, 4), dtype=np.uint8), [ChannelBC()])

        rotated, geometry = rotate_image_for_preview(image, 90)

        self.assertEqual(rotated.mode, "RGB")
        self.assertEqual(geometry.source_width, 4)
        self.assertEqual(geometry.source_height, 3)
        self.assertEqual(geometry.angle_degrees, 90)

    def test_parse_fluorescence_name_keeps_legacy_1lsd_mapping(self) -> None:
        metadata = parse_fluorescence_name(Path("1lsd7.tif"))

        self.assertEqual(metadata["mouse_id"], "1RS-D")
        self.assertEqual(metadata["original_side"], "LS")
        self.assertEqual(metadata["field_id"], 7)


if __name__ == "__main__":
    unittest.main()
