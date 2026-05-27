"""Matplotlib rendering helpers for figure-generator outputs."""

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, ScalarFormatter

from services.matplotlib_utils import close_figure, new_figure, prop_cycle_colors

from .constants import DPI, EPS
from .summary import _clip_to_range, _min_positive_x


def _plot_linear(groups, ylabel, title_txt, xmin, xmax):
    fig = new_figure(figsize=(6, 4.5), dpi=DPI)
    ax = fig.subplots()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        close_figure(fig)
        return None
    ax.set_xlabel("Power density (mW/mm^2)")
    ax.set_ylabel(ylabel)
    ax.set_xlim(xmin, xmax)
    ax.set_title(title_txt)
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, borderaxespad=0.0)
    fig.tight_layout(rect=(0.0, 0.0, 0.78, 1.0))
    return fig


def _plot_log(groups, ylabel, title_txt, xmin, xmax):
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = new_figure(figsize=(6, 4.5), dpi=DPI)
    ax = fig.subplots()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        close_figure(fig)
        return None

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)
    ax.set_xlabel("Power density (mW/mm^2, log scale)")
    ax.set_ylabel(ylabel)
    ax.set_title(title_txt + "  (log x)")
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, borderaxespad=0.0)
    fig.tight_layout(rect=(0.0, 0.0, 0.78, 1.0))
    return fig


def _plot_linear_svg_plotonly(groups, xmin, xmax, out_path):
    fig = new_figure(figsize=(6, 4.5), dpi=DPI)
    ax = fig.subplots()
    plotted = False
    for _, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
        )
        plotted = True

    if not plotted:
        close_figure(fig)
        return False

    ax.set_xlim(xmin, xmax)
    ax.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    close_figure(fig)
    return True


def _plot_log_svg_plotonly(groups, xmin, xmax, out_path):
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = new_figure(figsize=(6, 4.5), dpi=DPI)
    ax = fig.subplots()
    plotted = False
    for _, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
        )
        plotted = True

    if not plotted:
        close_figure(fig)
        return False

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    close_figure(fig)
    return True


def _legend_svg_only_no_text(groups, out_path):
    labels = list(groups.keys())
    if not labels:
        return False

    colors = prop_cycle_colors()

    handles = []
    for i, _lab in enumerate(labels):
        c = colors[i % len(colors)]
        handles.append(
            Line2D([], [], color=c, marker="o", linestyle="-", linewidth=1.2, markersize=3.5)
        )

    fig = new_figure(figsize=(1.1, 0.35 * max(1, len(handles))), dpi=DPI)
    ax = fig.subplots()
    ax.legend(
        handles=handles,
        labels=[""] * len(handles),
        loc="center",
        frameon=False,
        handlelength=1.6,
        handletextpad=0.0,
        borderaxespad=0.0,
        labelspacing=0.35,
    )
    ax.axis("off")
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    close_figure(fig)
    return True
