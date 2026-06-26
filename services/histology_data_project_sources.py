from __future__ import annotations

from pathlib import Path
from typing import Any

from services.histology_analysis import _now_iso
from services.histology_data_project_paths import (
    _case_dir_for_source,
    _case_name_for_source,
    _data_project_entry_analysis_path,
    _data_project_entry_geojson_path,
    _display_name_for_source,
    _has_project_primary_suffix,
    _primary_sources_for_project_path,
    _role_for_path,
    _safe_relative,
    _slide_prefix,
    _slide_stem_for_source,
    _source_entry_id,
)


def _associated_files_for_source(source_path: Path) -> list[dict[str, str]]:
    case_dir = _case_dir_for_source(source_path)
    if not case_dir.exists() or not case_dir.is_dir():
        return []
    slide_stem = _slide_stem_for_source(source_path)
    prefix = _slide_prefix(slide_stem)
    related: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sorted(case_dir.glob("*.vsi")):
        stem = item.stem
        stem_lower = stem.lower()
        is_direct_label = stem == slide_stem
        is_related_overview = bool(prefix) and stem.startswith(prefix) and "overview" in stem_lower
        if not is_direct_label and not is_related_overview:
            continue
        role = "overview_vsi" if "overview" in stem_lower else "label_vsi"
        resolved = item.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        related.append(
            {
                "role": role,
                "path": key,
                "name": item.name,
                "relative_path": _safe_relative(resolved, case_dir),
            }
        )
    return related


def _entry_associated_fields(source_path: Path, record: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (record or {}).get("associated_files")
    associated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("path") or item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        associated.append(item)
    for item in _associated_files_for_source(source_path):
        key = str(item.get("path") or item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        associated.append(item)
    label_vsi_path = str((record or {}).get("label_vsi_path") or "")
    overview_vsi_path = str((record or {}).get("overview_vsi_path") or "")
    for item in associated:
        role = str(item.get("role") or "")
        path = str(item.get("path") or "")
        if role == "label_vsi" and not label_vsi_path:
            label_vsi_path = path
        elif role == "overview_vsi" and not overview_vsi_path:
            overview_vsi_path = path
    return {
        "associated_files": associated,
        "associated_file_count": len(associated),
        "label_vsi_path": label_vsi_path,
        "overview_vsi_path": overview_vsi_path,
    }


def _record_source_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("image_path") or record.get("source_path") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _data_project_record_for_source(
    project_path: Path,
    source: Path,
    record: dict[str, Any] | None = None,
    now: str | None = None,
    preserve_display_name: bool = True,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    record = record if isinstance(record, dict) else {}
    timestamp = now or _now_iso()
    entry_id = _source_entry_id(source)
    default_name = _display_name_for_source(source)
    image_name = default_name
    if preserve_display_name:
        image_name = str(record.get("image_name") or record.get("display_name") or default_name).strip()
        if not image_name:
            image_name = default_name
    associated = _entry_associated_fields(source, record)
    entry = {
        "entry_id": entry_id,
        "image_name": image_name,
        "display_name": image_name,
        "source_name": source.name,
        "case_name": _case_name_for_source(source),
        "image_path": str(source),
        "source_path": str(source),
        "relative_path": source.name,
        "case_relative_path": source.name,
        "role": _role_for_path(source),
        "format": source.suffix.lower().lstrip("."),
        "roi_count": int(record.get("roi_count") or 0),
        "analysis_count": int(record.get("analysis_count") or 0),
        "analysis_path": str(_data_project_entry_analysis_path(project_path, entry_id)),
        "geojson_path": str(_data_project_entry_geojson_path(project_path, entry_id)),
        "latest_analysis_at": str(record.get("latest_analysis_at") or ""),
        "added_at": str(record.get("added_at") or timestamp),
        "updated_at": timestamp,
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
    if entry.get("record_type") == "sample":
        entry["role"] = "sample"
    return entry


def _normalize_data_project_images(
    project_path: Path,
    records: list[Any],
) -> tuple[list[dict[str, Any]], bool]:
    normalized: list[dict[str, Any]] = []
    index_by_source: dict[str, int] = {}
    preserved_by_source: dict[str, bool] = {}
    changed = False
    now = _now_iso()
    for record in records:
        if not isinstance(record, dict):
            changed = True
            continue
        source = _record_source_path(record)
        if source is None:
            changed = True
            continue
        if str(record.get("record_type") or "") == "sample":
            if not _has_project_primary_suffix(source):
                changed = True
                continue
            sample_record = dict(record)
            sample_record.setdefault("entry_id", _source_entry_id(source))
            sample_record.setdefault("role", "sample")
            sample_record.setdefault("format", source.suffix.lower().lstrip("."))
            sample_record.setdefault("image_path", str(source.resolve()))
            sample_record.setdefault("source_path", str(source.resolve()))
            entry_key = f"entry::{sample_record['entry_id']}"
            if entry_key in index_by_source:
                changed = True
                continue
            index_by_source[entry_key] = len(normalized)
            normalized.append(sample_record)
            continue
        primary_sources = _primary_sources_for_project_path(source)
        if not primary_sources:
            changed = True
            continue
        original_key = str(source.resolve())
        for primary in primary_sources:
            primary = primary.resolve()
            primary_key = str(primary)
            preserve_display_name = primary_key == original_key
            new_record = _data_project_record_for_source(
                project_path,
                primary,
                record=record if preserve_display_name else None,
                now=now,
                preserve_display_name=preserve_display_name,
            )
            if primary_key in index_by_source:
                if preserve_display_name and not preserved_by_source.get(primary_key, False):
                    normalized[index_by_source[primary_key]] = new_record
                    preserved_by_source[primary_key] = True
                changed = True
                continue
            index_by_source[primary_key] = len(normalized)
            preserved_by_source[primary_key] = preserve_display_name
            if (
                str(record.get("entry_id") or "") != str(new_record.get("entry_id") or "")
                or original_key != primary_key
                or record.get("associated_files") != new_record["associated_files"]
                or record.get("label_vsi_path", "") != new_record["label_vsi_path"]
                or record.get("overview_vsi_path", "") != new_record["overview_vsi_path"]
                or record.get("format") != new_record["format"]
            ):
                changed = True
            normalized.append(new_record)
    normalized.sort(
        key=lambda item: (
            str(item.get("image_name") or "").lower(),
            str(item.get("image_path") or "").lower(),
        )
    )
    if len(normalized) != len([r for r in records if isinstance(r, dict)]):
        changed = True
    return normalized, changed


def _iter_project_source_files(paths: list[str | Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if raw is None:
            continue
        path = Path(str(raw).strip()).expanduser()
        if not str(path):
            continue
        if not path.exists():
            warnings.append(f"Path not found: {path}")
            continue
        if path.is_file():
            candidates = [path]
            scan_root = path.parent
        else:
            candidates = [item for item in path.rglob("*") if item.is_file()]
            scan_root = path
        for item in candidates:
            try:
                rel = item.relative_to(scan_root)
            except ValueError:
                rel = item
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not _has_project_primary_suffix(item):
                continue
            for source in _primary_sources_for_project_path(item):
                key = str(source)
                if key in seen:
                    continue
                seen.add(key)
                files.append(source)
    files.sort(key=lambda item: str(item).lower())
    return files, warnings
