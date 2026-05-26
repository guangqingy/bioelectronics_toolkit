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
ETS_DATA_PROJECT_KIND = "dataprocess_histology_project"
ETS_DATA_PROJECT_FILE = "histology_project.dphistology"
PROJECT_IMAGE_SUFFIXES = (".ets", ".tif", ".tiff", ".vsi")
PROJECT_PRIMARY_SUFFIXES = (".ets",)
PROJECT_RELATED_SUFFIXES = (".vsi",)


def _has_project_image_suffix(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".ome.tif", ".ome.tiff")) or path.suffix.lower() in PROJECT_IMAGE_SUFFIXES


def _has_project_primary_suffix(path: Path) -> bool:
    return path.suffix.lower() in PROJECT_PRIMARY_SUFFIXES


def _has_project_related_suffix(path: Path) -> bool:
    return path.suffix.lower() in PROJECT_RELATED_SUFFIXES


def _friendly_image_read_error(path: Path, exc: Exception) -> str:
    message = str(exc)
    if path.suffix.lower() == ".ets" and ("SIS" in message or "not a TIFF" in message):
        return (
            "This Olympus/SIS .ets file is indexed in the DataProcess project, but this "
            "Python environment cannot decode it directly. Install Bio-Formats support "
            "or add a converted TIFF/OME-TIFF copy for analysis. The project entry and "
            "its display name are still saved without modifying the original ETS folder."
        )
    return message or f"Could not read image: {path}"


def _read_project_image(path: str | Path, max_side: int = 1600):
    image_path = Path(str(path)).expanduser()
    try:
        return _read_image(image_path, max_side=max(256, min(int(max_side), 2400)))
    except RuntimeError as exc:
        raise ValueError(_friendly_image_read_error(image_path, exc)) from exc


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


def _normalize_data_project_path(project_path: str | Path) -> Path:
    raw = str(project_path or "").strip()
    if not raw:
        raise FileNotFoundError("Histology project path is required")
    path = Path(raw).expanduser()
    if path.exists() and path.is_dir():
        if path.name == ETS_PROJECT_DIR and (path / ETS_INDEX_FILE).exists():
            return (path / ETS_INDEX_FILE).resolve()
        default_project = path / ETS_DATA_PROJECT_FILE
        if default_project.exists():
            return default_project.resolve()
        legacy_project = path / ETS_PROJECT_DIR / ETS_INDEX_FILE
        if legacy_project.exists():
            return legacy_project.resolve()
        return default_project.resolve()
    if path.name == ETS_INDEX_FILE and path.parent.name == ETS_PROJECT_DIR:
        return path.resolve()
    if path.suffix.lower() in {".dphistology", ".json"}:
        return path.resolve()
    if not path.suffix:
        return (path / ETS_DATA_PROJECT_FILE).resolve()
    return path.resolve()


def _data_project_dir(project_path: Path) -> Path:
    if project_path.name == ETS_INDEX_FILE and project_path.parent.name == ETS_PROJECT_DIR:
        return project_path.parent
    return project_path.with_name(f"{project_path.stem}.dataprocess_histology")


def _data_project_cache_dir(project_path: Path) -> Path:
    return _data_project_dir(project_path) / "cache"


def _data_project_cache_layout(project_path: Path) -> dict[str, str]:
    cache_dir = _data_project_cache_dir(project_path)
    return {
        "root": str(cache_dir),
        "previews": str(cache_dir / "previews"),
        "converted": str(cache_dir / "converted"),
        "tmp": str(cache_dir / "tmp"),
        "metadata": str(cache_dir / "metadata"),
    }


def _ensure_data_project_dirs(project_path: Path) -> None:
    _data_project_dir(project_path).mkdir(parents=True, exist_ok=True)
    for path in _data_project_cache_layout(project_path).values():
        Path(path).mkdir(parents=True, exist_ok=True)


def _data_project_entry_dir(project_path: Path, entry_id: str) -> Path:
    return _data_project_dir(project_path) / "images" / str(entry_id)


def _data_project_entry_analysis_path(project_path: Path, entry_id: str) -> Path:
    return _data_project_entry_dir(project_path, entry_id) / "analysis.json"


def _data_project_entry_geojson_path(project_path: Path, entry_id: str) -> Path:
    return _data_project_entry_dir(project_path, entry_id) / "rois.geojson"


def _source_entry_id(source_path: Path) -> str:
    digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8")).hexdigest()
    return f"img_{digest[:16]}"


def _case_name_for_source(source_path: Path) -> str:
    parts = list(source_path.parts)
    for idx, part in enumerate(parts):
        if part.startswith("_") and part.endswith("_") and idx > 0:
            return parts[idx - 1]
    if source_path.parent.name.lower().startswith("stack") and source_path.parent.parent.name:
        return source_path.parent.parent.name.strip("_") or source_path.parent.parent.name
    return source_path.parent.name


