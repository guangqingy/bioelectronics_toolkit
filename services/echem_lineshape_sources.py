from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.echem_lineshape_common import (
    DEFAULT_CROP_T0,
    DEFAULT_CROP_T1,
    DEVICE_DIR_RE,
    FLOAT_RE,
    SOURCE_SUFFIXES,
    _int_or,
    _natural_key,
    _pattern,
    _subfolder,
    infer_kind_from_path,
    normalize_kind,
    parse_chambers,
)


def list_materials(base_dir: str | Path) -> list[str]:
    root = Path(base_dir).expanduser()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {base_dir}")
    materials = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "Photocurrent").is_dir() or (child / "Photovoltage").is_dir():
            materials.append(child.name)
    return materials


def list_device_dirs(
    base_dir: str | Path,
    material: str,
    index_k: object,
    kind: str,
    chambers: object = None,
) -> list[Path]:
    root = Path(base_dir).expanduser() / str(material).strip() / _subfolder(kind)
    if not root.is_dir():
        raise ValueError(f"Directory not found: {root}")
    idx_target = _int_or(index_k, 1)
    chamber_set = set(parse_chambers(chambers))
    out: list[Path] = []
    for path in sorted(root.iterdir(), key=_natural_key):
        if not path.is_dir():
            continue
        match = DEVICE_DIR_RE.match(path.name)
        if not match:
            continue
        ch = _int_or(match.group("ch"), -1)
        idx = _int_or(match.group("idx"), -1)
        if idx == idx_target and ch in chamber_set:
            out.append(path)
    def _device_key(path: Path) -> tuple[int, str]:
        match = DEVICE_DIR_RE.match(path.name)
        return (_int_or(match.group("ch"), 999) if match else 999, path.name)

    return sorted(out, key=_device_key)


def segment_paths_from_source(source_path: str | Path, kind: str) -> tuple[list[Path], Path, Path]:
    source = Path(source_path).expanduser()
    if not source.exists():
        raise ValueError(f"Path not found: {source_path}")
    pattern = _pattern(kind)
    if source.is_dir():
        segment_dir = source
    elif re.search(r"_(pair|pulse)_\d+", source.stem):
        segment_dir = source.parent
    else:
        segment_dir = source.with_suffix("")

    if source.is_file() and re.search(r"_(pair|pulse)_\d+", source.stem):
        csv_paths = sorted(segment_dir.glob(pattern), key=_natural_key)
    else:
        csv_paths = sorted(segment_dir.rglob(pattern), key=_natural_key) if segment_dir.is_dir() else []
    if source.is_file() and source.match(pattern) and source not in csv_paths:
        csv_paths = [source]
    return csv_paths, source, segment_dir


def _source_path_list(data: dict[str, Any]) -> list[str]:
    raw = data.get("source_paths")
    if isinstance(raw, list):
        candidates = [str(item).strip() for item in raw]
    else:
        candidates = []
    single = str(data.get("source_path") or "").strip()
    if single:
        candidates.append(single)
    seen: set[str] = set()
    return [path for path in candidates if path and not (path in seen or seen.add(path))]


def list_source_files(folder: str | Path, kind: object = "photocurrent") -> list[dict[str, Any]]:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {folder}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=_natural_key):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if re.search(r"_(pair|pulse)_\d+", path.stem) or path.stem.endswith(
            ("_pairs_summary", "_pulses_summary")
        ):
            continue
        source_kind = infer_kind_from_path(path, kind)
        csv_paths, _source, segment_dir = segment_paths_from_source(path, source_kind)
        if not csv_paths:
            continue
        files.append(
            {
                "name": path.name,
                "path": str(path),
                "kind": source_kind,
                "segment_dir": str(segment_dir),
                "segment_count": len(csv_paths),
            }
        )
    return files


def read_two_column_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    path = Path(path)
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read CSV: {path.name}: {exc}") from exc

    col_time = next(
        (c for c in df.columns if str(c).strip().lower() in {"time_s", "time", "t", "t_s"}),
        None,
    )
    col_value = next(
        (
            c
            for c in df.columns
            if str(c).strip().lower()
            in {"current_ma", "current", "i_ma", "i", "voltage_v", "voltage", "v"}
        ),
        None,
    )
    if col_time is not None and col_value is not None:
        t = pd.to_numeric(df[col_time], errors="coerce").to_numpy()
        y = pd.to_numeric(df[col_value], errors="coerce").to_numpy()
    elif df.shape[1] >= 2:
        t = pd.to_numeric(df.iloc[:, 0], errors="coerce").to_numpy()
        y = pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy()
    else:
        t_values: list[float] = []
        y_values: list[float] = []
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                nums = FLOAT_RE.findall(raw.replace(",", " "))
                if len(nums) >= 2:
                    t_values.append(float(nums[0]))
                    y_values.append(float(nums[1]))
        if not t_values:
            raise RuntimeError(f"No numeric columns detected in {path.name}")
        t = np.asarray(t_values, dtype=float)
        y = np.asarray(y_values, dtype=float)

    mask = np.isfinite(t) & np.isfinite(y)
    t = t[mask]
    y = y[mask]
    if len(t) < 3:
        raise RuntimeError(f"Too few points in {path.name}")
    if np.any(np.diff(t) <= 0):
        order = np.argsort(t)
        t = t[order]
        y = y[order]
    return t, y


