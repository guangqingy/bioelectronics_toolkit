from __future__ import annotations

from pathlib import Path
from typing import Any

from services.histology_analysis import _png_b64
from services.histology_data_project import (
    _case_name_for_source,
    _entry_image_files,
    _entry_warnings,
    _find_data_project_entry,
    _has_project_image_suffix,
    _load_data_project_entry_analysis,
    _normalize_data_project_path,
    _source_entry_id,
)
from services.histology_image_io import (
    _read_project_image_preview,
    _read_project_image_region_preview,
)
from services.histology_project_preview_render import (
    _channel_rgb_slot,
    _clean_preview_warnings,
    _is_brightfield_label,
    _is_rgb_plane,
    _ordered_preview_channel_names,
    _preview_composite_from_image_files,
    _pseudo_color_channel,
    _region_composite_from_image_files,
    _rgb_channels_are_monochrome,
)


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

__all__ = [
    "_entry_preview_image_path",
    "_resolve_single_image_path",
    "load_histology_data_project_image_preview",
    "load_histology_data_project_image_region_preview",
    "load_histology_file_image_preview",
    "load_histology_file_image_region_preview",
]
