from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from services.histology_tiff_discovery import _file_size_mb, _now_iso, scan_raw_olympus_folder
from services.histology_tiff_grouping import (
    _attach_conversion_metadata,
    _conversion_dicts,
    _conversion_warnings,
    _prepare_image_sources,
    _successful_conversion_outputs,
    group_images_by_sample,
)
from services.histology_tiff_io import TIFF_SUFFIXES
from services.histology_tiff_models import (
    PROJECT_FILE_NAME,
    PROJECT_KIND,
    PROJECT_PROTOCOL,
    SampleRecord,
)


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
    "_analysis_root",
    "_default_parameters",
    "_entry_id_for_sample",
    "_manifest_rows",
    "_primary_display_image",
    "_project_entry",
    "_project_file_path",
    "_sample_analysis_paths",
    "create_analysis_folders",
    "create_project_from_exported_tiff",
    "export_file_manifest",
    "export_sample_placeholders",
    "load_project_config",
    "save_project_config",
    "scan_exported_tiff_project",
]
