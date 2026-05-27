from __future__ import annotations

"""
Histology service facade.

Functional implementation lives in smaller modules:
- histology_common: small parsing and naming helpers
- histology_discovery: case discovery helpers
- histology_preview: image preview, label preview, and OCR
- histology_analysis: ROI analysis helpers
- histology_tiff_project: exported TIFF project discovery and manifests
- histology_project: DataProcess histology project store and single-file ROI analysis
"""

from services.histology_common import (
    _bool,
    _int,
    _normalize_rotate_deg,
    normalize_rotate_deg,
    parse_bool,
    parse_int,
    sanitize_name,
)
from services.histology_discovery import (
    _candidate_overview_files,
    candidate_overview_files,
    find_histology_cases,
)
from services.histology_preview import image_to_b64, load_histology_preview_pair
from services.histology_project import (
    add_histology_data_project_paths,
    analyze_histology_data_project_rois,
    analyze_histology_file_rois,
    create_histology_data_project,
    load_histology_data_project,
    load_histology_data_project_image_preview,
    load_histology_data_project_image_region_preview,
    load_histology_file_image_preview,
    load_histology_file_image_region_preview,
    rename_histology_data_project_entry,
    save_histology_data_project_rois,
)
from services.histology_tiff_project import (
    ImageRecord,
    SampleRecord,
    convert_ets_folder_to_tiff,
    create_analysis_folders,
    create_project_from_exported_tiff,
    detect_channel_from_filename,
    discover_image_files,
    export_file_manifest,
    group_images_by_sample,
    infer_sample_id,
    load_image_for_analysis,
    load_image_for_display,
    load_project_config,
    save_project_config,
    scan_exported_tiff_project,
    scan_raw_olympus_folder,
)

__all__ = [
    "_bool",
    "_candidate_overview_files",
    "_int",
    "_normalize_rotate_deg",
    "ImageRecord",
    "SampleRecord",
    "add_histology_data_project_paths",
    "analyze_histology_file_rois",
    "analyze_histology_data_project_rois",
    "candidate_overview_files",
    "convert_ets_folder_to_tiff",
    "create_histology_data_project",
    "create_analysis_folders",
    "create_project_from_exported_tiff",
    "detect_channel_from_filename",
    "discover_image_files",
    "export_file_manifest",
    "find_histology_cases",
    "group_images_by_sample",
    "image_to_b64",
    "infer_sample_id",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_data_project_image_region_preview",
    "load_histology_file_image_preview",
    "load_histology_file_image_region_preview",
    "load_image_for_analysis",
    "load_image_for_display",
    "load_histology_preview_pair",
    "load_project_config",
    "normalize_rotate_deg",
    "parse_bool",
    "parse_int",
    "rename_histology_data_project_entry",
    "save_histology_data_project_rois",
    "save_project_config",
    "sanitize_name",
    "scan_exported_tiff_project",
    "scan_raw_olympus_folder",
]
