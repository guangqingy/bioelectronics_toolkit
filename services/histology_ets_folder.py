from __future__ import annotations

import re
from pathlib import Path

from services.histology_common import sanitize_name
from services.histology_ets_converter import (
    _existing_tiff_is_usable,
    _role_for_ets_path,
    convert_ets_to_tiff,
)
from services.histology_ets_models import (
    DEFAULT_Z_CHANNEL_NAMES,
    ETS_SUFFIX,
    EtsConversionResult,
    ProgressCallback,
)
from services.histology_ets_reader import _is_hidden, read_ets_index


def _case_dir_for_ets(root: Path, ets_path: Path) -> Path:
    root = root.resolve()
    ets_path = ets_path.resolve()
    for parent in [ets_path.parent, *ets_path.parents]:
        if parent == parent.parent:
            break
        if parent.is_dir() and any(item.suffix.lower() == ".vsi" for item in parent.glob("*.vsi")):
            return parent
        if parent == root:
            break
    try:
        rel = ets_path.relative_to(root)
    except ValueError:
        rel = ets_path.name
    if not isinstance(rel, str) and rel.parts:
        first = rel.parts[0]
        if first.startswith("_") or first.lower().startswith("stack"):
            return root
        return root / first
    return ets_path.parent


def _slide_token_for_ets(case_dir: Path, ets_path: Path) -> str:
    try:
        parts = ets_path.relative_to(case_dir).parts
    except ValueError:
        parts = ets_path.parts
    tokens: list[str] = []
    for part in parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            tokens.append(part.strip("_"))
        elif part.lower().startswith("stack"):
            tokens.append(part)
    return sanitize_name("_".join(tokens), fallback=ets_path.stem)


def _looks_like_stack_derivative(path: Path) -> bool:
    return bool(
        re.search(
            r"(?:^|[_\-\s])tray\d+[_\-\s]*slide.*[_\-\s]stack\d+(?:$|[_\-\s])",
            path.stem.lower(),
        )
    )


def _existing_tiff_for_role(
    case_dir: Path,
    role: str,
    role_suffix: str,
    used_outputs: set[str],
) -> Path | None:
    exact = [
        case_dir / f"{sanitize_name(case_dir.name, fallback='sample')}_{role_suffix}.tif",
        case_dir / f"{sanitize_name(case_dir.name, fallback='sample')}_{role_suffix}.tiff",
    ]
    candidates = [path for path in exact if path.is_file()]
    role_key = str(role_suffix).lower()
    for path in sorted([*case_dir.glob("*.tif"), *case_dir.glob("*.tiff")]):
        if path in candidates or _looks_like_stack_derivative(path):
            continue
        stem = path.stem.lower()
        if role in {"overview", "label"}:
            if role_key not in stem:
                continue
        elif role == "brightfield":
            if not ("brightfield" in stem or stem.endswith("_bf") or "_bf_" in stem):
                continue
        elif role == "fluorescence":
            if role_key not in stem:
                continue
        elif role_key not in stem:
            continue
        candidates.append(path)
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in used_outputs:
            continue
        if _existing_tiff_is_usable(candidate):
            return candidate
    return None


def _output_path_for_ets(
    root: Path,
    ets_path: Path,
    used_outputs: set[str],
) -> tuple[Path, str, str]:
    case_dir = _case_dir_for_ets(root, ets_path)
    sample_id = sanitize_name(case_dir.name, fallback="sample")
    role = _role_for_ets_path(ets_path)
    role_suffix = {
        "brightfield": "Brightfield",
        "overview": "Overview",
        "label": "Label",
    }.get(role, sanitize_name(role).title())
    existing = _existing_tiff_for_role(case_dir, role, role_suffix, used_outputs)
    if existing is not None:
        used_outputs.add(str(existing.resolve()))
        return existing, str(case_dir.resolve()), role
    candidates = [case_dir / f"{sample_id}_{role_suffix}.tif"]
    token = _slide_token_for_ets(case_dir, ets_path)
    if token:
        candidates.append(case_dir / f"{sample_id}_{token}_{role_suffix}.tif")
    for idx in range(2, 1000):
        candidates.append(case_dir / f"{sample_id}_{role_suffix}_{idx}.tif")
    for candidate in candidates:
        key = str(candidate.resolve())
        if key not in used_outputs:
            used_outputs.add(key)
            return candidate, str(case_dir.resolve()), role
    raise ValueError(f"Could not allocate a converted TIFF name for {ets_path}")


def _channel_name_for_z(index: int, z: int) -> str:
    if index < len(DEFAULT_Z_CHANNEL_NAMES):
        return DEFAULT_Z_CHANNEL_NAMES[index]
    return f"Channel_z{z}"


def _output_path_for_ets_channel(
    root: Path,
    ets_path: Path,
    channel_name: str,
    used_outputs: set[str],
) -> tuple[Path, str, str]:
    case_dir = _case_dir_for_ets(root, ets_path)
    sample_id = sanitize_name(case_dir.name, fallback="sample")
    channel = sanitize_name(channel_name, fallback="Channel")
    existing = _existing_tiff_for_role(case_dir, "fluorescence", channel, used_outputs)
    if existing is not None:
        used_outputs.add(str(existing.resolve()))
        return existing, str(case_dir.resolve()), channel
    candidates = [case_dir / f"{sample_id}_{channel}.tif"]
    token = _slide_token_for_ets(case_dir, ets_path)
    if token:
        candidates.append(case_dir / f"{sample_id}_{token}_{channel}.tif")
    for idx in range(2, 1000):
        candidates.append(case_dir / f"{sample_id}_{channel}_{idx}.tif")
    for candidate in candidates:
        key = str(candidate.resolve())
        if key not in used_outputs:
            used_outputs.add(key)
            return candidate, str(case_dir.resolve()), channel
    raise ValueError(f"Could not allocate a converted {channel} TIFF name for {ets_path}")


