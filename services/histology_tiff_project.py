from __future__ import annotations

import csv
import json
import os
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

from services.histology_ets_convert import (
    EtsConversionResult,
    convert_ets_folder_to_tiff,
    iter_ets_files,
)

SUPPORTED_IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
TIFF_SUFFIXES = {".tif", ".tiff"}
RAW_OLYMPUS_SUFFIXES = {".vsi", ".ets"}
PROJECT_KIND = "dataprocess_histology_project"
PROJECT_PROTOCOL = "dataprocess-tiff-histology"
PROJECT_FILE_NAME = "histology_project.dphistology"
DEFAULT_MAX_IMAGE_LOAD_BYTES = 1200 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 300_000_000


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


def _conversion_dicts(conversions: list[EtsConversionResult]) -> list[dict[str, Any]]:
    return [asdict(item) for item in conversions]


def _successful_conversion_outputs(conversions: list[EtsConversionResult]) -> list[Path]:
    outputs: list[Path] = []
    for item in conversions:
        if item.status not in {"converted", "skipped_existing", "skipped_existing_tiff"}:
            continue
        output = Path(item.output_path).expanduser()
        if output.is_file():
            outputs.append(output.resolve())
    return outputs


def _conversion_warnings(conversions: list[EtsConversionResult]) -> list[str]:
    warnings: list[str] = []
    for item in conversions:
        for message in item.warning_messages:
            warnings.append(f"{Path(item.source_path).name}: {message}")
    return warnings


def _conversion_analysis_outputs(conversions: list[EtsConversionResult]) -> list[Path]:
    role_by_output = {
        str(Path(item.output_path).expanduser().resolve()): item.role
        for item in conversions
    }
    return [
        path
        for path in _successful_conversion_outputs(conversions)
        if role_by_output.get(str(path), "") not in {"overview", "label"}
    ]


def _analysis_image_paths(image_files: list[Path]) -> list[Path]:
    return [
        path
        for path in image_files
        if not _looks_like_stack_derivative(path)
        if str(detect_channel_from_filename(path.name).get("detected_channel") or "")
        not in {"Overview", "Label"}
    ]


def _prepare_image_sources(
    source_dir: str | Path,
    *,
    convert_ets: bool = True,
    progress=None,
) -> tuple[Path, list[Path], list[EtsConversionResult]]:
    source_root = Path(source_dir).expanduser().resolve()
    conversions: list[EtsConversionResult] = []
    if not source_root.exists():
        # Let the normal image discovery path raise the established user-facing
        # file/folder error for unsupported or missing paths.
        return source_root, discover_image_files(source_root), conversions
    ets_files = iter_ets_files(source_root) if convert_ets else []
    if convert_ets and (source_root.suffix.lower() == ".ets" or ets_files):
        conversions = convert_ets_folder_to_tiff(source_root, progress=progress)
    if source_root.is_file() and source_root.suffix.lower() == ".ets":
        image_files = _conversion_analysis_outputs(conversions)
    else:
        image_files = _analysis_image_paths(discover_image_files(source_root))
        converted = _conversion_analysis_outputs(conversions)
        seen = {str(path.resolve()) for path in image_files}
        for path in converted:
            key = str(path.resolve())
            if key not in seen:
                image_files.append(path)
                seen.add(key)
    image_files.sort(key=lambda item: str(item).lower())
    return source_root, image_files, conversions


def _vsi_metadata_for_case(case_dir: str | Path) -> dict[str, Any]:
    case = Path(case_dir).expanduser()
    if not case.is_dir():
        return {"associated_files": [], "label_vsi_path": "", "overview_vsi_path": ""}
    associated: list[dict[str, str]] = []
    label_vsi_path = ""
    overview_vsi_path = ""
    for vsi in sorted(case.glob("*.vsi")):
        role = "overview_vsi" if "overview" in vsi.stem.lower() else "label_vsi"
        payload = {"role": role, "path": str(vsi.resolve()), "name": vsi.name}
        associated.append(payload)
        if role == "overview_vsi" and not overview_vsi_path:
            overview_vsi_path = str(vsi.resolve())
        elif role == "label_vsi" and not label_vsi_path:
            label_vsi_path = str(vsi.resolve())
    return {
        "associated_files": associated,
        "label_vsi_path": label_vsi_path,
        "overview_vsi_path": overview_vsi_path,
    }


