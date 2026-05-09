from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.fluorescence.stack import float_or, int_or


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
    with tifflib.TiffFile(stack_path) as tif:
        arr = tif.pages[0].asarray()
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return arr
    if arr.ndim >= 3:
        return np.squeeze(arr[0]) if arr.shape[0] == 1 else arr[0]
    raise ValueError(f"Unsupported TIFF page shape: {arr.shape}")


def shape_type(roi: dict | tuple | None) -> str:
    if not isinstance(roi, dict):
        return "rect"
    raw = str(roi.get("type", roi.get("shape", "rect")) or "rect").strip().lower()
    if raw in {"concentric", "concentric_circle", "concentric_circles", "circle", "radial"}:
        return "concentric"
    return "rect"


def empty_metrics() -> dict:
    return {
        "mean": np.nan,
        "top20_mean": np.nan,
        "sum": np.nan,
        "max": np.nan,
        "std": np.nan,
        "area_px": 0,
    }


def metrics_from_flat(flat: np.ndarray) -> dict:
    flat = np.asarray(flat, dtype=np.float64).ravel()
    if flat.size == 0:
        return empty_metrics()

    p80 = np.percentile(flat, 80.0)
    top20 = flat[flat >= p80]
    return {
        "mean": float(np.mean(flat)),
        "top20_mean": float(np.mean(top20)) if top20.size > 0 else np.nan,
        "sum": float(np.sum(flat)),
        "max": float(np.max(flat)),
        "std": float(np.std(flat)),
        "area_px": int(flat.size),
    }


def circle_geometry(roi: dict) -> tuple[int, int, int, int, int, int, int, int]:
    x1 = int_or(roi.get("x1", 0), 0)
    y1 = int_or(roi.get("y1", 0), 0)
    x2 = int_or(roi.get("x2", 0), 0)
    y2 = int_or(roi.get("y2", 0), 0)
    fallback_cx = int(round((x1 + x2) / 2.0))
    fallback_cy = int(round((y1 + y2) / 2.0))
    fallback_r = int(round(max(abs(x2 - x1), abs(y2 - y1)) / 2.0))
    cx = int_or(roi.get("cx", fallback_cx), fallback_cx)
    cy = int_or(roi.get("cy", fallback_cy), fallback_cy)
    radius = max(0, int_or(roi.get("radius", roi.get("r", fallback_r)), fallback_r))
    ring_width = max(1, int_or(roi.get("ring_width_px", roi.get("ringWidthPx", 10)), 10))
    return cx, cy, radius, cx - radius, cy - radius, cx + radius, cy + radius, ring_width


def ring_count(roi: dict) -> int | None:
    raw = int_or(roi.get("ring_count", roi.get("ringCount", 0)), 0)
    return raw if raw > 0 else None


def ring_width_px(roi: dict, pixel_size_um: float | None = None) -> float:
    count = ring_count(roi)
    if count is not None:
        _cx, _cy, radius, _x1, _y1, _x2, _y2, _ring_width = circle_geometry(roi)
        if radius > 0:
            return max(1.0, float(radius) / float(count))
    ring_um = float_or(roi.get("ring_width_um"), None)
    if (
        ring_um is not None
        and np.isfinite(ring_um)
        and ring_um > 0
        and pixel_size_um is not None
        and np.isfinite(pixel_size_um)
        and pixel_size_um > 0
    ):
        return max(1.0, float(ring_um) / float(pixel_size_um))
    return max(1.0, float_or(roi.get("ring_width_px", roi.get("ringWidthPx", 10)), 10.0))


