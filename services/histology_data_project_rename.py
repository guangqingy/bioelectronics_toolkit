from __future__ import annotations

from pathlib import Path
from typing import Any

from services.histology_analysis import _now_iso
from services.histology_common import sanitize_name
from services.histology_data_project_paths import _normalize_data_project_path
from services.histology_data_project_store import (
    _data_project_entry_from_record,
    _load_data_project_payload,
    _write_data_project_payload,
    load_histology_data_project,
)


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


__all__ = [
    "rename_histology_data_project_entry",
]
