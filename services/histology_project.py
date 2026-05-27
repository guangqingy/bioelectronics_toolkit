from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _array_to_rgb,
    _clean_rois,
    _dapi_analysis_mask,
    _geojson,
    _marker_analysis,
    _mask_for_roi,
    _now_iso,
    _png_b64,
    _read_json,
    _scale_to_uint8,
    _write_json,
)
from services.histology_common import sanitize_name
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)
from services.histology_tiff_project import (
    TIFF_SUFFIXES,
    load_image_for_analysis,
)

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


def _friendly_image_read_error(path: Path, exc: Exception) -> str:
    message = str(exc)
    if path.suffix.lower() == ".ets" and ("SIS" in message or "not a TIFF" in message):
        return (
            "Olympus/SIS .ets files are traceability-only in the current histology "
            "pipeline. Export 2D TIFF images from Olympus software and use those TIFFs "
            "for preview and analysis."
        )
    return message or f"Could not read image: {path}"


def _read_project_image(path: str | Path, max_side: int = 1600):
    image_path = Path(str(path)).expanduser()
    try:
        arr = load_image_for_analysis(image_path)
    except Exception as exc:
        raise ValueError(_friendly_image_read_error(image_path, exc)) from exc
    backend = "tifffile" if image_path.suffix.lower() in TIFF_SUFFIXES else "pillow"
    warnings: list[str] = []
    if image_path.suffix.lower() not in TIFF_SUFFIXES:
        warnings.append("Non-TIFF image loaded; TIFF is recommended for quantitative analysis.")
    return arr, backend, warnings


def _preview_array_from_tiled_tiff(path: Path, max_side: int) -> tuple[np.ndarray, int, int]:
    if tifffile is None:
        raise RuntimeError("tifffile is required to preview tiled TIFF images")
    with tifffile.TiffFile(str(path)) as tf:
        series = tf.series[0] if getattr(tf, "series", None) else None
        page = series.pages[0] if series is not None and getattr(series, "pages", None) else tf.pages[0]
        shape = tuple(int(x) for x in (series.shape if series is not None else page.shape))
        if len(shape) == 2:
            height, width = shape
        elif len(shape) == 3 and shape[-1] in {1, 3, 4}:
            height, width = shape[:2]
        else:
            raise ValueError(f"Unsupported TIFF preview shape: {shape}")
        if not getattr(page, "is_tiled", False):
            raise ValueError("Large non-tiled TIFF cannot be previewed safely; export a tiled/pyramidal TIFF or ROI TIFF.")

        preview_max = max(256, min(int(max_side), 2400))
        scale = min(1.0, float(preview_max) / max(1, max(width, height)))
        preview_w = max(1, int(round(width * scale)))
        preview_h = max(1, int(round(height * scale)))
        canvas = Image.new("RGB", (preview_w, preview_h), "white")

        for tile_data, tile_index, _tile_shape in page.segments():
            tile = _array_to_rgb(np.asarray(tile_data).squeeze())
            if tile.ndim != 3:
                continue
            if len(tile_index) >= 4:
                y0 = int(tile_index[-3])
                x0 = int(tile_index[-2])
            else:
                continue
            if y0 >= height or x0 >= width:
                continue
            tile_h = min(int(tile.shape[0]), height - y0)
            tile_w = min(int(tile.shape[1]), width - x0)
            if tile_h <= 0 or tile_w <= 0:
                continue
            x1 = x0 + tile_w
            y1 = y0 + tile_h
            px0 = int(round(x0 * scale))
            py0 = int(round(y0 * scale))
            px1 = int(round(x1 * scale))
            py1 = int(round(y1 * scale))
            if px1 <= px0 or py1 <= py0:
                continue
            tile_img = Image.fromarray(tile[:tile_h, :tile_w, :3], mode="RGB")
            tile_img = tile_img.resize((px1 - px0, py1 - py0), Image.Resampling.BOX)
            canvas.paste(tile_img, (px0, py0))
        return np.asarray(canvas, dtype=np.uint8), width, height


