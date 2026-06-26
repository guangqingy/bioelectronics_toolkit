from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

SUPPORTED_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
TIFF_SUFFIXES = {".tif", ".tiff"}
DEFAULT_MAX_IMAGE_LOAD_BYTES = 1200 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 300_000_000

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return int(default)
    return max(0, value)


def _max_image_load_bytes() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_IMAGE_LOAD_BYTES", DEFAULT_MAX_IMAGE_LOAD_BYTES)


def _max_image_pixels() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_IMAGE_PIXELS", DEFAULT_MAX_IMAGE_PIXELS)


def _shape_sample_count(shape: tuple[int, ...]) -> int:
    count = 1
    for dim in shape:
        count *= max(1, int(dim))
    return int(count)


def _shape_pixel_count(shape: tuple[int, ...]) -> int:
    if len(shape) >= 2:
        return int(max(1, int(shape[0])) * max(1, int(shape[1])))
    return _shape_sample_count(shape)


def _format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MB"
    return f"{value:,} bytes"


def _load_image_array_unchecked(path: Path) -> np.ndarray:
    path = Path(path).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image extension for analysis: {path.suffix}")
    if suffix in TIFF_SUFFIXES:
        if tifffile is None:
            raise RuntimeError("tifffile is required to load TIFF images")
        return np.asarray(tifffile.imread(str(path)))
    with Image.open(path) as img:
        return np.asarray(img)


def estimate_image_load_size(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    dtype, shape = _read_image_metadata(path)
    try:
        itemsize = int(np.dtype(dtype).itemsize)
    except Exception:
        itemsize = 1
    sample_count = _shape_sample_count(shape)
    return {
        "path": str(path),
        "dtype": dtype,
        "shape": shape,
        "pixel_count": _shape_pixel_count(shape),
        "sample_count": sample_count,
        "estimated_bytes": int(sample_count * max(1, itemsize)),
    }


def _guard_image_load(path: Path) -> None:
    meta = estimate_image_load_size(path)
    max_pixels = _max_image_pixels()
    max_bytes = _max_image_load_bytes()
    pixels = int(meta["pixel_count"])
    byte_count = int(meta["estimated_bytes"])
    if max_pixels and pixels > max_pixels:
        raise ValueError(
            f"Image is too large to load safely ({pixels:,} pixels; limit {max_pixels:,}). "
            "Export or downsample an ROI TIFF, or raise DP_HISTOLOGY_MAX_IMAGE_PIXELS."
        )
    if max_bytes and byte_count > max_bytes:
        raise ValueError(
            f"Image is too large to load safely ({_format_bytes(byte_count)}; "
            f"limit {_format_bytes(max_bytes)}). Export or downsample an ROI TIFF, "
            "or raise DP_HISTOLOGY_MAX_IMAGE_LOAD_BYTES."
        )


def load_image_for_analysis(file_path: str | Path) -> np.ndarray:
    """Load image values without normalization. TIFF uses tifffile; PNG/JPG uses Pillow."""
    path = Path(file_path).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image extension for analysis: {path.suffix}")
    _guard_image_load(path)
    return _load_image_array_unchecked(path)


def _bit_depth_for_dtype(dtype: np.dtype) -> int:
    dtype = np.dtype(dtype)
    if dtype.kind in {"u", "i"}:
        return int(dtype.itemsize * 8)
    if dtype.kind == "f":
        return 32 if dtype.itemsize <= 4 else 64
    return 0


def _bit_depth_for_array(arr: np.ndarray) -> int:
    return _bit_depth_for_dtype(np.dtype(arr.dtype))


# PIL image mode -> (channel count, numpy dtype string) used to derive shape/dtype
# without decoding the full pixel buffer during a scan.
_PIL_MODE_INFO: dict[str, tuple[int, str]] = {
    "1": (1, "bool"),
    "L": (1, "uint8"),
    "P": (1, "uint8"),
    "I": (1, "int32"),
    "I;16": (1, "uint16"),
    "I;16L": (1, "uint16"),
    "I;16B": (1, "uint16"),
    "I;16N": (1, "uint16"),
    "F": (1, "float32"),
    "LA": (2, "uint8"),
    "RGB": (3, "uint8"),
    "RGBa": (4, "uint8"),
    "YCbCr": (3, "uint8"),
    "LAB": (3, "uint8"),
    "HSV": (3, "uint8"),
    "RGBA": (4, "uint8"),
    "CMYK": (4, "uint8"),
}


def _read_image_metadata(path: Path) -> tuple[str, tuple[int, ...]]:
    """Read dtype and shape cheaply, without decoding the full pixel buffer."""
    suffix = path.suffix.lower()
    if suffix in TIFF_SUFFIXES and tifffile is not None:
        with tifffile.TiffFile(str(path)) as tf:
            source = tf.series[0] if getattr(tf, "series", None) else tf.pages[0]
            shape = tuple(int(x) for x in source.shape)
            dtype = np.dtype(source.dtype)
        return str(dtype), shape
    with Image.open(path) as img:
        width, height = img.size
        channels, dtype_str = _PIL_MODE_INFO.get(
            img.mode,
            (max(1, len(getattr(img, "getbands", lambda: ())())), "uint8"),
        )
    shape = (height, width) if channels <= 1 else (height, width, channels)
    return dtype_str, shape


def _shape_warnings(path: Path, shape: tuple[int, ...], bit_depth: int) -> list[str]:
    warnings: list[str] = []
    suffix = path.suffix.lower()
    if suffix not in TIFF_SUFFIXES:
        warnings.append("Non-TIFF image; use 16-bit TIFF for quantitative fluorescence.")
    ndim = len(shape)
    if ndim == 3:
        if shape[-1] <= 4:
            warnings.append("Multi-channel/color image stored in one file; expected single-channel XY.")
        else:
            warnings.append("Image has more than XY dimensions; expected 2D exported image.")
    elif ndim != 2:
        warnings.append("Image has unsupported dimensionality; expected XY only.")
    if suffix in TIFF_SUFFIXES and bit_depth < 16:
        warnings.append("TIFF is not 16-bit; confirm it is suitable for quantification.")
    return warnings


def load_image_for_display(
    file_path: str | Path,
    contrast_limits: tuple[float, float] | None = None,
) -> np.ndarray:
    arr = np.asarray(load_image_for_analysis(file_path))
    if arr.ndim == 3 and arr.shape[-1] > 3:
        arr = arr[..., :3]
    data = arr.astype(np.float32, copy=False)
    if contrast_limits is None:
        finite = data[np.isfinite(data)]
        if finite.size:
            lo = float(np.percentile(finite, 1.0))
            hi = float(np.percentile(finite, 99.8))
        else:
            lo, hi = 0.0, 1.0
    else:
        lo, hi = float(contrast_limits[0]), float(contrast_limits[1])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    return np.round(np.clip((data - lo) / (hi - lo), 0.0, 1.0) * 255).astype(np.uint8)


__all__ = [
    "SUPPORTED_IMAGE_SUFFIXES",
    "TIFF_SUFFIXES",
    "_bit_depth_for_array",
    "_bit_depth_for_dtype",
    "_read_image_metadata",
    "_shape_warnings",
    "estimate_image_load_size",
    "load_image_for_analysis",
    "load_image_for_display",
]
