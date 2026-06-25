from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DATA_DIR_NAME = "DataProcess"


def _clean_suffix(suffix: str) -> str:
    text = str(suffix or "").strip()
    if not text:
        raise ValueError("suffix must not be empty")
    return text if text.startswith(".") else f".{text}"


def sanitize_name_part(value: object, fallback: str = "output") -> str:
    """Return a filesystem-safe filename/prefix component."""
    raw = str(value or "").strip() or fallback
    cleaned = _SAFE_NAME_RE.sub("_", raw).strip("._")
    return cleaned or fallback


def next_available_path(
    directory: Path,
    stem: str,
    suffix: str,
    *,
    start: int = 1,
    width: int = 0,
    limit: int = 10000,
) -> Path:
    """Return ``directory / f"{stem}_{N}{suffix}"`` for the first free ``N``.

    The WebGUI intentionally uses short incrementing suffixes such as ``_1``
    and ``_2`` for user-facing exports. Callers that need zero-padding can set
    ``width=3`` without changing the shared collision policy.
    """
    directory = Path(directory)
    suffix = _clean_suffix(suffix)
    start = max(1, int(start))
    width = max(0, int(width))
    limit = max(start + 1, int(limit))
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(stem or "export")).strip("._-") or "export"

    directory.mkdir(parents=True, exist_ok=True)
    for idx in range(start, limit):
        number = f"{idx:0{width}d}" if width else str(idx)
        candidate = directory / f"{stem}_{number}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find available export name for {stem}{suffix}")


def next_numbered_path(base_path: Path, *, limit: int = 10000, width: int = 0) -> Path:
    """Return ``base_path`` with a short ``_N`` suffix, starting at ``_1``."""
    base_path = Path(base_path)
    return next_available_path(
        base_path.parent,
        base_path.stem,
        base_path.suffix,
        limit=limit,
        width=width,
    )


def output_dir_for_project(project_root: Path, view: str) -> Path:
    """Return the canonical cache-backed export directory for one view."""
    view_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(view or "exports")).strip("._-") or "exports"
    return Path(project_root) / ".dataprocess_cache" / "exports" / view_slug


def user_data_dir() -> Path:
    """Return the writable per-user data root for app-level fallbacks."""
    override = str(os.environ.get("DATAPROCESS_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base).expanduser() / APP_DATA_DIR_NAME if base else Path.home() / APP_DATA_DIR_NAME
    xdg_data = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg_data).expanduser() if xdg_data else Path.home() / ".local" / "share") / "dataprocess"


def resolve_output_dir(
    source_path: object = "",
    output_dir: object = "",
    *,
    default_suffix: str = "outputs",
    project_root: Path | None = None,
) -> Path:
    """Resolve an output directory without depending on process cwd."""
    root = Path(project_root).expanduser() if project_root is not None else user_data_dir() / "exports"
    source_text = str(source_path or "").strip()
    source = Path(source_text).expanduser() if source_text else None
    anchor = source.parent if source is not None and source.name else root

    raw_output = str(output_dir or "").strip()
    if raw_output:
        resolved = Path(raw_output).expanduser()
        if not resolved.is_absolute():
            resolved = anchor / resolved
        return resolved

    if source is not None and source.name:
        return source.with_name(f"{source.stem}_{default_suffix}")
    return root / default_suffix