def iter_ets_files(source: str | Path) -> list[Path]:
    root = Path(source).expanduser().resolve()
    if root.is_file():
        return [root] if root.suffix.lower() == ETS_SUFFIX else []
    if not root.is_dir():
        raise FileNotFoundError(f"ETS source folder not found: {root}")
    return [
        path.resolve()
        for path in sorted(root.rglob(f"*{ETS_SUFFIX}"))
        if path.is_file() and not _is_hidden(path, root)
    ]


def convert_ets_folder_to_tiff(
    source: str | Path,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> list[EtsConversionResult]:
    root = Path(source).expanduser().resolve()
    scan_root = root.parent if root.is_file() else root
    ets_files = iter_ets_files(root)
    results: list[EtsConversionResult] = []
    used_outputs: set[str] = set()
    used_roles: set[tuple[str, str]] = set()
    total = max(1, len(ets_files))
    for idx, ets_path in enumerate(ets_files, start=1):
        try:
            index = read_ets_index(ets_path)
        except Exception as exc:
            case_dir = str(_case_dir_for_ets(scan_root, ets_path).resolve())
            results.append(
                EtsConversionResult(
                    source_path=str(ets_path),
                    output_path="",
                    case_dir=case_dir,
                    sample_id=Path(case_dir).name,
                    role=_role_for_ets_path(ets_path),
                    status="error",
                    warning_messages=[str(exc)],
                )
            )
            continue

        role = _role_for_ets_path(ets_path)
        z_values = sorted(index.z_values or {int(tile.z) for tile in index.tiles})
        if role == "brightfield" and len(z_values) > 1:
            for z_index, z in enumerate(z_values):
                channel_name = _channel_name_for_z(z_index, int(z))
                output, case_dir, channel_role = _output_path_for_ets_channel(
                    scan_root,
                    ets_path,
                    channel_name,
                    used_outputs,
                )
                role_key = (case_dir, channel_role)
                if role_key in used_roles:
                    results.append(
                        EtsConversionResult(
                            source_path=str(ets_path),
                            output_path=str(output),
                            case_dir=case_dir,
                            sample_id=Path(case_dir).name,
                            role=channel_role,
                            status="skipped_duplicate_role",
                            warning_messages=[
                                f"Duplicate {channel_role} ETS skipped; the first {channel_role} TIFF is used for this case."
                            ],
                        )
                    )
                    continue

                def channel_progress(
                    fraction: float,
                    message: str,
                    idx: int = idx,
                    z_index: int = z_index,
                    z_total: int = len(z_values),
                    channel_name: str = channel_name,
                ) -> None:
                    if progress:
                        item_fraction = (z_index + max(0.0, min(1.0, fraction))) / max(1, z_total)
                        overall = (idx - 1 + item_fraction) / total
                        progress(overall, f"{channel_name}: {message}")

                try:
                    result = convert_ets_to_tiff(
                        ets_path,
                        output,
                        overwrite=overwrite,
                        selected_z=int(z),
                        progress=channel_progress,
                    )
                    result.case_dir = case_dir
                    result.sample_id = Path(case_dir).name
                    result.role = channel_role
                    results.append(result)
                    used_roles.add(role_key)
                except Exception as exc:
                    results.append(
                        EtsConversionResult(
                            source_path=str(ets_path),
                            output_path=str(output),
                            case_dir=case_dir,
                            sample_id=Path(case_dir).name,
                            role=channel_role,
                            status="error",
                            warning_messages=[str(exc)],
                        )
                    )
            continue

        output, case_dir, role = _output_path_for_ets(scan_root, ets_path, used_outputs)
        role_key = (case_dir, role)
        if role_key in used_roles:
            results.append(
                EtsConversionResult(
                    source_path=str(ets_path),
                    output_path=str(output),
                    case_dir=case_dir,
                    sample_id=Path(case_dir).name,
                    role=role,
                    status="skipped_duplicate_role",
                    warning_messages=[
                        f"Duplicate {role} ETS skipped; the first {role} TIFF is used for this case."
                    ],
                )
            )
            continue

        def item_progress(fraction: float, message: str, idx: int = idx) -> None:
            if progress:
                overall = (idx - 1 + max(0.0, min(1.0, fraction))) / total
                progress(overall, message)

        try:
            result = convert_ets_to_tiff(
                ets_path,
                output,
                overwrite=overwrite,
                progress=item_progress,
            )
            result.case_dir = case_dir
            result.sample_id = Path(case_dir).name
            result.role = role
            results.append(result)
            used_roles.add(role_key)
        except Exception as exc:
            results.append(
                EtsConversionResult(
                    source_path=str(ets_path),
                    output_path=str(output),
                    case_dir=case_dir,
                    sample_id=Path(case_dir).name,
                    role=role,
                    status="error",
                    warning_messages=[str(exc)],
                )
            )
    if progress:
        progress(1.0, f"ETS conversion checked {len(ets_files)} file(s)")
    return results

__all__ = [
    "_case_dir_for_ets",
    "_channel_name_for_z",
    "_existing_tiff_for_role",
    "_looks_like_stack_derivative",
    "_output_path_for_ets",
    "_output_path_for_ets_channel",
    "_slide_token_for_ets",
    "convert_ets_folder_to_tiff",
    "iter_ets_files",
]
