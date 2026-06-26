from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

import numpy as np
from scipy import stats

from services.fluorescence.marker_roi_core import _fmt

IMAGE_COLUMNS = [
    "image_name",
    "image_path",
    "mouse_id",
    "mouse_base",
    "side",
    "condition_code",
    "group",
    "field_id",
    "roi_label",
    "roi_area_px",
    "roi_area_um2",
    "dapi_channel",
    "sma_channel",
    "macrophage_channel",
    "dapi_threshold",
    "dapi_nuclei_count",
    "dapi_nuclei_area_um2",
    "dapi_nuclei_area_fraction",
    "dapi_nuclei_density_per_mm2",
    "sma_threshold",
    "sma_closed_area_px",
    "sma_closed_area_um2",
    "sma_area_fraction",
    "macrophage_threshold",
    "macrophage_min_area_um2",
    "macrophage_min_area_px",
    "macrophage_count",
    "macrophage_area_um2",
    "macrophage_area_fraction",
    "macrophage_density_per_mm2",
]

MOUSE_COLUMNS = [
    "mouse_id",
    "mouse_base",
    "condition_code",
    "group",
    "n_images",
    "mean_roi_area_um2",
    "mean_dapi_nuclei_count",
    "mean_dapi_nuclei_area_fraction",
    "mean_dapi_nuclei_density_per_mm2",
    "mean_sma_closed_area_um2",
    "mean_sma_area_fraction",
    "mean_macrophage_count",
    "mean_macrophage_area_um2",
    "mean_macrophage_area_fraction",
    "mean_macrophage_density_per_mm2",
]

STATS_COLUMNS = [
    "metric",
    "control_n",
    "device_n",
    "control_mean",
    "control_sd",
    "device_mean",
    "device_sd",
    "device_minus_control",
    "student_t",
    "student_p",
    "mannwhitney_u",
    "mannwhitney_p",
]

MARKER_METRICS = [
    "mean_sma_area_fraction",
    "mean_macrophage_area_fraction",
    "mean_macrophage_density_per_mm2",
    "mean_dapi_nuclei_density_per_mm2",
    "mean_dapi_nuclei_area_fraction",
]

NORMALIZED_MOUSE_COLUMNS = [
    "mouse_id",
    "mouse_base",
    "condition_code",
    "group",
    "metric",
    "raw_value",
    "control_mean",
    "normalized_to_control",
]

TUNING_COLUMNS = [
    "label",
    "dapi_percentile",
    "sma_percentile",
    "macrophage_percentile",
    "macrophage_mad_k",
    "macrophage_min_area_um2",
    "metric",
    "control_mean",
    "device_mean",
    "device_to_control",
    "student_p",
    "normalization_score",
]


