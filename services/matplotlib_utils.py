from __future__ import annotations

from typing import Any

from matplotlib import rcParams
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


DEFAULT_RCPARAMS = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.8,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "grid.color": "#EEEEEE",
    "grid.linewidth": 0.5,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
}


def configure_defaults() -> None:
    rcParams.update(DEFAULT_RCPARAMS)


def new_figure(
    *,
    figsize: tuple[float, float] | None = None,
    dpi: float | None = None,
    constrained_layout: bool = False,
) -> Figure:
    fig = Figure(figsize=figsize, dpi=dpi, constrained_layout=constrained_layout)
    FigureCanvasAgg(fig)
    return fig


def new_subplots(
    nrows: int = 1,
    ncols: int = 1,
    *,
    figsize: tuple[float, float] | None = None,
    dpi: float | None = None,
    constrained_layout: bool = False,
    **kwargs: Any,
):
    fig = new_figure(figsize=figsize, dpi=dpi, constrained_layout=constrained_layout)
    axes = fig.subplots(nrows=nrows, ncols=ncols, **kwargs)
    return fig, axes


def close_figure(fig: Any) -> None:
    if fig is not None:
        fig.clear()


def prop_cycle_colors() -> list[str]:
    colors = rcParams["axes.prop_cycle"].by_key().get("color", [])
    return list(colors) if colors else ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]
