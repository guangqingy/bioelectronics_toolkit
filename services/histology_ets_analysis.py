from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _clean_rois,
    _dapi_analysis_mask,
    _geojson,
    _marker_analysis,
    _mask_for_roi,
    _now_iso,
    _png_b64,
    _read_image,
    _read_json,
    _write_json,
)
from services.histology_discovery import find_histology_cases

ETS_PROTOCOL = "dataprocess-ets-histology"
ETS_PROJECT_DIR = ".dataprocess_histology"
ETS_INDEX_FILE = "project.json"


def _resolve_root(folder: str | Path) -> Path:
    raw = str(folder or "").strip()
    if not raw:
        raise FileNotFoundError("Histology folder is required")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() == ".qpproj":
        raise ValueError("Select a histology case folder, not a QuPath .qpproj file")
    if path.is_file() and path.suffix.lower() == ".ets":
        return path.parent
    if not path.is_dir():
        raise FileNotFoundError(f"Histology folder not found: {path}")
    return path


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _is_hidden_relative(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def _entry_id(case_dir: Path, image_path: Path) -> str:
    rel = _safe_relative(image_path, case_dir)
    digest = hashlib.sha1(f"{case_dir.resolve()}::{rel}".encode("utf-8")).hexdigest()
    return f"ets_{digest[:16]}"


def _case_index_path(case_dir: Path) -> Path:
    return case_dir / ETS_PROJECT_DIR / ETS_INDEX_FILE


def _entry_dir(case_dir: Path, entry_id: str) -> Path:
    return case_dir / ETS_PROJECT_DIR / "images" / str(entry_id)


def _entry_analysis_path(case_dir: Path, entry_id: str) -> Path:
    return _entry_dir(case_dir, entry_id) / "analysis.json"


def _entry_geojson_path(case_dir: Path, entry_id: str) -> Path:
    return _entry_dir(case_dir, entry_id) / "rois.geojson"


def _load_entry_analysis(case_dir: Path, entry_id: str) -> dict[str, Any]:
    path = _entry_analysis_path(case_dir, entry_id)
    if not path.is_file():
        return {"rois": [], "analyses": []}
    try:
        data = _read_json(path)
        return data if isinstance(data, dict) else {"rois": [], "analyses": []}
    except Exception:
        return {"rois": [], "analyses": []}


def _sidecar_stem_from_path(case_dir: Path, image_path: Path) -> str:
    try:
        parts = image_path.relative_to(case_dir).parts
    except ValueError:
        parts = image_path.parts
    for part in parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            return part.strip("_")
    return image_path.stem


def _display_name(case_dir: Path, image_path: Path) -> str:
    stem = _sidecar_stem_from_path(case_dir, image_path)
    try:
        rel = image_path.relative_to(case_dir)
        stack = next((part for part in rel.parts if part.lower().startswith("stack")), "")
    except ValueError:
        stack = ""
    if stack and stem != image_path.stem:
        return f"{stem} · {stack}"
    return image_path.name


def _role_for_path(image_path: Path) -> str:
    text = image_path.as_posix().lower()
    if "overview" in text:
        return "overview"
    return "image"


def _discover_case_roots(root: Path) -> list[Path]:
    cases = []
    seen: set[Path] = set()
    for item in find_histology_cases(root):
        raw = item.get("case_dir")
        if not raw:
            continue
        case_dir = Path(str(raw)).expanduser().resolve()
        if case_dir.is_dir() and case_dir not in seen:
            cases.append(case_dir)
            seen.add(case_dir)
    if cases:
        return cases
    return [root]


def _iter_ets_files(case_dir: Path) -> list[Path]:
    files: list[Path] = []
    for image_path in sorted(case_dir.rglob("*.ets")):
        if not image_path.is_file() or _is_hidden_relative(image_path, case_dir):
            continue
        files.append(image_path.resolve())
    return files


def _entry_from_path(root: Path, case_dir: Path, image_path: Path) -> dict[str, Any]:
    entry_id = _entry_id(case_dir, image_path)
    analysis = _load_entry_analysis(case_dir, entry_id)
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
    analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
    latest = analyses[-1] if analyses else {}
    return {
        "entry_id": entry_id,
        "image_name": _display_name(case_dir, image_path),
        "case_name": case_dir.name,
        "case_dir": str(case_dir),
        "image_path": str(image_path),
        "relative_path": _safe_relative(image_path, root),
        "case_relative_path": _safe_relative(image_path, case_dir),
        "role": _role_for_path(image_path),
        "exists": image_path.is_file(),
        "roi_count": len(rois),
        "analysis_count": len(analyses),
        "rois": rois,
        "latest_analysis": latest,
        "analysis_path": str(_entry_analysis_path(case_dir, entry_id)),
        "geojson_path": str(_entry_geojson_path(case_dir, entry_id)),
    }


def _discover_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case_dir in _discover_case_roots(root):
        for image_path in _iter_ets_files(case_dir):
            key = str(image_path)
            if key in seen:
                continue
            seen.add(key)
            entries.append(_entry_from_path(root, case_dir, image_path))
    entries.sort(
        key=lambda item: (
            0 if item.get("role") == "image" else 1,
            str(item.get("case_name") or "").lower(),
            str(item.get("relative_path") or "").lower(),
        )
    )
    return entries


def _index_entry(entry: dict[str, Any]) -> dict[str, Any]:
    latest = entry.get("latest_analysis") if isinstance(entry.get("latest_analysis"), dict) else {}
    return {
        "entry_id": entry.get("entry_id", ""),
        "image_name": entry.get("image_name", ""),
        "case_name": entry.get("case_name", ""),
        "case_dir": entry.get("case_dir", ""),
        "image_path": entry.get("image_path", ""),
        "relative_path": entry.get("relative_path", ""),
        "case_relative_path": entry.get("case_relative_path", ""),
        "role": entry.get("role", "image"),
        "roi_count": int(entry.get("roi_count") or 0),
        "analysis_count": int(entry.get("analysis_count") or 0),
        "analysis_path": entry.get("analysis_path", ""),
        "geojson_path": entry.get("geojson_path", ""),
        "latest_analysis_at": latest.get("created_at", ""),
    }


def _write_project_index(root: Path, entries: list[dict[str, Any]]) -> Path:
    index_path = _case_index_path(root)
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": ETS_PROTOCOL,
        "kind": "ets_histology_project",
        "project_root": str(root),
        "updated_at": _now_iso(),
        "entry_count": len(entries),
        "images": [_index_entry(entry) for entry in entries],
    }
    _write_json(index_path, payload)
    return index_path


