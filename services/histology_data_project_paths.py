from __future__ import annotations

import hashlib
from pathlib import Path

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


def _primary_sources_for_project_path(path: Path) -> list[Path]:
    if _has_project_primary_suffix(path):
        return [path.resolve()]
    return []