def _case_dir_for_source(source_path: Path) -> Path:
    parts = list(source_path.parts)
    for idx, part in enumerate(parts):
        if part.startswith("_") and part.endswith("_") and idx > 0:
            return Path(*parts[:idx])
    if source_path.parent.name.lower().startswith("stack") and source_path.parent.parent.parent.exists():
        return source_path.parent.parent.parent
    return source_path.parent


def _slide_stem_for_source(source_path: Path) -> str:
    case_dir = _case_dir_for_source(source_path)
    return _sidecar_stem_from_path(case_dir, source_path)


def _slide_prefix(stem: str) -> str:
    parts = stem.split("_")
    if len(parts) > 1 and (parts[-1].isdigit() or parts[-1].lower() in {"overview", "label"}):
        return "_".join(parts[:-1])
    return stem


def _display_name_for_source(source_path: Path) -> str:
    case_name = _case_name_for_source(source_path)
    slide = ""
    stack = ""
    for part in source_path.parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            slide = part.strip("_")
        elif part.lower().startswith("stack"):
            stack = part
    if case_name and slide and stack:
        return f"{case_name} · {slide} · {stack}"
    if case_name and source_path.name:
        return f"{case_name} · {source_path.name}"
    return source_path.name


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


def _load_data_project_payload(project_path: Path) -> dict[str, Any]:
    if not project_path.is_file():
        raise FileNotFoundError(f"Histology project not found: {project_path}")
    data = _read_json(project_path)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid histology project file: {project_path}")
    images = data.get("images")
    if not isinstance(images, list):
        data["images"] = []
    return data


def _write_data_project_payload(project_path: Path, data: dict[str, Any]) -> None:
    data = dict(data)
    data["version"] = ANALYSIS_VERSION
    data["protocol"] = ETS_PROTOCOL
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


