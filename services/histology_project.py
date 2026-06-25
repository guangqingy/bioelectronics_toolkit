from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _array_to_rgb,
    _clean_rois,
    _geojson,
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
    _read_project_image,
    _read_project_image_region_preview,
)
from services.histology_project_preview import (
    _composite_region_from_image_files,
    _entry_preview_image_path,
    _region_composite_from_image_files,
    _resolve_single_image_path,
    load_histology_data_project_image_preview,
    load_histology_data_project_image_region_preview,
    load_histology_file_image_preview,
    load_histology_file_image_region_preview,
)
from services.histology_roi_analysis import (
    _analysis_defaults,
    _analysis_image_files,
    _analysis_max_region_pixels,
    _analysis_params_for_region_scale,
    _analysis_rois_for_params,
    _analyze_marker_rois,
    _apply_physical_calibration_to_results,
    _entry_native_dimensions,
    _entry_pixel_calibration,
    _read_data_project_entry_image,
    _rescale_result_counts_for_native_pixels,
    _roi_crop_padding,
    _roi_native_bounds,
    _roi_shrink_percent,
    _shrink_roi,
    _translate_and_scale_rois,
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
    _find_data_project_entry,
    _infer_tiff_pixel_calibration,
    _load_data_project_entry_analysis,
    _load_data_project_payload,
    _load_external_entry_rois,
    _normalize_data_project_path,
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
