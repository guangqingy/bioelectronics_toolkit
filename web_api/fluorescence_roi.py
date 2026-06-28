from __future__ import annotations

from .fluorescence_roi_basic import register_fluorescence_roi_basic_routes
from .fluorescence_roi_export import register_fluorescence_roi_export_routes
from .fluorescence_roi_sequence import register_fluorescence_roi_sequence_routes


def register_fluorescence_roi_routes(app, fl):
    register_fluorescence_roi_basic_routes(app, fl)
    register_fluorescence_roi_sequence_routes(app, fl)
    register_fluorescence_roi_export_routes(app, fl)
