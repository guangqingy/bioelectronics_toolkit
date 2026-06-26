from __future__ import annotations

import base64
import csv
from pathlib import Path
from typing import Any

import numpy as np

from services.histology_analysis import ANALYSIS_VERSION, _now_iso, _write_json
from services.histology_batch_core import (
    _boolish,
    _finite_float,
    _normalized_metric_column,
    _scalar_for_table,
)
from services.matplotlib_utils import close_figure, new_subplots


def _write_csv_records(path: Path, rows: list[dict[str, Any]], preferred_fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(preferred_fields)
    seen = set(fieldnames)
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _scalar_for_table(row.get(key, "")) for key in fieldnames})


def _anova_label(stats: dict[str, Any], marker: str) -> str:
    anova = stats.get(marker) if isinstance(stats, dict) else None
    if not isinstance(anova, dict) or anova.get("f") is None or anova.get("p") is None:
        return "ANOVA: n/a"
    p_value = float(anova["p"])
    p_text = f"{p_value:.3g}" if p_value < 0.001 else f"{p_value:.5f}".rstrip("0").rstrip(".")
    return f"ANOVA: F = {float(anova['f']):.3f}, P = {p_text}"


def _plot_color(marker: str, index: int, total: int) -> tuple[float, float, float, float]:
    import matplotlib as mpl

    cmap = mpl.colormaps["Oranges" if marker == "sma" else "Greens"]
    if total <= 1:
        return cmap(0.72)
    return cmap(0.82 - 0.42 * (index / max(1, total - 1)))


def _save_batch_plot(
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    stats: dict[str, Any],
    marker: str,
    out_dir: Path,
    *,
    per_source: bool = False,
) -> dict[str, Any]:
    metric = _normalized_metric_column(marker)
    groups = [str(item["sample_group"]) for item in summary]
    x = np.arange(len(groups), dtype=np.float64)
    means = np.asarray([_finite_float(item.get(f"{marker}_normalized_mean")) for item in summary], dtype=np.float64)
    sems = np.asarray([_finite_float(item.get(f"{marker}_normalized_sem")) for item in summary], dtype=np.float64)
    fig, ax = new_subplots(figsize=(12.8, 6.8), dpi=150)
    colors = [_plot_color(marker, idx, len(groups)) for idx in range(len(groups))]
    ax.bar(x, means, color=colors, edgecolor="black", linewidth=1.25, width=0.6, zorder=2)
    ax.errorbar(x, means, yerr=sems, fmt="none", ecolor="black", elinewidth=1.4, capsize=4, zorder=3)
    letter_colors = {
        "A": "#1f77b4",
        "B": "#ff7f0e",
        "C": "#2ca02c",
        "D": "#d62728",
        "E": "#9467bd",
        "F": "#8c564b",
        "1": "#1f77b4",
        "2": "#ff7f0e",
        "3": "#2ca02c",
        "4": "#d62728",
        "5": "#9467bd",
        "6": "#8c564b",
    }
    plotted_letters: set[str] = set()
    for idx, group in enumerate(groups):
        group_rows = [
            row
            for row in rows
            if str(row.get("sample_group") or "") == group
            and _boolish(row.get(f"{marker}_include", True), default=True)
        ]
        for row_idx, row in enumerate(group_rows):
            y_value = _finite_float(row.get(metric))
            jitter = ((row_idx % 9) - 4) * 0.018
            letter = str(row.get("letter") or "").upper()[:1] or "A"
            if per_source:
                color = letter_colors.get(letter, "#666666")
                label = letter if letter not in plotted_letters else None
                plotted_letters.add(letter)
            else:
                color = "#D9D9D9"
                label = None
            ax.scatter(
                idx + jitter,
                y_value,
                s=38,
                facecolor=color,
                edgecolor="black",
                linewidth=0.45,
                label=label,
                zorder=4,
            )
            if per_source:
                ax.text(idx + jitter, y_value + 0.012, letter, ha="center", va="bottom", fontsize=8)
    marker_label = "SMA" if marker == "sma" else "Macrophage"
    if per_source:
        title = f"{marker_label} positive area ratio (per-image source labeled; normalized)"
    else:
        numeric_groups = all(str(group).strip().isdigit() for group in groups)
        sample_label = f"{groups[0]}-{groups[-1]}" if groups and numeric_groups else ""
        if sample_label:
            title = f"{marker_label} positive area ratio across samples {sample_label} (normalized)"
        else:
            title = f"{marker_label} positive area ratio across treatments (normalized)"
    ax.set_title(title, fontsize=17)
    ax.set_ylabel(f"{marker_label} positive area ratio (normalized to group 1)", fontsize=12)
    ax.set_xticks(x, groups)
    ax.grid(axis="y", linestyle="--", alpha=0.38, zorder=1)
    ax.set_axisbelow(True)
    ymax = 1.0
    values = [*_finite_float_list(means), *[max(0.0, a + b) for a, b in zip(means, sems, strict=False)]]
    for row in rows:
        if _boolish(row.get(f"{marker}_include", True), default=True):
            values.append(_finite_float(row.get(metric)))
    if values:
        ymax = max(1.0, float(np.nanmax(values)))
    ax.set_ylim(0, ymax * 1.18 + 0.05)
    ax.text(
        0.01,
        0.98,
        _anova_label(stats, marker),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=12,
        bbox={"facecolor": "white", "edgecolor": "black", "linewidth": 1.0, "pad": 5},
    )
    if per_source and plotted_letters:
        ax.legend(title="Letter", frameon=False, ncol=min(5, len(plotted_letters)), loc="upper right")
    safe_kind = "per_source" if per_source else "summary"
    png_path = out_dir / f"{marker}_positive_area_ratio_{safe_kind}_normalized.png"
    svg_path = out_dir / f"{marker}_positive_area_ratio_{safe_kind}_normalized.svg"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(svg_path, format="svg", bbox_inches="tight")
    close_figure(fig)
    return {
        "marker": marker,
        "kind": safe_kind,
        "path": str(png_path),
        "svg_path": str(svg_path),
        "img": base64.b64encode(png_path.read_bytes()).decode("ascii"),
    }


