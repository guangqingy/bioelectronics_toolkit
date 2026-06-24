from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from scipy import stats

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.fluorescence.manual_roi import (
    ROI_COLORS,
    RoiPolygon,
    load_tiff_channels,
    parse_fluorescence_name,
)
from services.fluorescence.preview_export import display_image_for_channels
from services.matplotlib_utils import close_figure, new_subplots

PIXEL_SIZE_UM = 1.62
PIXEL_AREA_UM2 = PIXEL_SIZE_UM**2
DEFAULT_ROI_JSON = Path("fluorescence_manual_rois.json")
DEFAULT_OUTPUT_DIRNAME = "fluorescence_marker_roi_analysis"

CHANNEL_NAMES = {
    "dapi": "DAPI",
    "sma": "SMA",
    "macrophage": "Macrophage",
}

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


@dataclass
class MarkerParams:
    pixel_size_um: float = PIXEL_SIZE_UM
    dapi_percentile_floor: float = 97.5
    dapi_min_area_um2: float = 8.0
    dapi_max_area_um2: float = 700.0
    sma_percentile_floor: float = 92.0
    sma_min_area_um2: float = 50.0
    macrophage_percentile_floor: float = 99.2
    macrophage_mad_k: float = 6.0
    macrophage_min_area_um2: float = 50.0

    @property
    def pixel_area_um2(self) -> float:
        return self.pixel_size_um**2

    def area_um2_to_px(self, value_um2: float) -> int:
        return max(1, int(round(value_um2 / self.pixel_area_um2)))


