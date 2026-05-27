from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.io_guards import assert_tiff_within_limits
from services.fluorescence.roi_primitives import (
    apply_metric_mode,  # noqa: F401 - public facade re-export
    background_mean,  # noqa: F401 - public facade re-export
    circle_geometry,  # noqa: F401 - public facade re-export
    empty_metrics,  # noqa: F401 - public facade re-export
    metrics_2d,
    metrics_from_flat,  # noqa: F401 - public facade re-export
    ring_count,  # noqa: F401 - public facade re-export
    ring_width_px,  # noqa: F401 - public facade re-export
    safe_ratio,  # noqa: F401 - public facade re-export
    sequence_number,  # noqa: F401 - public facade re-export
    shape_type,  # noqa: F401 - public facade re-export
    values_2d,  # noqa: F401 - public facade re-export
)
from services.fluorescence.roi_radial import (  # noqa: F401 - public facade re-export
    radial_metrics_2d,
    radial_pair_rows,
)


def import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile


def collect_pairs(folder: Path, include_unpaired: bool = True) -> list[dict]:
    """Find *_stack1_*.tif / *_stack2_*.tif pairs in a folder."""
    tifs = sorted(list(folder.glob("*.tif")) + list(folder.glob("*.tiff")))
    pair_map: dict[str, dict[str, Path]] = {}
    for path in tifs:
        stem_lower = path.stem.lower()
        if "_stack1_" in stem_lower:
            base = re.sub(r"_stack[12]_[^_]+$", "", path.stem, flags=re.IGNORECASE)
            pair_map.setdefault(base, {})["stack1"] = path
        elif "_stack2_" in stem_lower:
            base = re.sub(r"_stack[12]_[^_]+$", "", path.stem, flags=re.IGNORECASE)
            pair_map.setdefault(base, {})["stack2"] = path
        elif include_unpaired:
            pair_map.setdefault(path.stem, {})["stack1"] = path

    records = []
    for base, item in sorted(pair_map.items()):
        records.append(
            {
                "base": base,
                "stack1": str(item["stack1"]) if "stack1" in item else "",
                "stack2": str(item["stack2"]) if "stack2" in item else "",
            }
        )
    return records


def read_first_page(stack_path: str, tifflib_module: Any = None) -> np.ndarray:
    """Read only the first page of a TIFF for sequence-style analysis."""
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(stack_path, tifflib)
    with tifflib.TiffFile(stack_path) as tif:
        arr = tif.pages[0].asarray()
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return arr
    if arr.ndim >= 3:
        return np.squeeze(arr[0]) if arr.shape[0] == 1 else arr[0]
    raise ValueError(f"Unsupported TIFF page shape: {arr.shape}")


def compute_stack_roi(
    stack_path: str,
    rois: list,
    metric: str = "mean",
    tifflib_module: Any = None,
) -> tuple[dict[str, list[float]], int]:
    """
    Compute per-frame ROI metric for a TIFF stack.

    Streams one page at a time via TiffFile to avoid loading the entire stack
    into RAM for large multi-frame stacks.
    """
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(stack_path, tifflib)
    results = {roi["label"]: [] for roi in rois}
    with tifflib.TiffFile(stack_path) as tif:
        pages = tif.pages
        if len(pages) == 1:
            first = np.asarray(pages[0].asarray())
            frames = [first] if first.ndim == 2 else [np.asarray(first[i]) for i in range(first.shape[0])]
        else:
            frames = [page.asarray() for page in pages]
        n_frames = len(frames)
        for raw_frame in frames:
            frame = np.asarray(raw_frame, dtype=np.float64)
            if frame.ndim != 2:
                frame = np.squeeze(frame)
            if frame.ndim != 2:
                raise ValueError(f"Unsupported TIFF frame shape: {frame.shape}")
            for roi in rois:
                metrics = metrics_2d(frame, roi)
                val = float(metrics.get(metric, np.nan))
                results[roi["label"]].append(val)
    return results, n_frames


def shared_ylim(*series) -> tuple[float, float] | None:
    vals = []
    for item in series:
        arr = pd.to_numeric(pd.Series(item), errors="coerce").to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size:
            vals.append(arr)
    if not vals:
        return None

    all_vals = np.concatenate(vals)
    ymin = float(np.min(all_vals))
    ymax = float(np.max(all_vals))
    if not np.isfinite(ymin) or not np.isfinite(ymax):
        return None
    if abs(ymax - ymin) < 1e-12:
        pad = max(1.0, abs(ymax) * 0.05)
    else:
        pad = (ymax - ymin) * 0.08
    return ymin - pad, ymax + pad


def resolve_ref_index(df: pd.DataFrame, ref_sequence_raw: str) -> int | None:
    """
    Resolve reference index from a sequence value or row index string.
    Priority: sequence_number exact match -> 0-based index -> 1-based index.
    """
    text = str(ref_sequence_raw or "").strip()
    if not text or "sequence_number" not in df.columns:
        return None

    seq_vals = pd.to_numeric(df["sequence_number"], errors="coerce").to_numpy(dtype=float)
    try:
        ref_val = float(text)
        hits = np.where(np.isfinite(seq_vals) & np.isclose(seq_vals, ref_val, atol=1e-9))[0]
        if hits.size > 0:
            return int(hits[0])
    except Exception:
        pass

    try:
        idx0 = int(float(text))
        if 0 <= idx0 < len(df):
            return idx0
        idx1 = idx0 - 1
        if 0 <= idx1 < len(df):
            return idx1
    except Exception:
        pass

    return None


def normalize_to_reference(arr: np.ndarray, ref_idx: int | None) -> np.ndarray:
    """Normalize array to reference point so the reference value becomes 1."""
    y = np.asarray(arr, dtype=float)
    if ref_idx is None:
        return y
    if ref_idx < 0 or ref_idx >= y.size:
        return y

    ref = y[ref_idx]
    if not np.isfinite(ref) or abs(ref) < 1e-12:
        return np.full_like(y, np.nan, dtype=float)
    return y / ref


def delta_f_over_f0(arr: np.ndarray, ref_idx: int | None) -> np.ndarray:
    """Compute DeltaF/F0 = (F - F0) / F0 using reference index or first finite value."""
    y = np.asarray(arr, dtype=float)
    if y.size == 0:
        return y

    idx = ref_idx
    if idx is None or idx < 0 or idx >= y.size or not np.isfinite(y[idx]):
        finite = np.where(np.isfinite(y))[0]
        if finite.size == 0:
            return np.full_like(y, np.nan, dtype=float)
        idx = int(finite[0])

    f0 = y[idx]
    if not np.isfinite(f0) or abs(f0) < 1e-12:
        return np.full_like(y, np.nan, dtype=float)

    return (y - f0) / f0
