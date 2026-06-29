from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from services.histology_analysis import _array_to_rgb, _png_b64
from services.histology_batch_analysis import _finite_float, _parse_image_sample_and_treatment
from services.histology_data_project import (
    _entry_image_files,
    _find_data_project_entry,
    _normalize_data_project_path,
)
from services.histology_image_io import _read_project_image_region_preview
from services.histology_project_core import (
    _run_histology_data_project_roi_analysis,
    _saved_or_external_entry_rois,
)
from services.histology_project_preview import (
    _entry_preview_image_path,
    _region_composite_from_image_files,
)
from services.histology_roi_analysis import (
    _analysis_defaults,
    _entry_native_dimensions,
    _roi_crop_padding,
    _roi_native_bounds,
    _roi_shrink_percent,
    _shrink_roi,
)
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)


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

__all__ = [
    "_draw_roi_debug_overlay",
    "_roi_debug_delta",
    "_roi_debug_metrics",
    "_roi_debug_preview",
    "_roi_points_for_preview",
    "_select_roi_for_debug",
    "debug_histology_data_project_roi",
]