def _fmt(value: float | int | None, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except Exception:
        return ""
    if not np.isfinite(number):
        return ""
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def hex_rgb(color: str) -> tuple[float, float, float]:
    text = str(color or "").strip().lstrip("#")
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (0.0, 0.0, 0.0)


def infer_marker_channels(payload: dict, channel_count: int | None = None) -> dict[str, int]:
    colors = payload.get("display", {}).get("channel_colors") or []
    if channel_count is not None:
        colors = colors[:channel_count]
    if not isinstance(colors, list) or not colors:
        return {"macrophage": 1, "dapi": 2, "sma": 3}

    scores: list[dict[str, float]] = []
    for color in colors:
        r, g, b = hex_rgb(str(color))
        scores.append(
            {
                "red": r - max(g, b) * 0.35,
                "green": g - max(r, b) * 0.35,
                "blue": b + 0.45 * g - 0.35 * r,
            }
        )
    available = set(range(len(scores)))

    def choose(key: str) -> int:
        best = max(available, key=lambda idx: scores[idx][key])
        available.remove(best)
        return best + 1

    macrophage = choose("red")
    # This acquisition uses green for DAPI/nuclei and blue for SMA/NF-like
    # structural signal. Keep the automatic fallback aligned with the data.
    dapi = choose("green")
    sma = choose("blue") if available else 3
    return {"macrophage": macrophage, "sma": sma, "dapi": dapi}


def load_roi_payload(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "images" not in payload:
        raise ValueError(f"ROI JSON has no images: {path}")
    return payload


def polygon_mask(shape: tuple[int, int], points: Sequence[Sequence[float]]) -> np.ndarray:
    height, width = shape
    mask_img = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask_img).polygon([(float(x), float(y)) for x, y in points], fill=1)
    return np.asarray(mask_img, dtype=bool)


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    flat = np.asarray(values, dtype=np.float64)
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return float("nan")
    min_value = float(np.min(flat))
    max_value = float(np.max(flat))
    if max_value <= min_value:
        return min_value
    hist, edges = np.histogram(flat, bins=bins, range=(min_value, max_value))
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight1 = np.cumsum(hist).astype(float)
    weight2 = np.cumsum(hist[::-1]).astype(float)[::-1]
    mean1 = np.cumsum(hist * centers) / np.maximum(weight1, 1e-12)
    mean2 = (np.cumsum((hist * centers)[::-1]) / np.maximum(weight2[::-1], 1e-12))[::-1]
    variance12 = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2
    if variance12.size == 0:
        return min_value
    return float(centers[:-1][int(np.argmax(variance12))])


def clean_mask(binary: np.ndarray, roi_mask: np.ndarray, min_area_px: int, max_area_px: int | None = None) -> tuple[np.ndarray, int]:
    binary = np.asarray(binary, dtype=bool) & roi_mask
    labels, count = ndi.label(binary)
    if count == 0:
        return np.zeros(binary.shape, dtype=bool), 0
    sizes = np.bincount(labels.ravel())
    keep = np.zeros(count + 1, dtype=bool)
    for label in range(1, count + 1):
        size = int(sizes[label])
        if size < min_area_px:
            continue
        if max_area_px is not None and size > max_area_px:
            continue
        keep[label] = True
    cleaned = keep[labels]
    labels2, count2 = ndi.label(cleaned)
    return labels2 > 0, int(count2)


def segment_dapi(plane: np.ndarray, roi_mask: np.ndarray, params: MarkerParams) -> dict[str, float | int | np.ndarray]:
    smoothed = ndi.gaussian_filter(np.asarray(plane, dtype=np.float32), sigma=1.0)
    values = smoothed[roi_mask]
    otsu = otsu_threshold(values)
    floor = float(np.percentile(values, params.dapi_percentile_floor))
    threshold = max(otsu, floor)
    binary = smoothed > threshold
    binary = ndi.binary_opening(binary, structure=np.ones((2, 2)))
    min_area_px = params.area_um2_to_px(params.dapi_min_area_um2)
    max_area_px = params.area_um2_to_px(params.dapi_max_area_um2)
    cleaned, count = clean_mask(binary, roi_mask, min_area_px=min_area_px, max_area_px=max_area_px)
    area_px = int(np.sum(cleaned))
    return {"mask": cleaned, "threshold": threshold, "count": count, "area_px": area_px}


def segment_sma(plane: np.ndarray, roi_mask: np.ndarray, params: MarkerParams) -> dict[str, float | int | np.ndarray]:
    smoothed = ndi.gaussian_filter(np.asarray(plane, dtype=np.float32), sigma=1.0)
    values = smoothed[roi_mask]
    otsu = otsu_threshold(values)
    floor = float(np.percentile(values, params.sma_percentile_floor))
    threshold = max(otsu, floor)
    binary = smoothed > threshold
    binary = ndi.binary_closing(binary, structure=np.ones((5, 5)), iterations=2)
    binary = ndi.binary_fill_holes(binary)
    min_area_px = params.area_um2_to_px(params.sma_min_area_um2)
    cleaned, count = clean_mask(binary, roi_mask, min_area_px=min_area_px)
    area_px = int(np.sum(cleaned))
    return {"mask": cleaned, "threshold": threshold, "count": count, "area_px": area_px}


def segment_macrophage(plane: np.ndarray, roi_mask: np.ndarray, params: MarkerParams) -> dict[str, float | int | np.ndarray]:
    smoothed = ndi.gaussian_filter(np.asarray(plane, dtype=np.float32), sigma=1.0)
    values = smoothed[roi_mask]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    robust_threshold = median + params.macrophage_mad_k * 1.4826 * mad
    percentile_threshold = float(np.percentile(values, params.macrophage_percentile_floor))
    otsu = otsu_threshold(values)
    threshold = max(otsu, percentile_threshold, robust_threshold)
    binary = smoothed > threshold
    binary = ndi.binary_opening(binary, structure=np.ones((2, 2)))
    binary = ndi.binary_closing(binary, structure=np.ones((3, 3)))
    min_area_px = params.area_um2_to_px(params.macrophage_min_area_um2)
    cleaned, count = clean_mask(binary, roi_mask, min_area_px=min_area_px)
    area_px = int(np.sum(cleaned))
    return {"mask": cleaned, "threshold": threshold, "count": count, "area_px": area_px, "min_area_px": min_area_px}


def overlay_masks(
    image: Image.Image,
    roi: RoiPolygon,
    dapi_mask: np.ndarray,
    sma_mask: np.ndarray,
    macrophage_mask: np.ndarray,
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    rgba = np.asarray(overlay).copy()
    rgba[dapi_mask] = (0, 170, 255, 90)
    rgba[sma_mask] = (0, 255, 0, 95)
    rgba[macrophage_mask] = (255, 40, 40, 120)
    overlay = Image.fromarray(rgba, mode="RGBA")
    composed = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(composed)
    points = [(float(x), float(y)) for x, y in roi.points]
    draw.line(points + [points[0]], fill=ROI_COLORS.get(roi.kind, "#22d3ee"), width=3)
    draw.text((points[0][0] + 6, points[0][1] + 6), roi.label, fill=(255, 255, 255, 255))
    return composed.convert("RGB")


def analyze_roi_image(
    image_path: Path,
    roi: RoiPolygon,
    channels: dict[str, int],
    params: MarkerParams,
    preview_dir: Path | None = None,
    display_colors: Sequence[str] | None = None,
) -> dict[str, str]:
    arr = load_tiff_channels(image_path)
    height, width = arr.shape[1:]
    roi_mask = polygon_mask((height, width), roi.points)
    roi_area_px = int(np.sum(roi_mask))
    metadata = parse_fluorescence_name(image_path)

    dapi = segment_dapi(arr[channels["dapi"] - 1], roi_mask, params)
    sma = segment_sma(arr[channels["sma"] - 1], roi_mask, params)
    macrophage = segment_macrophage(arr[channels["macrophage"] - 1], roi_mask, params)

    roi_area_um2 = roi_area_px * params.pixel_area_um2
    roi_area_mm2 = roi_area_um2 / 1_000_000.0 if roi_area_um2 > 0 else float("nan")
    dapi_area_um2 = int(dapi["area_px"]) * params.pixel_area_um2
    sma_area_um2 = int(sma["area_px"]) * params.pixel_area_um2
    macrophage_area_um2 = int(macrophage["area_px"]) * params.pixel_area_um2

    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview = display_image_for_channels(arr, "Composite", 1.0, 99.9, display_colors)
        overlay = overlay_masks(
            preview,
            roi,
            dapi_mask=np.asarray(dapi["mask"], dtype=bool),
            sma_mask=np.asarray(sma["mask"], dtype=bool),
            macrophage_mask=np.asarray(macrophage["mask"], dtype=bool),
        )
        overlay.save(preview_dir / f"{image_path.stem}_marker_overlay.png")

    return {
        "image_name": image_path.name,
        "image_path": str(image_path),
        "mouse_id": str(metadata["mouse_id"]),
        "mouse_base": str(metadata["mouse_base"]),
        "side": str(metadata["side"]),
        "condition_code": str(metadata["condition_code"]),
        "group": str(metadata["group"]),
        "field_id": str(metadata["field_id"]),
        "roi_label": roi.label,
        "roi_area_px": str(roi_area_px),
        "roi_area_um2": _fmt(roi_area_um2),
        "dapi_channel": str(channels["dapi"]),
        "sma_channel": str(channels["sma"]),
        "macrophage_channel": str(channels["macrophage"]),
        "dapi_threshold": _fmt(float(dapi["threshold"])),
        "dapi_nuclei_count": str(int(dapi["count"])),
        "dapi_nuclei_area_um2": _fmt(dapi_area_um2),
        "dapi_nuclei_area_fraction": _fmt(dapi_area_um2 / roi_area_um2 if roi_area_um2 else float("nan")),
        "dapi_nuclei_density_per_mm2": _fmt(int(dapi["count"]) / roi_area_mm2 if roi_area_mm2 else float("nan")),
        "sma_threshold": _fmt(float(sma["threshold"])),
        "sma_closed_area_px": str(int(sma["area_px"])),
        "sma_closed_area_um2": _fmt(sma_area_um2),
        "sma_area_fraction": _fmt(sma_area_um2 / roi_area_um2 if roi_area_um2 else float("nan")),
        "macrophage_threshold": _fmt(float(macrophage["threshold"])),
        "macrophage_min_area_um2": _fmt(params.macrophage_min_area_um2),
        "macrophage_min_area_px": str(int(macrophage["min_area_px"])),
        "macrophage_count": str(int(macrophage["count"])),
        "macrophage_area_um2": _fmt(macrophage_area_um2),
        "macrophage_area_fraction": _fmt(macrophage_area_um2 / roi_area_um2 if roi_area_um2 else float("nan")),
        "macrophage_density_per_mm2": _fmt(int(macrophage["count"]) / roi_area_mm2 if roi_area_mm2 else float("nan")),
    }


def write_csv(path: Path, rows: Sequence[dict[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
        for idx, (code, label, color) in enumerate(order):
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
        for idx, (code, label, color) in enumerate(order):
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


def tuning_parameter_sets(base: MarkerParams) -> list[tuple[str, MarkerParams]]:
    return [
        ("current", base),
        (
            "sma_p90",
            MarkerParams(**{**base.__dict__, "sma_percentile_floor": 90.0}),
        ),
        (
            "sma_p94",
            MarkerParams(**{**base.__dict__, "sma_percentile_floor": 94.0}),
        ),
        (
            "mac_p98_8",
            MarkerParams(**{**base.__dict__, "macrophage_percentile_floor": 98.8}),
        ),
        (
            "mac_p99_5",
            MarkerParams(**{**base.__dict__, "macrophage_percentile_floor": 99.5}),
        ),
        (
            "dapi_p96_5",
            MarkerParams(**{**base.__dict__, "dapi_percentile_floor": 96.5}),
        ),
        (
            "dapi_p98_5",
            MarkerParams(**{**base.__dict__, "dapi_percentile_floor": 98.5}),
        ),
    ]


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_parameter_tuning(
    roi_json: Path,
    output_dir: Path,
    base_params: MarkerParams,
    channel_override: dict[str, int] | None = None,
) -> Path:
    tuning_dir = output_dir / "parameter_tuning_runs"
    summary_rows: list[dict[str, str]] = []
    for label, params in tuning_parameter_sets(base_params):
        run_dir = tuning_dir / label
        analyze(
            roi_json,
            output_dir=run_dir,
            params=params,
            write_previews=False,
            channel_override=channel_override,
        )
        stat_rows = read_csv_dicts(run_dir / "fluorescence_marker_device_vs_control_stats.csv")
        for stat_row in stat_rows:
            try:
                control_mean = float(stat_row.get("control_mean", "nan"))
                device_mean = float(stat_row.get("device_mean", "nan"))
            except Exception:
                control_mean = device_mean = float("nan")
            ratio = device_mean / control_mean if np.isfinite(control_mean) and abs(control_mean) > 1e-12 else float("nan")
            score = abs(math.log(ratio)) if np.isfinite(ratio) and ratio > 0 else float("nan")
            summary_rows.append(
                {
                    "label": label,
                    "dapi_percentile": _fmt(params.dapi_percentile_floor),
                    "sma_percentile": _fmt(params.sma_percentile_floor),
                    "macrophage_percentile": _fmt(params.macrophage_percentile_floor),
                    "macrophage_mad_k": _fmt(params.macrophage_mad_k),
                    "macrophage_min_area_um2": _fmt(params.macrophage_min_area_um2),
                    "metric": stat_row.get("metric", ""),
                    "control_mean": _fmt(control_mean),
                    "device_mean": _fmt(device_mean),
                    "device_to_control": _fmt(ratio),
                    "student_p": stat_row.get("student_p", ""),
                    "normalization_score": _fmt(score),
                }
            )
    tuning_path = output_dir / "fluorescence_marker_parameter_tuning.csv"
    write_csv(tuning_path, summary_rows, TUNING_COLUMNS)
    return tuning_path


def analyze(
    roi_json: Path,
    output_dir: Path | None = None,
    params: MarkerParams | None = None,
    write_previews: bool = True,
    channel_override: dict[str, int] | None = None,
) -> dict[str, Path | int | dict[str, int]]:
    params = params or MarkerParams()
    payload = load_roi_payload(roi_json)
    first_image = next((item for item in payload.get("images", []) if item.get("image_path")), None)
    channel_count = None
    if first_image is not None:
        image_path = Path(first_image.get("image_path", ""))
        if image_path.is_file():
            channel_count = int(load_tiff_channels(image_path).shape[0])
    channels = infer_marker_channels(payload, channel_count=channel_count)
    if channel_override:
        channels.update({key: int(value) for key, value in channel_override.items() if value})
    display_colors = payload.get("display", {}).get("channel_colors") or None
    output_dir = output_dir or (roi_json.parent / DEFAULT_OUTPUT_DIRNAME)
    preview_dir = output_dir / "segmentation_previews" if write_previews else None

    image_rows: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in payload.get("images", []):
        rois = [RoiPolygon.from_dict(roi_data) for roi_data in item.get("rois", []) if roi_data.get("kind", "signal") == "signal"]
        if not rois:
            skipped.append({"image_name": str(item.get("image_name", "")), "reason": "no signal ROI"})
            continue
        image_path = Path(item.get("image_path", ""))
        if not image_path.is_file():
            skipped.append({"image_name": str(item.get("image_name", "")), "reason": "missing TIFF"})
            continue
        for roi in rois:
            image_rows.append(
                analyze_roi_image(
                    image_path,
                    roi,
                    channels=channels,
                    params=params,
                    preview_dir=preview_dir,
                    display_colors=display_colors,
                )
            )

    mouse_rows = mouse_summary_rows(image_rows)
    stat_rows = stats_rows(mouse_rows)
    normalized_rows = normalized_mouse_rows(mouse_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "fluorescence_marker_image_summary.csv"
    mouse_path = output_dir / "fluorescence_marker_mouse_summary.csv"
    normalized_path = output_dir / "fluorescence_marker_mouse_summary_normalized_to_control.csv"
    stats_path = output_dir / "fluorescence_marker_device_vs_control_stats.csv"
    skipped_path = output_dir / "fluorescence_marker_skipped_images.csv"
    plot_path = output_dir / "fluorescence_marker_mouse_points.png"
    normalized_plot_path = output_dir / "fluorescence_marker_mouse_points_normalized_to_control.png"
    write_csv(image_path, image_rows, IMAGE_COLUMNS)
    write_csv(mouse_path, mouse_rows, MOUSE_COLUMNS)
    write_csv(normalized_path, normalized_rows, NORMALIZED_MOUSE_COLUMNS)
    write_csv(stats_path, stat_rows, STATS_COLUMNS)
    write_csv(skipped_path, skipped, ["image_name", "reason"])
    if mouse_rows:
        make_mouse_plot(mouse_rows, stat_rows, plot_path)
        make_normalized_mouse_plot(normalized_rows, normalized_plot_path)
    settings = {
        "pixel_size_um": params.pixel_size_um,
        "pixel_area_um2": params.pixel_area_um2,
        "channels": {CHANNEL_NAMES[key]: value for key, value in channels.items()},
        "parameters": params.__dict__,
    }
    (output_dir / "fluorescence_marker_analysis_settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return {
        "output_dir": output_dir,
        "image_rows": len(image_rows),
        "mouse_rows": len(mouse_rows),
        "skipped": len(skipped),
        "channels": channels,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze fluorescence manual ROIs for DAPI/SMA/macrophage markers.")
    parser.add_argument("--roi-json", default=str(DEFAULT_ROI_JSON), help="Path to fluorescence_manual_rois.json.")
    parser.add_argument("--output-dir", default="", help="Output folder. Defaults to <roi folder>/fluorescence_marker_roi_analysis.")
    parser.add_argument("--pixel-size-um", type=float, default=PIXEL_SIZE_UM, help="Microns per pixel.")
    parser.add_argument("--dapi-channel", type=int, default=0, help="Override DAPI channel, 1-based.")
    parser.add_argument("--sma-channel", type=int, default=0, help="Override SMA channel, 1-based.")
    parser.add_argument("--macrophage-channel", type=int, default=0, help="Override macrophage channel, 1-based.")
    parser.add_argument("--dapi-percentile", type=float, default=97.5, help="Manual DAPI percentile floor.")
    parser.add_argument("--sma-percentile", type=float, default=92.0, help="Manual SMA percentile floor.")
    parser.add_argument("--macrophage-percentile", type=float, default=99.2, help="Manual macrophage percentile floor.")
    parser.add_argument("--macrophage-mad-k", type=float, default=6.0, help="Robust MAD multiplier for macrophage threshold.")
    parser.add_argument("--dapi-min-area-um2", type=float, default=8.0, help="Minimum DAPI nucleus area.")
    parser.add_argument("--dapi-max-area-um2", type=float, default=700.0, help="Maximum DAPI nucleus area.")
    parser.add_argument("--sma-min-area-um2", type=float, default=50.0, help="Minimum SMA connected area.")
    parser.add_argument("--macrophage-min-area-um2", type=float, default=50.0, help="Minimum macrophage object area.")
    parser.add_argument("--no-previews", action="store_true", help="Skip segmentation overlay PNGs.")
    parser.add_argument("--tune-percentiles", action="store_true", help="Run a small percentile tuning sweep.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    params = MarkerParams(
        pixel_size_um=float(args.pixel_size_um),
        dapi_percentile_floor=float(args.dapi_percentile),
        dapi_min_area_um2=float(args.dapi_min_area_um2),
        dapi_max_area_um2=float(args.dapi_max_area_um2),
        sma_percentile_floor=float(args.sma_percentile),
        sma_min_area_um2=float(args.sma_min_area_um2),
        macrophage_percentile_floor=float(args.macrophage_percentile),
        macrophage_mad_k=float(args.macrophage_mad_k),
        macrophage_min_area_um2=float(args.macrophage_min_area_um2),
    )
    override = {
        "dapi": args.dapi_channel,
        "sma": args.sma_channel,
        "macrophage": args.macrophage_channel,
    }
    result = analyze(
        Path(args.roi_json).expanduser(),
        output_dir=output_dir,
        params=params,
        write_previews=not args.no_previews,
        channel_override=override,
    )
    print(f"Output: {result['output_dir']}")
    print(f"Rows: image={result['image_rows']} mouse={result['mouse_rows']} skipped={result['skipped']}")
    print(f"Channels: {result['channels']}")
    if args.tune_percentiles:
        tuning_path = run_parameter_tuning(
            Path(args.roi_json).expanduser(),
            Path(result["output_dir"]),
            params,
            channel_override=override,
        )
        print(f"Tuning: {tuning_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
