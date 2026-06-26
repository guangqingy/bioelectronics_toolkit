from __future__ import annotations

from typing import Any

import numpy as np

LUT_OPTIONS = ["Red", "Blue", "Gray", "Green", "Magenta", "Cyan", "Yellow"]
DENOISE_OPTIONS = ["Off", "Light", "Medium", "Strong"]
BACKGROUND_OPTIONS = ["Off", "Light", "Medium", "Strong"]

DEFAULT_LUT_BY_INDEX = {0: "Red", 1: "Blue", 2: "Gray"}
DEFAULT_DENOISE_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}
DEFAULT_BACKGROUND_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}


def bool_or(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile


def compute_default_min_max(
    img: np.ndarray,
    low_p: float = 1.0,
    high_p: float = 99.8,
) -> tuple[float, float]:
    arr = np.asarray(img, dtype=np.float32)
    if arr.size == 0:
        return 0.0, 1.0
    vmin = float(np.percentile(arr, low_p))
    vmax = float(np.percentile(arr, high_p))
    if vmax <= vmin:
        vmin = float(np.min(arr))
        vmax = float(np.max(arr))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return vmin, vmax


def convert_to_export_dtype(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img)
    if np.issubdtype(arr.dtype, np.integer):
        if arr.dtype == np.uint16:
            return arr
        if arr.dtype == np.uint8:
            return arr.astype(np.uint16) * 257
        return np.clip(arr, 0, 65535).astype(np.uint16)
    arr = arr.astype(np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=65535.0, neginf=0.0)
    arr = np.clip(arr, 0.0, 65535.0)
    return arr.astype(np.uint16)


def box_blur2d(img: np.ndarray, radius: int) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)
    if radius <= 0:
        return arr
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D image for denoise, got shape {arr.shape}")

    h, w = arr.shape
    max_r = max(0, min(h, w) // 2 - 1)
    r = min(radius, max_r)
    if r <= 0:
        return arr

    k = 2 * r + 1

    pad_x = np.pad(arr, ((0, 0), (r, r)), mode="reflect")
    cs_x = np.cumsum(pad_x, axis=1, dtype=np.float64)
    cs_x = np.pad(cs_x, ((0, 0), (1, 0)), mode="constant", constant_values=0.0)
    sum_x = cs_x[:, k:] - cs_x[:, :-k]
    blur_x = (sum_x / float(k)).astype(np.float32)

    pad_y = np.pad(blur_x, ((r, r), (0, 0)), mode="reflect")
    cs_y = np.cumsum(pad_y, axis=0, dtype=np.float64)
    cs_y = np.pad(cs_y, ((1, 0), (0, 0)), mode="constant", constant_values=0.0)
    sum_y = cs_y[k:, :] - cs_y[:-k, :]
    return (sum_y / float(k)).astype(np.float32)


def apply_background_suppression(img: np.ndarray, background_mode: str) -> np.ndarray:
    mode = str(background_mode).strip().lower()
    arr = np.asarray(img, dtype=np.float32)
    if mode in {"", "off", "none"}:
        return arr
    if mode == "light":
        radius = 15
    elif mode == "medium":
        radius = 25
    elif mode == "strong":
        radius = 40
    else:
        return arr

    bg = box_blur2d(arr, radius)
    corrected = arr - bg
    corrected = np.clip(corrected, 0.0, None)
    return corrected.astype(np.float32)


def apply_optional_denoise(img: np.ndarray, denoise_mode: str) -> np.ndarray:
    mode = str(denoise_mode).strip().lower()
    arr = np.asarray(img, dtype=np.float32)
    if mode in {"", "off", "none"}:
        return arr
    if mode == "light":
        radius, blend = 1, 0.55
    elif mode == "medium":
        radius, blend = 2, 0.75
    elif mode == "strong":
        radius, blend = 3, 1.0
    else:
        return arr

    blur = box_blur2d(arr, radius)
    return ((1.0 - blend) * arr + blend * blur).astype(np.float32)


def preprocess_stack_image(
    img: np.ndarray,
    background_mode: str,
    denoise_mode: str,
) -> np.ndarray:
    x = apply_background_suppression(img, background_mode)
    return apply_optional_denoise(x, denoise_mode)


def compute_auto_range_with_processing(
    img: np.ndarray,
    background_mode: str,
    denoise_mode: str,
) -> tuple[float, float]:
    proc = preprocess_stack_image(img, background_mode, denoise_mode)
    return compute_default_min_max(proc)


def clean_choice(raw: Any, options: list[str], default: str) -> str:
    text = str(raw or "").strip().lower()
    for option in options:
        if text == option.lower():
            return option
    return default

__all__ = [
    "BACKGROUND_OPTIONS",
    "DEFAULT_BACKGROUND_BY_INDEX",
    "DEFAULT_DENOISE_BY_INDEX",
    "DEFAULT_LUT_BY_INDEX",
    "DENOISE_OPTIONS",
    "LUT_OPTIONS",
    "apply_background_suppression",
    "apply_optional_denoise",
    "bool_or",
    "box_blur2d",
    "clean_choice",
    "compute_auto_range_with_processing",
    "compute_default_min_max",
    "convert_to_export_dtype",
    "float_or",
    "int_or",
    "preprocess_stack_image",
]
