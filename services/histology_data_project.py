from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _clean_rois,
    _now_iso,
    _read_json,
    _write_json,
)
from services.histology_common import sanitize_name
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)
from services.histology_tiff_project import TIFF_SUFFIXES

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None

ETS_PROTOCOL = "dataprocess-ets-histology"
ETS_PROJECT_DIR = ".dataprocess_histology"
ETS_INDEX_FILE = "project.json"
ETS_DATA_PROJECT_KIND = "dataprocess_histology_project"
ETS_DATA_PROJECT_FILE = "histology_project.dphistology"
PROJECT_IMAGE_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg")
PROJECT_PRIMARY_SUFFIXES = PROJECT_IMAGE_SUFFIXES
PROJECT_CONFIG_SUFFIXES = {".dphistology", ".json"}


def _has_project_image_suffix(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".ome.tif", ".ome.tiff")) or path.suffix.lower() in PROJECT_IMAGE_SUFFIXES


def _has_project_primary_suffix(path: Path) -> bool:
    return path.suffix.lower() in PROJECT_PRIMARY_SUFFIXES


def _rational_to_float(value: Any) -> float | None:
    try:
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                return None
            den = float(value[1])
            if abs(den) < 1e-12:
                return None
            return float(value[0]) / den
        return float(value)
    except Exception:
        return None


def _unit_to_um_scale(unit: str | None) -> float | None:
    text = str(unit or "").strip().lower()
    if text in {"um", "micron", "microns", "micrometer", "micrometers"}:
        return 1.0
    if text in {"nm", "nanometer", "nanometers"}:
        return 1e-3
    if text in {"mm", "millimeter", "millimeters"}:
        return 1e3
    if text in {"cm", "centimeter", "centimeters"}:
        return 1e4
    if text in {"m", "meter", "meters"}:
        return 1e6
    if text in {"in", "inch", "inches"}:
        return 25400.0
    return None


def _positive_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if np.isfinite(out) and out > 0:
        return out
    return None


def _infer_tiff_pixel_calibration(path: str | Path) -> dict[str, Any]:
    image_path = Path(str(path)).expanduser()
    if image_path.suffix.lower() not in TIFF_SUFFIXES or tifffile is None:
        return {}
    try:
        with tifffile.TiffFile(str(image_path)) as tf:
            ome_xml = tf.ome_metadata or ""
            if ome_xml:
                x_val = re.search(r'PhysicalSizeX="([0-9eE+\-.]+)"', ome_xml)
                y_val = re.search(r'PhysicalSizeY="([0-9eE+\-.]+)"', ome_xml)
                x_unit = re.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                y_unit = re.search(r'PhysicalSizeYUnit="([^"]+)"', ome_xml)
                x_scale = _unit_to_um_scale(x_unit.group(1) if x_unit else "um")
                y_scale = _unit_to_um_scale(y_unit.group(1) if y_unit else "um")
                px_w = _positive_float(float(x_val.group(1)) * x_scale) if x_val and x_scale else None
                px_h = _positive_float(float(y_val.group(1)) * y_scale) if y_val and y_scale else None
                if px_w is not None:
                    px_h = px_h or px_w
                    return {
                        "has_physical_scale": True,
                        "pixel_width_um": float(px_w),
                        "pixel_height_um": float(px_h),
                        "pixel_area_um2": float(px_w * px_h),
                        "source": "OME PhysicalSize",
                    }

            page = tf.pages[0]
            tags = page.tags
            xres_tag = tags.get("XResolution")
            yres_tag = tags.get("YResolution")
            unit_tag = tags.get("ResolutionUnit")
            xres = _positive_float(_rational_to_float(xres_tag.value) if xres_tag is not None else None)
            yres = _positive_float(_rational_to_float(yres_tag.value) if yres_tag is not None else None)
            unit_value = unit_tag.value if unit_tag is not None else None
            unit_scale = None
            try:
                unit_code = int(unit_value)
            except Exception:
                unit_code = 0
            if unit_code == 2:
                unit_scale = 25400.0
            elif unit_code == 3:
                unit_scale = 10000.0
            if unit_scale is not None and xres is not None:
                px_w = unit_scale / xres
                px_h = unit_scale / (yres or xres)
                return {
                    "has_physical_scale": True,
                    "pixel_width_um": float(px_w),
                    "pixel_height_um": float(px_h),
                    "pixel_area_um2": float(px_w * px_h),
                    "source": "TIFF resolution",
                }
    except Exception:
        return {}
    return {}


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _sidecar_stem_from_path(case_dir: Path, image_path: Path) -> str:
    try:
        parts = image_path.relative_to(case_dir).parts
    except ValueError:
        parts = image_path.parts
    for part in parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            return part.strip("_")
    return image_path.stem


