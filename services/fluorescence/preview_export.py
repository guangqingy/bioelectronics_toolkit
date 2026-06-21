from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import tifffile
from PIL import Image

IMAGE_SUFFIXES = {".tif", ".tiff"}
DEFAULT_FOLDER = Path(__file__).resolve().parents[2] / "temp" / "fl"
ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
SHIFT_MASK = 0x0001
LOCK_MASK = 0x0002
SCROLL_PIXELS_PER_NOTCH = 80.0
TRACKPAD_SCROLL_MULTIPLIER = 3.0
DEFAULT_CHANNEL_COLORS = (
    "#ff3b30",
    "#34c759",
    "#0a84ff",
    "#ffcc00",
    "#bf5af2",
    "#64d2ff",
    "#ff9f0a",
    "#ffffff",
)


def fmt_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except Exception:
        return ""
    if not np.isfinite(number):
        return ""
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def find_tiff_files(paths: Iterable[str | Path]) -> list[Path]:
    found: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            found.extend(
                sorted(
                    file
                    for file in path.rglob("*")
                    if file.is_file() and file.suffix.lower() in IMAGE_SUFFIXES
                )
            )
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


def natural_sort_key(path: Path) -> tuple:
    parts = re.split(r"(\d+)", path.stem.lower())
    key: list[int | str] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        elif part:
            key.append(part)
    return tuple(key)


def parse_fluorescence_name(path: Path) -> dict[str, str | int]:
    stem = path.stem.lower()
    match = re.match(r"^(?P<animal>\d+)(?P<side>ls|rs)(?P<condition>[cdn])(?P<field>\d*)$", stem)
    if not match:
        return {
            "mouse_id": path.stem,
            "mouse_base": path.stem,
            "side": "",
            "condition_code": "",
            "group": "",
            "field_id": "",
        }
    animal = match.group("animal")
    side = match.group("side").upper()
    original_side = side
    condition = match.group("condition").upper()
    field_text = match.group("field")
    field_id = int(field_text) if field_text else 0
    if animal == "1" and side == "LS" and condition == "D" and field_id >= 7:
        side = "RS"
    group = {"C": "Control", "D": "Device", "N": "N"}.get(condition, condition)
    return {
        "mouse_id": f"{animal}{side}-{condition}",
        "mouse_base": f"{animal}{side}",
        "side": side,
        "original_side": original_side,
        "condition_code": condition,
        "group": group,
        "field_id": field_id,
    }


def load_tiff_channels(path: str | Path) -> np.ndarray:
    arr = np.asarray(tifffile.imread(str(path)))
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr[np.newaxis, :, :]
    if arr.ndim == 3:
        if arr.shape[0] <= 8:
            return arr
        if arr.shape[-1] <= 8:
            return np.moveaxis(arr, -1, 0)
        return arr
    if arr.ndim == 4:
        squeezed = np.squeeze(arr)
        if squeezed.ndim == 3:
            return load_array_as_channels(squeezed)
    raise ValueError(f"Unsupported TIFF shape: {arr.shape}")