def _attach_conversion_metadata(
    samples: dict[str, SampleRecord],
    conversions: list[EtsConversionResult],
) -> None:
    by_sample: dict[str, list[EtsConversionResult]] = {}
    for item in conversions:
        if item.status not in {"converted", "skipped_existing", "skipped_existing_tiff"}:
            continue
        sample_id = Path(item.case_dir).name
        by_sample.setdefault(sample_id, []).append(item)
    for sample_id, items in by_sample.items():
        sample = samples.get(sample_id)
        if sample is None:
            continue
        primary = items[0]
        converted_payload = _conversion_dicts(items)
        sample.metadata["case_dir"] = primary.case_dir
        sample.metadata["physical_rename_dir"] = primary.case_dir
        sample.metadata["converted_from_ets"] = converted_payload
        sample.metadata["converted_tiff_paths"] = [item.output_path for item in items]
        sample.metadata["conversion_roles"] = {item.role: item.output_path for item in items}
        sample.metadata["ets_conversion_count"] = len(items)
        sample.metadata.update(_vsi_metadata_for_case(primary.case_dir))


def detect_channel_from_filename(filename: str) -> dict[str, Any]:
    stem = Path(filename).stem.lower()
    patterns: list[tuple[str, tuple[str, ...], float]] = [
        ("Overview", ("overview",), 0.98),
        ("Label", ("label", "barcode"), 0.98),
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


def _looks_like_stack_derivative(path: str | Path) -> bool:
    stem = Path(path).stem.lower()
    return bool(
        re.search(r"(?:^|[_\-\s])tray\d+[_\-\s]*slide.*[_\-\s]stack\d+(?:$|[_\-\s])", stem)
    )


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return int(default)
    return max(0, value)


def _max_image_load_bytes() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_IMAGE_LOAD_BYTES", DEFAULT_MAX_IMAGE_LOAD_BYTES)


def _max_image_pixels() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_IMAGE_PIXELS", DEFAULT_MAX_IMAGE_PIXELS)


def _shape_sample_count(shape: tuple[int, ...]) -> int:
    count = 1
    for dim in shape:
        count *= max(1, int(dim))
    return int(count)


def _shape_pixel_count(shape: tuple[int, ...]) -> int:
    if len(shape) >= 2:
        return int(max(1, int(shape[0])) * max(1, int(shape[1])))
    return _shape_sample_count(shape)


def _format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.2f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f} MB"
    return f"{value:,} bytes"


def _load_image_array_unchecked(path: Path) -> np.ndarray:
    path = Path(path).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image extension for analysis: {path.suffix}")
    if suffix in TIFF_SUFFIXES:
        if tifffile is None:
            raise RuntimeError("tifffile is required to load TIFF images")
        return np.asarray(tifffile.imread(str(path)))
    with Image.open(path) as img:
        return np.asarray(img)


