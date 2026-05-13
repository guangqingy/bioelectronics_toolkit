from __future__ import annotations

import re
from pathlib import Path


def _clean_suffix(suffix: str) -> str:
    text = str(suffix or "").strip()
    if not text:
        raise ValueError("suffix must not be empty")
    return text if text.startswith(".") else f".{text}"


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
