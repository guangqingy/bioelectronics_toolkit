from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from services.fluorescence.stack import float_or, int_or


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
