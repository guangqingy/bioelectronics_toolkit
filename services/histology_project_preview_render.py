from __future__ import annotations

import re

import numpy as np
from PIL import Image

from services.histology_analysis import _array_to_rgb
from services.histology_image_io import (
    _as_2d_channel,
    _read_project_image_preview,
    _read_project_image_region_for_analysis,
    _read_project_image_region_preview,
)
from services.histology_tiff_project import load_image_for_analysis


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

__all__ = [
    "_channel_intensity_plane",
    "_channel_rgb_slot",
    "_clean_preview_warnings",
    "_composite_from_image_files",
    "_composite_region_from_image_files",
    "_has_fluorescence_channels",
    "_is_brightfield_label",
    "_is_rgb_plane",
    "_ordered_preview_channel_names",
    "_planes_to_preview_rgb",
    "_preview_composite_from_image_files",
    "_preview_display_plane",
    "_preview_plane_shape",
    "_preview_scale_to_uint8",
    "_pseudo_color_channel",
    "_region_composite_from_image_files",
    "_resize_preview_plane",
    "_rgb_channels_are_monochrome",
    "_select_preview_image_files",
    "_single_channel_preview_warnings",
]