def load_array_as_channels(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return arr[np.newaxis, :, :]
    if arr.ndim == 3 and arr.shape[0] <= 8:
        return arr
    if arr.ndim == 3 and arr.shape[-1] <= 8:
        return np.moveaxis(arr, -1, 0)
    if arr.ndim == 3:
        return arr
    raise ValueError(f"Unsupported array shape: {arr.shape}")


def normalize_plane(plane: np.ndarray, low_percent: float, high_percent: float) -> np.ndarray:
    arr = np.asarray(plane, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    low = float(np.percentile(finite, low_percent))
    high = float(np.percentile(finite, high_percent))
    if high <= low:
        low = float(np.min(finite))
        high = float(np.max(finite))
    if high <= low:
        return np.zeros(arr.shape, dtype=np.uint8)
    scaled = np.clip((arr - low) / (high - low), 0.0, 1.0)
    return np.asarray(np.round(scaled * 255), dtype=np.uint8)


def hex_to_rgb01(color: str) -> tuple[float, float, float]:
    text = str(color or "").strip().lstrip("#")
    if len(text) != 6:
        return (1.0, 1.0, 1.0)
    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (1.0, 1.0, 1.0)


def colorized_plane(gray: np.ndarray, color: str) -> np.ndarray:
    gray_float = np.asarray(gray, dtype=np.float32) / 255.0
    rgb = np.zeros((*gray_float.shape, 3), dtype=np.float32)
    for channel_index, component in enumerate(hex_to_rgb01(color)):
        rgb[..., channel_index] = gray_float * component
    return np.asarray(np.round(np.clip(rgb, 0.0, 1.0) * 255), dtype=np.uint8)


def display_image_for_channels(
    channels: np.ndarray,
    mode: str,
    low_percent: float,
    high_percent: float,
    channel_colors: Sequence[str] | None = None,
    channel_enabled: Sequence[bool] | None = None,
) -> Image.Image:
    channels = load_array_as_channels(channels)
    colors = list(channel_colors or DEFAULT_CHANNEL_COLORS)
    enabled = list(channel_enabled) if channel_enabled is not None else [True] * channels.shape[0]
    mode = mode.lower()
    if mode == "composite":
        height, width = channels.shape[1:]
        rgb = np.zeros((height, width, 3), dtype=np.float32)
        for channel_zero in range(channels.shape[0]):
            if channel_zero < len(enabled) and not enabled[channel_zero]:
                continue
            color = colors[channel_zero] if channel_zero < len(colors) else "#ffffff"
            gray = normalize_plane(channels[channel_zero], low_percent, high_percent)
            rgb += colorized_plane(gray, color).astype(np.float32)
        return Image.fromarray(np.asarray(np.clip(rgb, 0, 255), dtype=np.uint8), mode="RGB")

    channel_index = 0
    match = re.match(r"ch\s*(\d+)", mode)
    if match:
        channel_index = max(0, min(channels.shape[0] - 1, int(match.group(1)) - 1))
    if channel_index < len(enabled) and not enabled[channel_index]:
        return Image.new("RGB", (channels.shape[2], channels.shape[1]), "black")
    gray = normalize_plane(channels[channel_index], low_percent, high_percent)
    color = colors[channel_index] if channel_index < len(colors) else "#ffffff"
    return Image.fromarray(colorized_plane(gray, color), mode="RGB")


@dataclass(frozen=True)
class RotationGeometry:
    source_width: int
    source_height: int
    angle_degrees: float
    min_x: float
    min_y: float
    rotated_width: int
    rotated_height: int


def _rotation_geometry(width: int, height: int, angle_degrees: float) -> RotationGeometry:
    angle = math.radians(angle_degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    cx = width / 2.0
    cy = height / 2.0
    rotated: list[tuple[float, float]] = []
    for x, y in ((0.0, 0.0), (float(width), 0.0), (0.0, float(height)), (float(width), float(height))):
        dx = x - cx
        dy = y - cy
        rx = cos_a * dx + sin_a * dy
        ry = -sin_a * dx + cos_a * dy
        rotated.append((rx, ry))
    min_x = min(x for x, _y in rotated)
    min_y = min(y for _x, y in rotated)
    max_x = max(x for x, _y in rotated)
    max_y = max(y for _x, y in rotated)
    return RotationGeometry(
        source_width=width,
        source_height=height,
        angle_degrees=angle_degrees,
        min_x=min_x,
        min_y=min_y,
        rotated_width=max(1, int(math.ceil(max_x - min_x))),
        rotated_height=max(1, int(math.ceil(max_y - min_y))),
    )


def rotate_image_for_preview(image: Image.Image, angle_degrees: float) -> tuple[Image.Image, RotationGeometry]:
    angle = float(angle_degrees or 0.0)
    normalized = angle % 360.0
    geometry = _rotation_geometry(image.width, image.height, normalized)
    if abs(normalized) < 1e-9:
        return image.copy(), geometry
    rotated = image.rotate(normalized, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    return rotated, RotationGeometry(
        source_width=image.width,
        source_height=image.height,
        angle_degrees=normalized,
        min_x=geometry.min_x,
        min_y=geometry.min_y,
        rotated_width=rotated.width,
        rotated_height=rotated.height,
    )


def image_point_to_rotated_view(point: tuple[float, float], geometry: RotationGeometry) -> tuple[float, float]:
    angle = math.radians(geometry.angle_degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    cx = geometry.source_width / 2.0
    cy = geometry.source_height / 2.0
    dx = float(point[0]) - cx
    dy = float(point[1]) - cy
    rx = cos_a * dx + sin_a * dy - geometry.min_x
    ry = -sin_a * dx + cos_a * dy - geometry.min_y
    return rx, ry


def rotated_view_to_image_point(point: tuple[float, float], geometry: RotationGeometry) -> tuple[float, float]:
    angle = math.radians(geometry.angle_degrees)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rx = float(point[0]) + geometry.min_x
    ry = float(point[1]) + geometry.min_y
    dx = cos_a * rx - sin_a * ry
    dy = sin_a * rx + cos_a * ry
    return dx + geometry.source_width / 2.0, dy + geometry.source_height / 2.0


@dataclass
class ChannelBC:
    enabled: bool = True
    color: str = "#ffffff"
    black_percent: float = 1.0
    white_percent: float = 99.8
    brightness: float = 0.0
    contrast: float = 1.0
    gamma: float = 1.0


def normalize_plane_bc(plane: np.ndarray, settings: ChannelBC) -> np.ndarray:
    arr = np.asarray(plane, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)

    black = max(0.0, min(100.0, float(settings.black_percent)))
    white = max(0.0, min(100.0, float(settings.white_percent)))
    if white <= black:
        white = min(100.0, black + 0.01)
    low = float(np.percentile(finite, black))
    high = float(np.percentile(finite, white))
    if high <= low:
        low = float(np.min(finite))
        high = float(np.max(finite))
    if high <= low:
        return np.zeros(arr.shape, dtype=np.uint8)

    gray = np.clip((arr - low) / (high - low), 0.0, 1.0)
    gamma = max(0.05, float(settings.gamma))
    gray = np.power(gray, 1.0 / gamma)
    gray = (gray - 0.5) * float(settings.contrast) + 0.5 + float(settings.brightness)
    gray = np.clip(gray, 0.0, 1.0)
    return np.asarray(np.round(gray * 255), dtype=np.uint8)


def composite_preview_image(channels: np.ndarray, settings: Sequence[ChannelBC]) -> Image.Image:
    height, width = channels.shape[1:]
    rgb = np.zeros((height, width, 3), dtype=np.float32)
    for channel_zero in range(channels.shape[0]):
        if channel_zero >= len(settings) or not settings[channel_zero].enabled:
            continue
        gray = normalize_plane_bc(channels[channel_zero], settings[channel_zero])
        rgb += colorized_plane(gray, settings[channel_zero].color).astype(np.float32)
    return Image.fromarray(np.asarray(np.clip(rgb, 0, 255), dtype=np.uint8), mode="RGB")


def single_channel_preview_image(channels: np.ndarray, channel_zero: int, settings: Sequence[ChannelBC]) -> Image.Image:
    height, width = channels.shape[1:]
    if channel_zero >= channels.shape[0] or channel_zero >= len(settings):
        return Image.new("RGB", (width, height))
    gray = normalize_plane_bc(channels[channel_zero], settings[channel_zero])
    rgb = colorized_plane(gray, settings[channel_zero].color)
    return Image.fromarray(np.asarray(np.clip(rgb, 0, 255), dtype=np.uint8), mode="RGB")