def estimate_image_load_size(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    dtype, shape = _read_image_metadata(path)
    try:
        itemsize = int(np.dtype(dtype).itemsize)
    except Exception:
        itemsize = 1
    sample_count = _shape_sample_count(shape)
    return {
        "path": str(path),
        "dtype": dtype,
        "shape": shape,
        "pixel_count": _shape_pixel_count(shape),
        "sample_count": sample_count,
        "estimated_bytes": int(sample_count * max(1, itemsize)),
    }


def _guard_image_load(path: Path) -> None:
    meta = estimate_image_load_size(path)
    max_pixels = _max_image_pixels()
    max_bytes = _max_image_load_bytes()
    pixels = int(meta["pixel_count"])
    byte_count = int(meta["estimated_bytes"])
    if max_pixels and pixels > max_pixels:
        raise ValueError(
            f"Image is too large to load safely ({pixels:,} pixels; limit {max_pixels:,}). "
            "Export or downsample an ROI TIFF, or raise DP_HISTOLOGY_MAX_IMAGE_PIXELS."
        )
    if max_bytes and byte_count > max_bytes:
        raise ValueError(
            f"Image is too large to load safely ({_format_bytes(byte_count)}; "
            f"limit {_format_bytes(max_bytes)}). Export or downsample an ROI TIFF, "
            "or raise DP_HISTOLOGY_MAX_IMAGE_LOAD_BYTES."
        )


def load_image_for_analysis(file_path: str | Path) -> np.ndarray:
    """Load image values without normalization. TIFF uses tifffile; PNG/JPG uses Pillow."""
    path = Path(file_path).expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image extension for analysis: {path.suffix}")
    _guard_image_load(path)
    return _load_image_array_unchecked(path)


def _bit_depth_for_dtype(dtype: np.dtype) -> int:
    dtype = np.dtype(dtype)
    if dtype.kind in {"u", "i"}:
        return int(dtype.itemsize * 8)
    if dtype.kind == "f":
        return 32 if dtype.itemsize <= 4 else 64
    return 0


def _bit_depth_for_array(arr: np.ndarray) -> int:
    return _bit_depth_for_dtype(np.dtype(arr.dtype))


# PIL image mode -> (channel count, numpy dtype string) used to derive shape/dtype
# without decoding the full pixel buffer during a scan.
_PIL_MODE_INFO: dict[str, tuple[int, str]] = {
    "1": (1, "bool"),
    "L": (1, "uint8"),
    "P": (1, "uint8"),
    "I": (1, "int32"),
    "I;16": (1, "uint16"),
    "I;16L": (1, "uint16"),
    "I;16B": (1, "uint16"),
    "I;16N": (1, "uint16"),
    "F": (1, "float32"),
    "LA": (2, "uint8"),
    "RGB": (3, "uint8"),
    "RGBa": (4, "uint8"),
    "YCbCr": (3, "uint8"),
    "LAB": (3, "uint8"),
    "HSV": (3, "uint8"),
    "RGBA": (4, "uint8"),
    "CMYK": (4, "uint8"),
}


def _read_image_metadata(path: Path) -> tuple[str, tuple[int, ...]]:
    """Read dtype and shape cheaply, without decoding the full pixel buffer."""
    suffix = path.suffix.lower()
    if suffix in TIFF_SUFFIXES and tifffile is not None:
        with tifffile.TiffFile(str(path)) as tf:
            source = tf.series[0] if getattr(tf, "series", None) else tf.pages[0]
            shape = tuple(int(x) for x in source.shape)
            dtype = np.dtype(source.dtype)
        return str(dtype), shape
    with Image.open(path) as img:
        width, height = img.size
        channels, dtype_str = _PIL_MODE_INFO.get(
            img.mode,
            (max(1, len(getattr(img, "getbands", lambda: ())())), "uint8"),
        )
    shape = (height, width) if channels <= 1 else (height, width, channels)
    return dtype_str, shape


def _shape_warnings(path: Path, shape: tuple[int, ...], bit_depth: int) -> list[str]:
    warnings: list[str] = []
    suffix = path.suffix.lower()
    if suffix not in TIFF_SUFFIXES:
        warnings.append("Non-TIFF image; use 16-bit TIFF for quantitative fluorescence.")
    ndim = len(shape)
    if ndim == 3:
        if shape[-1] <= 4:
            warnings.append("Multi-channel/color image stored in one file; expected single-channel XY.")
        else:
            warnings.append("Image has more than XY dimensions; expected 2D exported image.")
    elif ndim != 2:
        warnings.append("Image has unsupported dimensionality; expected XY only.")
    if suffix in TIFF_SUFFIXES and bit_depth < 16:
        warnings.append("TIFF is not 16-bit; confirm it is suitable for quantification.")
    return warnings


def _record_for_image(path: Path) -> ImageRecord:
    detection = detect_channel_from_filename(path.name)
    try:
        dtype, shape = _read_image_metadata(path)
        bit_depth = _bit_depth_for_dtype(np.dtype(dtype)) if dtype else 0
        warnings = _shape_warnings(path, shape, bit_depth)
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
        if _looks_like_stack_derivative(path):
            continue
        sample_id = infer_sample_id(path.name)
        record = _record_for_image(path)
        if record.detected_channel in {"Overview", "Label"}:
            continue
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
        "notes": "Generated from readable XY TIFF/images. Olympus ETS sources are converted before analysis when present.",
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
    for preferred in ("Hoechst", "FITC", "Cy5", "Mito", "Brightfield"):
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
        "case_dir": paths.get("case_dir", ""),
        "physical_rename_dir": paths.get("physical_rename_dir", ""),
        "converted_from_ets": paths.get("converted_from_ets", []),
        "converted_tiff_paths": paths.get("converted_tiff_paths", []),
        "conversion_roles": paths.get("conversion_roles", {}),
        "ets_conversion_count": paths.get("ets_conversion_count", 0),
        "associated_files": paths.get("associated_files", []),
        "label_vsi_path": paths.get("label_vsi_path", ""),
        "overview_vsi_path": paths.get("overview_vsi_path", ""),
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
    convert_ets: bool = True,
    progress=None,
) -> dict[str, Any]:
    exported_root, image_files, conversions = _prepare_image_sources(
        exported_dir,
        convert_ets=convert_ets,
        progress=progress,
    )
    samples = group_images_by_sample(image_files)
    _attach_conversion_metadata(samples, conversions)
    raw_ref = str(Path(raw_dir).expanduser().resolve()) if str(raw_dir or "").strip() else ""
    if not raw_ref and conversions:
        raw_ref = str(exported_root.parent if exported_root.is_file() else exported_root)
    raw_index = scan_raw_olympus_folder(raw_ref) if raw_ref else pd.DataFrame()
    analysis_root = (
        Path(analysis_dir).expanduser().resolve() if str(analysis_dir or "").strip() else None
    )
    for sample in samples.values():
        sample.raw_olympus_reference = raw_ref
        if analysis_root is not None:
            sample.analysis_folder = str((analysis_root / sample.sample_id).resolve())
            sample.metadata.update(_sample_analysis_paths(sample.sample_id, Path(sample.analysis_folder)))
    warnings = sorted(
        {
            warning
            for warning in [
                *[warning for sample in samples.values() for warning in sample.warnings],
                *_conversion_warnings(conversions),
            ]
            if warning
        }
    )
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
        "ets_conversion_count": len([item for item in conversions if item.status == "converted"]),
        "ets_converted_file_count": len(_successful_conversion_outputs(conversions)),
        "ets_conversions": _conversion_dicts(conversions),
        "samples": [asdict(sample) for sample in samples.values()],
        "warnings": warnings,
    }