def _read_project_image_preview(path: str | Path, max_side: int = 1600):
    image_path = Path(str(path)).expanduser()
    warnings: list[str] = []
    if image_path.suffix.lower() in TIFF_SUFFIXES and tifffile is not None:
        try:
            arr, width, height = _preview_array_from_tiled_tiff(image_path, max_side)
            return arr, "tifffile_tiled_preview", warnings, width, height
        except ValueError:
            pass
        except Exception as exc:
            warnings.append(f"Tiled preview fallback failed: {exc}")
    arr, backend, read_warnings = _read_project_image(image_path)
    preview_max = max(256, min(int(max_side), 2400))
    h, w = arr.shape[:2]
    if max(h, w) > preview_max:
        img = Image.fromarray(_array_to_rgb(arr), mode="RGB")
        img.thumbnail((preview_max, preview_max), Image.Resampling.LANCZOS)
        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return arr, backend, [*warnings, *read_warnings], w, h


def _resize_rgb_for_preview(arr: np.ndarray, max_side: int) -> np.ndarray:
    rgb = _array_to_rgb(arr)
    limit = max(128, min(int(max_side), 2400))
    if max(rgb.shape[:2]) <= limit:
        return np.ascontiguousarray(rgb)
    img = Image.fromarray(rgb, mode="RGB")
    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _normalize_region_box(
    x: float,
    y: float,
    width: float,
    height: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    x0 = max(0, min(int(np.floor(float(x))), max(0, image_width - 1)))
    y0 = max(0, min(int(np.floor(float(y))), max(0, image_height - 1)))
    x1 = max(x0 + 1, min(int(np.ceil(float(x) + float(width))), image_width))
    y1 = max(y0 + 1, min(int(np.ceil(float(y) + float(height))), image_height))
    return x0, y0, x1, y1


def _region_output_size(region_w: int, region_h: int, max_side: int) -> tuple[int, int, float]:
    limit = max(256, min(int(max_side), 2600))
    scale = min(1.0, float(limit) / max(1, int(region_w), int(region_h)))
    out_w = max(1, int(round(int(region_w) * scale)))
    out_h = max(1, int(round(int(region_h) * scale)))
    return out_w, out_h, scale


def _tiff_series_xy_shape(tf) -> tuple[Any, Any, tuple[int, ...], int, int]:
    series = tf.series[0] if getattr(tf, "series", None) else None
    page = series.pages[0] if series is not None and getattr(series, "pages", None) else tf.pages[0]
    shape = tuple(int(x) for x in (series.shape if series is not None else page.shape))
    if len(shape) == 2:
        height, width = shape
    elif len(shape) == 3 and shape[-1] in {1, 3, 4}:
        height, width = shape[:2]
    else:
        raise ValueError(f"Unsupported TIFF preview shape: {shape}")
    return series, page, shape, int(width), int(height)


def _preview_array_from_tiled_tiff_region(
    path: Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int,
) -> tuple[np.ndarray, int, int, tuple[int, int, int, int]]:
    if tifffile is None:
        raise RuntimeError("tifffile is required to preview tiled TIFF images")
    with tifffile.TiffFile(str(path)) as tf:
        _series, page, _shape, image_w, image_h = _tiff_series_xy_shape(tf)
        if not getattr(page, "is_tiled", False):
            raise ValueError("TIFF is not tiled")
        x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
        region_w = max(1, x1 - x0)
        region_h = max(1, y1 - y0)
        out_w, out_h, scale = _region_output_size(region_w, region_h, max_side)
        canvas = Image.new("RGB", (out_w, out_h), "black")
        for tile_data, tile_index, _tile_shape in page.segments():
            if len(tile_index) < 4:
                continue
            tile_y = int(tile_index[-3])
            tile_x = int(tile_index[-2])
            tile = _array_to_rgb(np.asarray(tile_data).squeeze())
            tile_h = int(tile.shape[0])
            tile_w = int(tile.shape[1])
            ix0 = max(x0, tile_x)
            iy0 = max(y0, tile_y)
            ix1 = min(x1, tile_x + tile_w)
            iy1 = min(y1, tile_y + tile_h)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            sx0 = ix0 - tile_x
            sy0 = iy0 - tile_y
            sx1 = sx0 + (ix1 - ix0)
            sy1 = sy0 + (iy1 - iy0)
            dx0 = int(round((ix0 - x0) * scale))
            dy0 = int(round((iy0 - y0) * scale))
            dx1 = int(round((ix1 - x0) * scale))
            dy1 = int(round((iy1 - y0) * scale))
            if dx1 <= dx0 or dy1 <= dy0:
                continue
            tile_img = Image.fromarray(tile[sy0:sy1, sx0:sx1, :3], mode="RGB")
            tile_img = tile_img.resize((dx1 - dx0, dy1 - dy0), Image.Resampling.LANCZOS)
            canvas.paste(tile_img, (dx0, dy0))
        return np.asarray(canvas, dtype=np.uint8), image_w, image_h, (x0, y0, x1, y1)


def _read_project_image_region_preview(
    path: str | Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
):
    image_path = Path(str(path)).expanduser()
    warnings: list[str] = []
    if image_path.suffix.lower() in TIFF_SUFFIXES and tifffile is not None:
        try:
            arr, image_w, image_h, box = _preview_array_from_tiled_tiff_region(
                image_path,
                x,
                y,
                width,
                height,
                max_side,
            )
            return arr, "tifffile_tiled_region", warnings, image_w, image_h, box
        except ValueError:
            pass
        except Exception as exc:
            warnings.append(f"Tiled region preview fallback failed: {exc}")
    arr, backend, read_warnings = _read_project_image(image_path)
    rgb = _array_to_rgb(arr)
    image_h, image_w = rgb.shape[:2]
    x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
    crop = rgb[y0:y1, x0:x1]
    return _resize_rgb_for_preview(crop, max_side), backend, [*warnings, *read_warnings], image_w, image_h, (x0, y0, x1, y1)


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


def _channel_rgb_slot(channel: str) -> int | None:
    key = channel.strip().lower()
    if key in {"cy5", "red", "macrophage", "cd68"}:
        return 0
    if key in {"fitc", "green", "sma", "mito", "mitotracker", "tmrm"}:
        return 1
    if key in {"hoechst", "dapi", "blue"}:
        return 2
    return None


def _as_2d_channel(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] <= 4:
        return data[..., :3].mean(axis=-1).astype(data.dtype, copy=False)
    while data.ndim > 2:
        data = np.max(data, axis=0)
    return data


def _planes_to_preview_rgb(planes: list[tuple[str, np.ndarray]]) -> np.ndarray:
    if not planes:
        raise ValueError("No readable image planes found for preview")
    first = _scale_to_uint8(_as_2d_channel(planes[0][1]))
    shape = first.shape[:2]
    recognized = [(_channel_rgb_slot(channel), channel, plane) for channel, plane in planes]
    if len(planes) == 1 and recognized[0][0] is None:
        return np.stack([first, first, first], axis=-1)
    rgb = np.zeros(shape + (3,), dtype=np.uint8)
    fallback_slot = 0
    for slot, channel, plane in recognized:
        plane_u8 = _scale_to_uint8(_as_2d_channel(plane))
        if plane_u8.shape[:2] != shape:
            img = Image.fromarray(plane_u8, mode="L").resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
            plane_u8 = np.asarray(img, dtype=np.uint8)
        if slot is None:
            if str(channel).strip().lower() in {"brightfield", "bf", "transmitted"} and len(planes) > 1:
                continue
            slot = fallback_slot % 3
            fallback_slot += 1
        rgb[..., slot] = np.maximum(rgb[..., slot], plane_u8)
    if not np.any(rgb):
        return np.stack([first, first, first], axis=-1)
    return rgb


def _preview_composite_from_image_files(
    image_files: dict[str, str],
    max_side: int,
) -> tuple[np.ndarray, str, list[str], int, int, list[str]]:
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    preview_shape: tuple[int, int] | None = None
    for channel, raw_path in image_files.items():
        try:
            arr, backend, read_warnings, w, h = _read_project_image_preview(raw_path, max_side=max_side)
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = _as_2d_channel(arr)
        if preview_shape is None:
            preview_shape = tuple(int(x) for x in plane.shape[:2])
            image_w = int(w)
            image_h = int(h)
        elif tuple(plane.shape[:2]) != preview_shape:
            img = Image.fromarray(_scale_to_uint8(plane), mode="L")
            img = img.resize((preview_shape[1], preview_shape[0]), Image.Resampling.BILINEAR)
            plane = np.asarray(img, dtype=np.uint8)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes:
        raise ValueError("No readable exported image channels found for this sample")
    return _planes_to_preview_rgb(planes), "composite_preview:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, channels


def _region_composite_from_image_files(
    image_files: dict[str, str],
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int,
) -> tuple[np.ndarray, str, list[str], int, int, tuple[int, int, int, int], list[str]]:
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    box: tuple[int, int, int, int] | None = None
    region_shape: tuple[int, int] | None = None
    for channel, raw_path in image_files.items():
        try:
            arr, backend, read_warnings, w, h, item_box = _read_project_image_region_preview(
                raw_path,
                x,
                y,
                width,
                height,
                max_side=max_side,
            )
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = _as_2d_channel(arr)
        if box is None:
            box = item_box
            image_w = int(w)
            image_h = int(h)
            region_shape = tuple(int(v) for v in plane.shape[:2])
        elif tuple(plane.shape[:2]) != region_shape:
            img = Image.fromarray(_scale_to_uint8(plane), mode="L")
            img = img.resize((region_shape[1], region_shape[0]), Image.Resampling.BILINEAR)
            plane = np.asarray(img, dtype=np.uint8)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes or box is None:
        raise ValueError("No readable exported image channels found for this sample")
    return _planes_to_preview_rgb(planes), "composite_region:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, box, channels


def _composite_from_image_files(image_files: dict[str, str]) -> tuple[np.ndarray, str, list[str]]:
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    dtype = np.uint16
    shape: tuple[int, int] | None = None
    for channel, path in image_files.items():
        try:
            plane = _as_2d_channel(load_image_for_analysis(path))
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        if shape is None:
            shape = tuple(int(x) for x in plane.shape[:2])
            dtype = plane.dtype
        elif tuple(plane.shape[:2]) != shape:
            warnings.append(f"{channel}: skipped mismatched shape {tuple(plane.shape[:2])}, expected {shape}")
            continue
        planes.append((channel, plane))
    if not planes or shape is None:
        raise ValueError("No readable exported image channels found for this sample")
    composite = np.zeros(shape + (3,), dtype=dtype)
    fallback_slot = 0
    for channel, plane in planes:
        slot = _channel_rgb_slot(channel)
        if slot is None:
            slot = fallback_slot % 3
            fallback_slot += 1
        composite[..., slot] = plane.astype(dtype, copy=False)
    return composite, "exported_tiff_channels", warnings


def _read_data_project_entry_image(entry: dict[str, Any]) -> tuple[np.ndarray, str, list[str]]:
    image_files = _entry_image_files(entry)
    if image_files:
        return _composite_from_image_files(image_files)
    image_path = entry.get("image_path", "")
    if not image_path:
        raise ValueError("Selected project entry has no image path")
    return _read_project_image(image_path)


def _entry_preview_image_path(entry: dict[str, Any]) -> str:
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        return direct
    image_files = _entry_image_files(entry)
    for preferred in ("Brightfield", "Hoechst", "Mito", "Overview"):
        if preferred in image_files:
            return image_files[preferred]
    return next(iter(image_files.values()), "")


def load_histology_data_project_image_preview(
    project_path: str | Path,
    entry_id: str,
    max_side: int = 1600,
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
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h = _read_project_image_preview(preview_path, max_side=preview_max)
    if not arr.size:
        raise ValueError("Selected project entry has no image path")
    return {
        **entry,
        "backend": backend,
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
    x0, y0, x1, y1 = box
    return {
        "entry_id": str(entry_id),
        "backend": backend,
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


def analyze_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    image_path = str(entry.get("image_path") or "")
    arr, backend, warnings = _read_data_project_entry_image(entry)
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
        raise ValueError("Select an exported TIFF, PNG, or JPG image file")
    return path


def load_histology_file_image_preview(
    image_path: str | Path,
    max_side: int = 1600,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    preview_max = max(256, min(int(max_side), 2400))
    arr, backend, warnings, w, h = _read_project_image_preview(path, max_side=preview_max)
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
    "create_histology_data_project",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_data_project_image_region_preview",
    "load_histology_file_image_preview",
    "load_histology_file_image_region_preview",
    "rename_histology_data_project_entry",
    "save_histology_data_project_rois",
]
