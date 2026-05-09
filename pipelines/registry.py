from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("registry.json")
MISSING_SCRIPT_MESSAGE = (
    "This pipeline is registered, but the project-specific script is not present "
    "in this checkout. Use the documented upstream WebGUI tools to generate data, "
    "or add the local project pipeline folder before running it."
)


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    with REGISTRY_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _base_path(base_dir: str | Path | None) -> Path:
    if base_dir is None:
        return Path.cwd()
    return Path(base_dir).expanduser()


def resolve_script_path(script: dict[str, Any], base_dir: str | Path | None = None) -> Path | None:
    raw_path = str(script.get("script_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return _base_path(base_dir) / path


def _annotate_script(
    script: dict[str, Any],
    base_dir: str | Path | None,
    *,
    include_resolved_path: bool = False,
) -> dict[str, Any]:
    out = deepcopy(script)
    resolved = resolve_script_path(out, base_dir)
    available = bool(resolved and resolved.is_file())
    out["available"] = available
    out["availability"] = "available" if available else "missing"
    out["availability_label"] = "Available" if available else "Local script missing"
    out["availability_message"] = "" if available else out.get("missing_message", MISSING_SCRIPT_MESSAGE)
    if include_resolved_path:
        out["resolved_script_path"] = str(resolved) if available and resolved else ""
    return out


def pipeline_catalog(
    base_dir: str | Path | None = None,
    *,
    include_availability: bool = False,
    include_resolved_paths: bool = False,
) -> dict[str, Any]:
    catalog = deepcopy(_load_registry())
    if not include_availability:
        return catalog

    for category in catalog.get("categories", []):
        scripts = category.get("scripts", [])
        category["scripts"] = [
            _annotate_script(script, base_dir, include_resolved_path=include_resolved_paths)
            for script in scripts
        ]
    return catalog


def pipeline_category_ids() -> tuple[str, ...]:
    return tuple(str(category["id"]) for category in _load_registry().get("categories", []))


def default_category_id() -> str:
    catalog = _load_registry()
    default = str(catalog.get("default_category") or "")
    return default if default in pipeline_category_ids() else pipeline_category_ids()[0]


def find_pipeline_script(
    script_id: str,
    base_dir: str | Path | None = None,
    *,
    include_availability: bool = True,
) -> dict[str, Any] | None:
    for category in pipeline_catalog(
        base_dir,
        include_availability=include_availability,
        include_resolved_paths=True,
    ).get("categories", []):
        for script in category.get("scripts", []):
            if script.get("id") != script_id:
                continue
            out = deepcopy(script)
            out["category"] = category.get("id", "")
            out["category_label"] = category.get("label", category.get("id", ""))
            out["category_documentation"] = category.get("documentation", "")
            return out
    return None


def validate_registry(catalog: dict[str, Any] | None = None) -> list[str]:
    data = catalog or _load_registry()
    errors: list[str] = []
    categories = data.get("categories", [])
    if not categories:
        errors.append("registry has no categories")

    category_ids: set[str] = set()
    script_ids: set[str] = set()
    for category in categories:
        cat_id = str(category.get("id") or "")
        if not cat_id:
            errors.append("category is missing id")
        elif cat_id in category_ids:
            errors.append(f"duplicate category id: {cat_id}")
        category_ids.add(cat_id)

        scripts = category.get("scripts", [])
        if not scripts:
            errors.append(f"category has no scripts: {cat_id}")
        for script in scripts:
            script_id = str(script.get("id") or "")
            if not script_id:
                errors.append(f"script in {cat_id} is missing id")
            elif script_id in script_ids:
                errors.append(f"duplicate script id: {script_id}")
            script_ids.add(script_id)

            if not script.get("name"):
                errors.append(f"script is missing name: {script_id or cat_id}")
            if not script.get("script"):
                errors.append(f"script is missing display script name: {script_id or cat_id}")
            if "params" not in script:
                errors.append(f"script is missing params list: {script_id or cat_id}")

    default = str(data.get("default_category") or "")
    if default and default not in category_ids:
        errors.append(f"default_category is unknown: {default}")
    return errors
