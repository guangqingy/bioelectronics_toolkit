from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_MAX_IMAGE_PIXELS = 300_000_000
DEFAULT_MAX_TIFF_BYTES = 4_000_000_000
DEFAULT_MAX_CSV_BYTES = 1_000_000_000


class InputTooLargeError(ValueError):
    """Raised when a user-selected input exceeds configured local safety limits."""


@dataclass(frozen=True)
class TiffEstimate:
    path: str
    shape: tuple[int, ...]
    dtype: str
    element_count: int
    estimated_bytes: int

    @property
    def image_pixels(self) -> int:
        if len(self.shape) >= 2:
            return int(self.shape[-1]) * int(self.shape[-2])
        return self.element_count


def max_image_pixels() -> int:
    return DEFAULT_MAX_IMAGE_PIXELS


def max_tiff_bytes() -> int:
    return DEFAULT_MAX_TIFF_BYTES


def max_csv_bytes() -> int:
    return DEFAULT_MAX_CSV_BYTES


def configure_pillow_image_limit(image_mod: Any) -> None:
    if image_mod is not None and hasattr(image_mod, "MAX_IMAGE_PIXELS"):
        image_mod.MAX_IMAGE_PIXELS = max_image_pixels()


def _import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile


def estimate_tiff(path: str | Path, tifflib_module: Any = None) -> TiffEstimate:
    tifflib = _import_tifffile(tifflib_module)
    p = Path(path)
    with tifflib.TiffFile(str(p)) as tif:
        if tif.series:
            series = tif.series[0]
            shape = tuple(int(v) for v in getattr(series, "shape", ()) or ())
            dtype = np.dtype(getattr(series, "dtype", np.uint8))
        elif len(tif.pages):
            first = tif.pages[0]
            page_shape = tuple(int(v) for v in first.shape)
            shape = (len(tif.pages), *page_shape) if len(tif.pages) > 1 else page_shape
            dtype = np.dtype(first.dtype)
        else:
            raise ValueError(f"TIFF contains no image pages: {p}")

    element_count = int(math.prod(shape)) if shape else 0
    estimated_bytes = int(element_count * dtype.itemsize)
    estimate = TiffEstimate(
        path=str(p),
        shape=shape,
        dtype=str(dtype),
        element_count=element_count,
        estimated_bytes=estimated_bytes,
    )
    return estimate


def assert_tiff_within_limits(
    path: str | Path,
    tifflib_module: Any = None,
    *,
    max_pixels: int | None = None,
    max_bytes: int | None = None,
) -> TiffEstimate:
    estimate = estimate_tiff(path, tifflib_module)
    pixel_limit = max_image_pixels() if max_pixels is None else int(max_pixels)
    byte_limit = max_tiff_bytes() if max_bytes is None else int(max_bytes)
    if estimate.image_pixels > pixel_limit:
        raise InputTooLargeError(
            f"TIFF image plane is too large for local preview/export "
            f"({estimate.image_pixels:,} pixels > {pixel_limit:,}). "
            "Downsample or crop the image before loading it."
        )
    if estimate.estimated_bytes > byte_limit:
        raise InputTooLargeError(
            f"TIFF stack is too large for this local session "
            f"({estimate.estimated_bytes / 1_000_000_000:.2f} GB > "
            f"{byte_limit / 1_000_000_000:.2f} GB). "
            "Use a smaller stack or downsample first."
        )
    return estimate


def assert_file_size_within_limit(
    path: str | Path,
    *,
    max_bytes: int | None = None,
    label: str = "file",
    ) -> None:
    p = Path(path)
    limit = max_csv_bytes() if max_bytes is None else int(max_bytes)
    size = int(p.stat().st_size)
    if size > limit:
        raise InputTooLargeError(
            f"{label} is too large for this local session "
            f"({size / 1_000_000:.1f} MB > {limit / 1_000_000:.1f} MB)."
        )
