from __future__ import annotations

import numpy as np

from services.fluorescence.roi_primitives import (
    apply_metric_mode,
    circle_geometry,
    metrics_from_flat,
    ring_count,
    ring_width_px,
    safe_ratio,
    shape_type,
)
from services.fluorescence.stack import float_or


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
    cx, cy, radius, x1, y1, x2, y2, _ring_width = circle_geometry(roi)
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

    if count is not None:
        return _fixed_count_ring_rows(
            patch,
            dist_px,
            radius,
            count,
            metric,
            bg_mean_value,
            plot_metric,
            pixel_size_um,
        )
    if use_um_rings:
        return _um_width_ring_rows(
            patch,
            dist_px,
            radius,
            float(ring_um),
            metric,
            bg_mean_value,
            plot_metric,
            float(pixel_size_um),
        )
    return _pixel_width_ring_rows(
        patch,
        dist2,
        radius,
        ring_width,
        roi,
        metric,
        bg_mean_value,
        plot_metric,
        pixel_size_um,
    )


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
                "difference": value1 - value2
                if np.isfinite(value1) and np.isfinite(value2)
                else np.nan,
                "stack1_area_px": int(row1.get("area_px", 0)),
                "stack2_area_px": int(row2.get("area_px", 0)),
                "stack1_bg_mean": float(bg1) if np.isfinite(bg1) else np.nan,
                "stack2_bg_mean": float(bg2) if np.isfinite(bg2) else np.nan,
            }
        )
    return out


def _fixed_count_ring_rows(
    patch: np.ndarray,
    dist_px: np.ndarray,
    radius: float,
    count: int,
    metric: str,
    bg_mean_value: float,
    plot_metric: str,
    pixel_size_um: float | None,
) -> list[dict]:
    rows = []
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
            raw_val, metrics.get("area_px", 0), metric, bg_mean_value, plot_metric
        )
        has_scale = pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
        ring_width_um = (
            (float(outer_px) - float(inner_px)) * float(pixel_size_um) if has_scale else np.nan
        )
        rows.append(
            {
                "inner_radius_px": float(inner_px),
                "outer_radius_px": float(outer_px),
                "radius_mid_px": (float(inner_px) + float(outer_px)) / 2.0,
                "inner_radius_um": float(inner_px) * float(pixel_size_um) if has_scale else np.nan,
                "outer_radius_um": float(outer_px) * float(pixel_size_um) if has_scale else np.nan,
                "ring_width_px": float(outer_px) - float(inner_px),
                "ring_width_um": ring_width_um,
                "ring_count": int(count),
                "raw": raw_val,
                "value": val,
                "area_px": int(metrics.get("area_px", 0)),
            }
        )
    return rows


def _um_width_ring_rows(
    patch: np.ndarray,
    dist_px: np.ndarray,
    radius: float,
    ring_um: float,
    metric: str,
    bg_mean_value: float,
    plot_metric: str,
    pixel_size_um: float,
) -> list[dict]:
    rows = []
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
            raw_val, metrics.get("area_px", 0), metric, bg_mean_value, plot_metric
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
                "ring_width_px": int(ring_width_px_from_um(ring_um, pixel_size_um)),
                "ring_width_um": float(ring_um),
                "ring_count": np.nan,
                "raw": raw_val,
                "value": val,
                "area_px": int(metrics.get("area_px", 0)),
            }
        )
        inner_um = outer_um
    return rows


def _pixel_width_ring_rows(
    patch: np.ndarray,
    dist2: np.ndarray,
    radius: int,
    ring_width: float,
    roi: dict,
    metric: str,
    bg_mean_value: float,
    plot_metric: str,
    pixel_size_um: float | None,
) -> list[dict]:
    rows = []
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
        val = apply_metric_mode(
            raw_val, metrics.get("area_px", 0), metric, bg_mean_value, plot_metric
        )
        has_scale = pixel_size_um is not None and np.isfinite(pixel_size_um) and pixel_size_um > 0
        rows.append(
            {
                "inner_radius_px": int(inner),
                "outer_radius_px": int(outer),
                "radius_mid_px": (float(inner) + float(outer)) / 2.0,
                "inner_radius_um": float(inner) * float(pixel_size_um) if has_scale else np.nan,
                "outer_radius_um": float(outer) * float(pixel_size_um) if has_scale else np.nan,
                "ring_width_px": int(ring_width),
                "ring_width_um": float(roi.get("ring_width_um", np.nan))
                if roi.get("ring_width_um", None) not in (None, "")
                else (float(ring_width) * float(pixel_size_um) if has_scale else np.nan),
                "ring_count": np.nan,
                "raw": raw_val,
                "value": val,
                "area_px": int(metrics.get("area_px", 0)),
            }
        )
        inner = outer
    return rows


def ring_width_px_from_um(ring_um: float, pixel_size_um: float) -> float:
    if pixel_size_um <= 0:
        return 0.0
    return float(ring_um) / float(pixel_size_um)
