from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _array_to_rgb,
    _clean_rois,
    _dapi_analysis_mask,
    _geojson,
    _marker_analysis,
    _mask_for_roi,
    _now_iso,
    _png_b64,
    _write_json,
)
from services.histology_batch_analysis import (
    _aggregate_roi_rows_by_entry,
    _apply_batch_grouping,
    _apply_marker_inclusion,
    _apply_normalization_to_rows,
    _batch_anova,
    _batch_group_summary,
    _boolish,
    _finite_float,
    _flatten_batch_row,
    _new_project_batch_dir,
    _normalize_batch_rows,
    _params_for_roi_parameter_override,
    _parse_image_sample_and_treatment,
    _write_batch_outputs,
)
from services.histology_image_io import (
    _as_2d_channel,
    _read_project_image,
    _read_project_image_preview,
    _read_project_image_region_for_analysis,
    _read_project_image_region_preview,
)
from services.histology_data_project import (
    ETS_DATA_PROJECT_FILE,
    ETS_DATA_PROJECT_KIND,
    ETS_INDEX_FILE,
    ETS_PROJECT_DIR,
    ETS_PROTOCOL,
    _case_name_for_source,
    _data_project_cache_dir,
    _data_project_cache_layout,
    _data_project_dir,
    _data_project_entry_analysis_path,
    _data_project_entry_geojson_path,
    _entry_image_files,
    _entry_warnings,
    _find_data_project_entry,
    _has_project_image_suffix,
    _infer_tiff_pixel_calibration,
    _load_data_project_entry_analysis,
    _load_data_project_payload,
    _load_external_entry_rois,
    _normalize_data_project_path,
    _positive_float,
    _source_entry_id,
    _write_data_project_payload,
    add_histology_data_project_paths,
    create_histology_data_project,
    load_histology_data_project,
    rename_histology_data_project_entry,
)
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)
from services.histology_tiff_project import (
    estimate_image_load_size,
    load_image_for_analysis,
)

_EXPECTED_RGB_CHANNEL_WARNING = "Multi-channel/color image stored in one file; expected single-channel XY."
_LOW_BIT_DEPTH_WARNING = "TIFF is not 16-bit; confirm it is suitable for quantification."
_LOW_BIT_DEPTH_PREVIEW_WARNING = "Fluorescence TIFFs are 8-bit; confirm exports are suitable for quantification."


def _ordered_preview_channel_names(image_files: dict[str, str]) -> list[str]:
    keys = [str(channel) for channel in image_files.keys()]
    preferred = ("Hoechst", "DAPI", "FITC", "Cy5", "Mito", "Brightfield", "BF", "Transmitted", "Overview")
    ordered: list[str] = []
    for name in preferred:
        for channel in keys:
            if channel == name and channel not in ordered:
                ordered.append(channel)
    ordered.extend(channel for channel in keys if channel not in ordered)
    return ordered


def _select_preview_image_files(
    image_files: dict[str, str],
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str]:
    if not image_files:
        return {}
    by_lower = {str(channel).strip().lower(): str(channel) for channel in image_files}
    selected: list[str] = []
    for raw in selected_channels or []:
        key = str(raw or "").strip().lower()
        channel = by_lower.get(key)
        if channel and channel not in selected:
            selected.append(channel)
    if not selected:
        ordered = _ordered_preview_channel_names(image_files)
        if _has_fluorescence_channels(tuple(image_files.keys())):
            for preferred in ("FITC", "Cy5", "Hoechst", "DAPI", "Mito"):
                if preferred in image_files:
                    selected = [preferred]
                    break
            if not selected:
                selected = [
                    channel
                    for channel in ordered
                    if _channel_rgb_slot(channel) is not None and not _is_brightfield_label(channel)
                ][:1]
        if not selected:
            selected = ordered[:1]
    return {channel: image_files[channel] for channel in selected if channel in image_files}


def _clean_preview_warnings(
    warnings: list[str],
    channels: list[str] | tuple[str, ...],
) -> list[str]:
    recognized_fluorescence = any(_channel_rgb_slot(str(channel)) is not None for channel in channels)
    low_bit_seen = False
    seen: set[str] = set()
    out: list[str] = []
    for raw in warnings:
        text = str(raw or "").strip()
        if not text:
            continue
        if recognized_fluorescence and _EXPECTED_RGB_CHANNEL_WARNING in text:
            continue
        if _LOW_BIT_DEPTH_WARNING in text:
            if low_bit_seen:
                continue
            low_bit_seen = True
            text = _LOW_BIT_DEPTH_PREVIEW_WARNING
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _no_readable_channels_error(warnings: list[str]) -> ValueError:
    base = "No readable exported image channels found for this sample"
    details = "; ".join(str(item).strip() for item in warnings if str(item).strip())
    if details:
        if len(details) > 800:
            details = details[:797].rstrip() + "..."
        return ValueError(f"{base}: {details}")
    return ValueError(base)


def _channel_rgb_slot(channel: str) -> int | None:
    key = channel.strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", key))
    if key in {"cy5", "red", "macrophage", "cd68"} or tokens & {"cy5", "red", "macrophage", "cd68"}:
        return 0
    if key in {"fitc", "green", "sma", "mito", "mitotracker", "tmrm"} or tokens & {"fitc", "green", "sma", "mito", "mitotracker", "tmrm"}:
        return 1
    if key in {"hoechst", "dapi", "blue"} or tokens & {"hoechst", "dapi", "blue"}:
        return 2
    return None


def _is_brightfield_label(channel: str) -> bool:
    key = str(channel or "").strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", key))
    return key in {"brightfield", "bf", "transmitted"} or bool(
        tokens & {"brightfield", "bf", "transmitted"}
    )