def _find_entry(root: Path, entry_id: str) -> dict[str, Any]:
    for entry in _discover_entries(root):
        if str(entry.get("entry_id")) == str(entry_id):
            return entry
    raise ValueError(f"ETS image entry not found: {entry_id}")


def load_ets_project(folder: str | Path) -> dict[str, Any]:
    root = _resolve_root(folder)
    entries = _discover_entries(root)
    index_path = _write_project_index(root, entries)
    return {
        "ok": True,
        "protocol": ETS_PROTOCOL,
        "project_root": str(root),
        "project_path": str(index_path),
        "index_path": str(index_path),
        "entry_count": len(entries),
        "entries": entries,
    }


def load_ets_image_preview(
    folder: str | Path,
    entry_id: str,
    max_side: int = 1600,
) -> dict[str, Any]:
    root = _resolve_root(folder)
    entry = _find_entry(root, str(entry_id))
    image_path = entry.get("image_path", "")
    if not image_path:
        raise ValueError("Selected ETS entry has no readable image path")
    arr, backend, warnings = _read_image(image_path, max_side=max(256, min(int(max_side), 2400)))
    analysis = _load_entry_analysis(Path(str(entry["case_dir"])), str(entry_id))
    h, w = arr.shape[:2]
    return {
        **entry,
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


def save_ets_rois(
    folder: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    append_analysis: bool = True,
) -> dict[str, Any]:
    root = _resolve_root(folder)
    entry = _find_entry(root, str(entry_id))
    case_dir = Path(str(entry["case_dir"]))
    clean_rois = _clean_rois(rois)
    existing = _load_entry_analysis(case_dir, str(entry_id))
    analyses = existing.get("analyses") if isinstance(existing.get("analyses"), list) else []
    if analysis:
        analysis = dict(analysis)
        analysis.setdefault("created_at", _now_iso())
        analyses = [*analyses, analysis] if append_analysis else [analysis]
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": ETS_PROTOCOL,
        "project_root": str(root),
        "case_dir": str(case_dir),
        "case_name": entry.get("case_name", ""),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "image_path": entry.get("image_path", ""),
        "relative_path": entry.get("relative_path", ""),
        "case_relative_path": entry.get("case_relative_path", ""),
        "updated_at": _now_iso(),
        "rois": clean_rois,
        "analyses": analyses,
    }
    analysis_path = _entry_analysis_path(case_dir, str(entry_id))
    _write_json(analysis_path, payload)
    latest_measurements = {}
    if analyses and isinstance(analyses[-1], dict):
        latest_measurements = {str(item.get("roi_id")): item for item in analyses[-1].get("results", [])}
    geojson_path = _entry_geojson_path(case_dir, str(entry_id))
    _write_json(geojson_path, _geojson(clean_rois, latest_measurements))
    entries = _discover_entries(root)
    index_path = _write_project_index(root, entries)
    return {
        "protocol": ETS_PROTOCOL,
        "project_root": str(root),
        "project_path": str(index_path),
        "index_path": str(index_path),
        "case_dir": str(case_dir),
        "entry_id": str(entry_id),
        "roi_count": len(clean_rois),
        "analysis_count": len(analyses),
        "analysis_path": str(analysis_path),
        "geojson_path": str(geojson_path),
        "summary_path": str(index_path),
        "rois": clean_rois,
        "latest_analysis": analyses[-1] if analyses else {},
    }


def _analysis_defaults(parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
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


def analyze_ets_rois(
    folder: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _resolve_root(folder)
    entry = _find_entry(root, str(entry_id))
    image_path = str(entry.get("image_path") or "")
    if not image_path:
        raise ValueError("Selected ETS entry has no readable image path")
    arr, backend, warnings = _read_image(image_path, max_side=1600)
    h, w = arr.shape[:2]
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = _analysis_defaults(parameters)
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
        "protocol": ETS_PROTOCOL,
        "image_name": entry.get("image_name", ""),
        "image_path": image_path,
        "case_name": entry.get("case_name", ""),
        "case_dir": entry.get("case_dir", ""),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    saved = save_ets_rois(root, str(entry_id), clean_rois, analysis=analysis)
    return {
        **saved,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


__all__ = [
    "ETS_INDEX_FILE",
    "ETS_PROJECT_DIR",
    "ETS_PROTOCOL",
    "analyze_ets_rois",
    "load_ets_image_preview",
    "load_ets_project",
    "save_ets_rois",
]
