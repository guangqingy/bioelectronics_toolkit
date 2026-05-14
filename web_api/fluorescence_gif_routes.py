from __future__ import annotations

from .fluorescence_gif_basic_routes import register_fluorescence_gif_basic_routes
from .fluorescence_gif_kymograph_routes import register_fluorescence_gif_kymograph_routes
from .fluorescence_gif_roi_analysis_routes import register_fluorescence_gif_roi_analysis_routes


def register_fluorescence_gif_routes(app, fl):
    register_fluorescence_gif_basic_routes(app, fl)
    register_fluorescence_gif_roi_analysis_routes(app, fl)
    register_fluorescence_gif_kymograph_routes(app, fl)