def _role_for_path(image_path: Path) -> str:
    text = image_path.as_posix().lower()
    if "overview" in text:
        return "overview"
    return "image"


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
    if path.exists() and path.is_file():
        if path.name == ETS_INDEX_FILE and path.parent.name == ETS_PROJECT_DIR:
            return path.resolve()
        if path.suffix.lower() in PROJECT_CONFIG_SUFFIXES:
            return path.resolve()
        if _has_project_image_suffix(path) or path.suffix.lower() in {".vsi", ".ets"}:
            raise ValueError(
                f"Selected file is an image/raw microscopy file, not a DataProcess histology project: {path.name}. "
                "Load histology_project.dphistology or its containing folder. Create that project from Histology Naming first."
            )
        raise ValueError(
            f"Unsupported histology project file type: {path.name}. "
            "Load histology_project.dphistology or its containing folder."
        )
    if path.name == ETS_INDEX_FILE and path.parent.name == ETS_PROJECT_DIR:
        return path.resolve()
    if path.suffix.lower() in PROJECT_CONFIG_SUFFIXES:
        return path.resolve()
    if not path.suffix:
        return (path / ETS_DATA_PROJECT_FILE).resolve()
    raise ValueError(
        f"Unsupported histology project file type: {path.name}. "
        "Load histology_project.dphistology or its containing folder."
    )


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


def _record_source_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("image_path") or record.get("source_path") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _primary_sources_for_project_path(path: Path) -> list[Path]:
    if _has_project_primary_suffix(path):
        return [path.resolve()]
    return []


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


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _replace_path_text(text: str, replacements: list[tuple[str, str]]) -> str:
    value = text
    for old, new in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        old_norm = old.rstrip("/")
        if not old_norm:
            continue
        if value == old_norm:
            return new
        if value.startswith(old_norm + "/"):
            return new.rstrip("/") + value[len(old_norm) :]
    return value


