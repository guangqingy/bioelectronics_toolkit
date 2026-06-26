from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from services.fluorescence.manual_roi import (
    ROI_COLORS,
    RoiPolygon,
    load_tiff_channels,
    parse_fluorescence_name,
)
from services.fluorescence.preview_export import display_image_for_channels

PIXEL_SIZE_UM = 1.62
PIXEL_AREA_UM2 = PIXEL_SIZE_UM**2

CHANNEL_NAMES = {
    "dapi": "DAPI",
    "sma": "SMA",
    "macrophage": "Macrophage",
}


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