def _finite_float_list(values: Any) -> list[float]:
    return [_finite_float(value, default=np.nan) for value in list(values)]


def _write_batch_outputs(
    out_dir: Path,
    roi_rows: list[dict[str, Any]],
    observation_rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    stats: dict[str, Any],
    normalization: dict[str, Any],
    params: dict[str, Any],
    skipped: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    observation_level: str = "image",
    roi_parameter_override_keys: list[str] | None = None,
) -> dict[str, Any]:
    roi_table_path = out_dir / "roi_measurements_normalized.csv"
    image_table_path = out_dir / "image_measurements_normalized.csv"
    summary_table_path = out_dir / "sample_summary_normalized.csv"
    statistics_path = out_dir / "statistics.json"
    manifest_path = out_dir / "manifest.json"
    _write_csv_records(
        roi_table_path,
        roi_rows,
        [
            "sample_group",
            "treatment",
            "sample_number",
            "letter",
            "roi_label",
            "roi_id",
            "image_name",
            "entry_id",
            "roi_parameter_override_key",
            "sma_positive_area_ratio",
            "sma_positive_area_ratio_normalized",
            "sma_include",
            "macrophage_positive_area_ratio",
            "macrophage_positive_area_ratio_normalized",
            "macrophage_include",
            "area_px",
            "analysis_area_px",
        ],
    )
    _write_csv_records(
        image_table_path,
        observation_rows,
        [
            "sample_group",
            "treatment",
            "sample_number",
            "letter",
            "image_name",
            "entry_id",
            "roi_count",
            "sma_positive_area_ratio",
            "sma_positive_area_ratio_normalized",
            "sma_include",
            "macrophage_positive_area_ratio",
            "macrophage_positive_area_ratio_normalized",
            "macrophage_include",
            "roi_labels",
        ],
    )
    _write_csv_records(
        summary_table_path,
        summary,
        [
            "sample_group",
            "n_observations",
            "n_roi",
            "n_entries",
            "sma_n_observations",
            "sma_normalized_mean",
            "sma_normalized_sem",
            "macrophage_n_observations",
            "macrophage_normalized_mean",
            "macrophage_normalized_sem",
        ],
    )
    plots = [
        _save_batch_plot(observation_rows, summary, stats, "sma", out_dir, per_source=False),
        _save_batch_plot(observation_rows, summary, stats, "macrophage", out_dir, per_source=False),
        _save_batch_plot(observation_rows, summary, stats, "sma", out_dir, per_source=True),
        _save_batch_plot(observation_rows, summary, stats, "macrophage", out_dir, per_source=True),
    ]
    stats_payload = {
        "statistics": stats,
        "normalization": normalization,
        "parameters": params,
        "observation_level": observation_level,
    }
    _write_json(statistics_path, stats_payload)
    output_records = [
        {"path": str(roi_table_path), "type": "csv", "role": "histology_roi_measurements_normalized"},
        {"path": str(image_table_path), "type": "csv", "role": "histology_image_measurements_normalized"},
        {"path": str(summary_table_path), "type": "csv", "role": "histology_sample_summary_normalized"},
        {"path": str(statistics_path), "type": "json", "role": "histology_statistics"},
        {"path": str(manifest_path), "type": "json", "role": "histology_batch_manifest"},
    ]
    for plot in plots:
        output_records.append({"path": plot["path"], "type": "png", "role": f"histology_{plot['marker']}_{plot['kind']}_plot"})
        output_records.append({"path": plot["svg_path"], "type": "svg", "role": f"histology_{plot['marker']}_{plot['kind']}_plot"})
    manifest = {
        "version": ANALYSIS_VERSION,
        "kind": "histology_saved_roi_batch_analysis",
        "created_at": _now_iso(),
        "run_dir": str(out_dir),
        "observation_level": observation_level,
        "observation_count": len(observation_rows),
        "roi_count": len(roi_rows),
        "sample_count": len(summary),
        "outputs": output_records,
        "normalization": normalization,
        "statistics": stats,
        "parameters": params,
        "roi_parameter_override_count": len(roi_parameter_override_keys or []),
        "roi_parameter_override_keys": list(roi_parameter_override_keys or []),
        "skipped_entries": skipped,
        "failed_entries": failures,
    }
    _write_json(manifest_path, manifest)
    return {
        "run_dir": str(out_dir),
        "roi_table_path": str(roi_table_path),
        "image_table_path": str(image_table_path),
        "summary_table_path": str(summary_table_path),
        "statistics_path": str(statistics_path),
        "manifest_path": str(manifest_path),
        "plots": plots,
        "outputs": output_records,
    }