def _channel_intensity_plane(channel: str, arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    slot = _channel_rgb_slot(str(channel))
    if data.ndim == 3 and data.shape[-1] in {3, 4} and slot is not None:
        return data[..., slot].astype(data.dtype, copy=False)
    return _as_2d_channel(data)


def _preview_display_plane(channel: str, arr: np.ndarray) -> np.ndarray:
    data = np.asarray(_channel_intensity_plane(channel, arr))
    if _channel_rgb_slot(str(channel)) is None:
        return data
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.reshape(-1)
    if finite.size == 0:
        return data
    p50 = float(np.percentile(finite, 50.0))
    p95 = float(np.percentile(finite, 95.0))
    p99 = float(np.percentile(finite, 99.0))
    # Some Olympus ETS-derived fluorescence planes are stored display-inverted
    # with a bright empty background. Invert only for display so ROI navigation
    # is not a blue/white sheet; analysis still uses source intensities below.
    if p50 > 170.0 and p95 > 230.0 and p99 > p50:
        hi = float(np.nanmax(finite))
        lo = float(np.nanmin(finite))
        return (hi + lo - data.astype(np.float32)).astype(np.float32, copy=False)
    return data


def _is_rgb_plane(arr: np.ndarray) -> bool:
    data = np.asarray(arr)
    return data.ndim == 3 and data.shape[-1] in {3, 4}


def _rgb_channels_are_monochrome(arr: np.ndarray) -> bool:
    data = np.asarray(arr)
    if not _is_rgb_plane(data):
        return False
    rgb = data[..., :3]
    if rgb.size == 0:
        return False
    return bool(np.array_equal(rgb[..., 0], rgb[..., 1]) and np.array_equal(rgb[..., 1], rgb[..., 2]))


def _single_channel_preview_warnings(channel: str, plane: np.ndarray) -> list[str]:
    text = str(channel or "").strip() or "Source"
    warnings: list[str] = []
    if _rgb_channels_are_monochrome(plane):
        warnings.append(f"{text}: RGB channels are identical; source preview is monochrome.")
    if _channel_rgb_slot(text) is None:
        if _is_brightfield_label(text):
            warnings.append(
                f"{text}: only a brightfield/transmitted image is indexed for this sample; "
                "SMA/macrophage color composite needs exported fluorescence channels."
            )
        else:
            warnings.append(
                f"{text}: only one unassigned image channel is indexed; "
                "color composite needs separate exported fluorescence channels or a multichannel TIFF."
            )
    warnings.append(f"{text}: preview uses display-only pseudocolor/contrast; analysis uses source intensities.")
    return warnings


def _preview_scale_to_uint8(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.reshape(-1)
    if finite.size == 0:
        return np.zeros(data.shape, dtype=np.uint8)
    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.4))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    norm = np.clip((data.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    # Keep bright single-channel previews from becoming a flat white sheet.
    norm = np.power(norm, 1.22)
    return np.round(norm * 235.0).astype(np.uint8)


def _pseudo_color_channel(channel: str, plane: np.ndarray) -> np.ndarray:
    value = _preview_scale_to_uint8(_preview_display_plane(channel, plane)).astype(np.float32)
    slot = _channel_rgb_slot(channel)
    rgb = np.zeros(value.shape + (3,), dtype=np.float32)
    if slot is not None:
        rgb[..., slot] = value
        return np.clip(rgb, 0, 255).astype(np.uint8)

    if _is_brightfield_label(channel):
        rgb[..., 0] = value
        rgb[..., 1] = value * 0.68
        rgb[..., 2] = value * 0.18
    else:
        rgb[..., 0] = value
        rgb[..., 1] = value * 0.52
        rgb[..., 2] = value * 0.88
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _preview_plane_shape(arr: np.ndarray) -> tuple[int, int]:
    data = np.asarray(arr)
    if data.ndim < 2:
        raise ValueError(f"Unsupported preview plane shape: {data.shape}")
    return tuple(int(x) for x in data.shape[:2])


def _resize_preview_plane(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    data = np.asarray(arr)
    if _is_rgb_plane(data):
        img = Image.fromarray(_array_to_rgb(data), mode="RGB")
    else:
        img = Image.fromarray(_preview_scale_to_uint8(_as_2d_channel(data)), mode="L")
    img = img.resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def _planes_to_preview_rgb(planes: list[tuple[str, np.ndarray]]) -> np.ndarray:
    if not planes:
        raise ValueError("No readable image planes found for preview")
    recognized = [(_channel_rgb_slot(channel), channel, plane) for channel, plane in planes]
    if len(planes) == 1:
        slot, channel, plane = recognized[0]
        if (
            slot is None
            and not _is_brightfield_label(channel)
            and _is_rgb_plane(plane)
            and not _rgb_channels_are_monochrome(plane)
        ):
            return _array_to_rgb(plane)
        return _pseudo_color_channel(channel, plane)
    first = _preview_scale_to_uint8(_preview_display_plane(planes[0][0], planes[0][1]))
    shape = first.shape[:2]
    rgb = np.zeros(shape + (3,), dtype=np.uint8)
    fallback_slot = 0
    for slot, channel, plane in recognized:
        plane_u8 = _preview_scale_to_uint8(_preview_display_plane(channel, plane))
        if plane_u8.shape[:2] != shape:
            img = Image.fromarray(plane_u8, mode="L").resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
            plane_u8 = np.asarray(img, dtype=np.uint8)
        if slot is None:
            if str(channel).strip().lower() in {"brightfield", "bf", "transmitted"} and len(planes) > 1:
                continue
            slot = fallback_slot % 3
            fallback_slot += 1
        rgb[..., slot] = np.maximum(rgb[..., slot], plane_u8)
    if not np.any(rgb):
        return np.stack([first, first, first], axis=-1)
    return rgb


def _has_fluorescence_channels(channels: list[str] | tuple[str, ...]) -> bool:
    return any(_channel_rgb_slot(str(channel)) is not None for channel in channels)


def _preview_composite_from_image_files(
    image_files: dict[str, str],
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, list[str]]:
    selected_files = _select_preview_image_files(image_files, selected_channels)
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    preview_shape: tuple[int, int] | None = None
    for channel, raw_path in selected_files.items():
        try:
            arr, backend, read_warnings, w, h = _read_project_image_preview(raw_path, max_side=max_side)
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = np.asarray(arr)
        if preview_shape is None:
            preview_shape = _preview_plane_shape(plane)
            image_w = int(w)
            image_h = int(h)
        elif _preview_plane_shape(plane) != preview_shape:
            plane = _resize_preview_plane(plane, preview_shape)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes:
        raise _no_readable_channels_error(warnings)
    if len(planes) == 1:
        warnings.extend(_single_channel_preview_warnings(planes[0][0], planes[0][1]))
    return _planes_to_preview_rgb(planes), "composite_preview:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, channels


def _region_composite_from_image_files(
    image_files: dict[str, str],
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, tuple[int, int, int, int], list[str]]:
    selected_files = _select_preview_image_files(image_files, selected_channels)
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    box: tuple[int, int, int, int] | None = None
    region_shape: tuple[int, int] | None = None
    for channel, raw_path in selected_files.items():
        try:
            arr, backend, read_warnings, w, h, item_box = _read_project_image_region_preview(
                raw_path,
                x,
                y,
                width,
                height,
                max_side=max_side,
            )
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = np.asarray(arr)
        if box is None:
            box = item_box
            image_w = int(w)
            image_h = int(h)
            region_shape = _preview_plane_shape(plane)
        elif _preview_plane_shape(plane) != region_shape:
            plane = _resize_preview_plane(plane, region_shape)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes or box is None:
        raise _no_readable_channels_error(warnings)
    if len(planes) == 1:
        warnings.extend(_single_channel_preview_warnings(planes[0][0], planes[0][1]))
    return _planes_to_preview_rgb(planes), "composite_region:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, box, channels


def _composite_from_image_files(image_files: dict[str, str]) -> tuple[np.ndarray, str, list[str]]:
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    dtype = np.uint16
    shape: tuple[int, int] | None = None
    first_rgb: np.ndarray | None = None
    first_channel = ""
    skip_brightfield = len(image_files) > 1 and _has_fluorescence_channels(tuple(image_files.keys()))
    for channel, path in image_files.items():
        if skip_brightfield and _is_brightfield_label(channel):
            continue
        try:
            raw = np.asarray(load_image_for_analysis(path))
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        if len(image_files) == 1 and _channel_rgb_slot(channel) is None and _is_rgb_plane(raw):
            first_rgb = raw
            first_channel = str(channel)
            break
        plane = _channel_intensity_plane(channel, raw)
        if shape is None:
            shape = tuple(int(x) for x in plane.shape[:2])
            dtype = plane.dtype
        elif tuple(plane.shape[:2]) != shape:
            warnings.append(f"{channel}: skipped mismatched shape {tuple(plane.shape[:2])}, expected {shape}")
            continue
        planes.append((channel, plane))
    if first_rgb is not None:
        return first_rgb[..., :3], f"exported_tiff_rgb:{first_channel}", warnings
    if not planes or shape is None:
        raise _no_readable_channels_error(warnings)
    composite = np.zeros(shape + (3,), dtype=dtype)
    fallback_slot = 0
    for channel, plane in planes:
        slot = _channel_rgb_slot(channel)
        if slot is None:
            slot = fallback_slot % 3
            fallback_slot += 1
        composite[..., slot] = plane.astype(dtype, copy=False)
    return composite, "exported_tiff_channels", warnings


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


def _composite_region_from_image_files(
    image_files: dict[str, str],
    box: tuple[int, int, int, int],
    max_pixels: int | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, tuple[int, int, int, int], float]:
    x0, y0, x1, y1 = box
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    dtype = np.uint16
    shape: tuple[int, int] | None = None
    native_w = 0
    native_h = 0
    first_rgb: np.ndarray | None = None
    first_channel = ""
    actual_box: tuple[int, int, int, int] | None = None
    skip_brightfield = len(image_files) > 1 and _has_fluorescence_channels(tuple(image_files.keys()))
    for channel, path in image_files.items():
        if skip_brightfield and _is_brightfield_label(channel):
            continue
        try:
            raw, backend, w, h, item_box, item_scale = _read_project_image_region_for_analysis(
                path,
                x0,
                y0,
                max(1, x1 - x0),
                max(1, y1 - y0),
                max_pixels=max_pixels,
            )
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        if native_w <= 0 or native_h <= 0:
            native_w = int(w)
            native_h = int(h)
            actual_box = item_box
            scale = float(item_scale)
        if len(image_files) == 1 and _channel_rgb_slot(channel) is None and _is_rgb_plane(raw):
            first_rgb = np.asarray(raw)[..., :3]
            first_channel = str(channel)
            backends.append(str(backend))
            break
        plane = _channel_intensity_plane(channel, raw)
        if shape is None:
            shape = tuple(int(v) for v in plane.shape[:2])
            dtype = plane.dtype
        elif tuple(plane.shape[:2]) != shape:
            warnings.append(f"{channel}: skipped mismatched region shape {tuple(plane.shape[:2])}, expected {shape}")
            continue
        planes.append((channel, plane))
        backends.append(str(backend))
    if first_rgb is not None:
        return (
            first_rgb,
            f"exported_tiff_rgb_region:{first_channel}:{'+'.join(sorted(set(backends)))}",
            warnings,
            native_w,
            native_h,
            actual_box or box,
            scale if "scale" in locals() else 1.0,
        )
    if not planes or shape is None:
        raise _no_readable_channels_error(warnings)
    composite = np.zeros(shape + (3,), dtype=dtype)
    fallback_slot = 0
    for channel, plane in planes:
        slot = _channel_rgb_slot(channel)
        if slot is None:
            slot = fallback_slot % 3
            fallback_slot += 1
        composite[..., slot] = plane.astype(dtype, copy=False)
    return (
        composite,
        "exported_tiff_channel_regions:" + "+".join(sorted(set(backends))),
        warnings,
        native_w,
        native_h,
        actual_box or box,
        scale if "scale" in locals() else 1.0,
    )


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


def _entry_preview_image_path(entry: dict[str, Any]) -> str:
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        return direct
    image_files = _entry_image_files(entry)
    for preferred in ("Hoechst", "FITC", "Cy5", "Mito", "Brightfield", "Overview"):
        if preferred in image_files:
            return image_files[preferred]
    return next(iter(image_files.values()), "")


def load_histology_data_project_image_preview(
    project_path: str | Path,
    entry_id: str,
    max_side: int = 1600,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    analysis = _load_data_project_entry_analysis(path, str(entry_id))
    preview_max = max(256, min(int(max_side), 2400))
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, channels = _preview_composite_from_image_files(
            image_files,
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h = _read_project_image_preview(preview_path, max_side=preview_max)
    if not arr.size:
        raise ValueError("Selected project entry has no image path")
    available_channels = _ordered_preview_channel_names(image_files)
    warnings = _clean_preview_warnings([*_entry_warnings(entry), *warnings], channels or available_channels)
    return {
        **entry,
        "backend": backend,
        "available_channels": available_channels,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "rois": analysis.get("rois") if isinstance(analysis.get("rois"), list) else [],
        "analyses": analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else [],
        "warnings": warnings,
    }


def load_histology_data_project_image_region_preview(
    project_path: str | Path,
    entry_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    preview_max = max(256, min(int(max_side), 2600))
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, box, channels = _region_composite_from_image_files(
            image_files,
            x,
            y,
            width,
            height,
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h, box = _read_project_image_region_preview(
            preview_path,
            x,
            y,
            width,
            height,
            max_side=preview_max,
        )
    available_channels = _ordered_preview_channel_names(image_files)
    warnings = _clean_preview_warnings([*_entry_warnings(entry), *warnings], channels or available_channels)
    x0, y0, x1, y1 = box
    return {
        "entry_id": str(entry_id),
        "backend": backend,
        "available_channels": available_channels,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "region_x": int(x0),
        "region_y": int(y0),
        "region_width": int(x1 - x0),
        "region_height": int(y1 - y0),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "warnings": warnings,
    }


def _update_data_project_entry_counts(project_path: Path, entry_id: str) -> None:
    data = _load_data_project_payload(project_path)
    images = [record for record in data.get("images", []) if isinstance(record, dict)]
    analysis = _load_data_project_entry_analysis(project_path, entry_id)
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
    analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
    latest = analyses[-1] if analyses and isinstance(analyses[-1], dict) else {}
    for record in images:
        if str(record.get("entry_id")) != str(entry_id):
            continue
        record["roi_count"] = len(rois)
        record["analysis_count"] = len(analyses)
        record["analysis_path"] = str(_data_project_entry_analysis_path(project_path, entry_id))
        record["geojson_path"] = str(_data_project_entry_geojson_path(project_path, entry_id))
        record["latest_analysis_at"] = latest.get("created_at", "") if isinstance(latest, dict) else ""
        record["updated_at"] = _now_iso()
        break
    data["images"] = images
    _write_data_project_payload(project_path, data)


def save_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    append_analysis: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    clean_rois = _clean_rois(rois)
    existing = _load_data_project_entry_analysis(path, str(entry_id))
    analyses = existing.get("analyses") if isinstance(existing.get("analyses"), list) else []
    if analysis:
        analysis = dict(analysis)
        analysis.setdefault("created_at", _now_iso())
        analyses = [*analyses, analysis] if append_analysis else [analysis]
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "display_name": entry.get("display_name", entry.get("image_name", "")),
        "image_path": entry.get("image_path", ""),
        "source_path": entry.get("source_path", entry.get("image_path", "")),
        "case_name": entry.get("case_name", ""),
        "associated_files": entry.get("associated_files", []),
        "image_files": entry.get("image_files", {}),
        "image_records": entry.get("image_records", []),
        "sample_id": entry.get("sample_id", ""),
        "case_dir": entry.get("case_dir", ""),
        "physical_rename_dir": entry.get("physical_rename_dir", ""),
        "converted_from_ets": entry.get("converted_from_ets", []),
        "converted_tiff_paths": entry.get("converted_tiff_paths", []),
        "conversion_roles": entry.get("conversion_roles", {}),
        "ets_conversion_count": entry.get("ets_conversion_count", 0),
        "analysis_folder": entry.get("analysis_folder", ""),
        "manifest_path": entry.get("manifest_path", ""),
        "parameters_path": entry.get("parameters_path", ""),
        "roi_measurements_path": entry.get("roi_measurements_path", ""),
        "label_vsi_path": entry.get("label_vsi_path", ""),
        "overview_vsi_path": entry.get("overview_vsi_path", ""),
        "updated_at": _now_iso(),
        "rois": clean_rois,
        "analyses": analyses,
    }
    analysis_path = _data_project_entry_analysis_path(path, str(entry_id))
    _write_json(analysis_path, payload)
    latest_measurements = {}
    if analyses and isinstance(analyses[-1], dict):
        latest_measurements = {str(item.get("roi_id")): item for item in analyses[-1].get("results", [])}
    geojson_path = _data_project_entry_geojson_path(path, str(entry_id))
    _write_json(geojson_path, _geojson(clean_rois, latest_measurements))
    _update_data_project_entry_counts(path, str(entry_id))
    return {
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "index_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "entry_id": str(entry_id),
        "roi_count": len(clean_rois),
        "analysis_count": len(analyses),
        "analysis_path": str(analysis_path),
        "geojson_path": str(geojson_path),
        "summary_path": str(path),
        "rois": clean_rois,
        "latest_analysis": analyses[-1] if analyses else {},
    }


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


def _run_histology_data_project_roi_analysis(
    path: Path,
    entry_id: str,
    entry: dict[str, Any],
    clean_rois: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    image_path = str(entry.get("image_path") or "")
    image_files = _entry_image_files(entry)
    analysis_rois = _analysis_rois_for_params(clean_rois, params)
    analysis_region: dict[str, int] = {}
    analysis_regions: list[dict[str, Any]] = []
    if image_files:
        image_files_for_analysis = _analysis_image_files(image_files, params)
        native_w, native_h = _entry_native_dimensions(entry)
        backend_parts: list[str] = []
        warnings: list[str] = []
        results: list[dict[str, Any]] = []
        max_pixels = _analysis_max_region_pixels(params)
        for original_roi, roi in zip(clean_rois, analysis_rois, strict=False):
            padded_box = _roi_native_bounds(
                [roi],
                native_w,
                native_h,
                padding=_roi_crop_padding(params),
            )
            arr, item_backend, item_warnings, item_w, item_h, actual_box, scale = _composite_region_from_image_files(
                image_files_for_analysis,
                padded_box,
                max_pixels=max_pixels,
            )
            backend_parts.append(str(item_backend))
            warnings.extend(item_warnings)
            if scale < 1.0:
                warnings.append(
                    f"{roi.get('label') or roi.get('id')}: ROI region was downsampled to {scale:.4f} "
                    "for large-image positive-area analysis."
                )
            translated_rois = _translate_and_scale_rois([roi], actual_box[0], actual_box[1], scale)
            scale_params = _analysis_params_for_region_scale(params, scale)
            _crop_h, _crop_w, roi_results = _analyze_marker_rois(arr, translated_rois, scale_params)
            _rescale_result_counts_for_native_pixels(roi_results, scale)
            for row in roi_results:
                row["roi_id"] = str(original_roi.get("id") or row.get("roi_id") or "")
                row["roi_label"] = str(original_roi.get("label") or row.get("roi_label") or "")
                row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
            results.extend(roi_results)
            analysis_regions.append(
                {
                    "roi_id": str(original_roi.get("id") or roi.get("id") or ""),
                    "x": int(actual_box[0]),
                    "y": int(actual_box[1]),
                    "width": int(actual_box[2] - actual_box[0]),
                    "height": int(actual_box[3] - actual_box[1]),
                    "analysis_scale": float(scale),
                    "analysis_width": int(arr.shape[1]),
                    "analysis_height": int(arr.shape[0]),
                }
            )
        backend = "+".join(sorted(set(backend_parts))) if backend_parts else "exported_tiff_channel_regions"
        w = int(native_w)
        h = int(native_h)
        w = int(w or native_w)
        h = int(h or native_h)
        for result in results:
            result["area_fraction_image"] = float(result.get("area_px", 0) / max(1, w * h))
        if analysis_regions:
            x0 = min(int(region["x"]) for region in analysis_regions)
            y0 = min(int(region["y"]) for region in analysis_regions)
            x1 = max(int(region["x"]) + int(region["width"]) for region in analysis_regions)
            y1 = max(int(region["y"]) + int(region["height"]) for region in analysis_regions)
            analysis_region = {
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
            }
    else:
        arr, backend, warnings = _read_data_project_entry_image(entry)
        h, w, results = _analyze_marker_rois(arr, analysis_rois, params)
        for row in results:
            row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
    calibration = _entry_pixel_calibration(entry)
    _apply_physical_calibration_to_results(results, calibration)

    analysis = {
        "created_at": _now_iso(),
        "protocol": ETS_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "display_name": entry.get("display_name", entry.get("image_name", "")),
        "image_path": image_path,
        "source_path": entry.get("source_path", image_path),
        "case_name": entry.get("case_name", ""),
        "associated_files": entry.get("associated_files", []),
        "image_files": entry.get("image_files", {}),
        "image_records": entry.get("image_records", []),
        "sample_id": entry.get("sample_id", ""),
        "case_dir": entry.get("case_dir", ""),
        "physical_rename_dir": entry.get("physical_rename_dir", ""),
        "converted_from_ets": entry.get("converted_from_ets", []),
        "converted_tiff_paths": entry.get("converted_tiff_paths", []),
        "conversion_roles": entry.get("conversion_roles", {}),
        "ets_conversion_count": entry.get("ets_conversion_count", 0),
        "analysis_folder": entry.get("analysis_folder", ""),
        "manifest_path": entry.get("manifest_path", ""),
        "parameters_path": entry.get("parameters_path", ""),
        "roi_measurements_path": entry.get("roi_measurements_path", ""),
        "label_vsi_path": entry.get("label_vsi_path", ""),
        "overview_vsi_path": entry.get("overview_vsi_path", ""),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "analysis_region": analysis_region,
        "analysis_regions": analysis_regions,
        "roi_shrink_percent": _roi_shrink_percent(params),
        "calibration": calibration,
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    return {
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


def analyze_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = _analysis_defaults(parameters)
    payload = _run_histology_data_project_roi_analysis(path, str(entry_id), entry, clean_rois, params)
    analysis = payload["analysis"]
    results = payload["results"]
    backend = payload["backend"]
    w = int(payload["width"])
    h = int(payload["height"])
    warnings = payload["warnings"]
    saved = save_histology_data_project_rois(path, str(entry_id), clean_rois, analysis=analysis)
    return {
        **saved,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


def _saved_or_external_entry_rois(project_path: Path, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    entry_id = str(entry.get("entry_id") or "")
    saved = _load_data_project_entry_analysis(project_path, entry_id) if entry_id else {}
    saved_rois = saved.get("rois") if isinstance(saved.get("rois"), list) else entry.get("rois")
    clean_rois = _clean_rois(saved_rois if isinstance(saved_rois, list) else [])
    if clean_rois:
        return clean_rois, "project"
    clean_rois, external_rois_path = _load_external_entry_rois(project_path, entry)
    if clean_rois:
        return clean_rois, external_rois_path or "external"
    return [], ""


def _select_roi_for_debug(
    rois: list[dict[str, Any]],
    roi_id: str = "",
    roi_index: int = 0,
) -> tuple[int, dict[str, Any]]:
    wanted_id = str(roi_id or "").strip()
    if wanted_id:
        for index, roi in enumerate(rois):
            if str(roi.get("id") or "") == wanted_id:
                return index, roi
        raise ValueError(f"ROI not found: {wanted_id}")
    index = int(roi_index or 0)
    if index < 0 or index >= len(rois):
        raise ValueError(f"ROI index {index} is outside the available ROI range 0-{max(0, len(rois) - 1)}")
    return index, rois[index]


def _roi_points_for_preview(
    roi: dict[str, Any],
    box: tuple[int, int, int, int],
    preview_w: int,
    preview_h: int,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = box
    scale_x = float(preview_w) / max(1.0, float(x1 - x0))
    scale_y = float(preview_h) / max(1.0, float(y1 - y0))
    points: list[tuple[float, float]] = []
    for point in roi.get("points", []):
        try:
            x = (float(point.get("x")) - x0) * scale_x
            y = (float(point.get("y")) - y0) * scale_y
        except Exception:
            continue
        if np.isfinite(x) and np.isfinite(y):
            points.append((x, y))
    return points


def _draw_roi_debug_overlay(
    arr: np.ndarray,
    box: tuple[int, int, int, int],
    original_roi: dict[str, Any],
    adjusted_roi: dict[str, Any],
) -> np.ndarray:
    rgb = _array_to_rgb(arr)
    img = Image.fromarray(rgb, mode="RGB").convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    line_w = max(2, int(round(max(img.size) / 320)))

    def draw_roi(roi: dict[str, Any], line: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
        pts = _roi_points_for_preview(roi, box, img.width, img.height)
        if len(pts) >= 3:
            draw.polygon(pts, fill=fill)
            draw.line([*pts, pts[0]], fill=line, width=line_w, joint="curve")
        elif len(pts) >= 2:
            draw.line(pts, fill=line, width=line_w)
        for x, y in pts:
            r = max(3, line_w + 1)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=line)

    draw_roi(original_roi, (255, 212, 72, 235), (255, 212, 72, 38))
    draw_roi(adjusted_roi, (0, 210, 255, 245), (0, 210, 255, 34))
    img.alpha_composite(overlay)
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _roi_debug_preview(
    entry: dict[str, Any],
    original_roi: dict[str, Any],
    adjusted_roi: dict[str, Any],
    params: dict[str, Any],
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    preview_max = max(256, min(int(max_side), 1800))
    native_w, native_h = _entry_native_dimensions(entry)
    padding = max(20, _roi_crop_padding(params))
    x0, y0, x1, y1 = _roi_native_bounds(
        [original_roi, adjusted_roi],
        native_w,
        native_h,
        padding=padding,
    )
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, box, channels = _region_composite_from_image_files(
            image_files,
            x0,
            y0,
            max(1, x1 - x0),
            max(1, y1 - y0),
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h, box = _read_project_image_region_preview(
            preview_path,
            x0,
            y0,
            max(1, x1 - x0),
            max(1, y1 - y0),
            max_side=preview_max,
        )
    overlay = _draw_roi_debug_overlay(arr, box, original_roi, adjusted_roi)
    bx0, by0, bx1, by1 = box
    return {
        "backend": backend,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "region_x": int(bx0),
        "region_y": int(by0),
        "region_width": int(bx1 - bx0),
        "region_height": int(by1 - by0),
        "preview_width": int(overlay.shape[1]),
        "preview_height": int(overlay.shape[0]),
        "img": _png_b64(overlay, max_side=preview_max),
        "warnings": warnings,
    }


def _roi_debug_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    rows = analysis.get("results") if isinstance(analysis.get("results"), list) else []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}

    def marker_block(marker: str) -> dict[str, Any]:
        return {
            "positive_area_ratio": _finite_float(row.get(f"{marker}_positive_fraction")),
            "positive_area_ratio_roi": _finite_float(row.get(f"{marker}_positive_fraction_roi")),
            "positive_px": int(_finite_float(row.get(f"{marker}_positive_px"), 0)),
            "threshold": _finite_float(row.get(f"{marker}_threshold")),
            "threshold_method": str(row.get(f"{marker}_threshold_method") or ""),
            "background": _finite_float(row.get(f"{marker}_background")),
            "mean": _finite_float(row.get(f"{marker}_mean")),
            "max": _finite_float(row.get(f"{marker}_max")),
            "object_count": int(_finite_float(row.get(f"{marker}_object_count"), 0)),
        }

    return {
        "roi_id": str(row.get("roi_id") or ""),
        "roi_label": str(row.get("roi_label") or ""),
        "area_px": int(_finite_float(row.get("area_px"), 0)),
        "analysis_area_px": int(_finite_float(row.get("analysis_area_px"), 0)),
        "dapi_positive_px": int(_finite_float(row.get("dapi_positive_px"), 0)),
        "sma": marker_block("sma"),
        "macrophage": marker_block("macrophage"),
        "double_positive_area_ratio": _finite_float(row.get("double_positive_fraction")),
        "row": row,
    }


def _roi_debug_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {
        "area_px": int(after.get("area_px") or 0) - int(before.get("area_px") or 0),
        "analysis_area_px": int(after.get("analysis_area_px") or 0) - int(before.get("analysis_area_px") or 0),
    }
    for marker in ("sma", "macrophage"):
        before_marker = before.get(marker) if isinstance(before.get(marker), dict) else {}
        after_marker = after.get(marker) if isinstance(after.get(marker), dict) else {}
        delta[marker] = {
            "positive_area_ratio": _finite_float(after_marker.get("positive_area_ratio"))
            - _finite_float(before_marker.get("positive_area_ratio")),
            "positive_px": int(after_marker.get("positive_px") or 0) - int(before_marker.get("positive_px") or 0),
            "threshold": _finite_float(after_marker.get("threshold")) - _finite_float(before_marker.get("threshold")),
            "object_count": int(after_marker.get("object_count") or 0) - int(before_marker.get("object_count") or 0),
        }
    return delta


def debug_histology_data_project_roi(
    project_path: str | Path,
    entry_id: str,
    roi_id: str = "",
    roi_index: int = 0,
    parameters: dict[str, Any] | None = None,
    before_parameters: dict[str, Any] | None = None,
    max_side: int = 900,
    selected_channels: list[str] | tuple[str, ...] | None = None,
    include_preview: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    rois, roi_source = _saved_or_external_entry_rois(path, entry)
    if not rois:
        raise ValueError("No saved ROI annotations are available for the selected image")
    selected_index, roi = _select_roi_for_debug(rois, roi_id=roi_id, roi_index=roi_index)
    after_params = _analysis_defaults(parameters)
    if before_parameters is None:
        before_raw = dict(after_params)
        before_raw["roi_shrink_percent"] = 0
    else:
        before_raw = dict(before_parameters)
    before_params = _analysis_defaults(before_raw)
    before_analysis_payload = _run_histology_data_project_roi_analysis(
        path,
        str(entry_id),
        entry,
        [roi],
        before_params,
    )
    after_analysis_payload = _run_histology_data_project_roi_analysis(
        path,
        str(entry_id),
        entry,
        [roi],
        after_params,
    )
    adjusted_roi = _shrink_roi(roi, _roi_shrink_percent(after_params))
    preview = (
        _roi_debug_preview(
            entry,
            roi,
            adjusted_roi,
            after_params,
            max_side=max_side,
            selected_channels=selected_channels,
        )
        if include_preview
        else {}
    )
    before = _roi_debug_metrics(before_analysis_payload["analysis"])
    after = _roi_debug_metrics(after_analysis_payload["analysis"])
    sample_number = ""
    treatment = ""
    for key in ("image_name", "display_name", "sample_id", "case_name"):
        sample_number, treatment = _parse_image_sample_and_treatment(entry.get(key))
        if treatment:
            break
    warnings = [
        *list(before_analysis_payload.get("warnings") or []),
        *list(after_analysis_payload.get("warnings") or []),
        *list(preview.get("warnings") or []),
    ]
    return {
        "ok": True,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "histology_roi_debug",
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": str(entry.get("image_name") or ""),
        "display_name": str(entry.get("display_name") or entry.get("image_name") or ""),
        "sample_id": str(entry.get("sample_id") or ""),
        "sample_number": sample_number,
        "treatment": treatment,
        "roi_source": roi_source,
        "roi_index": selected_index,
        "roi_id": str(roi.get("id") or ""),
        "roi_label": str(roi.get("label") or f"ROI {selected_index + 1}"),
        "roi": roi,
        "adjusted_roi": adjusted_roi,
        "roi_shrink_percent": _roi_shrink_percent(after_params),
        "parameters": after_params,
        "before_parameters": before_params,
        "before": before,
        "after": after,
        "delta": _roi_debug_delta(before, after),
        "preview": preview,
        "img": preview.get("img", ""),
        "warnings": warnings,
    }


def analyze_histology_data_project_saved_rois(
    project_path: str | Path,
    parameters: dict[str, Any] | None = None,
    progress: Callable[[float, str], None] | None = None,
    write_outputs: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    loaded = load_histology_data_project(path)
    entries = [entry for entry in loaded.get("entries", []) if isinstance(entry, dict)]
    params = _analysis_defaults(parameters)
    normalize_to_group = str(
        params.get("summary_normalize_to_group")
        or params.get("normalize_to_group")
        or params.get("normalize_to_sample")
        or "1"
    )
    group_by = str(params.get("summary_group_by") or params.get("group_by") or "sample").strip().lower()
    if group_by in {"treatment", "material"} and normalize_to_group == "1":
        normalize_to_group = "CB"
    roi_rows: list[dict[str, Any]] = []
    analyzed_entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    used_roi_parameter_override_keys: set[str] = set()
    total = max(1, len(entries))
    if progress:
        progress(0.01, "Loading saved ROI annotations")
    for entry_index, entry in enumerate(entries):
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id:
            skipped.append({"entry_id": "", "image_name": str(entry.get("image_name") or ""), "reason": "Missing entry id"})
            continue
        saved = _load_data_project_entry_analysis(path, entry_id)
        saved_rois = saved.get("rois") if isinstance(saved.get("rois"), list) else entry.get("rois")
        clean_rois = _clean_rois(saved_rois if isinstance(saved_rois, list) else [])
        roi_source = "project"
        if not clean_rois:
            clean_rois, external_rois_path = _load_external_entry_rois(path, entry)
            if clean_rois:
                roi_source = external_rois_path or "external"
        if not clean_rois:
            skipped.append(
                {
                    "entry_id": entry_id,
                    "image_name": str(entry.get("image_name") or ""),
                    "reason": "No saved ROI annotations",
                }
            )
            continue
        if progress:
            progress(0.05 + 0.78 * entry_index / total, f"Analyzing {entry.get('image_name') or entry_id}")
        try:
            entry_roi_params = [
                _params_for_roi_parameter_override(
                    params,
                    entry_id,
                    roi,
                    roi_index,
                    _analysis_defaults,
                )
                for roi_index, roi in enumerate(clean_rois)
            ]
            entry_has_roi_overrides = any(override_key for _roi_params, override_key in entry_roi_params)
            if entry_has_roi_overrides:
                for roi_index, (roi, (roi_params, override_key)) in enumerate(zip(clean_rois, entry_roi_params, strict=False)):
                    result = _run_histology_data_project_roi_analysis(
                        path,
                        entry_id,
                        entry,
                        [roi],
                        roi_params,
                    )
                    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
                    result_rows = analysis.get("results") if isinstance(analysis.get("results"), list) else result.get("results", [])
                    for result_row in result_rows if isinstance(result_rows, list) else []:
                        if isinstance(result_row, dict):
                            flat = _flatten_batch_row(path, entry, analysis, result_row, entry_index, roi_index)
                            flat["roi_parameter_override_key"] = override_key
                            roi_rows.append(flat)
                    if override_key:
                        used_roi_parameter_override_keys.add(override_key)
                result = {"analysis_path": ""}
            elif write_outputs:
                result = analyze_histology_data_project_rois(path, entry_id, clean_rois, parameters=params)
            else:
                result = _run_histology_data_project_roi_analysis(
                    path,
                    entry_id,
                    entry,
                    clean_rois,
                    params,
                )
        except Exception as exc:
            failures.append(
                {
                    "entry_id": entry_id,
                    "image_name": str(entry.get("image_name") or ""),
                    "reason": str(exc) or exc.__class__.__name__,
                }
            )
            continue
        if not entry_has_roi_overrides:
            analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
            result_rows = analysis.get("results") if isinstance(analysis.get("results"), list) else result.get("results", [])
            for roi_index, result_row in enumerate(result_rows if isinstance(result_rows, list) else []):
                if isinstance(result_row, dict):
                    roi_rows.append(_flatten_batch_row(path, entry, analysis, result_row, entry_index, roi_index))
        analyzed_entries.append(
            {
                "entry_id": entry_id,
                "image_name": str(entry.get("image_name") or ""),
                "roi_count": len(clean_rois),
                "roi_source": roi_source,
                "analysis_path": str(result.get("analysis_path") or ""),
            }
        )
    if not roi_rows:
        detail = "; ".join(item["reason"] for item in [*failures, *skipped][:3])
        suffix = f": {detail}" if detail else ""
        raise ValueError(f"No saved histology ROI could be analyzed{suffix}")
    if progress:
        progress(0.85, "Averaging ROI measurements by image")
    aggregate_by_entry = _boolish(
        params.get("summary_aggregate_rois_by_entry", params.get("aggregate_rois_by_entry", True)),
        default=True,
    )
    observation_rows = _aggregate_roi_rows_by_entry(roi_rows) if aggregate_by_entry else [dict(row) for row in roi_rows]
    observation_level = "image" if aggregate_by_entry else "roi"
    _apply_batch_grouping(observation_rows, group_by)
    _apply_batch_grouping(roi_rows, group_by)
    _apply_marker_inclusion(observation_rows, params)
    _apply_marker_inclusion(roi_rows, params)
    if progress:
        progress(0.88, "Normalizing image measurements")
    normalization = _normalize_batch_rows(observation_rows, normalize_to_group)
    normalization["observation_level"] = observation_level
    _apply_normalization_to_rows(roi_rows, normalization)
    summary = _batch_group_summary(observation_rows)
    stats = _batch_anova(observation_rows)
    if write_outputs:
        out_dir = _new_project_batch_dir(path)
        if progress:
            progress(0.92, "Writing CSV tables and plots")
        used_roi_parameter_override_key_list = sorted(used_roi_parameter_override_keys)
        outputs = _write_batch_outputs(
            out_dir,
            roi_rows,
            observation_rows,
            summary,
            stats,
            normalization,
            params,
            skipped,
            failures,
            observation_level=observation_level,
            roi_parameter_override_keys=used_roi_parameter_override_key_list,
        )
    else:
        used_roi_parameter_override_key_list = sorted(used_roi_parameter_override_keys)
        if progress:
            progress(0.92, "Prepared readout preview without writing output files")
        outputs = {
            "run_dir": "",
            "roi_table_path": "",
            "image_table_path": "",
            "summary_table_path": "",
            "statistics_path": "",
            "manifest_path": "",
            "plots": [],
            "outputs": [],
        }
    warnings = list(normalization.get("warnings") or [])
    warnings.extend(f"{item['image_name'] or item['entry_id']}: {item['reason']}" for item in failures)
    if progress:
        progress(1.0, "Histology saved ROI batch analysis complete")
    return {
        "ok": True,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "histology_saved_roi_batch_analysis",
        "write_outputs": bool(write_outputs),
        "project_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "created_at": _now_iso(),
        "entry_count": len(entries),
        "analyzed_entry_count": len(analyzed_entries),
        "skipped_entry_count": len(skipped),
        "failed_entry_count": len(failures),
        "roi_parameter_override_count": len(used_roi_parameter_override_key_list),
        "roi_parameter_override_keys": used_roi_parameter_override_key_list,
        "observation_level": observation_level,
        "observation_count": len(observation_rows),
        "roi_count": len(roi_rows),
        "sample_count": len(summary),
        "normalization": normalization,
        "statistics": stats,
        "summary": summary,
        "rows": observation_rows,
        "roi_rows": roi_rows,
        "analyzed_entries": analyzed_entries,
        "skipped_entries": skipped,
        "failed_entries": failures,
        "warnings": warnings,
        **outputs,
    }


def _resolve_single_image_path(image_path: str | Path) -> Path:
    raw = str(image_path or "").strip()
    if not raw:
        raise FileNotFoundError("Histology image path is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Histology image not found: {path}")
    if not _has_project_image_suffix(path):
        raise ValueError("Select an exported TIFF, PNG, or JPG image file")
    return path


def load_histology_file_image_preview(
    image_path: str | Path,
    max_side: int = 1600,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    preview_max = max(256, min(int(max_side), 2400))
    arr, backend, warnings, w, h = _read_project_image_preview(path, max_side=preview_max)
    channel_label = path.stem
    force_channel_display = _channel_rgb_slot(channel_label) is not None or _is_brightfield_label(channel_label)
    is_rgb = _is_rgb_plane(arr)
    is_mono_rgb = _rgb_channels_are_monochrome(arr) if is_rgb else False
    if force_channel_display or not is_rgb or is_mono_rgb:
        if is_mono_rgb:
            warnings.append(f"{path.name}: RGB channels are identical; source preview is monochrome.")
        elif force_channel_display:
            warnings.append(f"{path.name}: displayed as channel intensity from its filename, not as an RGB photo.")
        else:
            warnings.append(f"{path.name}: source preview is single-channel/monochrome.")
        warnings.append(
            f"{path.name}: preview uses display-only pseudocolor/contrast; analysis uses source intensities."
        )
        arr = _pseudo_color_channel(channel_label, arr)
    return {
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "rois": [],
        "analyses": [],
        "warnings": warnings,
    }


def load_histology_file_image_region_preview(
    image_path: str | Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    preview_max = max(256, min(int(max_side), 2600))
    arr, backend, warnings, w, h, box = _read_project_image_region_preview(
        path,
        x,
        y,
        width,
        height,
        max_side=preview_max,
    )
    x0, y0, x1, y1 = box
    return {
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "region_x": int(x0),
        "region_y": int(y0),
        "region_width": int(x1 - x0),
        "region_height": int(y1 - y0),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "warnings": warnings,
    }


def analyze_histology_file_rois(
    image_path: str | Path,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    arr, backend, warnings = _read_project_image(path, max_side=1600)
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")
    params = _analysis_defaults(parameters)
    analysis_rois = _analysis_rois_for_params(clean_rois, params)
    h, w, results = _analyze_marker_rois(arr, analysis_rois, params)
    for row in results:
        row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
    calibration = _infer_tiff_pixel_calibration(path) or {"has_physical_scale": False}
    _apply_physical_calibration_to_results(results, calibration)
    analysis = {
        "created_at": _now_iso(),
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "single_file_histology_analysis",
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "roi_shrink_percent": _roi_shrink_percent(params),
        "calibration": calibration,
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    return {
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "single_file_histology_analysis",
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "image_path": str(path),
        "roi_count": len(clean_rois),
        "analysis_count": 1,
        "rois": clean_rois,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


__all__ = [
    "ETS_INDEX_FILE",
    "ETS_DATA_PROJECT_FILE",
    "ETS_PROJECT_DIR",
    "ETS_PROTOCOL",
    "add_histology_data_project_paths",
    "analyze_histology_file_rois",
    "analyze_histology_data_project_rois",
    "analyze_histology_data_project_saved_rois",
    "create_histology_data_project",
    "debug_histology_data_project_roi",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_data_project_image_region_preview",
    "load_histology_file_image_preview",
    "load_histology_file_image_region_preview",
    "rename_histology_data_project_entry",
    "save_histology_data_project_rois",
]
