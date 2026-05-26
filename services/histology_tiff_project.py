from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None

SUPPORTED_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
TIFF_SUFFIXES = {".tif", ".tiff"}
RAW_OLYMPUS_SUFFIXES = {".vsi", ".ets"}
PROJECT_KIND = "dataprocess_histology_project"
PROJECT_PROTOCOL = "dataprocess-tiff-histology"
PROJECT_FILE_NAME = "histology_project.dphistology"


@dataclass
class ImageRecord:
    file_path: str
    channel_name: str
    detected_channel: str
    confidence: float
    original_filename: str
    dtype: str = ""
    shape: tuple[int, ...] = field(default_factory=tuple)
    bit_depth: int = 0
    warning_messages: list[str] = field(default_factory=list)


@dataclass
class SampleRecord:
    sample_id: str
    image_files: dict[str, str] = field(default_factory=dict)
    images: list[ImageRecord] = field(default_factory=list)
    raw_olympus_reference: str = ""
    analysis_folder: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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


def detect_channel_from_filename(filename: str) -> dict[str, Any]:
    stem = Path(filename).stem.lower()
    patterns: list[tuple[str, tuple[str, ...], float]] = [
        ("Hoechst", ("hoechst", "dapi", "blue"), 0.95),
        ("FITC", ("fitc", "sma", "green"), 0.95),
        ("Cy5", ("cy5", "cd68", "macrophage", "macro", "red"), 0.95),
        ("Mito", ("mitotracker", "mito", "tmrm"), 0.9),
        ("Brightfield", ("brightfield", "transmitted", "bf"), 0.95),
    ]
    tokenized = re.split(r"[^a-z0-9]+", stem)
    for channel, tokens, confidence in patterns:
        for token in tokens:
            if token in tokenized or token in stem:
                return {
                    "detected_channel": channel,
                    "channel_name": channel,
                    "confidence": confidence,
                    "matched_token": token,
                    "original_filename": filename,
                }
    fallback = Path(filename).stem.split("_")[-1].strip() or "Unknown"
    return {
        "detected_channel": fallback,
        "channel_name": fallback,
        "confidence": 0.25,
        "matched_token": "",
        "original_filename": filename,
    }


def infer_sample_id(filename: str) -> str:
    stem = Path(filename).stem
    detection = detect_channel_from_filename(filename)
    token = str(detection.get("matched_token") or "")
    if token:
        match = re.search(rf"(?i)(?:^|[_\-\s]){re.escape(token)}(?:$|[_\-\s])", stem)
        if match and match.start() > 0:
            sample = stem[: match.start()]
            return sample.rstrip("_- ").strip() or stem
    for sep in ("_", " - ", " "):
        if sep in stem:
            sample = stem.rsplit(sep, 1)[0].strip("_- ")
            if sample:
                return sample
    return stem


def load_image_for_analysis(file_path: str | Path) -> np.ndarray:
    """Load image values without normalization. TIFF uses tifffile; PNG/JPG uses Pillow."""
    path = Path(file_path).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image extension for analysis: {path.suffix}")
    if suffix in TIFF_SUFFIXES:
        if tifffile is None:
            raise RuntimeError("tifffile is required to load TIFF images")
        return np.asarray(tifffile.imread(str(path)))
    with Image.open(path) as img:
        return np.asarray(img)


def _bit_depth_for_array(arr: np.ndarray) -> int:
    dtype = np.dtype(arr.dtype)
    if dtype.kind in {"u", "i"}:
        return int(dtype.itemsize * 8)
    if dtype.kind == "f":
        return 32 if dtype.itemsize <= 4 else 64
    return 0


def _image_warnings(path: Path, arr: np.ndarray) -> list[str]:
    warnings: list[str] = []
    suffix = path.suffix.lower()
    if suffix not in TIFF_SUFFIXES:
        warnings.append("Non-TIFF image; use 16-bit TIFF for quantitative fluorescence.")
    if arr.ndim == 3:
        if arr.shape[-1] <= 4:
            warnings.append("Multi-channel/color image stored in one file; expected single-channel XY.")
        else:
            warnings.append("Image has more than XY dimensions; expected 2D exported image.")
    elif arr.ndim != 2:
        warnings.append("Image has unsupported dimensionality; expected XY only.")
    if suffix in TIFF_SUFFIXES and _bit_depth_for_array(arr) < 16:
        warnings.append("TIFF is not 16-bit; confirm it is suitable for quantification.")
    return warnings


