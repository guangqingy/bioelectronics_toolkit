from __future__ import annotations

from typing import Any

import numpy as np

from services.histology_analysis import (
    _dapi_analysis_mask,
    _marker_analysis,
    _mask_for_roi,
)
from services.histology_data_project import (
    _entry_image_files,
    _infer_tiff_pixel_calibration,
    _positive_float,
)
from services.histology_image_io import _read_project_image
from services.histology_project_preview import (
    _channel_rgb_slot,
    _composite_from_image_files,
)
from services.histology_tiff_project import estimate_image_load_size


def _roi_native_bounds(
    rois: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    xs: list[float] = []
    ys: list[float] = []
    for roi in rois:
        for point in roi.get("points", []):
            try:
                xs.append(float(point.get("x")))
                ys.append(float(point.get("y")))
            except Exception:
                continue
    if not xs or not ys:
        return 0, 0, int(image_width), int(image_height)
    x0 = max(0, int(np.floor(min(xs))) - int(padding))
    y0 = max(0, int(np.floor(min(ys))) - int(padding))
    x1 = min(int(image_width), int(np.ceil(max(xs))) + int(padding) + 1)
    y1 = min(int(image_height), int(np.ceil(max(ys))) + int(padding) + 1)
    if x1 <= x0:
        x1 = min(int(image_width), x0 + 1)
    if y1 <= y0:
        y1 = min(int(image_height), y0 + 1)
    return x0, y0, x1, y1


def _roi_crop_padding(params: dict[str, Any]) -> int:
    def rolling_for(prefix: str) -> int:
        mode = str(params.get(f"{prefix}_background_mode") or params.get("background_mode") or "percentile").lower()
        if mode not in {"rolling", "rolling_ball", "local"}:
            return 0
        return int(float(params.get(f"{prefix}_rolling_radius_px") or params.get("rolling_radius_px") or 0))

    rolling = max(0, rolling_for("sma"), rolling_for("macrophage"), rolling_for("dapi"))
    sigma = max(
        0.0,
        float(params.get("smooth_sigma") or 0),
        float(params.get("sma_smooth_sigma") or 0),
        float(params.get("macrophage_smooth_sigma") or 0),
        float(params.get("dapi_smooth_sigma") or 0),
    )
    return max(8, rolling, int(np.ceil(sigma * 4)))


def _translate_rois(rois: list[dict[str, Any]], x0: int, y0: int) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    for roi in rois:
        copy = dict(roi)
        copy["points"] = [
            {"x": float(point["x"]) - x0, "y": float(point["y"]) - y0}
            for point in roi.get("points", [])
        ]
        translated.append(copy)
    return translated


def _translate_and_scale_rois(
    rois: list[dict[str, Any]],
    x0: int,
    y0: int,
    scale: float,
) -> list[dict[str, Any]]:
    translated = _translate_rois(rois, x0, y0)
    if abs(float(scale) - 1.0) < 1e-9:
        return translated
    for roi in translated:
        roi["points"] = [
            {"x": float(point["x"]) * float(scale), "y": float(point["y"]) * float(scale)}
            for point in roi.get("points", [])
        ]
    return translated


def _roi_shrink_percent(params: dict[str, Any]) -> float:
    for key in ("roi_shrink_percent", "roi_shrink_pct", "roi_inset_percent", "roi_inset_pct"):
        if key not in params:
            continue
        try:
            value = float(params.get(key) or 0)
        except Exception:
            value = 0.0
        if np.isfinite(value):
            return max(0.0, min(90.0, value))
    return 0.0


def _polygon_centroid(points: list[dict[str, Any]]) -> tuple[float, float]:
    clean: list[tuple[float, float]] = []
    for point in points:
        try:
            x = float(point.get("x"))
            y = float(point.get("y"))
        except Exception:
            continue
        if np.isfinite(x) and np.isfinite(y):
            clean.append((x, y))
    if not clean:
        return 0.0, 0.0
    if len(clean) < 3:
        return float(np.mean([p[0] for p in clean])), float(np.mean([p[1] for p in clean]))
    signed_area = 0.0
    cx = 0.0
    cy = 0.0
    for idx, (x0, y0) in enumerate(clean):
        x1, y1 = clean[(idx + 1) % len(clean)]
        cross = x0 * y1 - x1 * y0
        signed_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    signed_area *= 0.5
    if abs(signed_area) < 1e-9:
        return float(np.mean([p[0] for p in clean])), float(np.mean([p[1] for p in clean]))
    return cx / (6.0 * signed_area), cy / (6.0 * signed_area)


def _shrink_roi(roi: dict[str, Any], percent: float) -> dict[str, Any]:
    copy = dict(roi)
    points = roi.get("points") if isinstance(roi.get("points"), list) else []
    copy["points"] = [
        {"x": float(point.get("x") or 0), "y": float(point.get("y") or 0)}
        for point in points
    ]
    shrink = max(0.0, min(90.0, float(percent or 0.0)))
    if shrink <= 0 or len(copy["points"]) < 3:
        return copy
    scale = 1.0 - shrink / 100.0
    cx, cy = _polygon_centroid(copy["points"])
    copy["points"] = [
        {
            "x": float(cx + (float(point["x"]) - cx) * scale),
            "y": float(cy + (float(point["y"]) - cy) * scale),
        }
        for point in copy["points"]
    ]
    copy["analysis_roi_shrink_percent"] = shrink
    return copy


def _analysis_rois_for_params(clean_rois: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    shrink = _roi_shrink_percent(params)
    return [_shrink_roi(roi, shrink) for roi in clean_rois]


def _analysis_max_region_pixels(params: dict[str, Any]) -> int:
    try:
        value = int(float(params.get("analysis_max_region_pixels", 8_000_000)))
    except Exception:
        value = 8_000_000
    return max(0, value)


def _analysis_params_for_region_scale(params: dict[str, Any], scale: float) -> dict[str, Any]:
    try:
        scale_value = float(scale or 1.0)
    except Exception:
        scale_value = 1.0
    if scale_value <= 0 or abs(scale_value - 1.0) < 1e-9:
        return params
    area_scale = scale_value * scale_value
    out = dict(params)

    def native_int(value: Any) -> int:
        try:
            native = int(round(float(value)))
        except Exception:
            return 0
        return max(0, native)

    def scaled_area(native_value: Any, *, is_max: bool = False) -> int:
        try:
            native = int(round(float(native_value)))
        except Exception:
            native = 0
        if native <= 0:
            return 0 if is_max else 1
        return max(1, int(round(native * area_scale)))

    global_min = params.get("min_positive_area_px", 12)
    global_max = params.get("max_positive_area_px", 0)
    for prefix in ("sma", "macrophage"):
        min_keys = (f"{prefix}_min_area_px", f"{prefix}_min_positive_area_px")
        max_keys = (f"{prefix}_max_area_px", f"{prefix}_max_positive_area_px")
        native_min = next((params[key] for key in min_keys if key in params), global_min)
        native_max = next((params[key] for key in max_keys if key in params), global_max)
        out[f"{prefix}_min_area_px_native"] = native_int(native_min)
        out[f"{prefix}_min_area_px"] = scaled_area(native_min)
        out[f"{prefix}_max_area_px_native"] = native_int(native_max)
        out[f"{prefix}_max_area_px"] = scaled_area(native_max, is_max=True)

    dapi_min = params.get("dapi_min_area_px", 8)
    out["dapi_min_area_px_native"] = native_int(dapi_min)
    out["dapi_min_area_px"] = scaled_area(dapi_min)
    return out


def _rescale_result_counts_for_native_pixels(results: list[dict[str, Any]], scale: float) -> None:
    scale = float(scale or 1.0)
    if scale <= 0 or abs(scale - 1.0) < 1e-9:
        return
    factor = 1.0 / (scale * scale)
    count_keys = (
        "area_px",
        "analysis_area_px",
        "dapi_positive_px",
        "sma_positive_px",
        "macrophage_positive_px",
        "double_positive_px",
    )
    for row in results:
        for key in count_keys:
            if key in row:
                row[key] = int(round(float(row.get(key) or 0) * factor))
        area_px = max(1, int(row.get("area_px") or 0))
        analysis_area_px = max(1, int(row.get("analysis_area_px") or 0))
        row["dapi_positive_fraction_roi"] = float((row.get("dapi_positive_px") or 0) / area_px)
        row["sma_positive_fraction"] = float((row.get("sma_positive_px") or 0) / analysis_area_px)
        row["sma_positive_fraction_roi"] = float((row.get("sma_positive_px") or 0) / area_px)
        row["macrophage_positive_fraction"] = float((row.get("macrophage_positive_px") or 0) / analysis_area_px)
        row["macrophage_positive_fraction_roi"] = float((row.get("macrophage_positive_px") or 0) / area_px)
        row["double_positive_fraction"] = float((row.get("double_positive_px") or 0) / analysis_area_px)
        row["double_positive_fraction_roi"] = float((row.get("double_positive_px") or 0) / area_px)
        row["analysis_scale"] = scale
        row["analysis_pixel_area_scale_factor"] = factor


def _analysis_image_files(image_files: dict[str, str], params: dict[str, Any]) -> dict[str, str]:
    if len(image_files) <= 1:
        return image_files
    needed_slots: set[int] = set()
    for prefix, default_channel in (("sma", "fitc"), ("macrophage", "cy5")):
        slot = _channel_rgb_slot(str(params.get(f"{prefix}_channel") or default_channel))
        if slot is not None:
            needed_slots.add(slot)
    dapi_enabled = str(params.get("dapi_mask_enabled", False)).strip().lower() in {"1", "true", "yes", "y", "on"}
    if dapi_enabled:
        slot = _channel_rgb_slot(str(params.get("dapi_channel") or "dapi"))
        if slot is not None:
            needed_slots.add(slot)
    selected = {
        channel: path
        for channel, path in image_files.items()
        if _channel_rgb_slot(str(channel)) in needed_slots
    }
    return selected or image_files


def _read_data_project_entry_image(entry: dict[str, Any]) -> tuple[np.ndarray, str, list[str]]:
    image_files = _entry_image_files(entry)
    if image_files:
        return _composite_from_image_files(image_files)
    image_path = entry.get("image_path", "")
    if not image_path:
        raise ValueError("Selected project entry has no image path")
    return _read_project_image(image_path)


def _entry_native_dimensions(entry: dict[str, Any]) -> tuple[int, int]:
    records = entry.get("image_records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            shape = record.get("shape")
            if isinstance(shape, (list, tuple)) and len(shape) >= 2:
                try:
                    return int(shape[1]), int(shape[0])
                except Exception:
                    pass
    image_files = _entry_image_files(entry)
    candidates = list(image_files.values())
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        candidates.append(direct)
    for raw_path in candidates:
        try:
            shape = estimate_image_load_size(raw_path).get("shape")
            if isinstance(shape, tuple) and len(shape) >= 2:
                return int(shape[1]), int(shape[0])
        except Exception:
            continue
    raise ValueError("Could not determine original TIFF dimensions for ROI analysis")


def _entry_pixel_calibration(entry: dict[str, Any]) -> dict[str, Any]:
    records = entry.get("image_records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            px_w = _positive_float(record.get("pixel_width_um"))
            px_h = _positive_float(record.get("pixel_height_um")) or px_w
            if px_w is not None and px_h is not None:
                return {
                    "has_physical_scale": True,
                    "pixel_width_um": float(px_w),
                    "pixel_height_um": float(px_h),
                    "pixel_area_um2": float(px_w * px_h),
                    "source": str(record.get("pixel_size_source") or "project image record"),
                }
    image_files = _entry_image_files(entry)
    candidates = list(image_files.values())
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        candidates.append(direct)
    seen: set[str] = set()
    for raw_path in candidates:
        key = str(raw_path)
        if not key or key in seen:
            continue
        seen.add(key)
        calibration = _infer_tiff_pixel_calibration(raw_path)
        if calibration:
            return calibration
    return {"has_physical_scale": False}


def _apply_physical_calibration_to_results(
    results: list[dict[str, Any]],
    calibration: dict[str, Any],
) -> None:
    if not calibration.get("has_physical_scale"):
        return
    pixel_area = _positive_float(calibration.get("pixel_area_um2"))
    pixel_w = _positive_float(calibration.get("pixel_width_um"))
    pixel_h = _positive_float(calibration.get("pixel_height_um")) or pixel_w
    if pixel_area is None or pixel_w is None or pixel_h is None:
        return
    for row in results:
        area_um2 = float(row.get("area_px", 0) or 0) * pixel_area
        analysis_area_um2 = float(row.get("analysis_area_px", 0) or 0) * pixel_area
        row["pixel_width_um"] = float(pixel_w)
        row["pixel_height_um"] = float(pixel_h)
        row["pixel_area_um2"] = float(pixel_area)
        row["area_um2"] = area_um2
        row["area_mm2"] = area_um2 / 1_000_000.0
        row["analysis_area_um2"] = analysis_area_um2
        row["analysis_area_mm2"] = analysis_area_um2 / 1_000_000.0
        roi_area_mm2 = max(area_um2 / 1_000_000.0, 1e-12)
        analysis_area_mm2 = max(analysis_area_um2 / 1_000_000.0, 1e-12)
        for prefix in ("sma", "macrophage"):
            positive_px = float(row.get(f"{prefix}_positive_px", 0) or 0)
            object_count = float(row.get(f"{prefix}_object_count", 0) or 0)
            integrated = float(row.get(f"{prefix}_integrated_density", 0) or 0)
            row[f"{prefix}_positive_area_um2"] = positive_px * pixel_area
            row[f"{prefix}_positive_area_mm2"] = positive_px * pixel_area / 1_000_000.0
            row[f"{prefix}_object_density_per_mm2"] = object_count / roi_area_mm2
            row[f"{prefix}_object_density_analysis_per_mm2"] = object_count / analysis_area_mm2
            row[f"{prefix}_integrated_density_um2"] = integrated * pixel_area
        double_px = float(row.get("double_positive_px", 0) or 0)
        row["double_positive_area_um2"] = double_px * pixel_area
        row["double_positive_area_mm2"] = double_px * pixel_area / 1_000_000.0


def _analysis_defaults(parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "dapi_channel": "dapi",
        "dapi_threshold_method": "otsu",
        "dapi_mask_enabled": False,
        "sma_channel": "fitc",
        "sma_invert_signal": "off",
        "sma_threshold_method": "otsu",
        "sma_threshold": 120,
        "macrophage_channel": "cy5",
        "macrophage_invert_signal": "off",
        "macrophage_threshold_method": "otsu",
        "macrophage_threshold": 120,
        "background_mode": "percentile",
        "background_percentile": 10,
        "rolling_radius_px": 35,
        "smooth_sigma": 1.0,
        "threshold_percentile": 97.5,
        "threshold_std_k": 2.0,
        "min_positive_area_px": 12,
        "roi_shrink_percent": 0,
        "summary_group_by": "sample",
        "summary_aggregate_rois_by_entry": True,
        "exclude_zero_observations": False,
        **dict(parameters or {}),
    }


def _analyze_marker_rois(
    arr: np.ndarray,
    clean_rois: list[dict[str, Any]],
    params: dict[str, Any],
) -> tuple[int, int, list[dict[str, Any]]]:
    h, w = arr.shape[:2]
    results: list[dict[str, Any]] = []
    for roi in clean_rois:
        mask = _mask_for_roi(w, h, roi)
        area_px = int(np.count_nonzero(mask))
        analysis_mask, dapi = _dapi_analysis_mask(arr, mask, params)
        sma, sma_positive = _marker_analysis(arr, mask, analysis_mask, params, "sma", "fitc")
        macrophage, macrophage_positive = _marker_analysis(
            arr, mask, analysis_mask, params, "macrophage", "cy5"
        )
        double_positive = np.count_nonzero(sma_positive & macrophage_positive)
        analysis_area_px = int(np.count_nonzero(analysis_mask))
        results.append(
            {
                "roi_id": roi["id"],
                "roi_label": roi["label"],
                "area_px": area_px,
                "area_fraction_image": float(area_px / max(1, w * h)),
                "analysis_area_px": analysis_area_px,
                "dapi_channel": dapi["channel"],
                "dapi_threshold": dapi["threshold"],
                "dapi_threshold_method": dapi["threshold_method"],
                "dapi_positive_px": dapi["positive_px"],
                "dapi_positive_fraction_roi": dapi["positive_fraction_roi"],
                "dapi_object_count": dapi["object_count"],
                "sma_channel": sma["channel"],
                "sma_invert_signal": sma.get("invert_signal", "off"),
                "sma_signal_inverted": bool(sma.get("signal_inverted", False)),
                "sma_background": sma["background"],
                "sma_threshold": sma["threshold"],
                "sma_threshold_method": sma["threshold_method"],
                "sma_min_area_px": sma["min_area_px"],
                "sma_min_area_px_native": sma.get("min_area_px_native", sma["min_area_px"]),
                "sma_max_area_px": sma["max_area_px"],
                "sma_max_area_px_native": sma.get("max_area_px_native", sma["max_area_px"]),
                "sma_opening_px": sma.get("opening_px", 2),
                "sma_mean": sma["mean_corrected"],
                "sma_max": sma["max_corrected"],
                "sma_integrated_density": sma["integrated_density"],
                "sma_positive_px": sma["positive_px"],
                "sma_positive_fraction": sma["positive_fraction"],
                "sma_positive_fraction_roi": sma["positive_fraction_roi"],
                "sma_positive_mean": sma["positive_mean_corrected"],
                "sma_object_count": sma["object_count"],
                "macrophage_channel": macrophage["channel"],
                "macrophage_invert_signal": macrophage.get("invert_signal", "off"),
                "macrophage_signal_inverted": bool(macrophage.get("signal_inverted", False)),
                "macrophage_background": macrophage["background"],
                "macrophage_threshold": macrophage["threshold"],
                "macrophage_threshold_method": macrophage["threshold_method"],
                "macrophage_min_area_px": macrophage["min_area_px"],
                "macrophage_min_area_px_native": macrophage.get("min_area_px_native", macrophage["min_area_px"]),
                "macrophage_max_area_px": macrophage["max_area_px"],
                "macrophage_max_area_px_native": macrophage.get("max_area_px_native", macrophage["max_area_px"]),
                "macrophage_opening_px": macrophage.get("opening_px", 2),
                "macrophage_mean": macrophage["mean_corrected"],
                "macrophage_max": macrophage["max_corrected"],
                "macrophage_integrated_density": macrophage["integrated_density"],
                "macrophage_positive_px": macrophage["positive_px"],
                "macrophage_positive_fraction": macrophage["positive_fraction"],
                "macrophage_positive_fraction_roi": macrophage["positive_fraction_roi"],
                "macrophage_positive_mean": macrophage["positive_mean_corrected"],
                "macrophage_object_count": macrophage["object_count"],
                "double_positive_px": int(double_positive),
                "double_positive_fraction": float(double_positive / max(1, analysis_area_px)),
                "double_positive_fraction_roi": float(double_positive / max(1, area_px)),
            }
        )
    return h, w, results


__all__ = [
    "_analysis_defaults",
    "_analysis_image_files",
    "_analysis_max_region_pixels",
    "_analysis_params_for_region_scale",
    "_analysis_rois_for_params",
    "_analyze_marker_rois",
    "_apply_physical_calibration_to_results",
    "_entry_native_dimensions",
    "_entry_pixel_calibration",
    "_read_data_project_entry_image",
    "_rescale_result_counts_for_native_pixels",
    "_roi_crop_padding",
    "_roi_native_bounds",
    "_roi_shrink_percent",
    "_shrink_roi",
    "_translate_and_scale_rois",
]