def _data_project_entry_from_record(project_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(record.get("image_path") or record.get("source_path") or "")).expanduser()
    entry_id = str(record.get("entry_id") or (_source_entry_id(source) if str(source) else ""))
    analysis = _load_data_project_entry_analysis(project_path, entry_id) if entry_id else {}
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
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
    return {
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
        "latest_analysis_at": latest.get("created_at", "") if isinstance(latest, dict) else "",
        "added_at": str(record.get("added_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        **associated,
    }


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
        "protocol": ETS_PROTOCOL,
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
    migrated_associations = False
    if isinstance(images, list):
        for record in images:
            if not isinstance(record, dict):
                continue
            raw_source = str(record.get("image_path") or record.get("source_path") or "").strip()
            if not raw_source:
                continue
            source = Path(raw_source).expanduser()
            associated = _entry_associated_fields(source, record)
            if (
                record.get("associated_files") != associated["associated_files"]
                or record.get("label_vsi_path", "") != associated["label_vsi_path"]
                or record.get("overview_vsi_path", "") != associated["overview_vsi_path"]
            ):
                record.update(associated)
                migrated_associations = True
    if migrated_associations:
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
        "protocol": ETS_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_name": data.get("project_name") or path.stem,
        "project_root": str(path.parent.parent if path.parent.name == ETS_PROJECT_DIR else path.parent),
        "project_path": str(path),
        "index_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "entry_count": len(entries),
        "entries": entries,
    }


def _ets_files_for_related_path(path: Path) -> list[Path]:
    case_dir = _case_dir_for_source(path)
    if not case_dir.exists() or not case_dir.is_dir():
        return []
    prefix = _slide_prefix(path.stem)
    candidates: list[Path] = []
    for item in sorted(case_dir.rglob("*.ets")):
        if not item.is_file():
            continue
        text = item.as_posix().lower()
        if "overview" in text:
            continue
        slide_stem = _slide_stem_for_source(item)
        if path.stem == slide_stem or (prefix and slide_stem.startswith(prefix)):
            candidates.append(item.resolve())
    return candidates


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
        candidates: list[Path]
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
            source_candidates: list[Path] = []
            if _has_project_primary_suffix(item):
                if path.is_dir() and "overview" in item.as_posix().lower():
                    continue
                source_candidates = [item.resolve()]
            elif path.is_file() and _has_project_related_suffix(item):
                source_candidates = _ets_files_for_related_path(item)
                if not source_candidates:
                    warnings.append(f"No matching ETS file found for related VSI: {item}")
            for source in source_candidates:
                key = str(source)
                if key in seen:
                    continue
                seen.add(key)
                files.append(source)
    files.sort(key=lambda item: str(item).lower())
    return files, warnings


def add_histology_data_project_paths(
    project_path: str | Path,
    paths: list[str | Path],
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    if not path.is_file():
        create_histology_data_project(path)
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
        associated = _entry_associated_fields(source)
        entry = {
            "entry_id": entry_id,
            "image_name": _display_name_for_source(source),
            "display_name": _display_name_for_source(source),
            "source_name": source.name,
            "case_name": _case_name_for_source(source),
            "image_path": str(source),
            "source_path": str(source),
            "relative_path": source.name,
            "case_relative_path": source.name,
            "role": _role_for_path(source),
            "format": source.suffix.lower().lstrip("."),
            "roi_count": 0,
            "analysis_count": 0,
            "analysis_path": str(_data_project_entry_analysis_path(path, entry_id)),
            "geojson_path": str(_data_project_entry_geojson_path(path, entry_id)),
            "latest_analysis_at": "",
            "added_at": now,
            "updated_at": now,
            **associated,
        }
        images.append(entry)
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


def rename_histology_data_project_entry(
    project_path: str | Path,
    entry_id: str,
    display_name: str,
) -> dict[str, Any]:
    name = str(display_name or "").strip()
    if not name:
        raise ValueError("Enter a display name")
    path = _normalize_data_project_path(project_path)
    data = _load_data_project_payload(path)
    images = [record for record in data.get("images", []) if isinstance(record, dict)]
    renamed: dict[str, Any] | None = None
    for record in images:
        if str(record.get("entry_id")) != str(entry_id):
            continue
        record["image_name"] = name
        record["display_name"] = name
        record["updated_at"] = _now_iso()
        renamed = record
        break
    if renamed is None:
        raise ValueError(f"Histology project entry not found: {entry_id}")
    data["images"] = images
    _write_data_project_payload(path, data)
    loaded = load_histology_data_project(path)
    return {
        **loaded,
        "renamed_entry": _data_project_entry_from_record(path, renamed),
    }


def _find_data_project_entry(project_path: Path, entry_id: str) -> dict[str, Any]:
    for entry in load_histology_data_project(project_path).get("entries", []):
        if str(entry.get("entry_id")) == str(entry_id):
            return entry
    raise ValueError(f"Histology project entry not found: {entry_id}")


def load_histology_data_project_image_preview(
    project_path: str | Path,
    entry_id: str,
    max_side: int = 1600,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    image_path = entry.get("image_path", "")
    if not image_path:
        raise ValueError("Selected project entry has no image path")
    arr, backend, warnings = _read_project_image(
        image_path,
        max_side=max(256, min(int(max_side), 2400)),
    )
    analysis = _load_data_project_entry_analysis(path, str(entry_id))
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
        "protocol": ETS_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "display_name": entry.get("display_name", entry.get("image_name", "")),
        "image_path": entry.get("image_path", ""),
        "source_path": entry.get("source_path", entry.get("image_path", "")),
        "case_name": entry.get("case_name", ""),
        "associated_files": entry.get("associated_files", []),
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
        "protocol": ETS_PROTOCOL,
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
    arr, backend, warnings = _read_project_image(
        image_path,
        max_side=max(256, min(int(max_side), 2400)),
    )
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
    return h, w, results


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
    arr, backend, warnings = _read_project_image(image_path, max_side=1600)
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = _analysis_defaults(parameters)
    h, w, results = _analyze_marker_rois(arr, clean_rois, params)

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


def analyze_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    image_path = str(entry.get("image_path") or "")
    if not image_path:
        raise ValueError("Selected project entry has no image path")
    arr, backend, warnings = _read_project_image(image_path, max_side=1600)
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = _analysis_defaults(parameters)
    h, w, results = _analyze_marker_rois(arr, clean_rois, params)

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
        "label_vsi_path": entry.get("label_vsi_path", ""),
        "overview_vsi_path": entry.get("overview_vsi_path", ""),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
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


def _resolve_single_image_path(image_path: str | Path) -> Path:
    raw = str(image_path or "").strip()
    if not raw:
        raise FileNotFoundError("Histology image path is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Histology image not found: {path}")
    if not _has_project_image_suffix(path):
        raise ValueError("Select an ETS, TIFF, OME-TIFF, or VSI image file")
    return path


def load_histology_file_image_preview(
    image_path: str | Path,
    max_side: int = 1600,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    arr, backend, warnings = _read_project_image(
        path,
        max_side=max(256, min(int(max_side), 2400)),
    )
    h, w = arr.shape[:2]
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
        "preview_width": int(w),
        "preview_height": int(h),
        "img": _png_b64(arr),
        "rois": [],
        "analyses": [],
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
    h, w, results = _analyze_marker_rois(arr, clean_rois, params)
    analysis = {
        "created_at": _now_iso(),
        "protocol": ETS_PROTOCOL,
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
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    return {
        "protocol": ETS_PROTOCOL,
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
    "analyze_ets_rois",
    "analyze_histology_file_rois",
    "analyze_histology_data_project_rois",
    "create_histology_data_project",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_file_image_preview",
    "load_ets_image_preview",
    "load_ets_project",
    "rename_histology_data_project_entry",
    "save_histology_data_project_rois",
    "save_ets_rois",
]
