from __future__ import annotations

"""
Histology service facade.

Functional implementation lives in smaller modules:
- histology_common: small parsing and naming helpers
- histology_discovery: case discovery and QuPath display-name reads
- histology_preview: image preview, label preview, and OCR
- histology_qupath: case renaming and QuPath project synchronization
- histology_analysis: legacy QuPath project ROI analysis helpers
- histology_ets_analysis: DataProcess histology project and single-file ROI analysis
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
    read_qupath_display_name,
)
from services.histology_ets_analysis import (
    add_histology_data_project_paths,
    analyze_histology_data_project_rois,
    analyze_histology_file_rois,
    create_histology_data_project,
    load_histology_data_project,
    load_histology_data_project_image_preview,
    load_histology_file_image_preview,
    rename_histology_data_project_entry,
    save_histology_data_project_rois,
)
from services.histology_preview import image_to_b64, load_histology_preview_pair
from services.histology_qupath import rename_histology_case, sync_qupath_names_from_histology_cases

__all__ = [
    "_bool",
    "_candidate_overview_files",
    "_int",
    "_normalize_rotate_deg",
    "add_histology_data_project_paths",
    "analyze_histology_file_rois",
    "analyze_histology_data_project_rois",
    "candidate_overview_files",
    "create_histology_data_project",
    "find_histology_cases",
    "image_to_b64",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_file_image_preview",
    "load_histology_preview_pair",
    "normalize_rotate_deg",
    "parse_bool",
    "parse_int",
    "read_qupath_display_name",
    "rename_histology_data_project_entry",
    "rename_histology_case",
    "save_histology_data_project_rois",
    "sanitize_name",
    "sync_qupath_names_from_histology_cases",
]