def values_2d(img2d: np.ndarray, roi: dict | tuple[int, int, int, int]) -> np.ndarray:
    h, w = img2d.shape

    if isinstance(roi, dict) and shape_type(roi) == "concentric":
        cx, cy, radius, x1, y1, x2, y2, _ring_width = circle_geometry(roi)
        if radius <= 0:
            return np.asarray([], dtype=np.float64)
        bx1 = max(0, min(w, int(x1)))
        bx2 = max(0, min(w, int(x2) + 1))
        by1 = max(0, min(h, int(y1)))
        by2 = max(0, min(h, int(y2) + 1))
        if bx2 <= bx1 or by2 <= by1:
            return np.asarray([], dtype=np.float64)
        yy, xx = np.ogrid[by1:by2, bx1:bx2]
        mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2
        return np.asarray(img2d[by1:by2, bx1:bx2], dtype=np.float64)[mask]

    if isinstance(roi, dict):
        x1 = int_or(roi.get("x1", 0), 0)
        y1 = int_or(roi.get("y1", 0), 0)
        x2 = int_or(roi.get("x2", 0), 0)
        y2 = int_or(roi.get("y2", 0), 0)
    else:
        x1, y1, x2, y2 = roi

    x1 = max(0, min(w, int(x1)))
    x2 = max(0, min(w, int(x2)))
    y1 = max(0, min(h, int(y1)))
    y2 = max(0, min(h, int(y2)))
    if x2 <= x1 or y2 <= y1:
        return np.asarray([], dtype=np.float64)
    return np.asarray(img2d[y1:y2, x1:x2], dtype=np.float64).ravel()


def metrics_2d(img2d: np.ndarray, roi: dict | tuple[int, int, int, int]) -> dict:
    """Compute ROI metrics on one 2D image for rectangular or concentric-circle ROIs."""
    if img2d is None:
        return empty_metrics()
    return metrics_from_flat(values_2d(img2d, roi))


def safe_ratio(a: Any, b: Any) -> float:
    try:
        af = float(a)
        bf = float(b)
    except Exception:
        return float("nan")
    if not np.isfinite(af) or not np.isfinite(bf) or abs(bf) < 1e-12:
        return float("nan")
    return af / bf


def sequence_number(base_name: str) -> float:
    nums = re.findall(r"\d+", Path(base_name).stem)
    if not nums:
        return float("nan")
    try:
        return float(nums[-1])
    except Exception:
        return float("nan")


