from __future__ import annotations

from services.fluorescence.route_context import build_fluorescence_route_contexts


def register_fluorescence_routes(app, ctx) -> None:
    contexts = build_fluorescence_route_contexts(ctx)

    from .fluorescence_3d import register_fluorescence_3d_routes
    from .fluorescence_gif import register_fluorescence_gif_routes
    from .fluorescence_roi import register_fluorescence_roi_routes
    from .fluorescence_stack import register_fluorescence_stack_routes

    register_fluorescence_stack_routes(app, contexts["stack"])
    register_fluorescence_3d_routes(app, contexts["volume"])
    register_fluorescence_gif_routes(app, contexts["gif"])
    register_fluorescence_roi_routes(app, contexts["roi"])
