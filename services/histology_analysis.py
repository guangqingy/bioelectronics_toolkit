from __future__ import annotations

import base64
import json
import math
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

try:
    from scipy import ndimage as ndi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ndi = None

ANALYSIS_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _scale_to_uint8(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    if data.dtype == np.uint8:
        return data
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.reshape(-1)
    if finite.size == 0:
        return np.zeros(data.shape, dtype=np.uint8)
    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.8))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((data.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    return np.round(scaled * 255.0).astype(np.uint8)


def _normalize_array(arr: Any) -> np.ndarray:
    data = np.asarray(arr)
    data = np.squeeze(data)
    if data.ndim == 2:
        return _scale_to_uint8(data)
    if data.ndim == 3:
        if data.shape[-1] in {3, 4}:
            return _scale_to_uint8(data[..., :3])
        if data.shape[0] in {2, 3, 4}:
            return _scale_to_uint8(np.moveaxis(data[:3], 0, -1))
        return _scale_to_uint8(np.max(data, axis=0))
    if data.ndim > 3:
        while data.ndim > 3:
            data = np.max(data, axis=0)
        return _normalize_array(data)
    return _scale_to_uint8(data.reshape((1, -1)))


def _array_to_rgb(arr: np.ndarray) -> np.ndarray:
    data = _normalize_array(arr)
    if data.ndim == 2:
        return np.stack([data, data, data], axis=-1)
    if data.ndim == 3 and data.shape[-1] == 1:
        return np.repeat(data, 3, axis=-1)
    if data.ndim == 3 and data.shape[-1] >= 3:
        return data[..., :3]
    return np.stack([data.squeeze()] * 3, axis=-1)


def _png_b64(arr: np.ndarray, max_side: int | None = None) -> str:
    rgb = _array_to_rgb(arr)
    img = Image.fromarray(rgb, mode="RGB")
    if max_side and max(img.size) > int(max_side):
        img.thumbnail((int(max_side), int(max_side)), Image.Resampling.LANCZOS)
    stream = BytesIO()
    img.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def _clean_rois(rois: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(rois, list):
        return out
    for idx, roi in enumerate(rois, start=1):
        if not isinstance(roi, dict):
            continue
        points_raw = roi.get("points")
        if not isinstance(points_raw, list) or len(points_raw) < 3:
            continue
        points: list[dict[str, float]] = []
        for point in points_raw:
            if isinstance(point, dict):
                x = point.get("x")
                y = point.get("y")
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                x, y = point[0], point[1]
            else:
                continue
            try:
                px = float(x)
                py = float(y)
            except Exception:
                continue
            if math.isfinite(px) and math.isfinite(py):
                points.append({"x": px, "y": py})
        if len(points) < 3:
            continue
        label = str(roi.get("label") or roi.get("name") or f"ROI {idx}").strip()
        out.append(
            {
                "id": str(roi.get("id") or f"roi_{idx}"),
                "label": label,
                "classification": str(roi.get("classification") or "Annotation"),
                "coordinate_space": str(roi.get("coordinate_space") or roi.get("coordinateSpace") or "native"),
                "color": str(roi.get("color") or "#2F80ED"),
                "points": points,
            }
        )
    return out


def _channel_values(arr: np.ndarray, channel: str) -> np.ndarray:
    rgb = _array_to_rgb(arr)
    ch = str(channel or "red").strip().lower()
    if ch in {"red", "r", "0", "cy5", "macrophage"}:
        return rgb[..., 0].astype(np.float32)
    if ch in {"green", "g", "1", "fitc", "sma"}:
        return rgb[..., 1].astype(np.float32)
    if ch in {"blue", "b", "2", "dapi"}:
        return rgb[..., 2].astype(np.float32)
    if ch in {"gray", "grey", "mean", "luma"}:
        return rgb.astype(np.float32).mean(axis=-1)
    return rgb[..., 0].astype(np.float32)


def _mask_for_roi(width: int, height: int, roi: dict[str, Any]) -> np.ndarray:
    points = [(float(p["x"]), float(p["y"])) for p in roi.get("points", [])]
    mask_img = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask_img).polygon(points, outline=1, fill=1)
    return np.asarray(mask_img, dtype=bool)


def _float_param(params: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(params.get(key, default))
    except Exception:
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return value


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(float(params.get(key, default)))
    except Exception:
        return int(default)


def _bool_param(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _text_param(params: dict[str, Any], key: str, default: str = "") -> str:
    value = params.get(key, default)
    return str(value if value is not None else default).strip().lower()


def _auto_invert_signal(values: np.ndarray) -> bool:
    data = np.asarray(values, dtype=np.float32)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return False
    p0, p1, p50, p90, p95, p99, p100 = np.percentile(finite, [0, 1, 50, 90, 95, 99, 100])
    bright_background = p50 >= 170.0 and p90 >= 240.0 and p95 >= 245.0
    sparse_dark_signal = (p1 <= p50 - 20.0) or (p0 <= p50 - 35.0)
    display_inverted = p50 > 170.0 and p95 > 230.0 and p99 > p50
    return bool((bright_background and sparse_dark_signal) or display_inverted or (p100 <= p50 + 1.0 and sparse_dark_signal))


def _maybe_invert_signal(values: np.ndarray, params: dict[str, Any], prefix: str) -> tuple[np.ndarray, bool, str]:
    mode = _text_param(
        params,
        f"{prefix}_invert_signal",
        _text_param(params, f"{prefix}_invert", _text_param(params, "invert_fluorescence_signal", "off")),
    )
    if mode in {"1", "true", "yes", "y", "on", "invert"}:
        should_invert = True
        mode = "on"
    elif mode in {"auto", "automatic", "detect"}:
        should_invert = _auto_invert_signal(values)
        mode = "auto"
    else:
        should_invert = False
        mode = "off"
    if not should_invert:
        return np.asarray(values, dtype=np.float32), False, mode
    data = np.asarray(values, dtype=np.float32)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return data, False, mode
    lo = float(np.min(finite))
    hi = float(np.max(finite))
    if hi <= lo:
        return data, False, mode
    return (hi + lo - data).astype(np.float32, copy=False), True, mode


def _otsu_threshold(values: np.ndarray) -> float:
    data = np.asarray(values, dtype=np.float32)
    data = data[np.isfinite(data)]
    if data.size == 0:
        return 0.0
    lo = float(np.min(data))
    hi = float(np.max(data))
    if hi <= lo:
        return hi
    hist, edges = np.histogram(data, bins=256, range=(lo, hi))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return hi
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    mean_bg = np.cumsum(hist * centers) / np.maximum(weight_bg, 1)
    mean_fg = (np.cumsum((hist * centers)[::-1]) / np.maximum(np.cumsum(hist[::-1]), 1))[::-1]
    variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    idx = int(np.nanargmax(variance))
    return float(centers[idx])


def _background_correct(values: np.ndarray, params: dict[str, Any], prefix: str) -> tuple[np.ndarray, float]:
    data = np.asarray(values, dtype=np.float32)
    mode = str(params.get(f"{prefix}_background_mode") or params.get("background_mode") or "percentile").lower()
    percentile = _float_param(params, f"{prefix}_background_percentile", _float_param(params, "background_percentile", 10.0))
    rolling_radius = max(1, _int_param(params, f"{prefix}_rolling_radius_px", _int_param(params, "rolling_radius_px", 35)))
    background = 0.0
    if mode in {"none", "off", "raw"}:
        corrected = data.copy()
    elif mode in {"rolling", "rolling_ball", "local"} and ndi is not None:
        bg_img = ndi.grey_opening(data, size=(rolling_radius, rolling_radius))
        background = float(np.median(bg_img))
        corrected = data - bg_img
    else:
        finite = data[np.isfinite(data)]
        background = float(np.percentile(finite, np.clip(percentile, 0, 100))) if finite.size else 0.0
        corrected = data - background
    return np.clip(corrected, 0.0, None), background


def _smooth_values(values: np.ndarray, params: dict[str, Any], prefix: str) -> np.ndarray:
    sigma = _float_param(params, f"{prefix}_smooth_sigma", _float_param(params, "smooth_sigma", 1.0))
    if sigma <= 0 or ndi is None:
        return values
    return ndi.gaussian_filter(values, sigma=sigma)


def _threshold_for(values: np.ndarray, mask: np.ndarray, params: dict[str, Any], prefix: str) -> tuple[float, str]:
    method = str(params.get(f"{prefix}_threshold_method") or params.get("threshold_method") or "otsu").lower()
    roi_values = np.asarray(values[mask], dtype=np.float32)
    roi_values = roi_values[np.isfinite(roi_values)]
    if roi_values.size == 0:
        return 0.0, method
    if method in {"manual", "absolute", "fixed"}:
        return _float_param(params, f"{prefix}_threshold", _float_param(params, "threshold", 120.0)), "manual"
    if method in {"percentile", "quantile"}:
        percentile = _float_param(params, f"{prefix}_threshold_percentile", _float_param(params, "threshold_percentile", 97.5))
        signal_values = roi_values[roi_values > 0]
        threshold_values = signal_values if signal_values.size else roi_values
        return float(np.percentile(threshold_values, np.clip(percentile, 0, 100))), "percentile"
    if method in {"mean_std", "mean+std", "z"}:
        k = _float_param(params, f"{prefix}_threshold_std_k", _float_param(params, "threshold_std_k", 2.0))
        return float(np.mean(roi_values) + k * np.std(roi_values)), "mean_std"
    return _otsu_threshold(roi_values), "otsu"


def _filter_positive_mask(
    mask: np.ndarray,
    min_area_px: int,
    max_area_px: int = 0,
    opening_px: int = 2,
) -> tuple[np.ndarray, int]:
    positive = np.asarray(mask, dtype=bool)
    if ndi is None:
        return positive, int(np.count_nonzero(positive) > 0)
    if int(opening_px or 0) > 1:
        positive = ndi.binary_opening(positive, structure=np.ones((int(opening_px), int(opening_px)), dtype=bool))
    labels, count = ndi.label(positive)
    if count <= 0:
        return np.zeros_like(positive, dtype=bool), 0
    sizes = np.bincount(labels.ravel())
    keep = sizes >= max(1, int(min_area_px))
    if int(max_area_px or 0) > 0:
        keep &= sizes <= int(max_area_px)
    keep[0] = False
    filtered = keep[labels]
    _, filtered_count = ndi.label(filtered)
    return filtered, int(filtered_count)


def _marker_analysis(
    arr: np.ndarray,
    roi_mask: np.ndarray,
    analysis_mask: np.ndarray,
    params: dict[str, Any],
    prefix: str,
    default_channel: str,
) -> tuple[dict[str, Any], np.ndarray]:
    channel = str(params.get(f"{prefix}_channel") or default_channel)
    raw = _channel_values(arr, channel)
    raw, signal_inverted, invert_mode = _maybe_invert_signal(raw, params, prefix)
    corrected, background = _background_correct(raw, params, prefix)
    smooth = _smooth_values(corrected, params, prefix)
    threshold, method = _threshold_for(smooth, analysis_mask, params, prefix)
    positive_raw = analysis_mask & (smooth > threshold)
    native_min_area = _int_param(
        params,
        f"{prefix}_min_area_px_native",
        _int_param(
            params,
            f"{prefix}_min_positive_area_px_native",
            _int_param(params, f"{prefix}_min_area_px", _int_param(params, "min_positive_area_px", 12)),
        ),
    )
    min_area = _int_param(
        params,
        f"{prefix}_min_area_px",
        _int_param(params, f"{prefix}_min_positive_area_px", _int_param(params, "min_positive_area_px", 12)),
    )
    native_max_area = _int_param(
        params,
        f"{prefix}_max_area_px_native",
        _int_param(
            params,
            f"{prefix}_max_positive_area_px_native",
            _int_param(params, f"{prefix}_max_area_px", _int_param(params, "max_positive_area_px", 0)),
        ),
    )
    max_area = _int_param(
        params,
        f"{prefix}_max_area_px",
        _int_param(params, f"{prefix}_max_positive_area_px", _int_param(params, "max_positive_area_px", 0)),
    )
    opening_px = _int_param(
        params,
        f"{prefix}_opening_px",
        _int_param(params, f"{prefix}_positive_opening_px", _int_param(params, "positive_opening_px", 2)),
    )
    positive, object_count = _filter_positive_mask(positive_raw, min_area, max_area, opening_px)
    values = corrected[analysis_mask]
    pos_values = corrected[positive]
    analysis_area = int(np.count_nonzero(analysis_mask))
    roi_area = int(np.count_nonzero(roi_mask))
    positive_px = int(np.count_nonzero(positive))
    return (
        {
            "channel": channel,
            "invert_signal": invert_mode,
            "signal_inverted": bool(signal_inverted),
            "background": background,
            "threshold": float(threshold),
            "threshold_method": method,
            "min_area_px": int(min_area),
            "min_area_px_native": int(native_min_area),
            "max_area_px": int(max_area),
            "max_area_px_native": int(native_max_area),
            "opening_px": int(opening_px),
            "mean_corrected": float(np.mean(values)) if values.size else 0.0,
            "max_corrected": float(np.max(values)) if values.size else 0.0,
            "integrated_density": float(np.sum(values)) if values.size else 0.0,
            "positive_px": positive_px,
            "positive_fraction": float(positive_px / max(1, analysis_area)),
            "positive_fraction_roi": float(positive_px / max(1, roi_area)),
            "positive_mean_corrected": float(np.mean(pos_values)) if pos_values.size else 0.0,
            "object_count": int(object_count),
        },
        positive,
    )


def _dapi_analysis_mask(
    arr: np.ndarray, roi_mask: np.ndarray, params: dict[str, Any]
) -> tuple[np.ndarray, dict[str, Any]]:
    enabled = _bool_param(params, "dapi_mask_enabled", False)
    channel = str(params.get("dapi_channel") or "dapi")
    raw = _channel_values(arr, channel)
    corrected, background = _background_correct(raw, params, "dapi")
    smooth = _smooth_values(corrected, params, "dapi")
    threshold, method = _threshold_for(smooth, roi_mask, params, "dapi")
    dapi_positive = roi_mask & (smooth >= threshold)
    min_area = _int_param(params, "dapi_min_area_px", 8)
    dapi_positive, object_count = _filter_positive_mask(dapi_positive, min_area)
    if enabled and np.any(dapi_positive):
        if ndi is not None:
            dilate_px = max(0, _int_param(params, "dapi_dilate_px", 2))
            if dilate_px:
                dapi_positive = ndi.binary_dilation(dapi_positive, iterations=dilate_px)
        analysis_mask = roi_mask & dapi_positive
    else:
        analysis_mask = roi_mask
    roi_area = int(np.count_nonzero(roi_mask))
    dapi_px = int(np.count_nonzero(roi_mask & dapi_positive))
    return analysis_mask, {
        "channel": channel,
        "mask_enabled": enabled,
        "background": background,
        "threshold": float(threshold),
        "threshold_method": method,
        "min_area_px": int(min_area),
        "positive_px": dapi_px,
        "positive_fraction_roi": float(dapi_px / max(1, roi_area)),
        "object_count": int(object_count),
        "analysis_area_px": int(np.count_nonzero(analysis_mask)),
    }


def _geojson(rois: list[dict[str, Any]], measurements_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    features = []
    for roi in rois:
        coords = [[float(p["x"]), float(p["y"])] for p in roi.get("points", [])]
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {
                    "id": roi.get("id"),
                    "name": roi.get("label"),
                    "classification": roi.get("classification", "Annotation"),
                    "objectType": "annotation",
                    "color": roi.get("color", "#2F80ED"),
                    "measurements": measurements_by_id.get(str(roi.get("id")), {}),
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "DataProcess histology ROI annotations",
        "features": features,
    }