def _replace_paths_in_obj(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return _replace_path_text(value, replacements)
    if isinstance(value, list):
        return [_replace_paths_in_obj(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_paths_in_obj(item, replacements) for key, item in value.items()}
    return value


def _record_path_values(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("image_path", "source_path"):
        text = str(record.get(key) or "").strip()
        if text:
            values.append(text)
    image_files = record.get("image_files")
    if isinstance(image_files, dict):
        values.extend(str(path) for path in image_files.values() if str(path or "").strip())
    converted = record.get("converted_tiff_paths")
    if isinstance(converted, list):
        values.extend(str(path) for path in converted if str(path or "").strip())
    conversions = record.get("converted_from_ets")
    if isinstance(conversions, list):
        for item in conversions:
            if isinstance(item, dict):
                text = str(item.get("output_path") or "").strip()
                if text:
                    values.append(text)
    return values


def _converted_tiff_paths_for_record(record: dict[str, Any], case_dir: Path) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in _record_path_values(record):
        path = Path(raw).expanduser()
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if not _path_is_relative_to(resolved, case_dir):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _rename_target_for_tiff(path: Path, old_case_name: str, new_case_name: str) -> Path:
    if path.name.startswith(old_case_name):
        return path.with_name(new_case_name + path.name[len(old_case_name) :])
    return path.with_name(f"{new_case_name}{path.suffix}")


def _rename_entry_physical_sources(
    project_path: Path,
    record: dict[str, Any],
    display_name: str,
) -> dict[str, Any]:
    raw_dir = str(record.get("physical_rename_dir") or record.get("case_dir") or "").strip()
    if not raw_dir:
        return {"renamed": False, "path_replacements": [], "warnings": []}
    old_dir = Path(raw_dir).expanduser().resolve()
    if not old_dir.is_dir():
        return {
            "renamed": False,
            "path_replacements": [],
            "warnings": [f"Physical source folder not found: {old_dir}"],
        }
    if _path_is_relative_to(project_path, old_dir):
        raise ValueError("Move the DataProcess project file outside the case folder before renaming the case folder")

    new_case_name = sanitize_name(display_name, fallback=old_dir.name)
    new_dir = old_dir.with_name(new_case_name)
    rename_dir = old_dir != new_dir
    if rename_dir and new_dir.exists():
        raise FileExistsError(f"Rename target folder already exists: {new_dir}")

    replacements: list[tuple[str, str]] = []
    warnings: list[str] = []
    old_tiffs = _converted_tiff_paths_for_record(record, old_dir)
    actual_tiffs = [
        Path(_replace_path_text(str(path), [(str(old_dir), str(new_dir))])) if rename_dir else path
        for path in old_tiffs
    ]
    tiff_moves: list[tuple[Path, Path, Path]] = []
    target_keys: set[str] = set()
    for original, actual in zip(old_tiffs, actual_tiffs, strict=False):
        target = _rename_target_for_tiff(actual, old_dir.name, new_case_name)
        key = str(target)
        if key in target_keys:
            raise FileExistsError(f"Multiple converted TIFF files would rename to: {target}")
        target_keys.add(key)
        if target != actual and target.exists():
            raise FileExistsError(f"Rename target TIFF already exists: {target}")
        tiff_moves.append((original, actual, target))

    if rename_dir:
        old_dir.rename(new_dir)
        replacements.append((str(old_dir), str(new_dir)))
    for original, actual, target in tiff_moves:
        if not actual.exists():
            warnings.append(f"Converted TIFF not found during rename: {actual}")
            continue
        if target != actual:
            actual.rename(target)
            replacements.append((str(actual), str(target)))
            replacements.append((str(original), str(target)))

    return {
        "renamed": bool(rename_dir or any(actual != target for _original, actual, target in tiff_moves)),
        "case_dir": str(new_dir if rename_dir else old_dir),
        "case_name": new_case_name,
        "path_replacements": replacements,
        "renamed_tiffs": [
            {"from": str(original), "to": str(target)}
            for original, _actual, target in tiff_moves
            if original != target
        ],
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
    physical = _rename_entry_physical_sources(path, renamed, name)
    replacements = physical.get("path_replacements") if isinstance(physical, dict) else []
    if isinstance(replacements, list) and replacements:
        data = _replace_paths_in_obj(data, [(str(old), str(new)) for old, new in replacements])
        images = [record for record in data.get("images", []) if isinstance(record, dict)]
        renamed = next(
            (record for record in images if str(record.get("entry_id")) == str(entry_id)),
            renamed,
        )
    if isinstance(physical, dict) and physical.get("case_name"):
        old_sample = str(renamed.get("sample_id") or renamed.get("case_name") or "")
        new_sample = str(physical.get("case_name") or "")
        for record in images:
            if str(record.get("sample_id") or "") == old_sample:
                record["sample_id"] = new_sample
            if str(record.get("case_name") or "") == old_sample:
                record["case_name"] = new_sample
        renamed["sample_id"] = new_sample
        renamed["case_name"] = new_sample
    data["images"] = images
    _write_data_project_payload(path, data)
    loaded = load_histology_data_project(path)
    return {
        **loaded,
        "renamed_entry": _data_project_entry_from_record(path, renamed),
        "physical_rename": physical,
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

__all__ = [
    "ETS_DATA_PROJECT_FILE",
    "ETS_DATA_PROJECT_KIND",
    "ETS_INDEX_FILE",
    "ETS_PROJECT_DIR",
    "ETS_PROTOCOL",
    "PROJECT_CONFIG_SUFFIXES",
    "PROJECT_IMAGE_SUFFIXES",
    "PROJECT_PRIMARY_SUFFIXES",
    "_case_name_for_source",
    "_data_project_cache_dir",
    "_data_project_cache_layout",
    "_data_project_dir",
    "_data_project_entry_analysis_path",
    "_data_project_entry_geojson_path",
    "_data_project_entry_from_record",
    "_entry_image_files",
    "_entry_warnings",
    "_external_rois_candidates",
    "_find_data_project_entry",
    "_has_project_image_suffix",
    "_has_project_primary_suffix",
    "_infer_tiff_pixel_calibration",
    "_load_data_project_entry_analysis",
    "_load_data_project_payload",
    "_load_external_entry_rois",
    "_normalize_data_project_path",
    "_positive_float",
    "_source_entry_id",
    "_write_data_project_payload",
    "add_histology_data_project_paths",
    "create_histology_data_project",
    "load_histology_data_project",
    "rename_histology_data_project_entry",
]
