from __future__ import annotations

import base64
import json
import math
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np
from PIL import Image, ImageDraw

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tifffile = None

try:
    import openslide  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    openslide = None

try:
    from scipy import ndimage as ndi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ndi = None

ANALYSIS_KEY = "dataprocessHistologyAnalysis"
ANALYSIS_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backup_once(path: Path) -> str:
    backup = path.with_suffix(path.suffix + ".dataprocess.bak")
    if not backup.exists() and path.exists():
        backup.write_bytes(path.read_bytes())
    return str(backup)


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_dir():
        qpproj = path / "project.qpproj"
        if qpproj.is_file():
            return qpproj
    if not path.is_file() or path.suffix.lower() != ".qpproj":
        raise FileNotFoundError(f"QuPath project not found: {path}")
    return path


def _qupath_uri_to_path(uri: Any) -> str:
    text = str(uri or "").strip()
    if not text:
        return ""
    if text.startswith("file:"):
        parsed = urlparse(text)
        raw_path = unquote(parsed.path or "")
        if not raw_path:
            raw_path = text[len("file:") :]
        if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return raw_path
    return text


def _find_first_uri(value: Any) -> str:
    if isinstance(value, str):
        if value.startswith("file:") or "/" in value or "\\" in value:
            return value
        return ""
    if isinstance(value, list):
        for item in value:
            found = _find_first_uri(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in ("uri", "path", "imagePath", "url"):
            found = _find_first_uri(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _find_first_uri(item)
            if found:
                return found
    return ""


def _entry_id(entry: dict[str, Any], fallback: int) -> str:
    raw = entry.get("entryID", fallback)
    return str(raw)


def _entry_data_dir(project_path: Path, entry_id: str) -> Path:
    return project_path.parent / "data" / str(entry_id)


def _entry_analysis_path(project_path: Path, entry_id: str) -> Path:
    return _entry_data_dir(project_path, entry_id) / "dataprocess_histology_analysis.json"


def _entry_geojson_path(project_path: Path, entry_id: str) -> Path:
    return _entry_data_dir(project_path, entry_id) / "dataprocess_histology_rois.geojson"


def _load_entry_analysis(project_path: Path, entry_id: str) -> dict[str, Any]:
    path = _entry_analysis_path(project_path, entry_id)
    if not path.is_file():
        return {"rois": [], "analyses": []}
    try:
        data = _read_json(path)
        return data if isinstance(data, dict) else {"rois": [], "analyses": []}
    except Exception:
        return {"rois": [], "analyses": []}


def _project_entries(data: dict[str, Any], project_path: Path) -> list[dict[str, Any]]:
    images = data.get("images")
    if not isinstance(images, list):
        return []

    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(images):
        if not isinstance(entry, dict):
            continue
        entry_id = _entry_id(entry, idx + 1)
        server_builder = entry.get("serverBuilder") if isinstance(entry.get("serverBuilder"), dict) else {}
        uri = _find_first_uri(server_builder) or _find_first_uri(entry)
        image_path = _qupath_uri_to_path(uri)
        analysis = _load_entry_analysis(project_path, entry_id)
        rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
        analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
        out.append(
            {
                "entry_id": entry_id,
                "image_name": str(entry.get("imageName") or f"Image {idx + 1}"),
                "randomized_name": str(entry.get("randomizedName") or ""),
                "uri": str(uri or ""),
                "image_path": image_path,
                "exists": bool(image_path and Path(image_path).expanduser().exists()),
                "roi_count": len(rois),
                "analysis_count": len(analyses),
                "rois": rois,
                "latest_analysis": analyses[-1] if analyses else {},
            }
        )
    return out


def load_qupath_project(project: str | Path) -> dict[str, Any]:
    qpproj = _project_path(project)
    data = _read_json(qpproj)
    if not isinstance(data, dict):
        raise ValueError("Invalid QuPath project JSON")
    entries = _project_entries(data, qpproj)
    return {
        "project_path": str(qpproj),
        "project_dir": str(qpproj.parent),
        "entry_count": len(entries),
        "entries": entries,
        "has_dataprocess_index": bool(data.get(ANALYSIS_KEY)),
    }


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


def _read_with_pil(path: Path) -> tuple[np.ndarray, str]:
    img = Image.open(path)
    return np.asarray(img.convert("RGB")), "pillow"


def _read_with_tifffile(path: Path) -> tuple[np.ndarray, str]:
    if tifffile is None:
        raise RuntimeError("tifffile unavailable")
    with tifffile.TiffFile(str(path)) as tf:
        if getattr(tf, "series", None):
            arr = tf.series[0].asarray()
            return _array_to_rgb(arr), "tifffile"
        if len(tf.pages) > 0:
            arr = tf.pages[0].asarray()
            return _array_to_rgb(arr), "tifffile"
    raise RuntimeError("No image series found")


def _read_with_openslide(path: Path, max_side: int | None = None) -> tuple[np.ndarray, str]:
    if openslide is None:
        raise RuntimeError("openslide unavailable")
    slide = openslide.OpenSlide(str(path))
    try:
        width, height = slide.dimensions
        if max_side:
            scale = min(1.0, float(max_side) / max(1, max(width, height)))
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = slide.get_thumbnail(size).convert("RGB")
        else:
            img = slide.get_thumbnail((width, height)).convert("RGB")
        return np.asarray(img), "openslide"
    finally:
        try:
            slide.close()
        except Exception:
            pass


def _read_image(path_raw: str | Path, max_side: int | None = None) -> tuple[np.ndarray, str, list[str]]:
    path = Path(path_raw).expanduser().resolve()
    errors: list[str] = []
    suffix = path.suffix.lower()

    readers = []
    if suffix in {".svs", ".ndpi", ".mrxs", ".scn"}:
        readers.append(_read_with_openslide)
    if suffix in {".tif", ".tiff", ".vsi", ".ome.tif", ".ome.tiff"}:
        readers.append(_read_with_tifffile)
    readers.append(_read_with_pil)
    if _read_with_openslide not in readers:
        readers.append(_read_with_openslide)

    seen: set[str] = set()
    for reader in readers:
        name = getattr(reader, "__name__", str(reader))
        if name in seen:
            continue
        seen.add(name)
        try:
            if reader is _read_with_openslide:
                arr, backend = reader(path, max_side=max_side)
            else:
                arr, backend = reader(path)
            arr = _array_to_rgb(arr)
            if max_side and max(arr.shape[:2]) > max_side:
                img = Image.fromarray(arr, mode="RGB")
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                arr = np.asarray(img.convert("RGB"))
            return arr, backend, errors
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
    raise RuntimeError("; ".join(errors[:4]) or f"Could not read image: {path}")


def _find_entry(project_path: Path, entry_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _read_json(project_path)
    if not isinstance(data, dict):
        raise ValueError("Invalid QuPath project JSON")
    images = data.get("images")
    if not isinstance(images, list):
        raise ValueError("QuPath project is missing images")
    for idx, entry in enumerate(images):
        if not isinstance(entry, dict):
            continue
        if _entry_id(entry, idx + 1) == str(entry_id):
            uri = _find_first_uri(entry.get("serverBuilder")) or _find_first_uri(entry)
            return entry, {
                "entry_id": str(entry_id),
                "image_name": str(entry.get("imageName") or f"Image {idx + 1}"),
                "uri": str(uri or ""),
                "image_path": _qupath_uri_to_path(uri),
            }
    raise ValueError(f"Entry not found in QuPath project: {entry_id}")


def _png_b64(arr: np.ndarray, max_side: int | None = None) -> str:
    rgb = _array_to_rgb(arr)
    img = Image.fromarray(rgb, mode="RGB")
    if max_side and max(img.size) > int(max_side):
        img.thumbnail((int(max_side), int(max_side)), Image.Resampling.LANCZOS)
    stream = BytesIO()
    img.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def load_project_image_preview(
    project: str | Path,
    entry_id: str,
    max_side: int = 1600,
) -> dict[str, Any]:
    qpproj = _project_path(project)
    _entry, info = _find_entry(qpproj, str(entry_id))
    image_path = info.get("image_path", "")
    if not image_path:
        raise ValueError("Selected QuPath entry has no readable image URI")
    arr, backend, warnings = _read_image(image_path, max_side=max(256, min(int(max_side), 2400)))
    analysis = _load_entry_analysis(qpproj, str(entry_id))
    h, w = arr.shape[:2]
    return {
        **info,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "preview_width": int(w),
        "preview_height": int(h),
        "img": _png_b64(arr),
        "rois": analysis.get("rois") if isinstance(analysis.get("rois"), list) else [],
        "analyses": analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else [],
        "warnings": warnings,
    }


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


def _metric_block(values: np.ndarray, mask: np.ndarray, threshold: float) -> dict[str, Any]:
    roi_vals = values[mask]
    if roi_vals.size == 0:
        return {
            "mean": 0.0,
            "max": 0.0,
            "positive_px": 0,
            "positive_fraction": 0.0,
            "positive_mean": 0.0,
        }
    positive = roi_vals >= float(threshold)
    pos_vals = roi_vals[positive]
    return {
        "mean": float(np.mean(roi_vals)),
        "max": float(np.max(roi_vals)),
        "positive_px": int(np.count_nonzero(positive)),
        "positive_fraction": float(np.count_nonzero(positive) / max(1, roi_vals.size)),
        "positive_mean": float(np.mean(pos_vals)) if pos_vals.size else 0.0,
    }


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
        return float(np.percentile(roi_values, np.clip(percentile, 0, 100))), "percentile"
    if method in {"mean_std", "mean+std", "z"}:
        k = _float_param(params, f"{prefix}_threshold_std_k", _float_param(params, "threshold_std_k", 2.0))
        return float(np.mean(roi_values) + k * np.std(roi_values)), "mean_std"
    return _otsu_threshold(roi_values), "otsu"


def _filter_positive_mask(mask: np.ndarray, min_area_px: int) -> tuple[np.ndarray, int]:
    positive = np.asarray(mask, dtype=bool)
    if ndi is None:
        return positive, int(np.count_nonzero(positive) > 0)
    positive = ndi.binary_opening(positive, structure=np.ones((2, 2), dtype=bool))
    labels, count = ndi.label(positive)
    if count <= 0:
        return np.zeros_like(positive, dtype=bool), 0
    sizes = np.bincount(labels.ravel())
    keep = sizes >= max(1, int(min_area_px))
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
    corrected, background = _background_correct(raw, params, prefix)
    smooth = _smooth_values(corrected, params, prefix)
    threshold, method = _threshold_for(smooth, analysis_mask, params, prefix)
    positive_raw = analysis_mask & (smooth >= threshold)
    min_area = _int_param(params, f"{prefix}_min_area_px", _int_param(params, "min_positive_area_px", 12))
    positive, object_count = _filter_positive_mask(positive_raw, min_area)
    values = corrected[analysis_mask]
    pos_values = corrected[positive]
    analysis_area = int(np.count_nonzero(analysis_mask))
    roi_area = int(np.count_nonzero(roi_mask))
    positive_px = int(np.count_nonzero(positive))
    return (
        {
            "channel": channel,
            "background": background,
            "threshold": float(threshold),
            "threshold_method": method,
            "min_area_px": int(min_area),
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


def _save_project_index(
    project_path: Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    data = _read_json(project_path)
    if not isinstance(data, dict):
        raise ValueError("Invalid QuPath project JSON")
    backup_path = _backup_once(project_path)
    index = data.get(ANALYSIS_KEY)
    if not isinstance(index, dict):
        index = {"version": ANALYSIS_VERSION, "entries": {}}
    index["version"] = ANALYSIS_VERSION
    index["updatedAt"] = _now_iso()
    entries = index.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    entries[str(entry_id)] = {
        "roiCount": len(rois),
        "analysisCount": len(analyses),
        "analysisJson": str(_entry_analysis_path(project_path, entry_id).relative_to(project_path.parent)),
        "roiGeoJson": str(_entry_geojson_path(project_path, entry_id).relative_to(project_path.parent)),
        "updatedAt": _now_iso(),
    }
    index["entries"] = entries
    data[ANALYSIS_KEY] = index
    if "modifyTimestamp" in data:
        data["modifyTimestamp"] = _now_ms()
    _write_json(project_path, data)
    return {"project_path": str(project_path), "backup_path": backup_path}


def _write_summary(project_path: Path, entry_id: str, rois: list[dict[str, Any]], analyses: list[Any]) -> str:
    summary_path = _entry_data_dir(project_path, entry_id) / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            raw = _read_json(summary_path)
            if isinstance(raw, dict):
                summary = raw
        except Exception:
            summary = {}
    summary[ANALYSIS_KEY] = {
        "roiCount": len(rois),
        "analysisCount": len(analyses),
        "updatedAt": _now_iso(),
    }
    _write_json(summary_path, summary)
    return str(summary_path)


def save_project_rois(
    project: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    append_analysis: bool = True,
) -> dict[str, Any]:
    qpproj = _project_path(project)
    clean_rois = _clean_rois(rois)
    existing = _load_entry_analysis(qpproj, str(entry_id))
    analyses = existing.get("analyses") if isinstance(existing.get("analyses"), list) else []
    if analysis:
        analysis = dict(analysis)
        analysis.setdefault("created_at", _now_iso())
        analyses = [*analyses, analysis] if append_analysis else [analysis]
    payload = {
        "version": ANALYSIS_VERSION,
        "project_path": str(qpproj),
        "entry_id": str(entry_id),
        "updated_at": _now_iso(),
        "rois": clean_rois,
        "analyses": analyses,
    }
    analysis_path = _entry_analysis_path(qpproj, str(entry_id))
    _write_json(analysis_path, payload)
    latest_measurements = {}
    if analyses and isinstance(analyses[-1], dict):
        latest_measurements = {
            str(item.get("roi_id")): item for item in analyses[-1].get("results", [])
        }
    geojson_path = _entry_geojson_path(qpproj, str(entry_id))
    _write_json(geojson_path, _geojson(clean_rois, latest_measurements))
    summary_path = _write_summary(qpproj, str(entry_id), clean_rois, analyses)
    project_info = _save_project_index(qpproj, str(entry_id), clean_rois, analyses)
    return {
        **project_info,
        "entry_id": str(entry_id),
        "roi_count": len(clean_rois),
        "analysis_count": len(analyses),
        "analysis_path": str(analysis_path),
        "geojson_path": str(geojson_path),
        "summary_path": summary_path,
        "rois": clean_rois,
        "latest_analysis": analyses[-1] if analyses else {},
    }


def analyze_project_rois(
    project: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qpproj = _project_path(project)
    _entry, info = _find_entry(qpproj, str(entry_id))
    image_path = info.get("image_path", "")
    if not image_path:
        raise ValueError("Selected QuPath entry has no readable image URI")
    arr, backend, warnings = _read_image(image_path, max_side=1600)
    h, w = arr.shape[:2]
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = {
        "dapi_channel": "dapi",
        "dapi_threshold_method": "otsu",
        "dapi_mask_enabled": False,
        "sma_channel": "fitc",
        "sma_threshold_method": "otsu",
        "sma_threshold": 120,
        "macrophage_channel": "cy5",
        "macrophage_threshold_method": "otsu",
        "macrophage_threshold": 120,
        "background_mode": "percentile",
        "background_percentile": 10,
        "rolling_radius_px": 35,
        "smooth_sigma": 1.0,
        "threshold_percentile": 97.5,
        "threshold_std_k": 2.0,
        "min_positive_area_px": 12,
        **dict(parameters or {}),
    }

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
                "sma_background": sma["background"],
                "sma_threshold": sma["threshold"],
                "sma_threshold_method": sma["threshold_method"],
                "sma_mean": sma["mean_corrected"],
                "sma_max": sma["max_corrected"],
                "sma_integrated_density": sma["integrated_density"],
                "sma_positive_px": sma["positive_px"],
                "sma_positive_fraction": sma["positive_fraction"],
                "sma_positive_fraction_roi": sma["positive_fraction_roi"],
                "sma_positive_mean": sma["positive_mean_corrected"],
                "sma_object_count": sma["object_count"],
                "macrophage_channel": macrophage["channel"],
                "macrophage_background": macrophage["background"],
                "macrophage_threshold": macrophage["threshold"],
                "macrophage_threshold_method": macrophage["threshold_method"],
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

    analysis = {
        "created_at": _now_iso(),
        "image_name": info.get("image_name", ""),
        "image_path": image_path,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    saved = save_project_rois(qpproj, str(entry_id), clean_rois, analysis=analysis)
    return {
        **saved,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }
