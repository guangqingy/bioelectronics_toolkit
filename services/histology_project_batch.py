from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from services.histology_analysis import _clean_rois, _now_iso
from services.histology_batch_analysis import (
    _aggregate_roi_rows_by_entry,
    _apply_batch_grouping,
    _apply_marker_inclusion,
    _apply_normalization_to_rows,
    _batch_anova,
    _batch_group_summary,
    _boolish,
    _flatten_batch_row,
    _new_project_batch_dir,
    _normalize_batch_rows,
    _params_for_roi_parameter_override,
    _write_batch_outputs,
)
from services.histology_data_project import (
    _data_project_cache_dir,
    _data_project_dir,
    _load_data_project_entry_analysis,
    _load_external_entry_rois,
    _normalize_data_project_path,
    load_histology_data_project,
)
from services.histology_project_core import (
    _run_histology_data_project_roi_analysis,
    analyze_histology_data_project_rois,
)
from services.histology_roi_analysis import _analysis_defaults
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)


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

__all__ = [
    "analyze_histology_data_project_saved_rois",
]
