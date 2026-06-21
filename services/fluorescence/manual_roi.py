from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw

from services.fluorescence.preview_export import (
    fmt_float,
    load_tiff_channels,
    parse_fluorescence_name,
)

ROI_COLORS = {
    "signal": "#22d3ee",
    "background": "#f59e0b",
}


def polygon_bounds(points: Sequence[Sequence[float]], width: int, height: int) -> tuple[int, int, int, int]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    x1 = max(0, int(math.floor(min(xs))))
    y1 = max(0, int(math.floor(min(ys))))
    x2 = min(width, int(math.ceil(max(xs))) + 1)
    y2 = min(height, int(math.ceil(max(ys))) + 1)
    return x1, y1, x2, y2


def polygon_values(plane: np.ndarray, points: Sequence[Sequence[float]]) -> np.ndarray:
    if len(points) < 3:
        return np.asarray([], dtype=np.float64)
    height, width = plane.shape
    x1, y1, x2, y2 = polygon_bounds(points, width, height)
    if x2 <= x1 or y2 <= y1:
        return np.asarray([], dtype=np.float64)
    local_points = [(float(x) - x1, float(y) - y1) for x, y in points]
    mask_img = Image.new("L", (x2 - x1, y2 - y1), 0)
    ImageDraw.Draw(mask_img).polygon(local_points, fill=1)
    mask = np.asarray(mask_img, dtype=bool)
    crop = np.asarray(plane[y1:y2, x1:x2], dtype=np.float64)
    return crop[mask]


def metrics_from_values(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {
            "area_px": 0,
            "mean": float("nan"),
            "median": float("nan"),
            "std": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "sum": float("nan"),
        }
    return {
        "area_px": int(values.size),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "sum": float(np.sum(values)),
    }


@dataclass
class RoiPolygon:
    label: str
    points: list[tuple[float, float]]
    kind: str = "signal"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "points": [[float(x), float(y)] for x, y in self.points],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoiPolygon":
        return cls(
            label=str(data.get("label") or "ROI"),
            kind=str(data.get("kind") or "signal"),
            points=[(float(x), float(y)) for x, y in data.get("points", [])],
            created_at=str(data.get("created_at") or datetime.now().isoformat(timespec="seconds")),
        )


@dataclass
class ImageRois:
    path: Path
    rois: list[RoiPolygon] = field(default_factory=list)


MEASUREMENT_COLUMNS = [
    "image_name",
    "image_path",
    "mouse_id",
    "mouse_base",
    "side",
    "condition_code",
    "group",
    "field_id",
    "roi_index",
    "roi_label",
    "roi_kind",
    "channel_index",
    "channel_label",
    "area_px",
    "mean",
    "median",
    "std",
    "min",
    "max",
    "sum",
    "background_mean",
    "mean_bg_subtracted",
    "sum_bg_subtracted",
]


SUMMARY_COLUMNS = [
    "mouse_id",
    "mouse_base",
    "condition_code",
    "group",
    "roi_label",
    "channel_label",
    "n_images",
    "mean_of_image_means",
    "sd_of_image_means",
    "sem_of_image_means",
]


def analyze_image(path: Path, rois: Sequence[RoiPolygon]) -> list[dict[str, str]]:
    channels = load_tiff_channels(path)
    metadata = parse_fluorescence_name(path)
    background_rois = [roi for roi in rois if roi.kind == "background"]
    bg_roi = background_rois[-1] if background_rois else None
    rows: list[dict[str, str]] = []

    background_by_channel: dict[int, float] = {}
    if bg_roi is not None:
        for channel_index, plane in enumerate(channels, start=1):
            values = polygon_values(plane, bg_roi.points)
            background_by_channel[channel_index] = float(metrics_from_values(values)["mean"])

    for roi_index, roi in enumerate(rois, start=1):
        for channel_zero, plane in enumerate(channels):
            channel_index = channel_zero + 1
            values = polygon_values(plane, roi.points)
            metrics = metrics_from_values(values)
            bg_mean = background_by_channel.get(channel_index, float("nan"))
            mean_value = float(metrics["mean"])
            sum_value = float(metrics["sum"])
            area_px = int(metrics["area_px"])
            mean_bg = mean_value - bg_mean if np.isfinite(bg_mean) else float("nan")
            sum_bg = sum_value - bg_mean * area_px if np.isfinite(bg_mean) else float("nan")
            rows.append(
                {
                    "image_name": path.name,
                    "image_path": str(path),
                    "mouse_id": str(metadata["mouse_id"]),
                    "mouse_base": str(metadata["mouse_base"]),
                    "side": str(metadata["side"]),
                    "condition_code": str(metadata["condition_code"]),
                    "group": str(metadata["group"]),
                    "field_id": str(metadata["field_id"]),
                    "roi_index": str(roi_index),
                    "roi_label": roi.label,
                    "roi_kind": roi.kind,
                    "channel_index": str(channel_index),
                    "channel_label": f"Ch{channel_index}",
                    "area_px": str(area_px),
                    "mean": fmt_float(float(metrics["mean"])),
                    "median": fmt_float(float(metrics["median"])),
                    "std": fmt_float(float(metrics["std"])),
                    "min": fmt_float(float(metrics["min"])),
                    "max": fmt_float(float(metrics["max"])),
                    "sum": fmt_float(float(metrics["sum"])),
                    "background_mean": fmt_float(bg_mean),
                    "mean_bg_subtracted": fmt_float(mean_bg),
                    "sum_bg_subtracted": fmt_float(sum_bg),
                }
            )
    return rows


def write_measurements_csv(rows: Sequence[dict[str, str]], output_path: str | Path) -> None:
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MEASUREMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def summarize_measurements(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    buckets: dict[tuple[str, str, str, str, str, str], list[float]] = {}
    for row in rows:
        if row.get("roi_kind") != "signal":
            continue
        value = row.get("mean_bg_subtracted") or row.get("mean")
        try:
            number = float(value)
        except Exception:
            continue
        if not np.isfinite(number):
            continue
        key = (
            row.get("mouse_id", ""),
            row.get("group", ""),
            row.get("roi_label", ""),
            row.get("channel_label", ""),
            row.get("condition_code", ""),
            row.get("mouse_base", ""),
        )
        buckets.setdefault(key, []).append(number)

    summary: list[dict[str, str]] = []
    for key, values in sorted(buckets.items()):
        mouse_id, group, roi_label, channel_label, condition_code, mouse_base = key
        arr = np.asarray(values, dtype=float)
        summary.append(
            {
                "mouse_id": mouse_id,
                "mouse_base": mouse_base,
                "condition_code": condition_code,
                "group": group,
                "roi_label": roi_label,
                "channel_label": channel_label,
                "n_images": str(arr.size),
                "mean_of_image_means": fmt_float(float(np.mean(arr))),
                "sd_of_image_means": fmt_float(float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0),
                "sem_of_image_means": fmt_float(
                    float(np.std(arr, ddof=1) / math.sqrt(arr.size)) if arr.size > 1 else 0.0
                ),
            }
        )
    return summary


def write_summary_csv(rows: Sequence[dict[str, str]], output_path: str | Path) -> None:
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