def background_mean(img2d: np.ndarray, bg_mode: str, bg_roi: dict | None = None) -> float:
    if img2d is None:
        return float("nan")
    h, w = img2d.shape
    if bg_mode == "corner_br":
        sz = min(40, max(8, h // 4), max(8, w // 4))
        roi = (w - sz, h - sz, w, h)
    elif bg_mode == "corner_tl":
        sz = min(40, max(8, h // 4), max(8, w // 4))
        roi = (0, 0, sz, sz)
    elif bg_mode == "roi" and bg_roi:
        roi = bg_roi
    else:
        return float("nan")

    metrics = metrics_2d(img2d, roi)
    return float(metrics.get("mean", np.nan))


def apply_metric_mode(
    raw_val: float,
    area_px: int,
    metric: str,
    bg_mean: float,
    plot_metric: str,
) -> float:
    if plot_metric == "bg_subtracted" and np.isfinite(bg_mean):
        if metric == "sum":
            return float(raw_val) - float(bg_mean) * max(1, int(area_px))
        return float(raw_val) - float(bg_mean)
    if plot_metric == "bg_normalized" and np.isfinite(bg_mean):
        denom = float(bg_mean) * max(1, int(area_px)) if metric == "sum" else float(bg_mean)
        return safe_ratio(raw_val, denom)
    return float(raw_val)


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
    results = {roi["label"]: [] for roi in rois}
    with tifflib.TiffFile(stack_path) as tif:
        pages = tif.pages
        n_frames = len(pages)
        for page in pages:
            frame = page.asarray().astype(np.float64)
            if frame.ndim != 2:
                frame = np.squeeze(frame)
            for roi in rois:
                metrics = metrics_2d(frame, roi)
                val = float(metrics.get(metric, np.nan))
                results[roi["label"]].append(val)
    return results, n_frames


def radial_metrics_2d(
    img2d: np.ndarray | None,
    roi: dict,
    metric: str,
    bg_mean_value: float,
    plot_metric: str,
    pixel_size_um: float | None = None,
) -> list[dict]:
    """Compute annular metrics for a concentric-circle ROI."""
    if img2d is None or shape_type(roi) != "concentric":
        return []

    h, w = img2d.shape
    cx, cy, radius, x1, y1, x2, y2, ring_width = circle_geometry(roi)
    ring_width = ring_width_px(roi, pixel_size_um)
    if radius <= 0:
        return []

    bx1 = max(0, min(w, int(x1)))
    bx2 = max(0, min(w, int(x2) + 1))
    by1 = max(0, min(h, int(y1)))
    by2 = max(0, min(h, int(y2) + 1))
    if bx2 <= bx1 or by2 <= by1:
        return []

    patch = np.asarray(img2d[by1:by2, bx1:bx2], dtype=np.float64)
    yy, xx = np.ogrid[by1:by2, bx1:bx2]
    dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
    dist_px = np.sqrt(dist2)

    count = ring_count(roi)
    ring_um = float_or(roi.get("ring_width_um"), None)
    use_um_rings = (
        ring_um is not None
        and np.isfinite(ring_um)
        and ring_um > 0
        and pixel_size_um is not None
        and np.isfinite(pixel_size_um)
        and pixel_size_um > 0
    )

    rows = []
    if count is not None:
        edges_px = np.linspace(0.0, float(radius), count + 1)
        for inner_px, outer_px in zip(edges_px[:-1], edges_px[1:]):
            if outer_px <= inner_px:
                continue
            if outer_px >= radius:
                mask = (dist_px >= inner_px) & (dist_px <= radius)
            else:
                mask = (dist_px >= inner_px) & (dist_px < outer_px)
            metrics = metrics_from_flat(patch[mask])
            raw_val = float(metrics.get(metric, np.nan))
            val = apply_metric_mode(
                raw_val,
                metrics.get("area_px", 0),
                metric,
                bg_mean_value,
                plot_metric,
            )
            has_scale = pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
            rows.append(
                {
                    "inner_radius_px": float(inner_px),
                    "outer_radius_px": float(outer_px),
                    "radius_mid_px": (float(inner_px) + float(outer_px)) / 2.0,
                    "inner_radius_um": float(inner_px) * float(pixel_size_um) if has_scale else np.nan,
                    "outer_radius_um": float(outer_px) * float(pixel_size_um) if has_scale else np.nan,
                    "ring_width_px": float(outer_px) - float(inner_px),
                    "ring_width_um": (float(outer_px) - float(inner_px)) * float(pixel_size_um) if has_scale else np.nan,
                    "ring_count": int(count),
                    "raw": raw_val,
                    "value": val,
                    "area_px": int(metrics.get("area_px", 0)),
                }
            )
        return rows

    if use_um_rings:
        radius_um = float(radius) * float(pixel_size_um)
        inner_um = 0.0
        while inner_um < radius_um:
            outer_um = min(radius_um, inner_um + float(ring_um))
            if outer_um <= inner_um:
                break
            dist_um = dist_px * float(pixel_size_um)
            if outer_um >= radius_um:
                mask = (dist_um >= inner_um) & (dist_um <= radius_um)
            else:
                mask = (dist_um >= inner_um) & (dist_um < outer_um)
            metrics = metrics_from_flat(patch[mask])
            raw_val = float(metrics.get(metric, np.nan))
            val = apply_metric_mode(
                raw_val,
                metrics.get("area_px", 0),
                metric,
                bg_mean_value,
                plot_metric,
            )
            inner_px = inner_um / float(pixel_size_um)
            outer_px = outer_um / float(pixel_size_um)
            rows.append(
                {
                    "inner_radius_px": inner_px,
                    "outer_radius_px": outer_px,
                    "radius_mid_px": (inner_px + outer_px) / 2.0,
                    "inner_radius_um": inner_um,
                    "outer_radius_um": outer_um,
                    "ring_width_px": int(ring_width),
                    "ring_width_um": float(ring_um),
                    "ring_count": np.nan,
                    "raw": raw_val,
                    "value": val,
                    "area_px": int(metrics.get("area_px", 0)),
                }
            )
            inner_um = outer_um
        return rows

    inner = 0
    while inner < radius:
        outer = min(radius, inner + ring_width)
        if outer <= inner:
            break
        if outer >= radius:
            mask = (dist2 >= inner**2) & (dist2 <= radius**2)
        else:
            mask = (dist2 >= inner**2) & (dist2 < outer**2)
        metrics = metrics_from_flat(patch[mask])
        raw_val = float(metrics.get(metric, np.nan))
        val = apply_metric_mode(raw_val, metrics.get("area_px", 0), metric, bg_mean_value, plot_metric)
        rows.append(
            {
                "inner_radius_px": int(inner),
                "outer_radius_px": int(outer),
                "radius_mid_px": (float(inner) + float(outer)) / 2.0,
                "inner_radius_um": float(inner) * float(pixel_size_um)
                if pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
                else np.nan,
                "outer_radius_um": float(outer) * float(pixel_size_um)
                if pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
                else np.nan,
                "ring_width_px": int(ring_width),
                "ring_width_um": float(roi.get("ring_width_um", np.nan))
                if roi.get("ring_width_um", None) not in (None, "")
                else (
                    float(ring_width) * float(pixel_size_um)
                    if pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
                    else np.nan
                ),
                "ring_count": np.nan,
                "raw": raw_val,
                "value": val,
                "area_px": int(metrics.get("area_px", 0)),
            }
        )
        inner = outer

    return rows


def radial_pair_rows(
    img1: np.ndarray | None,
    img2: np.ndarray | None,
    roi: dict,
    metric: str,
    bg1: float,
    bg2: float,
    plot_metric: str,
    pixel_size_um: float | None,
    base_name: str,
    sequence_number_value: float,
) -> list[dict]:
    stack1_rows = radial_metrics_2d(img1, roi, metric, bg1, plot_metric, pixel_size_um)
    stack2_rows = radial_metrics_2d(img2, roi, metric, bg2, plot_metric, pixel_size_um)
    n = max(len(stack1_rows), len(stack2_rows))
    if n <= 0:
        return []

    out = []
    for i in range(n):
        row1 = stack1_rows[i] if i < len(stack1_rows) else {}
        row2 = stack2_rows[i] if i < len(stack2_rows) else {}
        ring = row1 or row2
        radius_mid_px = float(ring.get("radius_mid_px", np.nan))
        radius_mid_um = (
            radius_mid_px * float(pixel_size_um)
            if pixel_size_um is not None
            and np.isfinite(pixel_size_um)
            and pixel_size_um > 0
            and np.isfinite(radius_mid_px)
            else np.nan
        )
        value1 = float(row1.get("value", np.nan))
        value2 = float(row2.get("value", np.nan))
        out.append(
            {
                "base_name": base_name,
                "sequence_number": sequence_number_value,
                "roi_label": roi["label"],
                "roi_key": roi["key"],
                "center_x_px": roi.get("cx", np.nan),
                "center_y_px": roi.get("cy", np.nan),
                "stim_radius_px": roi.get("radius", np.nan),
                "stim_radius_um": float(roi.get("radius", np.nan)) * float(pixel_size_um)
                if pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
                else np.nan,
                "ring_width_px": ring.get("ring_width_px", roi.get("ring_width_px", np.nan)),
                "ring_width_um": ring.get("ring_width_um", roi.get("ring_width_um", np.nan)),
                "ring_count": ring.get("ring_count", roi.get("ring_count", np.nan)),
                "inner_radius_px": ring.get("inner_radius_px", np.nan),
                "outer_radius_px": ring.get("outer_radius_px", np.nan),
                "inner_radius_um": ring.get("inner_radius_um", np.nan),
                "outer_radius_um": ring.get("outer_radius_um", np.nan),
                "radius_mid_px": radius_mid_px,
                "radius_mid_um": radius_mid_um,
                f"stack1_raw_{metric}": float(row1.get("raw", np.nan)),
                f"stack2_raw_{metric}": float(row2.get("raw", np.nan)),
                "stack1_value": value1,
                "stack2_value": value2,
                "ratio": safe_ratio(value1, value2),
                "difference": value1 - value2 if np.isfinite(value1) and np.isfinite(value2) else np.nan,
                "stack1_area_px": int(row1.get("area_px", 0)),
                "stack2_area_px": int(row2.get("area_px", 0)),
                "stack1_bg_mean": float(bg1) if np.isfinite(bg1) else np.nan,
                "stack2_bg_mean": float(bg2) if np.isfinite(bg2) else np.nan,
            }
        )
    return out


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
