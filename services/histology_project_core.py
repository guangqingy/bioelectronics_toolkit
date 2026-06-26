from __future__ import annotations

from pathlib import Path
from typing import Any

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _clean_rois,
    _geojson,
    _now_iso,
    _write_json,
)
from services.histology_data_project import (
    ETS_DATA_PROJECT_KIND,
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
)
from services.histology_image_io import _read_project_image
from services.histology_project_preview import (
    _composite_region_from_image_files,
    _resolve_single_image_path,
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
    _translate_and_scale_rois,
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
    "_run_histology_data_project_roi_analysis",
    "_saved_or_external_entry_rois",
    "_update_data_project_entry_counts",
    "analyze_histology_data_project_rois",
    "analyze_histology_file_rois",
    "save_histology_data_project_rois",
]