def _record_for_image(path: Path) -> ImageRecord:
    detection = detect_channel_from_filename(path.name)
    try:
        arr = load_image_for_analysis(path)
        dtype = str(arr.dtype)
        shape = tuple(int(x) for x in arr.shape)
        bit_depth = _bit_depth_for_array(arr)
        warnings = _image_warnings(path, arr)
    except Exception as exc:
        dtype = ""
        shape = ()
        bit_depth = 0
        warnings = [f"Unreadable image: {exc}"]
    return ImageRecord(
        file_path=str(path),
        channel_name=str(detection["channel_name"]),
        detected_channel=str(detection["detected_channel"]),
        confidence=float(detection["confidence"]),
        original_filename=path.name,
        dtype=dtype,
        shape=shape,
        bit_depth=bit_depth,
        warning_messages=warnings,
    )


def group_images_by_sample(image_files: list[str | Path]) -> dict[str, SampleRecord]:
    grouped: dict[str, SampleRecord] = {}
    for raw_path in image_files:
        path = Path(raw_path).expanduser().resolve()
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        sample_id = infer_sample_id(path.name)
        record = _record_for_image(path)
        sample = grouped.setdefault(sample_id, SampleRecord(sample_id=sample_id))
        channel = record.channel_name
        if channel in sample.image_files:
            sample.warnings.append(f"Duplicate channel {channel}: {path.name}")
            channel = f"{channel}_{len([k for k in sample.image_files if k.startswith(channel)]) + 1}"
            record.channel_name = channel
        sample.image_files[channel] = str(path)
        sample.images.append(record)

    for sample in grouped.values():
        channels = set(sample.image_files)
        if not ({"Hoechst", "FITC", "Cy5", "Mito"} & channels):
            sample.warnings.append("Missing expected fluorescence channel.")
        shapes = {
            tuple(image.shape[:2])
            for image in sample.images
            if len(image.shape) >= 2 and not image.warning_messages
        }
        if len(shapes) > 1:
            sample.warnings.append("Mismatched XY shape between channels.")
        for image in sample.images:
            sample.warnings.extend(image.warning_messages)
    return grouped


def load_image_for_display(
    file_path: str | Path,
    contrast_limits: tuple[float, float] | None = None,
) -> np.ndarray:
    arr = np.asarray(load_image_for_analysis(file_path))
    if arr.ndim == 3 and arr.shape[-1] > 3:
        arr = arr[..., :3]
    data = arr.astype(np.float32, copy=False)
    if contrast_limits is None:
        finite = data[np.isfinite(data)]
        if finite.size:
            lo = float(np.percentile(finite, 1.0))
            hi = float(np.percentile(finite, 99.8))
        else:
            lo, hi = 0.0, 1.0
    else:
        lo, hi = float(contrast_limits[0]), float(contrast_limits[1])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    return np.round(np.clip((data - lo) / (hi - lo), 0.0, 1.0) * 255).astype(np.uint8)


def _sample_analysis_paths(sample_id: str, analysis_folder: Path) -> dict[str, str]:
    return {
        "manifest_path": str(analysis_folder / f"{sample_id}_file_manifest.csv"),
        "parameters_path": str(analysis_folder / f"{sample_id}_parameters.json"),
        "roi_measurements_path": str(analysis_folder / f"{sample_id}_roi_measurements.csv"),
        "rois_path": str(analysis_folder / f"{sample_id}_rois.json"),
        "qc_overlay_path": str(analysis_folder / f"{sample_id}_qc_overlay.png"),
    }


def create_analysis_folders(
    sample_records: dict[str, SampleRecord],
    output_dir: str | Path,
) -> dict[str, SampleRecord]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for sample in sample_records.values():
        sample_dir = root / sample.sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        sample.analysis_folder = str(sample_dir)
        sample.metadata.update(_sample_analysis_paths(sample.sample_id, sample_dir))
    return sample_records