def center_trace(t_abs: np.ndarray, y: np.ndarray, kind: str) -> tuple[np.ndarray, np.ndarray]:
    if normalize_kind(kind) == "photovoltage":
        center_idx = int(np.argmin(y))
        y_proc = -np.asarray(y, dtype=float)
    else:
        center_idx = int(np.argmax(y))
        y_proc = np.asarray(y, dtype=float).copy()
    return np.asarray(t_abs, dtype=float) - float(t_abs[center_idx]), y_proc


def short_label(device_dir: Path, csv_path: Path) -> str:
    match = re.search(r"_(pair|pulse)_(\d+)$", csv_path.stem)
    suffix = f"{match.group(1)[0]}{int(match.group(2)):03d}" if match else "seg"
    return f"{device_dir.name} {suffix}"


def y_limits_for_samples(samples: list[dict[str, Any]]) -> list[float] | None:
    values: list[float] = []
    for sample in samples:
        y = np.asarray(sample.get("y") or [], dtype=float)
        if y.size:
            finite = y[np.isfinite(y)]
            if finite.size:
                values.extend([float(np.min(finite)), float(np.max(finite))])
    if not values:
        return None
    ymin = min(values)
    ymax = max(values)
    span = max(1e-12, ymax - ymin)
    pad = 0.05 * span
    return [ymin - pad, ymax + pad]


def samples_payload_from_csvs(
    csv_paths: list[Path],
    *,
    kind: str,
    source_path: Path | None = None,
    segment_dir: Path | None = None,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    for csv_path in csv_paths:
        device_dir = csv_path.parent
        try:
            t, y = read_two_column_csv(csv_path)
            t_rel, y_proc = center_trace(t, y, kind)
        except Exception as exc:
            warnings.append(f"Skipped {csv_path.name}: {exc}")
            continue
        samples.append(
            {
                "label": short_label(device_dir, csv_path),
                "device": device_dir.name,
                "file": str(csv_path),
                "kind": kind,
                "source": str(source_path) if source_path else "",
                "t": t_rel.tolist(),
                "y": y_proc.tolist(),
            }
        )
    if not samples:
        root = segment_dir or (csv_paths[0].parent if csv_paths else None)
        target = root if root is not None else source_path
        raise ValueError(f"No valid segments found under {target}")
    return {
        "samples": samples,
        "n": len(samples),
        "warnings": warnings,
        "y_limits": y_limits_for_samples(samples),
        "x_limits": [DEFAULT_CROP_T0, DEFAULT_CROP_T1],
        "kind": kind,
        "source_path": str(source_path) if source_path else "",
        "segment_dir": str(segment_dir) if segment_dir else "",
    }


def load_samples_from_sources(data: dict[str, Any]) -> dict[str, Any]:
    source_texts = _source_path_list(data)
    if not source_texts:
        raise ValueError("source_path is required")
    kind = infer_kind_from_path(source_texts[0], data.get("kind"))
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    segment_dirs: list[str] = []
    valid_sources: list[str] = []
    for source_text in source_texts:
        source_kind = infer_kind_from_path(source_text, kind)
        if source_kind != kind:
            warnings.append(f"Skipped {Path(source_text).name}: mixed source kind")
            continue
        try:
            csv_paths, source, segment_dir = segment_paths_from_source(source_text, kind)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        if not csv_paths:
            warnings.append(f"Skipped {source.name}: no {_pattern(kind)} files found")
            continue
        try:
            payload = samples_payload_from_csvs(
                csv_paths,
                kind=kind,
                source_path=source,
                segment_dir=segment_dir,
            )
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        samples.extend(payload["samples"])
        warnings.extend(payload.get("warnings", []))
        valid_sources.append(str(source))
        segment_dirs.append(str(segment_dir))

    if not samples:
        raise ValueError("No valid pair segments found in selected files")
    return {
        "samples": samples,
        "n": len(samples),
        "warnings": warnings,
        "y_limits": y_limits_for_samples(samples),
        "x_limits": [DEFAULT_CROP_T0, DEFAULT_CROP_T1],
        "kind": kind,
        "source_path": valid_sources[0] if valid_sources else "",
        "source_paths": valid_sources,
        "segment_dir": segment_dirs[0] if segment_dirs else "",
        "segment_dirs": segment_dirs,
        "n_sources": len(valid_sources),
    }


def load_samples_payload(data: dict[str, Any]) -> dict[str, Any]:
    if _source_path_list(data):
        return load_samples_from_sources(data)

    base_dir = data.get("base_dir", "")
    material = str(data.get("material") or "").strip()
    if not material:
        raise ValueError("material is required")
    kind = normalize_kind(data.get("kind"))
    device_dirs = list_device_dirs(
        base_dir,
        material,
        data.get("index_k", 1),
        kind,
        data.get("chambers"),
    )
    csv_paths: list[Path] = []
    for device_dir in device_dirs:
        csv_paths.extend(sorted(device_dir.rglob(_pattern(kind))))
    if not csv_paths:
        root = Path(base_dir).expanduser() / material / _subfolder(kind)
        raise ValueError(f"No {_pattern(kind)} files found under {root}")
    payload = samples_payload_from_csvs(csv_paths, kind=kind)
    payload["chambers"] = parse_chambers(data.get("chambers"))
    payload["material"] = material
    payload["index_k"] = _int_or(data.get("index_k", 1), 1)
    return payload


__all__ = [
    "_source_path_list",
    "center_trace",
    "list_device_dirs",
    "list_materials",
    "list_source_files",
    "load_samples_from_sources",
    "load_samples_payload",
    "read_two_column_csv",
    "samples_payload_from_csvs",
    "segment_paths_from_source",
    "short_label",
    "y_limits_for_samples",
]
