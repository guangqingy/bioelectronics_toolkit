from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from services.histology_ets_convert import (
    EtsConversionResult,
    convert_ets_folder_to_tiff,
    iter_ets_files,
)
from services.histology_tiff_discovery import discover_image_files
from services.histology_tiff_io import (
    SUPPORTED_IMAGE_SUFFIXES,
    _bit_depth_for_dtype,
    _read_image_metadata,
    _shape_warnings,
)
from services.histology_tiff_models import ImageRecord, SampleRecord

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


def _record_for_image(path: Path) -> ImageRecord:
    detection = detect_channel_from_filename(path.name)
    try:
        dtype, shape = _read_image_metadata(path)
        bit_depth = _bit_depth_for_dtype(dtype) if dtype else 0
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

__all__ = [
    "_analysis_image_paths",
    "_attach_conversion_metadata",
    "_conversion_analysis_outputs",
    "_conversion_dicts",
    "_conversion_warnings",
    "_prepare_image_sources",
    "_record_for_image",
    "_successful_conversion_outputs",
    "_vsi_metadata_for_case",
    "detect_channel_from_filename",
    "group_images_by_sample",
    "infer_sample_id",
]
