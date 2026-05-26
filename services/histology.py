from __future__ import annotations

"""
Histology service facade.

Functional implementation lives in smaller modules:
- histology_common: small parsing and naming helpers
- histology_discovery: case discovery and QuPath display-name reads
- histology_preview: image preview, label preview, and OCR
- histology_qupath: case renaming and QuPath project synchronization
- histology_analysis: QuPath project ROI analysis sidecars
- histology_ets_analysis: direct ETS ROI analysis sidecars
"""

from services.histology_analysis import (
    analyze_project_rois,
    load_project_image_preview,
    load_qupath_project,
    save_project_rois,
)
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
    analyze_ets_rois,
    load_ets_image_preview,
    load_ets_project,
    save_ets_rois,
)
from services.histology_preview import image_to_b64, load_histology_preview_pair
from services.histology_qupath import rename_histology_case, sync_qupath_names_from_histology_cases

__all__ = [
    "_bool",
    "_candidate_overview_files",
    "_int",
    "_normalize_rotate_deg",
    "analyze_ets_rois",
    "candidate_overview_files",
    "find_histology_cases",
    "analyze_project_rois",
    "image_to_b64",
    "load_ets_image_preview",
    "load_ets_project",
    "load_project_image_preview",
    "load_histology_preview_pair",
    "load_qupath_project",
    "normalize_rotate_deg",
    "parse_bool",
    "parse_int",
    "read_qupath_display_name",
    "rename_histology_case",
    "save_ets_rois",
    "save_project_rois",
    "sanitize_name",
    "sync_qupath_names_from_histology_cases",
]
