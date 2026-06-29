from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from services.histology_tiff_io import SUPPORTED_IMAGE_SUFFIXES
from services.histology_tiff_models import RAW_OLYMPUS_SUFFIXES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _file_size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 * 1024), 6)
    except OSError:
        return 0.0


def _modified_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        )
    except OSError:
        return ""


def _is_hidden(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def scan_raw_olympus_folder(raw_dir: str | Path | None) -> pd.DataFrame:
    """Index Olympus proprietary files for traceability without reading image data."""
    columns = [
        "sample_folder",
        "file_type",
        "file_name",
        "file_path",
        "relative_path",
        "file_size_MB",
        "modified_at",
    ]
    raw_text = str(raw_dir or "").strip()
    if not raw_text:
        return pd.DataFrame(columns=columns)
    root = Path(raw_text).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Raw Olympus folder not found: {root}")

    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_hidden(path, root):
            continue
        suffix = path.suffix.lower()
        if suffix not in RAW_OLYMPUS_SUFFIXES:
            continue
        rel = path.relative_to(root)
        sample_folder = rel.parts[0] if rel.parts else root.name
        rows.append(
            {
                "sample_folder": sample_folder,
                "file_type": suffix.lstrip("."),
                "file_name": path.name,
                "file_path": str(path),
                "relative_path": rel.as_posix(),
                "file_size_MB": _file_size_mb(path),
                "modified_at": _modified_iso(path),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def discover_image_files(exported_dir: str | Path) -> list[Path]:
    root = Path(str(exported_dir or "").strip()).expanduser().resolve()
    if root.is_file():
        if root.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported exported image extension: {root.suffix}")
        return [root]
    if not root.is_dir():
        raise FileNotFoundError(f"Exported TIFF/image folder not found: {root}")
    images: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_hidden(path, root):
            continue
        if path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            images.append(path.resolve())
    return images

__all__ = [
    "_file_size_mb",
    "_is_hidden",
    "_modified_iso",
    "_now_iso",
    "_safe_relative",
    "discover_image_files",
    "scan_raw_olympus_folder",
]