def _manifest_rows(sample_record: SampleRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for image in sample_record.images:
        path = Path(image.file_path)
        shape_y = int(image.shape[0]) if len(image.shape) >= 2 else 0
        shape_x = int(image.shape[1]) if len(image.shape) >= 2 else 0
        rows.append(
            {
                "sample_id": sample_record.sample_id,
                "channel": image.channel_name,
                "file_path": image.file_path,
                "file_name": path.name,
                "extension": path.suffix.lower(),
                "shape_y": shape_y,
                "shape_x": shape_x,
                "dtype": image.dtype,
                "bit_depth": image.bit_depth,
                "file_size_MB": _file_size_mb(path),
                "is_tiff": path.suffix.lower() in TIFF_SUFFIXES,
                "warning": "; ".join(image.warning_messages),
            }
        )
    return rows


def export_file_manifest(sample_record: SampleRecord) -> str:
    manifest_path = sample_record.metadata.get("manifest_path", "")
    if not manifest_path:
        if not sample_record.analysis_folder:
            raise ValueError("Sample analysis_folder is required before exporting manifest")
        manifest_path = str(Path(sample_record.analysis_folder) / f"{sample_record.sample_id}_file_manifest.csv")
        sample_record.metadata["manifest_path"] = manifest_path
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _manifest_rows(sample_record)
    fieldnames = [
        "sample_id",
        "channel",
        "file_path",
        "file_name",
        "extension",
        "shape_y",
        "shape_x",
        "dtype",
        "bit_depth",
        "file_size_MB",
        "is_tiff",
        "warning",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def _default_parameters(sample: SampleRecord) -> dict[str, Any]:
    return {
        "sample_id": sample.sample_id,
        "created_at": _now_iso(),
        "channels": sorted(sample.image_files),
        "channel_assignments": dict(sample.image_files),
        "roi_measurements_path": sample.metadata.get("roi_measurements_path", ""),
        "rois_path": sample.metadata.get("rois_path", ""),
        "qc_overlay_path": sample.metadata.get("qc_overlay_path", ""),
        "notes": "Generated from exported XY TIFF/images. Raw Olympus files are traceability-only.",
    }


def export_sample_placeholders(sample_record: SampleRecord) -> dict[str, str]:
    paths = dict(sample_record.metadata)
    parameters_text = str(paths.get("parameters_path", "") or "")
    if parameters_text:
        parameters_path = Path(parameters_text)
        parameters_path.parent.mkdir(parents=True, exist_ok=True)
        parameters_path.write_text(
            json.dumps(_default_parameters(sample_record), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    rois_text = str(paths.get("rois_path", "") or "")
    if rois_text:
        rois_path = Path(rois_text)
    else:
        rois_path = None
    if rois_path and not rois_path.exists():
        rois_path.write_text("[]\n", encoding="utf-8")
    measurements_text = str(paths.get("roi_measurements_path", "") or "")
    if measurements_text:
        roi_measurements_path = Path(measurements_text)
    else:
        roi_measurements_path = None
    if roi_measurements_path and not roi_measurements_path.exists():
        roi_measurements_path.write_text("sample_id,roi_id,channel,measurement,value\n", encoding="utf-8")
    return {k: v for k, v in paths.items() if k.endswith("_path")}


def save_project_config(config_dict: dict[str, Any], output_path: str | Path) -> str:
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config_dict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path.resolve())


def load_project_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def _project_file_path(project_path: str | Path, analysis_dir: str | Path | None = None) -> Path:
    raw = str(project_path or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            return (path / PROJECT_FILE_NAME).resolve()
        if path.suffix.lower() in {".dphistology", ".json"}:
            return path.resolve()
        if not path.suffix:
            return (path / PROJECT_FILE_NAME).resolve()
        return path.resolve()
    if analysis_dir:
        return (Path(analysis_dir).expanduser().resolve() / PROJECT_FILE_NAME).resolve()
    raise FileNotFoundError("Project file or analysis folder is required")


def _analysis_root(project_path: Path, analysis_dir: str | Path | None) -> Path:
    raw = str(analysis_dir or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (project_path.parent / "analysis").resolve()


def _primary_display_image(sample: SampleRecord) -> str:
    for preferred in ("Brightfield", "Hoechst", "Mito"):
        if preferred in sample.image_files:
            return sample.image_files[preferred]
    if sample.image_files:
        return next(iter(sample.image_files.values()))
    return ""


def _entry_id_for_sample(sample: SampleRecord) -> str:
    import hashlib

    digest = hashlib.sha1(sample.sample_id.encode("utf-8")).hexdigest()
    return f"sample_{digest[:16]}"


def _project_entry(sample: SampleRecord, project_path: Path) -> dict[str, Any]:
    image_path = _primary_display_image(sample)
    entry_id = _entry_id_for_sample(sample)
    paths = sample.metadata
    return {
        "entry_id": entry_id,
        "record_type": "sample",
        "sample_id": sample.sample_id,
        "image_name": sample.sample_id,
        "display_name": sample.sample_id,
        "case_name": sample.sample_id,
        "source_name": Path(image_path).name if image_path else "",
        "image_path": image_path,
        "source_path": image_path,
        "image_files": dict(sample.image_files),
        "image_records": [asdict(image) for image in sample.images],
        "raw_olympus_reference": sample.raw_olympus_reference,
        "analysis_folder": sample.analysis_folder,
        "manifest_path": paths.get("manifest_path", ""),
        "parameters_path": paths.get("parameters_path", ""),
        "roi_measurements_path": paths.get("roi_measurements_path", ""),
        "rois_path": paths.get("rois_path", ""),
        "qc_overlay_path": paths.get("qc_overlay_path", ""),
        "warnings": sorted(set(sample.warnings)),
        "role": "sample",
        "format": Path(image_path).suffix.lower().lstrip(".") if image_path else "sample",
        "roi_count": 0,
        "analysis_count": 0,
        "analysis_path": str(project_path.with_name(f"{project_path.stem}.dataprocess_histology") / "images" / entry_id / "analysis.json"),
        "geojson_path": str(project_path.with_name(f"{project_path.stem}.dataprocess_histology") / "images" / entry_id / "rois.geojson"),
        "latest_analysis_at": "",
        "added_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def scan_exported_tiff_project(
    exported_dir: str | Path,
    raw_dir: str | Path | None = None,
    analysis_dir: str | Path | None = None,
) -> dict[str, Any]:
    exported_root = Path(exported_dir).expanduser().resolve()
    image_files = discover_image_files(exported_root)
    samples = group_images_by_sample(image_files)
    raw_index = scan_raw_olympus_folder(raw_dir)
    raw_ref = str(Path(raw_dir).expanduser().resolve()) if str(raw_dir or "").strip() else ""
    analysis_root = (
        Path(analysis_dir).expanduser().resolve() if str(analysis_dir or "").strip() else None
    )
    for sample in samples.values():
        sample.raw_olympus_reference = raw_ref
        if analysis_root is not None:
            sample.analysis_folder = str((analysis_root / sample.sample_id).resolve())
            sample.metadata.update(_sample_analysis_paths(sample.sample_id, Path(sample.analysis_folder)))
    warnings = sorted({warning for sample in samples.values() for warning in sample.warnings})
    return {
        "ok": True,
        "kind": PROJECT_KIND,
        "protocol": PROJECT_PROTOCOL,
        "exported_dir": str(exported_root),
        "raw_dir": raw_ref,
        "analysis_dir": str(analysis_root) if analysis_root is not None else "",
        "image_count": len(image_files),
        "sample_count": len(samples),
        "raw_olympus_file_count": int(len(raw_index)),
        "samples": [asdict(sample) for sample in samples.values()],
        "warnings": warnings,
    }


def create_project_from_exported_tiff(
    project_path: str | Path,
    exported_dir: str | Path,
    raw_dir: str | Path | None = None,
    analysis_dir: str | Path | None = None,
    name: str = "",
) -> dict[str, Any]:
    project_file = _project_file_path(project_path, analysis_dir)
    analysis_root = _analysis_root(project_file, analysis_dir)
    scan = scan_exported_tiff_project(exported_dir, raw_dir=raw_dir, analysis_dir=analysis_root)
    image_files = discover_image_files(exported_dir)
    samples = group_images_by_sample(image_files)
    raw_ref = str(Path(raw_dir).expanduser().resolve()) if str(raw_dir or "").strip() else ""
    for sample in samples.values():
        sample.raw_olympus_reference = raw_ref
    samples = create_analysis_folders(samples, analysis_root)
    for sample in samples.values():
        export_file_manifest(sample)
        export_sample_placeholders(sample)

    raw_index_path = ""
    if raw_ref:
        raw_index = scan_raw_olympus_folder(raw_ref)
        raw_index_path = str((analysis_root / "raw_olympus_index.csv").resolve())
        raw_index.to_csv(raw_index_path, index=False)

    entries = [_project_entry(sample, project_file) for sample in samples.values()]
    payload = {
        "version": 1,
        "protocol": PROJECT_PROTOCOL,
        "kind": PROJECT_KIND,
        "project_name": str(name or project_file.stem),
        "project_path": str(project_file),
        "project_root": str(project_file.parent),
        "exported_dir": str(Path(exported_dir).expanduser().resolve()),
        "raw_dir": raw_ref,
        "analysis_dir": str(analysis_root),
        "raw_olympus_index_path": raw_index_path,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "entry_count": len(entries),
        "images": entries,
        "samples": [asdict(sample) for sample in samples.values()],
        "warnings": scan.get("warnings", []),
    }
    save_project_config(payload, project_file)
    return {
        "ok": True,
        **payload,
        "entries": entries,
        "raw_olympus_file_count": scan.get("raw_olympus_file_count", 0),
    }


__all__ = [
    "ImageRecord",
    "SampleRecord",
    "create_analysis_folders",
    "create_project_from_exported_tiff",
    "detect_channel_from_filename",
    "discover_image_files",
    "export_file_manifest",
    "group_images_by_sample",
    "infer_sample_id",
    "load_image_for_analysis",
    "load_image_for_display",
    "load_project_config",
    "save_project_config",
    "scan_exported_tiff_project",
    "scan_raw_olympus_folder",
]
