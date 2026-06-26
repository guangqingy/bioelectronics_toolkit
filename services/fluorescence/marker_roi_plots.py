from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from services.fluorescence.marker_roi_tables import sd
from services.matplotlib_utils import close_figure, new_subplots


def make_mouse_plot(mouse_rows: Sequence[dict[str, str]], stats_table: Sequence[dict[str, str]], output_path: Path) -> None:
    metrics = [
        ("mean_sma_area_fraction", "SMA closed area fraction"),
        ("mean_macrophage_area_fraction", "Macrophage area fraction"),
        ("mean_dapi_nuclei_density_per_mm2", "DAPI nuclei density / mm2"),
    ]
    stat_by_metric = {row["metric"]: row for row in stats_table}
    fig, axes = new_subplots(1, 3, figsize=(11.4, 3.8), dpi=220)
    order = [("C", "Control", "#2f6f9f"), ("D", "Device", "#c23b3b")]
    for ax, (metric, title) in zip(axes, metrics):
        for idx, (code, _label, color) in enumerate(order):
            group_rows = [row for row in mouse_rows if row["condition_code"] == code]
            values = np.asarray([float(row[metric]) for row in group_rows if row.get(metric)], dtype=float)
            values = values[np.isfinite(values)]
            mouse_ids = [row["mouse_id"] for row in group_rows]
            offsets = np.linspace(-0.07, 0.07, len(values)) if len(values) > 1 else np.asarray([0.0])
            ax.scatter(np.full(len(values), idx) + offsets, values, s=44, color=color, edgecolor="white", linewidth=0.8, zorder=3)
            for x, y, mouse_id in zip(np.full(len(values), idx) + offsets, values, mouse_ids):
                ax.text(x, y, mouse_id, fontsize=6.5, ha="center", va="bottom", color="#333333")
            if values.size:
                ax.errorbar(idx, float(np.mean(values)), yerr=sd(values), fmt="_", color="#111111", markersize=20, markeredgewidth=2, capsize=4)
        p_text = ""
        stat = stat_by_metric.get(metric)
        if stat and stat.get("student_p"):
            p_text = f"Student p={float(stat['student_p']):.3g}"
        ax.set_title(f"{title}\n{p_text}", fontsize=9)
        ax.set_xticks([0, 1], ["Control", "Device"])
        ax.grid(axis="y", color="#dddddd", linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".pdf"), bbox_inches="tight")
    close_figure(fig)


def make_normalized_mouse_plot(normalized_rows: Sequence[dict[str, str]], output_path: Path) -> None:
    metrics = [
        ("mean_sma_area_fraction", "SMA area"),
        ("mean_macrophage_area_fraction", "Macrophage area"),
        ("mean_dapi_nuclei_density_per_mm2", "DAPI nuclei density"),
    ]
    fig, axes = new_subplots(1, 3, figsize=(11.4, 3.6), dpi=220)
    order = [("C", "Control", "#2f6f9f"), ("D", "Device", "#c23b3b")]
    for ax, (metric, title) in zip(axes, metrics):
        rows_for_metric = [row for row in normalized_rows if row["metric"] == metric]
        for idx, (code, _label, color) in enumerate(order):
            group_rows = [row for row in rows_for_metric if row["condition_code"] == code]
            values = np.asarray([float(row["normalized_to_control"]) for row in group_rows if row.get("normalized_to_control")], dtype=float)
            values = values[np.isfinite(values)]
            mouse_ids = [row["mouse_id"] for row in group_rows]
            offsets = np.linspace(-0.07, 0.07, len(values)) if len(values) > 1 else np.asarray([0.0])
            ax.scatter(np.full(len(values), idx) + offsets, values, s=44, color=color, edgecolor="white", linewidth=0.8, zorder=3)
            for x, y, mouse_id in zip(np.full(len(values), idx) + offsets, values, mouse_ids):
                ax.text(x, y, mouse_id, fontsize=6.5, ha="center", va="bottom", color="#333333")
            if values.size:
                ax.errorbar(idx, float(np.mean(values)), yerr=sd(values), fmt="_", color="#111111", markersize=20, markeredgewidth=2, capsize=4)
        ax.axhline(1.0, color="#666666", linewidth=0.9, linestyle="--")
        ax.set_title(title, fontsize=9)
        ax.set_xticks([0, 1], ["Control", "Device"])
        ax.set_ylabel("normalized to Control mean")
        ax.grid(axis="y", color="#dddddd", linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".pdf"), bbox_inches="tight")
    close_figure(fig)
