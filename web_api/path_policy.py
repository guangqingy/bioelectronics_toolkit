from __future__ import annotations

import re
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize_name_part(value: object, fallback: str = "output") -> str:
    """Return a filesystem-safe filename/prefix component."""
    raw = str(value or "").strip() or fallback
    cleaned = _SAFE_NAME_RE.sub("_", raw).strip("._")
    return cleaned or fallback


def resolve_output_dir(source_path: object, output_dir: object = "", default_suffix: str = "outputs") -> Path:
    """Resolve a user-provided output directory relative to a source file."""
    source = Path(str(source_path or "")).expanduser()
    raw_output = str(output_dir or "").strip()
    if raw_output:
        resolved = Path(raw_output).expanduser()
        if not resolved.is_absolute() and source.parent:
            resolved = source.parent / resolved
        return resolved
    if source.name:
        return source.with_name(f"{source.stem}_{default_suffix}")
    return Path.cwd() / default_suffix


def ensure_output_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(path: Path, overwrite: bool = True) -> Path:
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(2, 10000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an unused output path for {path}")
