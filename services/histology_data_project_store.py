from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _clean_rois,
    _now_iso,
    _read_json,
    _write_json,
)
from services.histology_data_project_paths import (
    ETS_DATA_PROJECT_KIND,
    ETS_PROJECT_DIR,
    _case_name_for_source,
    _data_project_cache_dir,
    _data_project_cache_layout,
    _data_project_dir,
    _data_project_entry_analysis_path,
    _data_project_entry_geojson_path,
    _display_name_for_source,
    _ensure_data_project_dirs,
    _normalize_data_project_path,
    _role_for_path,
    _source_entry_id,
)
from services.histology_data_project_sources import (
    _data_project_record_for_source,
    _entry_associated_fields,
    _iter_project_source_files,
    _normalize_data_project_images,
)
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)


def _load_data_project_payload(project_path: Path) -> dict[str, Any]:
    if not project_path.is_file():
        raise FileNotFoundError(f"Histology project not found: {project_path}")
    try:
        data = _read_json(project_path)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Histology project is not valid UTF-8 JSON: {project_path}. "
            "Load histology_project.dphistology or its containing folder, not a TIFF/VSI/ETS image file."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Histology project is not valid JSON: {project_path}. "
            "Load histology_project.dphistology or its containing folder."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid histology project file: {project_path}")
    images = data.get("images")
    if not isinstance(images, list):
        data["images"] = []
    return data


def _write_data_project_payload(project_path: Path, data: dict[str, Any]) -> None:
    data = dict(data)
    data["version"] = ANALYSIS_VERSION
    data["protocol"] = str(data.get("protocol") or TIFF_PROJECT_PROTOCOL)
    data["kind"] = ETS_DATA_PROJECT_KIND
    data["project_path"] = str(project_path)
    data["data_dir"] = str(_data_project_dir(project_path))
    data["cache_dir"] = str(_data_project_cache_dir(project_path))
    data["cache_layout"] = _data_project_cache_layout(project_path)
    data["updated_at"] = _now_iso()
    images = data.get("images")
    data["entry_count"] = len(images) if isinstance(images, list) else 0
    _write_json(project_path, data)
    _ensure_data_project_dirs(project_path)


def _load_data_project_entry_analysis(project_path: Path, entry_id: str) -> dict[str, Any]:
    path = _data_project_entry_analysis_path(project_path, entry_id)
    if not path.is_file():
        return {"rois": [], "analyses": []}
    try:
        data = _read_json(path)
        return data if isinstance(data, dict) else {"rois": [], "analyses": []}
    except Exception:
        return {"rois": [], "analyses": []}


