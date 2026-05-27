from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_DISTRIBUTION = "bioelectronics-toolkit"
PROJECT_IMPORT_NAME = "bioelectronics_toolkit"
DEFAULT_DEPENDENCIES = (
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "tifffile",
    "Pillow",
    "imagecodecs",
    "pyabf",
    "readlif",
    "statsmodels",
)


def _project_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=4)
def project_version(root: str | Path | None = None) -> str:
    project_root = _project_root(root)
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
            if match:
                return match.group(1)
        except OSError:
            pass
    try:
        return importlib.metadata.version(PROJECT_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


@lru_cache(maxsize=4)
def git_commit(root: str | Path | None = None) -> str:
    for key in ("BIOELECTRONICS_TOOLKIT_COMMIT", "BTE_COMMIT", "GITHUB_SHA"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value[:7]

    project_root = _project_root(root)
    if not (project_root / ".git").exists():
        return ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def dependency_versions(names: tuple[str, ...] = DEFAULT_DEPENDENCIES) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def runtime_provenance(root: str | Path | None = None) -> dict[str, Any]:
    version = project_version(root)
    commit = git_commit(root)
    return {
        "app": PROJECT_IMPORT_NAME,
        "version": version,
        "commit": commit,
        "label": version_label(version, commit),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": dependency_versions(),
    }


def version_label(version: str | None = None, commit: str | None = None) -> str:
    resolved_version = version if version is not None else project_version()
    resolved_commit = commit if commit is not None else git_commit()
    return f"v{resolved_version}" + (f" · {resolved_commit}" if resolved_commit else "")


def generator_label(provenance: dict[str, Any] | None = None) -> str:
    info = provenance or runtime_provenance()
    label = str(info.get("label") or version_label(str(info.get("version") or "")))
    return f"{PROJECT_IMPORT_NAME} {label}"


def dependency_label(
    names: tuple[str, ...] = ("numpy", "scipy", "pandas", "tifffile"),
    *,
    provenance: dict[str, Any] | None = None,
) -> str:
    deps = (
        provenance.get("dependencies", {})
        if isinstance(provenance, dict) and isinstance(provenance.get("dependencies"), dict)
        else dependency_versions(DEFAULT_DEPENDENCIES)
    )
    parts = [f"{name}={deps.get(name, 'not-installed')}" for name in names]
    return ", ".join(parts)
