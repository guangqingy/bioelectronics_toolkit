from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from services.io_guards import assert_tiff_within_limits

_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_CACHE: OrderedDict[tuple[str, int, int, int], np.ndarray] = OrderedDict()
_PAGE_CACHE_BYTES = 0


def _cache_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _page_cache_limits() -> tuple[int, int]:
    return (
        _cache_int("DP_TIFF_PAGE_CACHE_ITEMS", 12),
        _cache_int("DP_TIFF_PAGE_CACHE_BYTES", 256_000_000),
    )


def _page_cache_get(key: tuple[str, int, int, int]) -> np.ndarray | None:
    with _PAGE_CACHE_LOCK:
        arr = _PAGE_CACHE.get(key)
        if arr is None:
            return None
        _PAGE_CACHE.move_to_end(key)
        return arr


def _page_cache_put(key: tuple[str, int, int, int], arr: np.ndarray) -> None:
    global _PAGE_CACHE_BYTES
    max_items, max_bytes = _page_cache_limits()
    if max_items <= 0 or max_bytes <= 0 or int(arr.nbytes) > max_bytes:
        return
    cached = np.asarray(arr)
    cached.setflags(write=False)
    with _PAGE_CACHE_LOCK:
        previous = _PAGE_CACHE.pop(key, None)
        if previous is not None:
            _PAGE_CACHE_BYTES -= int(previous.nbytes)
        _PAGE_CACHE[key] = cached
        _PAGE_CACHE_BYTES += int(cached.nbytes)
        while _PAGE_CACHE and (len(_PAGE_CACHE) > max_items or _PAGE_CACHE_BYTES > max_bytes):
            _old_key, old_arr = _PAGE_CACHE.popitem(last=False)
            _PAGE_CACHE_BYTES -= int(old_arr.nbytes)

def import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile

def split_tiff_array_to_pages(arr: np.ndarray) -> list[np.ndarray]:
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return [arr]
    if arr.ndim == 3:
        return [np.asarray(arr[i]) for i in range(arr.shape[0])]
    raise ValueError(
        f"Unsupported TIFF shape: {arr.shape}. "
        "Only grayscale TIFF or multi-page grayscale TIFF is supported."
    )


def read_tiff_as_pages(tiff_path: Path, tifflib_module: Any = None) -> list[np.ndarray]:
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(tiff_path, tifflib)
    with tifflib.TiffFile(str(tiff_path)) as tif:
        if len(tif.pages) == 1:
            return split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
        return [np.asarray(page.asarray()) for page in tif.pages]


def tiff_stack_info(tiff_path: Path | str, tifflib_module: Any = None) -> dict[str, Any]:
    tifflib = import_tifffile(tifflib_module)
    estimate = assert_tiff_within_limits(tiff_path, tifflib)
    shape = estimate.shape
    if len(shape) == 2:
        n_frames, h, w = 1, int(shape[0]), int(shape[1])
    elif len(shape) >= 3:
        n_frames = int(shape[0])
        h = int(shape[-2])
        w = int(shape[-1])
    else:
        raise ValueError(f"Unsupported TIFF shape: {shape}")
    return {
        "n_frames": n_frames,
        "height": h,
        "width": w,
        "dtype": estimate.dtype,
        "shape": list(shape),
        "estimated_bytes": estimate.estimated_bytes,
    }


def read_tiff_page(
    tiff_path: Path | str,
    page_index: int = 0,
    tifflib_module: Any = None,
) -> tuple[np.ndarray, int, int]:
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(tiff_path, tifflib)
    p = Path(tiff_path)
    stat = p.stat()
    resolved = str(p.resolve())
    with tifflib.TiffFile(str(p)) as tif:
        if len(tif.pages) == 1:
            pages = split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
            n_frames = len(pages)
            idx = max(0, min(int(page_index), n_frames - 1))
            return np.asarray(pages[idx]), idx, n_frames
        n_frames = len(tif.pages)
        idx = max(0, min(int(page_index), n_frames - 1))
        key = (resolved, int(stat.st_mtime_ns), int(stat.st_size), idx)
        cached = _page_cache_get(key)
        if cached is not None:
            return cached, idx, n_frames
        arr = np.asarray(tif.pages[idx].asarray())
        _page_cache_put(key, arr)
        return arr, idx, n_frames


def _read_open_tiff_page(
    tif,
    idx: int,
    *,
    cache_prefix: tuple[str, int, int],
) -> tuple[np.ndarray, int]:
    if len(tif.pages) == 1:
        pages = split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
        n_frames = len(pages)
        actual = max(0, min(int(idx), n_frames - 1))
        return np.asarray(pages[actual]), n_frames
    n_frames = len(tif.pages)
    actual = max(0, min(int(idx), n_frames - 1))
    key = (*cache_prefix, actual)
    cached = _page_cache_get(key)
    if cached is not None:
        return cached, n_frames
    arr = np.asarray(tif.pages[actual].asarray())
    _page_cache_put(key, arr)
    return arr, n_frames


def select_display_frame_from_tiff(
    tiff_path: Path | str,
    frame_idx: int,
    mode: str,
    z_start: int | None,
    z_end: int | None,
    tifflib_module: Any = None,
) -> tuple[np.ndarray, dict]:
    info = tiff_stack_info(tiff_path, tifflib_module)
    n = int(info["n_frames"])
    frame_idx = max(0, min(int(frame_idx), n - 1))
    mode_clean = str(mode or "single").strip().lower()
    if mode_clean not in {"max", "mean"}:
        frame, idx, _n = read_tiff_page(tiff_path, frame_idx, tifflib_module)
        return frame, {"mode": "single", "frame": idx, "z_start": idx, "z_end": idx}

    z0 = 0 if z_start is None else max(0, min(int(z_start), n - 1))
    z1 = n - 1 if z_end is None else max(0, min(int(z_end), n - 1))
    if z1 < z0:
        z0, z1 = z1, z0

    p = Path(tiff_path)
    stat = p.stat()
    cache_prefix = (str(p.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    tifflib = import_tifffile(tifflib_module)
    with tifflib.TiffFile(str(p)) as tif:
        if mode_clean == "max":
            acc = None
            for idx in range(z0, z1 + 1):
                frame, _n = _read_open_tiff_page(tif, idx, cache_prefix=cache_prefix)
                arr = np.asarray(frame, dtype=np.float32)
                acc = arr if acc is None else np.maximum(acc, arr)
            out = acc
        else:
            total = None
            count = 0
            for idx in range(z0, z1 + 1):
                frame, _n = _read_open_tiff_page(tif, idx, cache_prefix=cache_prefix)
                arr = np.asarray(frame, dtype=np.float64)
                total = arr if total is None else total + arr
                count += 1
            out = (total / max(1, count)).astype(np.float32) if total is not None else None

    if out is None:
        out = read_tiff_page(tiff_path, frame_idx, tifflib_module)[0]
    return out, {"mode": mode_clean, "frame": frame_idx, "z_start": z0, "z_end": z1}

__all__ = [
    "_cache_int",
    "_page_cache_get",
    "_page_cache_limits",
    "_page_cache_put",
    "_read_open_tiff_page",
    "import_tifffile",
    "read_tiff_as_pages",
    "read_tiff_page",
    "select_display_frame_from_tiff",
    "split_tiff_array_to_pages",
    "tiff_stack_info",
]