def _external_rois_candidates(project_path: Path, entry: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for key in ("rois_path", "geojson_path"):
        raw = str(entry.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
    sample_id = str(entry.get("sample_id") or entry.get("image_name") or entry.get("case_name") or "").strip()
    analysis_folder = str(entry.get("analysis_folder") or "").strip()
    if analysis_folder and sample_id:
        candidates.append(Path(analysis_folder).expanduser() / f"{sample_id}_rois.json")
    if sample_id:
        candidates.append(project_path.parent / "analysis" / sample_id / f"{sample_id}_rois.json")
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _load_external_entry_rois(project_path: Path, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    for candidate in _external_rois_candidates(project_path, entry):
        if not candidate.is_file():
            continue
        try:
            data = _read_json(candidate)
        except Exception:
            continue
        raw_rois = data.get("rois") if isinstance(data, dict) else data
        clean_rois = _clean_rois(raw_rois)
        if clean_rois:
            return clean_rois, str(candidate)
    return [], ""


def _data_project_entry_from_record(project_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(record.get("image_path") or record.get("source_path") or "")).expanduser()
    entry_id = str(record.get("entry_id") or (_source_entry_id(source) if str(source) else ""))
    analysis = _load_data_project_entry_analysis(project_path, entry_id) if entry_id else {}
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
    external_rois_path = ""
    if not rois:
        rois, external_rois_path = _load_external_entry_rois(project_path, record)
    analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
    latest = analyses[-1] if analyses else {}
    image_name = str(
        record.get("image_name")
        or record.get("display_name")
        or (_display_name_for_source(source) if str(source) else entry_id)
    )
    associated = _entry_associated_fields(source, record) if str(source) else {
        "associated_files": [],
        "associated_file_count": 0,
        "label_vsi_path": "",
        "overview_vsi_path": "",
    }
    entry = {
        "entry_id": entry_id,
        "image_name": image_name,
        "display_name": image_name,
        "source_name": source.name if str(source) else "",
        "case_name": str(record.get("case_name") or (_case_name_for_source(source) if str(source) else "")),
        "image_path": str(source) if str(source) else "",
        "source_path": str(source) if str(source) else "",
        "relative_path": str(record.get("relative_path") or source.name),
        "case_relative_path": str(record.get("case_relative_path") or source.name),
        "role": str(record.get("role") or _role_for_path(source)),
        "exists": source.is_file() if str(source) else False,
        "format": str(record.get("format") or source.suffix.lower().lstrip(".")),
        "roi_count": len(rois),
        "analysis_count": len(analyses),
        "rois": rois,
        "latest_analysis": latest,
        "analysis_path": str(_data_project_entry_analysis_path(project_path, entry_id)),
        "geojson_path": str(_data_project_entry_geojson_path(project_path, entry_id)),
        "external_rois_path": external_rois_path,
        "latest_analysis_at": latest.get("created_at", "") if isinstance(latest, dict) else "",
        "added_at": str(record.get("added_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        **associated,
    }
    for key in (
        "record_type",
        "sample_id",
        "image_files",
        "image_records",
        "raw_olympus_reference",
        "case_dir",
        "physical_rename_dir",
        "converted_from_ets",
        "converted_tiff_paths",
        "conversion_roles",
        "ets_conversion_count",
        "analysis_folder",
        "manifest_path",
        "parameters_path",
        "roi_measurements_path",
        "rois_path",
        "qc_overlay_path",
        "warnings",
    ):
        if key in record:
            entry[key] = record[key]
    entry["warnings"] = _entry_warnings(record)
    return entry


def create_histology_data_project(project_path: str | Path, name: str = "") -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    if path.is_file():
        return load_histology_data_project(path)
    now = _now_iso()
    project_name = str(name or "").strip()
    if not project_name:
        project_name = path.parent.parent.name if path.parent.name == ETS_PROJECT_DIR else path.stem
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_name": project_name,
        "project_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "created_at": now,
        "updated_at": now,
        "entry_count": 0,
        "images": [],
    }
    _write_json(path, payload)
    _ensure_data_project_dirs(path)
    return load_histology_data_project(path)


def load_histology_data_project(project_path: str | Path) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    data = _load_data_project_payload(path)
    _ensure_data_project_dirs(path)
    expected_cache_dir = str(_data_project_cache_dir(path))
    if data.get("cache_dir") != expected_cache_dir or not isinstance(data.get("cache_layout"), dict):
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
    images = data.get("images", [])
    if isinstance(images, list):
        images, migrated = _normalize_data_project_images(path, images)
    else:
        images, migrated = [], True
    if migrated:
        data["images"] = images
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
    entries = [
        _data_project_entry_from_record(path, record)
        for record in data.get("images", [])
        if isinstance(record, dict)
    ]
    entries.sort(
        key=lambda item: (
            0 if item.get("role") == "image" else 1,
            str(item.get("image_name") or "").lower(),
            str(item.get("image_path") or "").lower(),
        )
    )
    return {
        "ok": True,
        "protocol": data.get("protocol") or TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_name": data.get("project_name") or path.stem,
        "project_root": str(path.parent.parent if path.parent.name == ETS_PROJECT_DIR else path.parent),
        "project_path": str(path),
        "index_path": str(path),
        "exported_dir": str(data.get("exported_dir") or ""),
        "raw_dir": str(data.get("raw_dir") or ""),
        "analysis_dir": str(data.get("analysis_dir") or ""),
        "raw_olympus_index_path": str(data.get("raw_olympus_index_path") or ""),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "entry_count": len(entries),
        "entries": entries,
        "warnings": data.get("warnings") if isinstance(data.get("warnings"), list) else [],
    }


def add_histology_data_project_paths(
    project_path: str | Path,
    paths: list[str | Path],
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    if not path.is_file():
        create_histology_data_project(path)
    data = _load_data_project_payload(path)
    images, migrated = _normalize_data_project_images(path, data.get("images", []))
    if migrated:
        data["images"] = images
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
        images = [record for record in data.get("images", []) if isinstance(record, dict)]
    existing_by_id = {str(record.get("entry_id") or ""): record for record in images}
    existing_by_source = {
        str(Path(str(record.get("image_path") or record.get("source_path") or "")).expanduser().resolve()): record
        for record in images
        if str(record.get("image_path") or record.get("source_path") or "").strip()
    }
    files, warnings = _iter_project_source_files(paths)
    added = 0
    skipped = 0
    now = _now_iso()
    for source in files:
        source_key = str(source.resolve())
        entry_id = _source_entry_id(source)
        if entry_id in existing_by_id or source_key in existing_by_source:
            skipped += 1
            continue
        entry = _data_project_record_for_source(path, source, now=now)
        images.append(entry)
        existing_by_id[entry_id] = entry
        existing_by_source[source_key] = entry
        added += 1
    data["images"] = images
    _write_data_project_payload(path, data)
    loaded = load_histology_data_project(path)
    return {
        **loaded,
        "added_count": added,
        "skipped_count": skipped,
        "warnings": warnings,
    }


def _find_data_project_entry(project_path: Path, entry_id: str) -> dict[str, Any]:
    for entry in load_histology_data_project(project_path).get("entries", []):
        if str(entry.get("entry_id")) == str(entry_id):
            return entry
    raise ValueError(f"Histology project entry not found: {entry_id}")


def _entry_image_files(entry: dict[str, Any]) -> dict[str, str]:
    raw = entry.get("image_files")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for channel, path in raw.items():
        text = str(path or "").strip()
        if text:
            out[str(channel)] = text
    return out


def _legacy_multiz_brightfield_warnings(entry: dict[str, Any]) -> list[str]:
    image_files = _entry_image_files(entry)
    channels = {str(channel).strip().lower() for channel in image_files}
    if channels != {"brightfield"}:
        return []
    converted = entry.get("converted_from_ets")
    if not isinstance(converted, list):
        return []
    for item in converted:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        try:
            z_count = int(item.get("z_plane_count") or 0)
        except Exception:
            z_count = 0
        if role == "brightfield" and z_count > 1:
            selected = item.get("selected_z")
            selected_text = f" selected z={selected}" if selected is not None else ""
            return [
                "Legacy ETS conversion collapsed a multi-channel ETS into one file labeled Brightfield;"
                f"{selected_text} from the source. Re-scan/recreate this histology project with ETS conversion"
                " enabled so Hoechst/FITC/Cy5 are exported as separate channels."
            ]
    return []


def _entry_warnings(entry: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    raw = entry.get("warnings")
    if isinstance(raw, list):
        warnings.extend(str(item).strip() for item in raw if str(item or "").strip())
    warnings.extend(_legacy_multiz_brightfield_warnings(entry))
    seen: set[str] = set()
    out: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        out.append(warning)
    return out