def create_project_from_exported_tiff(
    project_path: str | Path,
    exported_dir: str | Path,
    raw_dir: str | Path | None = None,
    analysis_dir: str | Path | None = None,
    name: str = "",
    convert_ets: bool = True,
    progress=None,
) -> dict[str, Any]:
    project_file = _project_file_path(project_path, analysis_dir)
    analysis_root = _analysis_root(project_file, analysis_dir)
    # Discover and read each exported image exactly once; the previous
    # implementation scanned (read every image) and then re-grouped (read them
    # again), doubling disk I/O on whole-slide TIFFs.
    exported_root, image_files, conversions = _prepare_image_sources(
        exported_dir,
        convert_ets=convert_ets,
        progress=progress,
    )
    samples = group_images_by_sample(image_files)
    _attach_conversion_metadata(samples, conversions)
    raw_ref = str(Path(raw_dir).expanduser().resolve()) if str(raw_dir or "").strip() else ""
    if not raw_ref and conversions:
        raw_ref = str(exported_root.parent if exported_root.is_file() else exported_root)
    for sample in samples.values():
        sample.raw_olympus_reference = raw_ref
    samples = create_analysis_folders(samples, analysis_root)
    for sample in samples.values():
        export_file_manifest(sample)
        export_sample_placeholders(sample)

    raw_index_path = ""
    raw_file_count = 0
    if raw_ref:
        raw_index = scan_raw_olympus_folder(raw_ref)
        raw_file_count = int(len(raw_index))
        raw_index_path = str((analysis_root / "raw_olympus_index.csv").resolve())
        raw_index.to_csv(raw_index_path, index=False)

    warnings = sorted(
        {
            warning
            for warning in [
                *[warning for sample in samples.values() for warning in sample.warnings],
                *_conversion_warnings(conversions),
            ]
            if warning
        }
    )
    entries = [_project_entry(sample, project_file) for sample in samples.values()]
    payload = {
        "version": 1,
        "protocol": PROJECT_PROTOCOL,
        "kind": PROJECT_KIND,
        "project_name": str(name or project_file.stem),
        "project_path": str(project_file),
        "project_root": str(project_file.parent),
        "exported_dir": str(exported_root),
        "raw_dir": raw_ref,
        "analysis_dir": str(analysis_root),
        "raw_olympus_index_path": raw_index_path,
        "ets_conversion_count": len([item for item in conversions if item.status == "converted"]),
        "ets_converted_file_count": len(_successful_conversion_outputs(conversions)),
        "ets_conversions": _conversion_dicts(conversions),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "entry_count": len(entries),
        "images": entries,
        "samples": [asdict(sample) for sample in samples.values()],
        "warnings": warnings,
    }
    save_project_config(payload, project_file)
    return {
        "ok": True,
        **payload,
        "entries": entries,
        "raw_olympus_file_count": raw_file_count,
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
    "convert_ets_folder_to_tiff",
    "scan_exported_tiff_project",
    "scan_raw_olympus_folder",
]