def write_csv(path: Path, rows: Sequence[dict[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mean_numeric(rows: Sequence[dict[str, str]], key: str) -> float:
    values = []
    for row in rows:
        try:
            value = float(row[key])
        except Exception:
            continue
        if np.isfinite(value):
            values.append(value)
    return float(np.mean(values)) if values else float("nan")


def mouse_summary_rows(image_rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in image_rows:
        groups.setdefault(row["mouse_id"], []).append(row)
    rows: list[dict[str, str]] = []
    for mouse_id, items in sorted(groups.items()):
        first = items[0]
        rows.append(
            {
                "mouse_id": mouse_id,
                "mouse_base": first["mouse_base"],
                "condition_code": first["condition_code"],
                "group": first["group"],
                "n_images": str(len(items)),
                "mean_roi_area_um2": _fmt(mean_numeric(items, "roi_area_um2")),
                "mean_dapi_nuclei_count": _fmt(mean_numeric(items, "dapi_nuclei_count")),
                "mean_dapi_nuclei_area_fraction": _fmt(mean_numeric(items, "dapi_nuclei_area_fraction")),
                "mean_dapi_nuclei_density_per_mm2": _fmt(mean_numeric(items, "dapi_nuclei_density_per_mm2")),
                "mean_sma_closed_area_um2": _fmt(mean_numeric(items, "sma_closed_area_um2")),
                "mean_sma_area_fraction": _fmt(mean_numeric(items, "sma_area_fraction")),
                "mean_macrophage_count": _fmt(mean_numeric(items, "macrophage_count")),
                "mean_macrophage_area_um2": _fmt(mean_numeric(items, "macrophage_area_um2")),
                "mean_macrophage_area_fraction": _fmt(mean_numeric(items, "macrophage_area_fraction")),
                "mean_macrophage_density_per_mm2": _fmt(mean_numeric(items, "macrophage_density_per_mm2")),
            }
        )
    return rows


def sd(values: np.ndarray) -> float:
    return float(np.std(values, ddof=1)) if values.size > 1 else float("nan")


def stats_rows(mouse_rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    control_rows = [row for row in mouse_rows if row["condition_code"] == "C"]
    device_rows = [row for row in mouse_rows if row["condition_code"] == "D"]
    for metric in MARKER_METRICS:
        control = np.asarray([float(row[metric]) for row in control_rows if row.get(metric)], dtype=float)
        device = np.asarray([float(row[metric]) for row in device_rows if row.get(metric)], dtype=float)
        control = control[np.isfinite(control)]
        device = device[np.isfinite(device)]
        if control.size >= 2 and device.size >= 2:
            t_stat, t_p = stats.ttest_ind(device, control, equal_var=True)
            mw = stats.mannwhitneyu(device, control, alternative="two-sided")
        else:
            t_stat = t_p = mw_stat = mw_p = float("nan")
            rows.append(
                {
                    "metric": metric,
                    "control_n": str(control.size),
                    "device_n": str(device.size),
                    "control_mean": _fmt(float(np.mean(control)) if control.size else float("nan")),
                    "control_sd": _fmt(sd(control)),
                    "device_mean": _fmt(float(np.mean(device)) if device.size else float("nan")),
                    "device_sd": _fmt(sd(device)),
                    "device_minus_control": _fmt(float(np.mean(device) - np.mean(control)) if control.size and device.size else float("nan")),
                    "student_t": _fmt(t_stat),
                    "student_p": _fmt(t_p),
                    "mannwhitney_u": _fmt(mw_stat),
                    "mannwhitney_p": _fmt(mw_p),
                }
            )
            continue
        rows.append(
            {
                "metric": metric,
                "control_n": str(control.size),
                "device_n": str(device.size),
                "control_mean": _fmt(float(np.mean(control))),
                "control_sd": _fmt(sd(control)),
                "device_mean": _fmt(float(np.mean(device))),
                "device_sd": _fmt(sd(device)),
                "device_minus_control": _fmt(float(np.mean(device) - np.mean(control))),
                "student_t": _fmt(float(t_stat)),
                "student_p": _fmt(float(t_p)),
                "mannwhitney_u": _fmt(float(mw.statistic)),
                "mannwhitney_p": _fmt(float(mw.pvalue)),
            }
        )
    return rows


def normalized_mouse_rows(mouse_rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    control_means: dict[str, float] = {}
    control_rows = [row for row in mouse_rows if row["condition_code"] == "C"]
    for metric in MARKER_METRICS:
        values = np.asarray([float(row[metric]) for row in control_rows if row.get(metric)], dtype=float)
        values = values[np.isfinite(values)]
        control_means[metric] = float(np.mean(values)) if values.size else float("nan")

    rows: list[dict[str, str]] = []
    for row in mouse_rows:
        for metric in MARKER_METRICS:
            try:
                raw_value = float(row[metric])
            except Exception:
                raw_value = float("nan")
            control_mean = control_means.get(metric, float("nan"))
            normalized = raw_value / control_mean if np.isfinite(control_mean) and abs(control_mean) > 1e-12 else float("nan")
            rows.append(
                {
                    "mouse_id": row["mouse_id"],
                    "mouse_base": row["mouse_base"],
                    "condition_code": row["condition_code"],
                    "group": row["group"],
                    "metric": metric,
                    "raw_value": _fmt(raw_value),
                    "control_mean": _fmt(control_mean),
                    "normalized_to_control": _fmt(normalized),
                }
            )
    return rows
