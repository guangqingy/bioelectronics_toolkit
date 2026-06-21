from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from services.histology_analysis import (
    ANALYSIS_VERSION,
    _array_to_rgb,
    _clean_rois,
    _dapi_analysis_mask,
    _geojson,
    _marker_analysis,
    _mask_for_roi,
    _now_iso,
    _png_b64,
    _read_json,
    _scale_to_uint8,
    _write_json,
)
from services.histology_common import sanitize_name
from services.histology_tiff_project import (
    PROJECT_PROTOCOL as TIFF_PROJECT_PROTOCOL,
)
from services.histology_tiff_project import (
    TIFF_SUFFIXES,
    estimate_image_load_size,
    load_image_for_analysis,
)
from services.matplotlib_utils import close_figure, new_subplots

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None

ETS_PROTOCOL = "dataprocess-ets-histology"
ETS_PROJECT_DIR = ".dataprocess_histology"
ETS_INDEX_FILE = "project.json"
ETS_DATA_PROJECT_KIND = "dataprocess_histology_project"
ETS_DATA_PROJECT_FILE = "histology_project.dphistology"
PROJECT_IMAGE_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg")
PROJECT_PRIMARY_SUFFIXES = PROJECT_IMAGE_SUFFIXES
PROJECT_CONFIG_SUFFIXES = {".dphistology", ".json"}


def _has_project_image_suffix(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".ome.tif", ".ome.tiff")) or path.suffix.lower() in PROJECT_IMAGE_SUFFIXES


def _has_project_primary_suffix(path: Path) -> bool:
    return path.suffix.lower() in PROJECT_PRIMARY_SUFFIXES


def _friendly_image_read_error(path: Path, exc: Exception) -> str:
    message = str(exc)
    if path.suffix.lower() == ".ets" and ("SIS" in message or "not a TIFF" in message):
        return (
            "Olympus/SIS .ets files are traceability-only in the current histology "
            "pipeline. Export 2D TIFF images from Olympus software and use those TIFFs "
            "for preview and analysis."
        )
    return message or f"Could not read image: {path}"


def _read_project_image(path: str | Path, max_side: int = 1600):
    image_path = Path(str(path)).expanduser()
    try:
        arr = load_image_for_analysis(image_path)
    except Exception as exc:
        raise ValueError(_friendly_image_read_error(image_path, exc)) from exc
    backend = "tifffile" if image_path.suffix.lower() in TIFF_SUFFIXES else "pillow"
    warnings: list[str] = []
    if image_path.suffix.lower() not in TIFF_SUFFIXES:
        warnings.append("Non-TIFF image loaded; TIFF is recommended for quantitative analysis.")
    return arr, backend, warnings


def _preview_array_from_tiled_tiff(path: Path, max_side: int) -> tuple[np.ndarray, int, int]:
    if tifffile is None:
        raise RuntimeError("tifffile is required to preview tiled TIFF images")
    with tifffile.TiffFile(str(path)) as tf:
        series = tf.series[0] if getattr(tf, "series", None) else None
        page = series.pages[0] if series is not None and getattr(series, "pages", None) else tf.pages[0]
        shape = tuple(int(x) for x in (series.shape if series is not None else page.shape))
        if len(shape) == 2:
            height, width = shape
        elif len(shape) == 3 and shape[-1] in {1, 3, 4}:
            height, width = shape[:2]
        else:
            raise ValueError(f"Unsupported TIFF preview shape: {shape}")
        if not getattr(page, "is_tiled", False):
            raise ValueError("Large non-tiled TIFF cannot be previewed safely; export a tiled/pyramidal TIFF or ROI TIFF.")

        preview_max = max(256, min(int(max_side), 2400))
        scale = min(1.0, float(preview_max) / max(1, max(width, height)))
        preview_w = max(1, int(round(width * scale)))
        preview_h = max(1, int(round(height * scale)))
        canvas = Image.new("RGB", (preview_w, preview_h), "white")

        for tile_data, tile_index, _tile_shape in page.segments():
            tile = _array_to_rgb(np.asarray(tile_data).squeeze())
            if tile.ndim != 3:
                continue
            if len(tile_index) >= 4:
                y0 = int(tile_index[-3])
                x0 = int(tile_index[-2])
            else:
                continue
            if y0 >= height or x0 >= width:
                continue
            tile_h = min(int(tile.shape[0]), height - y0)
            tile_w = min(int(tile.shape[1]), width - x0)
            if tile_h <= 0 or tile_w <= 0:
                continue
            x1 = x0 + tile_w
            y1 = y0 + tile_h
            px0 = int(round(x0 * scale))
            py0 = int(round(y0 * scale))
            px1 = int(round(x1 * scale))
            py1 = int(round(y1 * scale))
            if px1 <= px0 or py1 <= py0:
                continue
            tile_img = Image.fromarray(tile[:tile_h, :tile_w, :3], mode="RGB")
            tile_img = tile_img.resize((px1 - px0, py1 - py0), Image.Resampling.BOX)
            canvas.paste(tile_img, (px0, py0))
        return np.asarray(canvas, dtype=np.uint8), width, height


def _read_project_image_preview(path: str | Path, max_side: int = 1600):
    image_path = Path(str(path)).expanduser()
    warnings: list[str] = []
    if image_path.suffix.lower() in TIFF_SUFFIXES and tifffile is not None:
        try:
            arr, width, height = _preview_array_from_tiled_tiff(image_path, max_side)
            return arr, "tifffile_tiled_preview", warnings, width, height
        except ValueError:
            pass
        except Exception as exc:
            warnings.append(f"Tiled preview fallback failed: {exc}")
    arr, backend, read_warnings = _read_project_image(image_path)
    preview_max = max(256, min(int(max_side), 2400))
    h, w = arr.shape[:2]
    if max(h, w) > preview_max:
        img = Image.fromarray(_array_to_rgb(arr), mode="RGB")
        img.thumbnail((preview_max, preview_max), Image.Resampling.LANCZOS)
        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return arr, backend, [*warnings, *read_warnings], w, h


def _resize_rgb_for_preview(arr: np.ndarray, max_side: int) -> np.ndarray:
    rgb = _array_to_rgb(arr)
    limit = max(128, min(int(max_side), 2400))
    if max(rgb.shape[:2]) <= limit:
        return np.ascontiguousarray(rgb)
    img = Image.fromarray(rgb, mode="RGB")
    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _normalize_region_box(
    x: float,
    y: float,
    width: float,
    height: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    x0 = max(0, min(int(np.floor(float(x))), max(0, image_width - 1)))
    y0 = max(0, min(int(np.floor(float(y))), max(0, image_height - 1)))
    x1 = max(x0 + 1, min(int(np.ceil(float(x) + float(width))), image_width))
    y1 = max(y0 + 1, min(int(np.ceil(float(y) + float(height))), image_height))
    return x0, y0, x1, y1


def _region_output_size(region_w: int, region_h: int, max_side: int) -> tuple[int, int, float]:
    limit = max(256, min(int(max_side), 2600))
    scale = min(1.0, float(limit) / max(1, int(region_w), int(region_h)))
    out_w = max(1, int(round(int(region_w) * scale)))
    out_h = max(1, int(round(int(region_h) * scale)))
    return out_w, out_h, scale


def _tiff_series_xy_shape(tf) -> tuple[Any, Any, tuple[int, ...], int, int]:
    series = tf.series[0] if getattr(tf, "series", None) else None
    page = series.pages[0] if series is not None and getattr(series, "pages", None) else tf.pages[0]
    shape = tuple(int(x) for x in (series.shape if series is not None else page.shape))
    if len(shape) == 2:
        height, width = shape
    elif len(shape) == 3 and shape[-1] in {1, 3, 4}:
        height, width = shape[:2]
    else:
        raise ValueError(f"Unsupported TIFF preview shape: {shape}")
    return series, page, shape, int(width), int(height)


def _preview_array_from_tiled_tiff_region(
    path: Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int,
) -> tuple[np.ndarray, int, int, tuple[int, int, int, int]]:
    if tifffile is None:
        raise RuntimeError("tifffile is required to preview tiled TIFF images")
    with tifffile.TiffFile(str(path)) as tf:
        _series, page, _shape, image_w, image_h = _tiff_series_xy_shape(tf)
        if not getattr(page, "is_tiled", False):
            raise ValueError("TIFF is not tiled")
        x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
        region_w = max(1, x1 - x0)
        region_h = max(1, y1 - y0)
        out_w, out_h, scale = _region_output_size(region_w, region_h, max_side)
        canvas = Image.new("RGB", (out_w, out_h), "black")
        for tile_data, tile_index, _tile_shape in page.segments():
            if len(tile_index) < 4:
                continue
            tile_y = int(tile_index[-3])
            tile_x = int(tile_index[-2])
            tile = _array_to_rgb(np.asarray(tile_data).squeeze())
            tile_h = int(tile.shape[0])
            tile_w = int(tile.shape[1])
            ix0 = max(x0, tile_x)
            iy0 = max(y0, tile_y)
            ix1 = min(x1, tile_x + tile_w)
            iy1 = min(y1, tile_y + tile_h)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            sx0 = ix0 - tile_x
            sy0 = iy0 - tile_y
            sx1 = sx0 + (ix1 - ix0)
            sy1 = sy0 + (iy1 - iy0)
            dx0 = int(round((ix0 - x0) * scale))
            dy0 = int(round((iy0 - y0) * scale))
            dx1 = int(round((ix1 - x0) * scale))
            dy1 = int(round((iy1 - y0) * scale))
            if dx1 <= dx0 or dy1 <= dy0:
                continue
            tile_img = Image.fromarray(tile[sy0:sy1, sx0:sx1, :3], mode="RGB")
            tile_img = tile_img.resize((dx1 - dx0, dy1 - dy0), Image.Resampling.LANCZOS)
            canvas.paste(tile_img, (dx0, dy0))
        return np.asarray(canvas, dtype=np.uint8), image_w, image_h, (x0, y0, x1, y1)


def _read_project_image_region_preview(
    path: str | Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
):
    image_path = Path(str(path)).expanduser()
    warnings: list[str] = []
    if image_path.suffix.lower() in TIFF_SUFFIXES and tifffile is not None:
        try:
            arr, image_w, image_h, box = _preview_array_from_tiled_tiff_region(
                image_path,
                x,
                y,
                width,
                height,
                max_side,
            )
            return arr, "tifffile_tiled_region", warnings, image_w, image_h, box
        except ValueError:
            pass
        except Exception as exc:
            warnings.append(f"Tiled region preview fallback failed: {exc}")
    arr, backend, read_warnings = _read_project_image(image_path)
    rgb = _array_to_rgb(arr)
    image_h, image_w = rgb.shape[:2]
    x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
    crop = rgb[y0:y1, x0:x1]
    return _resize_rgb_for_preview(crop, max_side), backend, [*warnings, *read_warnings], image_w, image_h, (x0, y0, x1, y1)


def _analysis_region_scale(region_w: int, region_h: int, max_pixels: int | None) -> float:
    pixel_limit = int(max_pixels or 0)
    if pixel_limit <= 0:
        return 1.0
    pixel_count = max(1, int(region_w) * int(region_h))
    if pixel_count <= pixel_limit:
        return 1.0
    return float(np.sqrt(pixel_limit / pixel_count))


def _resize_analysis_tile(src: np.ndarray, width: int, height: int, dtype: np.dtype) -> np.ndarray:
    data = np.asarray(src)
    try:
        image = Image.fromarray(data)
        resized = image.resize((int(width), int(height)), Image.Resampling.BILINEAR)
        return np.asarray(resized).astype(dtype, copy=False)
    except Exception:
        scaled = _scale_to_uint8(data)
        if scaled.ndim == 3 and scaled.shape[-1] > 3:
            scaled = scaled[..., :3]
        mode = "RGB" if scaled.ndim == 3 and scaled.shape[-1] >= 3 else "L"
        image = Image.fromarray(scaled, mode=mode)
        resized = image.resize((int(width), int(height)), Image.Resampling.BILINEAR)
        return np.asarray(resized).astype(dtype, copy=False)


def _read_tiled_tiff_region_for_analysis(
    path: Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_pixels: int | None = None,
) -> tuple[np.ndarray, int, int, tuple[int, int, int, int], float]:
    if tifffile is None:
        raise RuntimeError("tifffile is required to read TIFF image regions")
    with tifffile.TiffFile(str(path)) as tf:
        _series, page, shape, image_w, image_h = _tiff_series_xy_shape(tf)
        if not getattr(page, "is_tiled", False):
            raise ValueError("TIFF is not tiled")
        x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
        region_w = max(1, x1 - x0)
        region_h = max(1, y1 - y0)
        scale = _analysis_region_scale(region_w, region_h, max_pixels)
        out_w = max(1, int(round(region_w * scale)))
        out_h = max(1, int(round(region_h * scale)))
        dtype = np.dtype(getattr(page, "dtype", np.uint8))
        if len(shape) == 3 and shape[-1] in {1, 3, 4}:
            canvas = np.zeros((out_h, out_w, int(shape[-1])), dtype=dtype)
        else:
            canvas = np.zeros((out_h, out_w), dtype=dtype)

        tile_w = int(getattr(page, "tilewidth", 0) or 0)
        tile_h = int(getattr(page, "tilelength", 0) or 0)
        if tile_w <= 0 or tile_h <= 0:
            raise ValueError("TIFF tile dimensions are missing")
        tiles_across = max(1, int(np.ceil(image_w / tile_w)))
        row0 = max(0, int(y0 // tile_h))
        row1 = min(int(np.ceil(image_h / tile_h)), int((y1 - 1) // tile_h) + 1)
        col0 = max(0, int(x0 // tile_w))
        col1 = min(tiles_across, int((x1 - 1) // tile_w) + 1)
        offsets = tuple(getattr(page, "dataoffsets", ()) or ())
        bytecounts = tuple(getattr(page, "databytecounts", ()) or ())
        if not offsets or len(offsets) != len(bytecounts):
            raise ValueError("TIFF tile offsets are missing")

        handle = tf.filehandle
        for tile_row in range(row0, row1):
            for tile_col in range(col0, col1):
                tile_number = tile_row * tiles_across + tile_col
                if tile_number < 0 or tile_number >= len(offsets):
                    continue
                handle.seek(int(offsets[tile_number]))
                encoded = handle.read(int(bytecounts[tile_number]))
                tile = np.asarray(page.decode(encoded, tile_number)[0]).squeeze()
                if tile.ndim not in {2, 3}:
                    continue
                tile_y = tile_row * tile_h
                tile_x = tile_col * tile_w
                actual_tile_h = min(int(tile.shape[0]), image_h - tile_y)
                actual_tile_w = min(int(tile.shape[1]), image_w - tile_x)
                if actual_tile_h <= 0 or actual_tile_w <= 0:
                    continue
                ix0 = max(x0, tile_x)
                iy0 = max(y0, tile_y)
                ix1 = min(x1, tile_x + actual_tile_w)
                iy1 = min(y1, tile_y + actual_tile_h)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                sx0 = ix0 - tile_x
                sy0 = iy0 - tile_y
                sx1 = sx0 + (ix1 - ix0)
                sy1 = sy0 + (iy1 - iy0)
                src = tile[sy0:sy1, sx0:sx1]
                if scale < 1.0:
                    dx0 = int(np.floor((ix0 - x0) * scale))
                    dy0 = int(np.floor((iy0 - y0) * scale))
                    dx1 = int(np.ceil((ix1 - x0) * scale))
                    dy1 = int(np.ceil((iy1 - y0) * scale))
                    dx0 = max(0, min(dx0, out_w - 1))
                    dy0 = max(0, min(dy0, out_h - 1))
                    dx1 = max(dx0 + 1, min(dx1, out_w))
                    dy1 = max(dy0 + 1, min(dy1, out_h))
                    src = _resize_analysis_tile(src, dx1 - dx0, dy1 - dy0, dtype)
                else:
                    dx0 = ix0 - x0
                    dy0 = iy0 - y0
                    dx1 = dx0 + (ix1 - ix0)
                    dy1 = dy0 + (iy1 - iy0)
                if canvas.ndim == 3 and src.ndim == 2:
                    canvas[dy0:dy1, dx0:dx1, 0] = src.astype(dtype, copy=False)
                elif canvas.ndim == 3 and src.ndim == 3:
                    channels = min(canvas.shape[-1], src.shape[-1])
                    canvas[dy0:dy1, dx0:dx1, :channels] = src[..., :channels].astype(dtype, copy=False)
                elif canvas.ndim == 2 and src.ndim == 3:
                    canvas[dy0:dy1, dx0:dx1] = _as_2d_channel(src).astype(dtype, copy=False)
                else:
                    canvas[dy0:dy1, dx0:dx1] = src.astype(dtype, copy=False)
        return canvas, image_w, image_h, (x0, y0, x1, y1), scale


def _read_project_image_region_for_analysis(
    path: str | Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_pixels: int | None = None,
) -> tuple[np.ndarray, str, int, int, tuple[int, int, int, int], float]:
    image_path = Path(str(path)).expanduser()
    if image_path.suffix.lower() in TIFF_SUFFIXES and tifffile is not None:
        try:
            arr, image_w, image_h, box, scale = _read_tiled_tiff_region_for_analysis(
                image_path,
                x,
                y,
                width,
                height,
                max_pixels=max_pixels,
            )
            return arr, "tifffile_tiled_region_analysis", image_w, image_h, box, scale
        except ValueError:
            pass
    arr, backend, _warnings = _read_project_image(image_path)
    image_h, image_w = arr.shape[:2]
    x0, y0, x1, y1 = _normalize_region_box(x, y, width, height, image_w, image_h)
    crop = np.asarray(arr)[y0:y1, x0:x1]
    scale = _analysis_region_scale(x1 - x0, y1 - y0, max_pixels)
    if scale < 1.0:
        crop = _resize_analysis_tile(
            crop,
            max(1, int(round((x1 - x0) * scale))),
            max(1, int(round((y1 - y0) * scale))),
            crop.dtype,
        )
    return crop, backend, image_w, image_h, (x0, y0, x1, y1), scale


def _rational_to_float(value: Any) -> float | None:
    try:
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                return None
            den = float(value[1])
            if abs(den) < 1e-12:
                return None
            return float(value[0]) / den
        return float(value)
    except Exception:
        return None


def _unit_to_um_scale(unit: str | None) -> float | None:
    text = str(unit or "").strip().lower()
    if text in {"um", "micron", "microns", "micrometer", "micrometers"}:
        return 1.0
    if text in {"nm", "nanometer", "nanometers"}:
        return 1e-3
    if text in {"mm", "millimeter", "millimeters"}:
        return 1e3
    if text in {"cm", "centimeter", "centimeters"}:
        return 1e4
    if text in {"m", "meter", "meters"}:
        return 1e6
    if text in {"in", "inch", "inches"}:
        return 25400.0
    return None


def _positive_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if np.isfinite(out) and out > 0:
        return out
    return None


def _infer_tiff_pixel_calibration(path: str | Path) -> dict[str, Any]:
    image_path = Path(str(path)).expanduser()
    if image_path.suffix.lower() not in TIFF_SUFFIXES or tifffile is None:
        return {}
    try:
        with tifffile.TiffFile(str(image_path)) as tf:
            ome_xml = tf.ome_metadata or ""
            if ome_xml:
                x_val = re.search(r'PhysicalSizeX="([0-9eE+\-.]+)"', ome_xml)
                y_val = re.search(r'PhysicalSizeY="([0-9eE+\-.]+)"', ome_xml)
                x_unit = re.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                y_unit = re.search(r'PhysicalSizeYUnit="([^"]+)"', ome_xml)
                x_scale = _unit_to_um_scale(x_unit.group(1) if x_unit else "um")
                y_scale = _unit_to_um_scale(y_unit.group(1) if y_unit else "um")
                px_w = _positive_float(float(x_val.group(1)) * x_scale) if x_val and x_scale else None
                px_h = _positive_float(float(y_val.group(1)) * y_scale) if y_val and y_scale else None
                if px_w is not None:
                    px_h = px_h or px_w
                    return {
                        "has_physical_scale": True,
                        "pixel_width_um": float(px_w),
                        "pixel_height_um": float(px_h),
                        "pixel_area_um2": float(px_w * px_h),
                        "source": "OME PhysicalSize",
                    }

            page = tf.pages[0]
            tags = page.tags
            xres_tag = tags.get("XResolution")
            yres_tag = tags.get("YResolution")
            unit_tag = tags.get("ResolutionUnit")
            xres = _positive_float(_rational_to_float(xres_tag.value) if xres_tag is not None else None)
            yres = _positive_float(_rational_to_float(yres_tag.value) if yres_tag is not None else None)
            unit_value = unit_tag.value if unit_tag is not None else None
            unit_scale = None
            try:
                unit_code = int(unit_value)
            except Exception:
                unit_code = 0
            if unit_code == 2:
                unit_scale = 25400.0
            elif unit_code == 3:
                unit_scale = 10000.0
            if unit_scale is not None and xres is not None:
                px_w = unit_scale / xres
                px_h = unit_scale / (yres or xres)
                return {
                    "has_physical_scale": True,
                    "pixel_width_um": float(px_w),
                    "pixel_height_um": float(px_h),
                    "pixel_area_um2": float(px_w * px_h),
                    "source": "TIFF resolution",
                }
    except Exception:
        return {}
    return {}


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _sidecar_stem_from_path(case_dir: Path, image_path: Path) -> str:
    try:
        parts = image_path.relative_to(case_dir).parts
    except ValueError:
        parts = image_path.parts
    for part in parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            return part.strip("_")
    return image_path.stem


def _role_for_path(image_path: Path) -> str:
    text = image_path.as_posix().lower()
    if "overview" in text:
        return "overview"
    return "image"


def _normalize_data_project_path(project_path: str | Path) -> Path:
    raw = str(project_path or "").strip()
    if not raw:
        raise FileNotFoundError("Histology project path is required")
    path = Path(raw).expanduser()
    if path.exists() and path.is_dir():
        if path.name == ETS_PROJECT_DIR and (path / ETS_INDEX_FILE).exists():
            return (path / ETS_INDEX_FILE).resolve()
        default_project = path / ETS_DATA_PROJECT_FILE
        if default_project.exists():
            return default_project.resolve()
        legacy_project = path / ETS_PROJECT_DIR / ETS_INDEX_FILE
        if legacy_project.exists():
            return legacy_project.resolve()
        return default_project.resolve()
    if path.exists() and path.is_file():
        if path.name == ETS_INDEX_FILE and path.parent.name == ETS_PROJECT_DIR:
            return path.resolve()
        if path.suffix.lower() in PROJECT_CONFIG_SUFFIXES:
            return path.resolve()
        if _has_project_image_suffix(path) or path.suffix.lower() in {".vsi", ".ets"}:
            raise ValueError(
                f"Selected file is an image/raw microscopy file, not a DataProcess histology project: {path.name}. "
                "Load histology_project.dphistology or its containing folder. Create that project from Histology Naming first."
            )
        raise ValueError(
            f"Unsupported histology project file type: {path.name}. "
            "Load histology_project.dphistology or its containing folder."
        )
    if path.name == ETS_INDEX_FILE and path.parent.name == ETS_PROJECT_DIR:
        return path.resolve()
    if path.suffix.lower() in PROJECT_CONFIG_SUFFIXES:
        return path.resolve()
    if not path.suffix:
        return (path / ETS_DATA_PROJECT_FILE).resolve()
    raise ValueError(
        f"Unsupported histology project file type: {path.name}. "
        "Load histology_project.dphistology or its containing folder."
    )


def _data_project_dir(project_path: Path) -> Path:
    if project_path.name == ETS_INDEX_FILE and project_path.parent.name == ETS_PROJECT_DIR:
        return project_path.parent
    return project_path.with_name(f"{project_path.stem}.dataprocess_histology")


def _data_project_cache_dir(project_path: Path) -> Path:
    return _data_project_dir(project_path) / "cache"


def _data_project_cache_layout(project_path: Path) -> dict[str, str]:
    cache_dir = _data_project_cache_dir(project_path)
    return {
        "root": str(cache_dir),
        "previews": str(cache_dir / "previews"),
        "converted": str(cache_dir / "converted"),
        "tmp": str(cache_dir / "tmp"),
        "metadata": str(cache_dir / "metadata"),
    }


def _ensure_data_project_dirs(project_path: Path) -> None:
    _data_project_dir(project_path).mkdir(parents=True, exist_ok=True)
    for path in _data_project_cache_layout(project_path).values():
        Path(path).mkdir(parents=True, exist_ok=True)


def _data_project_entry_dir(project_path: Path, entry_id: str) -> Path:
    return _data_project_dir(project_path) / "images" / str(entry_id)


def _data_project_entry_analysis_path(project_path: Path, entry_id: str) -> Path:
    return _data_project_entry_dir(project_path, entry_id) / "analysis.json"


def _data_project_entry_geojson_path(project_path: Path, entry_id: str) -> Path:
    return _data_project_entry_dir(project_path, entry_id) / "rois.geojson"


def _source_entry_id(source_path: Path) -> str:
    digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8")).hexdigest()
    return f"img_{digest[:16]}"


def _case_name_for_source(source_path: Path) -> str:
    parts = list(source_path.parts)
    for idx, part in enumerate(parts):
        if part.startswith("_") and part.endswith("_") and idx > 0:
            return parts[idx - 1]
    if source_path.parent.name.lower().startswith("stack") and source_path.parent.parent.name:
        return source_path.parent.parent.name.strip("_") or source_path.parent.parent.name
    return source_path.parent.name


def _case_dir_for_source(source_path: Path) -> Path:
    parts = list(source_path.parts)
    for idx, part in enumerate(parts):
        if part.startswith("_") and part.endswith("_") and idx > 0:
            return Path(*parts[:idx])
    if source_path.parent.name.lower().startswith("stack") and source_path.parent.parent.parent.exists():
        return source_path.parent.parent.parent
    return source_path.parent


def _slide_stem_for_source(source_path: Path) -> str:
    case_dir = _case_dir_for_source(source_path)
    return _sidecar_stem_from_path(case_dir, source_path)


def _slide_prefix(stem: str) -> str:
    parts = stem.split("_")
    if len(parts) > 1 and (parts[-1].isdigit() or parts[-1].lower() in {"overview", "label"}):
        return "_".join(parts[:-1])
    return stem


def _display_name_for_source(source_path: Path) -> str:
    case_name = _case_name_for_source(source_path)
    slide = ""
    stack = ""
    for part in source_path.parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            slide = part.strip("_")
        elif part.lower().startswith("stack"):
            stack = part
    if case_name and slide and stack:
        return f"{case_name} · {slide} · {stack}"
    if case_name and source_path.name:
        return f"{case_name} · {source_path.name}"
    return source_path.name


def _associated_files_for_source(source_path: Path) -> list[dict[str, str]]:
    case_dir = _case_dir_for_source(source_path)
    if not case_dir.exists() or not case_dir.is_dir():
        return []
    slide_stem = _slide_stem_for_source(source_path)
    prefix = _slide_prefix(slide_stem)
    related: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sorted(case_dir.glob("*.vsi")):
        stem = item.stem
        stem_lower = stem.lower()
        is_direct_label = stem == slide_stem
        is_related_overview = bool(prefix) and stem.startswith(prefix) and "overview" in stem_lower
        if not is_direct_label and not is_related_overview:
            continue
        role = "overview_vsi" if "overview" in stem_lower else "label_vsi"
        resolved = item.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        related.append(
            {
                "role": role,
                "path": key,
                "name": item.name,
                "relative_path": _safe_relative(resolved, case_dir),
            }
        )
    return related


def _entry_associated_fields(source_path: Path, record: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (record or {}).get("associated_files")
    associated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("path") or item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        associated.append(item)
    for item in _associated_files_for_source(source_path):
        key = str(item.get("path") or item.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        associated.append(item)
    label_vsi_path = str((record or {}).get("label_vsi_path") or "")
    overview_vsi_path = str((record or {}).get("overview_vsi_path") or "")
    for item in associated:
        role = str(item.get("role") or "")
        path = str(item.get("path") or "")
        if role == "label_vsi" and not label_vsi_path:
            label_vsi_path = path
        elif role == "overview_vsi" and not overview_vsi_path:
            overview_vsi_path = path
    return {
        "associated_files": associated,
        "associated_file_count": len(associated),
        "label_vsi_path": label_vsi_path,
        "overview_vsi_path": overview_vsi_path,
    }


def _record_source_path(record: dict[str, Any]) -> Path | None:
    raw = str(record.get("image_path") or record.get("source_path") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _primary_sources_for_project_path(path: Path) -> list[Path]:
    if _has_project_primary_suffix(path):
        return [path.resolve()]
    return []


def _data_project_record_for_source(
    project_path: Path,
    source: Path,
    record: dict[str, Any] | None = None,
    now: str | None = None,
    preserve_display_name: bool = True,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    record = record if isinstance(record, dict) else {}
    timestamp = now or _now_iso()
    entry_id = _source_entry_id(source)
    default_name = _display_name_for_source(source)
    image_name = default_name
    if preserve_display_name:
        image_name = str(record.get("image_name") or record.get("display_name") or default_name).strip()
        if not image_name:
            image_name = default_name
    associated = _entry_associated_fields(source, record)
    entry = {
        "entry_id": entry_id,
        "image_name": image_name,
        "display_name": image_name,
        "source_name": source.name,
        "case_name": _case_name_for_source(source),
        "image_path": str(source),
        "source_path": str(source),
        "relative_path": source.name,
        "case_relative_path": source.name,
        "role": _role_for_path(source),
        "format": source.suffix.lower().lstrip("."),
        "roi_count": int(record.get("roi_count") or 0),
        "analysis_count": int(record.get("analysis_count") or 0),
        "analysis_path": str(_data_project_entry_analysis_path(project_path, entry_id)),
        "geojson_path": str(_data_project_entry_geojson_path(project_path, entry_id)),
        "latest_analysis_at": str(record.get("latest_analysis_at") or ""),
        "added_at": str(record.get("added_at") or timestamp),
        "updated_at": timestamp,
        **associated,
    }
    for key in (
        "record_type",
        "sample_id",
        "image_files",
        "image_records",
        "raw_olympus_reference",
        "case_dir",
        "physical_rename_dir",
        "converted_from_ets",
        "converted_tiff_paths",
        "conversion_roles",
        "ets_conversion_count",
        "analysis_folder",
        "manifest_path",
        "parameters_path",
        "roi_measurements_path",
        "rois_path",
        "qc_overlay_path",
        "warnings",
    ):
        if key in record:
            entry[key] = record[key]
    if entry.get("record_type") == "sample":
        entry["role"] = "sample"
    return entry


def _normalize_data_project_images(
    project_path: Path,
    records: list[Any],
) -> tuple[list[dict[str, Any]], bool]:
    normalized: list[dict[str, Any]] = []
    index_by_source: dict[str, int] = {}
    preserved_by_source: dict[str, bool] = {}
    changed = False
    now = _now_iso()
    for record in records:
        if not isinstance(record, dict):
            changed = True
            continue
        source = _record_source_path(record)
        if source is None:
            changed = True
            continue
        if str(record.get("record_type") or "") == "sample":
            if not _has_project_primary_suffix(source):
                changed = True
                continue
            sample_record = dict(record)
            sample_record.setdefault("entry_id", _source_entry_id(source))
            sample_record.setdefault("role", "sample")
            sample_record.setdefault("format", source.suffix.lower().lstrip("."))
            sample_record.setdefault("image_path", str(source.resolve()))
            sample_record.setdefault("source_path", str(source.resolve()))
            entry_key = f"entry::{sample_record['entry_id']}"
            if entry_key in index_by_source:
                changed = True
                continue
            index_by_source[entry_key] = len(normalized)
            normalized.append(sample_record)
            continue
        primary_sources = _primary_sources_for_project_path(source)
        if not primary_sources:
            changed = True
            continue
        original_key = str(source.resolve())
        for primary in primary_sources:
            primary = primary.resolve()
            primary_key = str(primary)
            preserve_display_name = primary_key == original_key
            new_record = _data_project_record_for_source(
                project_path,
                primary,
                record=record if preserve_display_name else None,
                now=now,
                preserve_display_name=preserve_display_name,
            )
            if primary_key in index_by_source:
                if preserve_display_name and not preserved_by_source.get(primary_key, False):
                    normalized[index_by_source[primary_key]] = new_record
                    preserved_by_source[primary_key] = True
                changed = True
                continue
            index_by_source[primary_key] = len(normalized)
            preserved_by_source[primary_key] = preserve_display_name
            if (
                str(record.get("entry_id") or "") != str(new_record.get("entry_id") or "")
                or original_key != primary_key
                or record.get("associated_files") != new_record["associated_files"]
                or record.get("label_vsi_path", "") != new_record["label_vsi_path"]
                or record.get("overview_vsi_path", "") != new_record["overview_vsi_path"]
                or record.get("format") != new_record["format"]
            ):
                changed = True
            normalized.append(new_record)
    normalized.sort(
        key=lambda item: (
            str(item.get("image_name") or "").lower(),
            str(item.get("image_path") or "").lower(),
        )
    )
    if len(normalized) != len([r for r in records if isinstance(r, dict)]):
        changed = True
    return normalized, changed


def _load_data_project_payload(project_path: Path) -> dict[str, Any]:
    if not project_path.is_file():
        raise FileNotFoundError(f"Histology project not found: {project_path}")
    try:
        data = _read_json(project_path)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Histology project is not valid UTF-8 JSON: {project_path}. "
            "Load histology_project.dphistology or its containing folder, not a TIFF/VSI/ETS image file."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Histology project is not valid JSON: {project_path}. "
            "Load histology_project.dphistology or its containing folder."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid histology project file: {project_path}")
    images = data.get("images")
    if not isinstance(images, list):
        data["images"] = []
    return data


def _write_data_project_payload(project_path: Path, data: dict[str, Any]) -> None:
    data = dict(data)
    data["version"] = ANALYSIS_VERSION
    data["protocol"] = str(data.get("protocol") or TIFF_PROJECT_PROTOCOL)
    data["kind"] = ETS_DATA_PROJECT_KIND
    data["project_path"] = str(project_path)
    data["data_dir"] = str(_data_project_dir(project_path))
    data["cache_dir"] = str(_data_project_cache_dir(project_path))
    data["cache_layout"] = _data_project_cache_layout(project_path)
    data["updated_at"] = _now_iso()
    images = data.get("images")
    data["entry_count"] = len(images) if isinstance(images, list) else 0
    _write_json(project_path, data)
    _ensure_data_project_dirs(project_path)


def _load_data_project_entry_analysis(project_path: Path, entry_id: str) -> dict[str, Any]:
    path = _data_project_entry_analysis_path(project_path, entry_id)
    if not path.is_file():
        return {"rois": [], "analyses": []}
    try:
        data = _read_json(path)
        return data if isinstance(data, dict) else {"rois": [], "analyses": []}
    except Exception:
        return {"rois": [], "analyses": []}


def _external_rois_candidates(project_path: Path, entry: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for key in ("rois_path", "geojson_path"):
        raw = str(entry.get(key) or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
    sample_id = str(entry.get("sample_id") or entry.get("image_name") or entry.get("case_name") or "").strip()
    analysis_folder = str(entry.get("analysis_folder") or "").strip()
    if analysis_folder and sample_id:
        candidates.append(Path(analysis_folder).expanduser() / f"{sample_id}_rois.json")
    if sample_id:
        candidates.append(project_path.parent / "analysis" / sample_id / f"{sample_id}_rois.json")
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _load_external_entry_rois(project_path: Path, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    for candidate in _external_rois_candidates(project_path, entry):
        if not candidate.is_file():
            continue
        try:
            data = _read_json(candidate)
        except Exception:
            continue
        raw_rois = data.get("rois") if isinstance(data, dict) else data
        clean_rois = _clean_rois(raw_rois)
        if clean_rois:
            return clean_rois, str(candidate)
    return [], ""


def _data_project_entry_from_record(project_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(record.get("image_path") or record.get("source_path") or "")).expanduser()
    entry_id = str(record.get("entry_id") or (_source_entry_id(source) if str(source) else ""))
    analysis = _load_data_project_entry_analysis(project_path, entry_id) if entry_id else {}
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
    external_rois_path = ""
    if not rois:
        rois, external_rois_path = _load_external_entry_rois(project_path, record)
    analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
    latest = analyses[-1] if analyses else {}
    image_name = str(
        record.get("image_name")
        or record.get("display_name")
        or (_display_name_for_source(source) if str(source) else entry_id)
    )
    associated = _entry_associated_fields(source, record) if str(source) else {
        "associated_files": [],
        "associated_file_count": 0,
        "label_vsi_path": "",
        "overview_vsi_path": "",
    }
    entry = {
        "entry_id": entry_id,
        "image_name": image_name,
        "display_name": image_name,
        "source_name": source.name if str(source) else "",
        "case_name": str(record.get("case_name") or (_case_name_for_source(source) if str(source) else "")),
        "image_path": str(source) if str(source) else "",
        "source_path": str(source) if str(source) else "",
        "relative_path": str(record.get("relative_path") or source.name),
        "case_relative_path": str(record.get("case_relative_path") or source.name),
        "role": str(record.get("role") or _role_for_path(source)),
        "exists": source.is_file() if str(source) else False,
        "format": str(record.get("format") or source.suffix.lower().lstrip(".")),
        "roi_count": len(rois),
        "analysis_count": len(analyses),
        "rois": rois,
        "latest_analysis": latest,
        "analysis_path": str(_data_project_entry_analysis_path(project_path, entry_id)),
        "geojson_path": str(_data_project_entry_geojson_path(project_path, entry_id)),
        "external_rois_path": external_rois_path,
        "latest_analysis_at": latest.get("created_at", "") if isinstance(latest, dict) else "",
        "added_at": str(record.get("added_at") or ""),
        "updated_at": str(record.get("updated_at") or ""),
        **associated,
    }
    for key in (
        "record_type",
        "sample_id",
        "image_files",
        "image_records",
        "raw_olympus_reference",
        "case_dir",
        "physical_rename_dir",
        "converted_from_ets",
        "converted_tiff_paths",
        "conversion_roles",
        "ets_conversion_count",
        "analysis_folder",
        "manifest_path",
        "parameters_path",
        "roi_measurements_path",
        "rois_path",
        "qc_overlay_path",
        "warnings",
    ):
        if key in record:
            entry[key] = record[key]
    entry["warnings"] = _entry_warnings(record)
    return entry


def create_histology_data_project(project_path: str | Path, name: str = "") -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    if path.is_file():
        return load_histology_data_project(path)
    now = _now_iso()
    project_name = str(name or "").strip()
    if not project_name:
        project_name = path.parent.parent.name if path.parent.name == ETS_PROJECT_DIR else path.stem
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_name": project_name,
        "project_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "created_at": now,
        "updated_at": now,
        "entry_count": 0,
        "images": [],
    }
    _write_json(path, payload)
    _ensure_data_project_dirs(path)
    return load_histology_data_project(path)


def load_histology_data_project(project_path: str | Path) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    data = _load_data_project_payload(path)
    _ensure_data_project_dirs(path)
    expected_cache_dir = str(_data_project_cache_dir(path))
    if data.get("cache_dir") != expected_cache_dir or not isinstance(data.get("cache_layout"), dict):
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
    images = data.get("images", [])
    if isinstance(images, list):
        images, migrated = _normalize_data_project_images(path, images)
    else:
        images, migrated = [], True
    if migrated:
        data["images"] = images
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
    entries = [
        _data_project_entry_from_record(path, record)
        for record in data.get("images", [])
        if isinstance(record, dict)
    ]
    entries.sort(
        key=lambda item: (
            0 if item.get("role") == "image" else 1,
            str(item.get("image_name") or "").lower(),
            str(item.get("image_path") or "").lower(),
        )
    )
    return {
        "ok": True,
        "protocol": data.get("protocol") or TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_name": data.get("project_name") or path.stem,
        "project_root": str(path.parent.parent if path.parent.name == ETS_PROJECT_DIR else path.parent),
        "project_path": str(path),
        "index_path": str(path),
        "exported_dir": str(data.get("exported_dir") or ""),
        "raw_dir": str(data.get("raw_dir") or ""),
        "analysis_dir": str(data.get("analysis_dir") or ""),
        "raw_olympus_index_path": str(data.get("raw_olympus_index_path") or ""),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "entry_count": len(entries),
        "entries": entries,
        "warnings": data.get("warnings") if isinstance(data.get("warnings"), list) else [],
    }


def _iter_project_source_files(paths: list[str | Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if raw is None:
            continue
        path = Path(str(raw).strip()).expanduser()
        if not str(path):
            continue
        if not path.exists():
            warnings.append(f"Path not found: {path}")
            continue
        if path.is_file():
            candidates = [path]
            scan_root = path.parent
        else:
            candidates = [item for item in path.rglob("*") if item.is_file()]
            scan_root = path
        for item in candidates:
            try:
                rel = item.relative_to(scan_root)
            except ValueError:
                rel = item
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not _has_project_primary_suffix(item):
                continue
            for source in _primary_sources_for_project_path(item):
                key = str(source)
                if key in seen:
                    continue
                seen.add(key)
                files.append(source)
    files.sort(key=lambda item: str(item).lower())
    return files, warnings


def add_histology_data_project_paths(
    project_path: str | Path,
    paths: list[str | Path],
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    if not path.is_file():
        create_histology_data_project(path)
    data = _load_data_project_payload(path)
    images, migrated = _normalize_data_project_images(path, data.get("images", []))
    if migrated:
        data["images"] = images
        _write_data_project_payload(path, data)
        data = _load_data_project_payload(path)
        images = [record for record in data.get("images", []) if isinstance(record, dict)]
    existing_by_id = {str(record.get("entry_id") or ""): record for record in images}
    existing_by_source = {
        str(Path(str(record.get("image_path") or record.get("source_path") or "")).expanduser().resolve()): record
        for record in images
        if str(record.get("image_path") or record.get("source_path") or "").strip()
    }
    files, warnings = _iter_project_source_files(paths)
    added = 0
    skipped = 0
    now = _now_iso()
    for source in files:
        source_key = str(source.resolve())
        entry_id = _source_entry_id(source)
        if entry_id in existing_by_id or source_key in existing_by_source:
            skipped += 1
            continue
        entry = _data_project_record_for_source(path, source, now=now)
        images.append(entry)
        existing_by_id[entry_id] = entry
        existing_by_source[source_key] = entry
        added += 1
    data["images"] = images
    _write_data_project_payload(path, data)
    loaded = load_histology_data_project(path)
    return {
        **loaded,
        "added_count": added,
        "skipped_count": skipped,
        "warnings": warnings,
    }


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _replace_path_text(text: str, replacements: list[tuple[str, str]]) -> str:
    value = text
    for old, new in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        old_norm = old.rstrip("/")
        if not old_norm:
            continue
        if value == old_norm:
            return new
        if value.startswith(old_norm + "/"):
            return new.rstrip("/") + value[len(old_norm) :]
    return value


def _replace_paths_in_obj(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return _replace_path_text(value, replacements)
    if isinstance(value, list):
        return [_replace_paths_in_obj(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_paths_in_obj(item, replacements) for key, item in value.items()}
    return value


def _record_path_values(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("image_path", "source_path"):
        text = str(record.get(key) or "").strip()
        if text:
            values.append(text)
    image_files = record.get("image_files")
    if isinstance(image_files, dict):
        values.extend(str(path) for path in image_files.values() if str(path or "").strip())
    converted = record.get("converted_tiff_paths")
    if isinstance(converted, list):
        values.extend(str(path) for path in converted if str(path or "").strip())
    conversions = record.get("converted_from_ets")
    if isinstance(conversions, list):
        for item in conversions:
            if isinstance(item, dict):
                text = str(item.get("output_path") or "").strip()
                if text:
                    values.append(text)
    return values


def _converted_tiff_paths_for_record(record: dict[str, Any], case_dir: Path) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for raw in _record_path_values(record):
        path = Path(raw).expanduser()
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if not _path_is_relative_to(resolved, case_dir):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _rename_target_for_tiff(path: Path, old_case_name: str, new_case_name: str) -> Path:
    if path.name.startswith(old_case_name):
        return path.with_name(new_case_name + path.name[len(old_case_name) :])
    return path.with_name(f"{new_case_name}{path.suffix}")


def _rename_entry_physical_sources(
    project_path: Path,
    record: dict[str, Any],
    display_name: str,
) -> dict[str, Any]:
    raw_dir = str(record.get("physical_rename_dir") or record.get("case_dir") or "").strip()
    if not raw_dir:
        return {"renamed": False, "path_replacements": [], "warnings": []}
    old_dir = Path(raw_dir).expanduser().resolve()
    if not old_dir.is_dir():
        return {
            "renamed": False,
            "path_replacements": [],
            "warnings": [f"Physical source folder not found: {old_dir}"],
        }
    if _path_is_relative_to(project_path, old_dir):
        raise ValueError("Move the DataProcess project file outside the case folder before renaming the case folder")

    new_case_name = sanitize_name(display_name, fallback=old_dir.name)
    new_dir = old_dir.with_name(new_case_name)
    rename_dir = old_dir != new_dir
    if rename_dir and new_dir.exists():
        raise FileExistsError(f"Rename target folder already exists: {new_dir}")

    replacements: list[tuple[str, str]] = []
    warnings: list[str] = []
    old_tiffs = _converted_tiff_paths_for_record(record, old_dir)
    actual_tiffs = [
        Path(_replace_path_text(str(path), [(str(old_dir), str(new_dir))])) if rename_dir else path
        for path in old_tiffs
    ]
    tiff_moves: list[tuple[Path, Path, Path]] = []
    target_keys: set[str] = set()
    for original, actual in zip(old_tiffs, actual_tiffs, strict=False):
        target = _rename_target_for_tiff(actual, old_dir.name, new_case_name)
        key = str(target)
        if key in target_keys:
            raise FileExistsError(f"Multiple converted TIFF files would rename to: {target}")
        target_keys.add(key)
        if target != actual and target.exists():
            raise FileExistsError(f"Rename target TIFF already exists: {target}")
        tiff_moves.append((original, actual, target))

    if rename_dir:
        old_dir.rename(new_dir)
        replacements.append((str(old_dir), str(new_dir)))
    for original, actual, target in tiff_moves:
        if not actual.exists():
            warnings.append(f"Converted TIFF not found during rename: {actual}")
            continue
        if target != actual:
            actual.rename(target)
            replacements.append((str(actual), str(target)))
            replacements.append((str(original), str(target)))

    return {
        "renamed": bool(rename_dir or any(actual != target for _original, actual, target in tiff_moves)),
        "case_dir": str(new_dir if rename_dir else old_dir),
        "case_name": new_case_name,
        "path_replacements": replacements,
        "renamed_tiffs": [
            {"from": str(original), "to": str(target)}
            for original, _actual, target in tiff_moves
            if original != target
        ],
        "warnings": warnings,
    }


def rename_histology_data_project_entry(
    project_path: str | Path,
    entry_id: str,
    display_name: str,
) -> dict[str, Any]:
    name = str(display_name or "").strip()
    if not name:
        raise ValueError("Enter a display name")
    path = _normalize_data_project_path(project_path)
    data = _load_data_project_payload(path)
    images = [record for record in data.get("images", []) if isinstance(record, dict)]
    renamed: dict[str, Any] | None = None
    for record in images:
        if str(record.get("entry_id")) != str(entry_id):
            continue
        record["image_name"] = name
        record["display_name"] = name
        record["updated_at"] = _now_iso()
        renamed = record
        break
    if renamed is None:
        raise ValueError(f"Histology project entry not found: {entry_id}")
    physical = _rename_entry_physical_sources(path, renamed, name)
    replacements = physical.get("path_replacements") if isinstance(physical, dict) else []
    if isinstance(replacements, list) and replacements:
        data = _replace_paths_in_obj(data, [(str(old), str(new)) for old, new in replacements])
        images = [record for record in data.get("images", []) if isinstance(record, dict)]
        renamed = next(
            (record for record in images if str(record.get("entry_id")) == str(entry_id)),
            renamed,
        )
    if isinstance(physical, dict) and physical.get("case_name"):
        old_sample = str(renamed.get("sample_id") or renamed.get("case_name") or "")
        new_sample = str(physical.get("case_name") or "")
        for record in images:
            if str(record.get("sample_id") or "") == old_sample:
                record["sample_id"] = new_sample
            if str(record.get("case_name") or "") == old_sample:
                record["case_name"] = new_sample
        renamed["sample_id"] = new_sample
        renamed["case_name"] = new_sample
    data["images"] = images
    _write_data_project_payload(path, data)
    loaded = load_histology_data_project(path)
    return {
        **loaded,
        "renamed_entry": _data_project_entry_from_record(path, renamed),
        "physical_rename": physical,
    }


def _find_data_project_entry(project_path: Path, entry_id: str) -> dict[str, Any]:
    for entry in load_histology_data_project(project_path).get("entries", []):
        if str(entry.get("entry_id")) == str(entry_id):
            return entry
    raise ValueError(f"Histology project entry not found: {entry_id}")


def _entry_image_files(entry: dict[str, Any]) -> dict[str, str]:
    raw = entry.get("image_files")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for channel, path in raw.items():
        text = str(path or "").strip()
        if text:
            out[str(channel)] = text
    return out


def _legacy_multiz_brightfield_warnings(entry: dict[str, Any]) -> list[str]:
    image_files = _entry_image_files(entry)
    channels = {str(channel).strip().lower() for channel in image_files}
    if channels != {"brightfield"}:
        return []
    converted = entry.get("converted_from_ets")
    if not isinstance(converted, list):
        return []
    for item in converted:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        try:
            z_count = int(item.get("z_plane_count") or 0)
        except Exception:
            z_count = 0
        if role == "brightfield" and z_count > 1:
            selected = item.get("selected_z")
            selected_text = f" selected z={selected}" if selected is not None else ""
            return [
                "Legacy ETS conversion collapsed a multi-channel ETS into one file labeled Brightfield;"
                f"{selected_text} from the source. Re-scan/recreate this histology project with ETS conversion"
                " enabled so Hoechst/FITC/Cy5 are exported as separate channels."
            ]
    return []


def _entry_warnings(entry: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    raw = entry.get("warnings")
    if isinstance(raw, list):
        warnings.extend(str(item).strip() for item in raw if str(item or "").strip())
    warnings.extend(_legacy_multiz_brightfield_warnings(entry))
    seen: set[str] = set()
    out: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        out.append(warning)
    return out


_EXPECTED_RGB_CHANNEL_WARNING = "Multi-channel/color image stored in one file; expected single-channel XY."
_LOW_BIT_DEPTH_WARNING = "TIFF is not 16-bit; confirm it is suitable for quantification."
_LOW_BIT_DEPTH_PREVIEW_WARNING = "Fluorescence TIFFs are 8-bit; confirm exports are suitable for quantification."


def _ordered_preview_channel_names(image_files: dict[str, str]) -> list[str]:
    keys = [str(channel) for channel in image_files.keys()]
    preferred = ("Hoechst", "DAPI", "FITC", "Cy5", "Mito", "Brightfield", "BF", "Transmitted", "Overview")
    ordered: list[str] = []
    for name in preferred:
        for channel in keys:
            if channel == name and channel not in ordered:
                ordered.append(channel)
    ordered.extend(channel for channel in keys if channel not in ordered)
    return ordered


def _select_preview_image_files(
    image_files: dict[str, str],
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str]:
    if not image_files:
        return {}
    by_lower = {str(channel).strip().lower(): str(channel) for channel in image_files}
    selected: list[str] = []
    for raw in selected_channels or []:
        key = str(raw or "").strip().lower()
        channel = by_lower.get(key)
        if channel and channel not in selected:
            selected.append(channel)
    if not selected:
        ordered = _ordered_preview_channel_names(image_files)
        if _has_fluorescence_channels(tuple(image_files.keys())):
            for preferred in ("FITC", "Cy5", "Hoechst", "DAPI", "Mito"):
                if preferred in image_files:
                    selected = [preferred]
                    break
            if not selected:
                selected = [
                    channel
                    for channel in ordered
                    if _channel_rgb_slot(channel) is not None and not _is_brightfield_label(channel)
                ][:1]
        if not selected:
            selected = ordered[:1]
    return {channel: image_files[channel] for channel in selected if channel in image_files}


def _clean_preview_warnings(
    warnings: list[str],
    channels: list[str] | tuple[str, ...],
) -> list[str]:
    recognized_fluorescence = any(_channel_rgb_slot(str(channel)) is not None for channel in channels)
    low_bit_seen = False
    seen: set[str] = set()
    out: list[str] = []
    for raw in warnings:
        text = str(raw or "").strip()
        if not text:
            continue
        if recognized_fluorescence and _EXPECTED_RGB_CHANNEL_WARNING in text:
            continue
        if _LOW_BIT_DEPTH_WARNING in text:
            if low_bit_seen:
                continue
            low_bit_seen = True
            text = _LOW_BIT_DEPTH_PREVIEW_WARNING
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _no_readable_channels_error(warnings: list[str]) -> ValueError:
    base = "No readable exported image channels found for this sample"
    details = "; ".join(str(item).strip() for item in warnings if str(item).strip())
    if details:
        if len(details) > 800:
            details = details[:797].rstrip() + "..."
        return ValueError(f"{base}: {details}")
    return ValueError(base)


def _channel_rgb_slot(channel: str) -> int | None:
    key = channel.strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", key))
    if key in {"cy5", "red", "macrophage", "cd68"} or tokens & {"cy5", "red", "macrophage", "cd68"}:
        return 0
    if key in {"fitc", "green", "sma", "mito", "mitotracker", "tmrm"} or tokens & {"fitc", "green", "sma", "mito", "mitotracker", "tmrm"}:
        return 1
    if key in {"hoechst", "dapi", "blue"} or tokens & {"hoechst", "dapi", "blue"}:
        return 2
    return None


def _is_brightfield_label(channel: str) -> bool:
    key = str(channel or "").strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", key))
    return key in {"brightfield", "bf", "transmitted"} or bool(
        tokens & {"brightfield", "bf", "transmitted"}
    )


def _as_2d_channel(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] <= 4:
        return data[..., :3].mean(axis=-1).astype(data.dtype, copy=False)
    while data.ndim > 2:
        data = np.max(data, axis=0)
    return data


def _channel_intensity_plane(channel: str, arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    slot = _channel_rgb_slot(str(channel))
    if data.ndim == 3 and data.shape[-1] in {3, 4} and slot is not None:
        return data[..., slot].astype(data.dtype, copy=False)
    return _as_2d_channel(data)


def _preview_display_plane(channel: str, arr: np.ndarray) -> np.ndarray:
    data = np.asarray(_channel_intensity_plane(channel, arr))
    if _channel_rgb_slot(str(channel)) is None:
        return data
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.reshape(-1)
    if finite.size == 0:
        return data
    p50 = float(np.percentile(finite, 50.0))
    p95 = float(np.percentile(finite, 95.0))
    p99 = float(np.percentile(finite, 99.0))
    # Some Olympus ETS-derived fluorescence planes are stored display-inverted
    # with a bright empty background. Invert only for display so ROI navigation
    # is not a blue/white sheet; analysis still uses source intensities below.
    if p50 > 170.0 and p95 > 230.0 and p99 > p50:
        hi = float(np.nanmax(finite))
        lo = float(np.nanmin(finite))
        return (hi + lo - data.astype(np.float32)).astype(np.float32, copy=False)
    return data


def _is_rgb_plane(arr: np.ndarray) -> bool:
    data = np.asarray(arr)
    return data.ndim == 3 and data.shape[-1] in {3, 4}


def _rgb_channels_are_monochrome(arr: np.ndarray) -> bool:
    data = np.asarray(arr)
    if not _is_rgb_plane(data):
        return False
    rgb = data[..., :3]
    if rgb.size == 0:
        return False
    return bool(np.array_equal(rgb[..., 0], rgb[..., 1]) and np.array_equal(rgb[..., 1], rgb[..., 2]))


def _single_channel_preview_warnings(channel: str, plane: np.ndarray) -> list[str]:
    text = str(channel or "").strip() or "Source"
    warnings: list[str] = []
    if _rgb_channels_are_monochrome(plane):
        warnings.append(f"{text}: RGB channels are identical; source preview is monochrome.")
    if _channel_rgb_slot(text) is None:
        if _is_brightfield_label(text):
            warnings.append(
                f"{text}: only a brightfield/transmitted image is indexed for this sample; "
                "SMA/macrophage color composite needs exported fluorescence channels."
            )
        else:
            warnings.append(
                f"{text}: only one unassigned image channel is indexed; "
                "color composite needs separate exported fluorescence channels or a multichannel TIFF."
            )
    warnings.append(f"{text}: preview uses display-only pseudocolor/contrast; analysis uses source intensities.")
    return warnings


def _preview_scale_to_uint8(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data.reshape(-1)
    if finite.size == 0:
        return np.zeros(data.shape, dtype=np.uint8)
    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.4))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    norm = np.clip((data.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    # Keep bright single-channel previews from becoming a flat white sheet.
    norm = np.power(norm, 1.22)
    return np.round(norm * 235.0).astype(np.uint8)


def _pseudo_color_channel(channel: str, plane: np.ndarray) -> np.ndarray:
    value = _preview_scale_to_uint8(_preview_display_plane(channel, plane)).astype(np.float32)
    slot = _channel_rgb_slot(channel)
    rgb = np.zeros(value.shape + (3,), dtype=np.float32)
    if slot is not None:
        rgb[..., slot] = value
        return np.clip(rgb, 0, 255).astype(np.uint8)

    if _is_brightfield_label(channel):
        rgb[..., 0] = value
        rgb[..., 1] = value * 0.68
        rgb[..., 2] = value * 0.18
    else:
        rgb[..., 0] = value
        rgb[..., 1] = value * 0.52
        rgb[..., 2] = value * 0.88
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _preview_plane_shape(arr: np.ndarray) -> tuple[int, int]:
    data = np.asarray(arr)
    if data.ndim < 2:
        raise ValueError(f"Unsupported preview plane shape: {data.shape}")
    return tuple(int(x) for x in data.shape[:2])


def _resize_preview_plane(arr: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    data = np.asarray(arr)
    if _is_rgb_plane(data):
        img = Image.fromarray(_array_to_rgb(data), mode="RGB")
    else:
        img = Image.fromarray(_preview_scale_to_uint8(_as_2d_channel(data)), mode="L")
    img = img.resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def _planes_to_preview_rgb(planes: list[tuple[str, np.ndarray]]) -> np.ndarray:
    if not planes:
        raise ValueError("No readable image planes found for preview")
    recognized = [(_channel_rgb_slot(channel), channel, plane) for channel, plane in planes]
    if len(planes) == 1:
        slot, channel, plane = recognized[0]
        if (
            slot is None
            and not _is_brightfield_label(channel)
            and _is_rgb_plane(plane)
            and not _rgb_channels_are_monochrome(plane)
        ):
            return _array_to_rgb(plane)
        return _pseudo_color_channel(channel, plane)
    first = _preview_scale_to_uint8(_preview_display_plane(planes[0][0], planes[0][1]))
    shape = first.shape[:2]
    rgb = np.zeros(shape + (3,), dtype=np.uint8)
    fallback_slot = 0
    for slot, channel, plane in recognized:
        plane_u8 = _preview_scale_to_uint8(_preview_display_plane(channel, plane))
        if plane_u8.shape[:2] != shape:
            img = Image.fromarray(plane_u8, mode="L").resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
            plane_u8 = np.asarray(img, dtype=np.uint8)
        if slot is None:
            if str(channel).strip().lower() in {"brightfield", "bf", "transmitted"} and len(planes) > 1:
                continue
            slot = fallback_slot % 3
            fallback_slot += 1
        rgb[..., slot] = np.maximum(rgb[..., slot], plane_u8)
    if not np.any(rgb):
        return np.stack([first, first, first], axis=-1)
    return rgb


def _has_fluorescence_channels(channels: list[str] | tuple[str, ...]) -> bool:
    return any(_channel_rgb_slot(str(channel)) is not None for channel in channels)


def _preview_composite_from_image_files(
    image_files: dict[str, str],
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, list[str]]:
    selected_files = _select_preview_image_files(image_files, selected_channels)
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    preview_shape: tuple[int, int] | None = None
    for channel, raw_path in selected_files.items():
        try:
            arr, backend, read_warnings, w, h = _read_project_image_preview(raw_path, max_side=max_side)
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = np.asarray(arr)
        if preview_shape is None:
            preview_shape = _preview_plane_shape(plane)
            image_w = int(w)
            image_h = int(h)
        elif _preview_plane_shape(plane) != preview_shape:
            plane = _resize_preview_plane(plane, preview_shape)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes:
        raise _no_readable_channels_error(warnings)
    if len(planes) == 1:
        warnings.extend(_single_channel_preview_warnings(planes[0][0], planes[0][1]))
    return _planes_to_preview_rgb(planes), "composite_preview:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, channels


def _region_composite_from_image_files(
    image_files: dict[str, str],
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, tuple[int, int, int, int], list[str]]:
    selected_files = _select_preview_image_files(image_files, selected_channels)
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    channels: list[str] = []
    image_w = 0
    image_h = 0
    box: tuple[int, int, int, int] | None = None
    region_shape: tuple[int, int] | None = None
    for channel, raw_path in selected_files.items():
        try:
            arr, backend, read_warnings, w, h, item_box = _read_project_image_region_preview(
                raw_path,
                x,
                y,
                width,
                height,
                max_side=max_side,
            )
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        plane = np.asarray(arr)
        if box is None:
            box = item_box
            image_w = int(w)
            image_h = int(h)
            region_shape = _preview_plane_shape(plane)
        elif _preview_plane_shape(plane) != region_shape:
            plane = _resize_preview_plane(plane, region_shape)
        planes.append((channel, plane))
        channels.append(str(channel))
        backends.append(str(backend))
        warnings.extend(f"{channel}: {message}" for message in read_warnings)
    if not planes or box is None:
        raise _no_readable_channels_error(warnings)
    if len(planes) == 1:
        warnings.extend(_single_channel_preview_warnings(planes[0][0], planes[0][1]))
    return _planes_to_preview_rgb(planes), "composite_region:" + "+".join(sorted(set(backends))), warnings, image_w, image_h, box, channels


def _composite_from_image_files(image_files: dict[str, str]) -> tuple[np.ndarray, str, list[str]]:
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    dtype = np.uint16
    shape: tuple[int, int] | None = None
    first_rgb: np.ndarray | None = None
    first_channel = ""
    skip_brightfield = len(image_files) > 1 and _has_fluorescence_channels(tuple(image_files.keys()))
    for channel, path in image_files.items():
        if skip_brightfield and _is_brightfield_label(channel):
            continue
        try:
            raw = np.asarray(load_image_for_analysis(path))
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        if len(image_files) == 1 and _channel_rgb_slot(channel) is None and _is_rgb_plane(raw):
            first_rgb = raw
            first_channel = str(channel)
            break
        plane = _channel_intensity_plane(channel, raw)
        if shape is None:
            shape = tuple(int(x) for x in plane.shape[:2])
            dtype = plane.dtype
        elif tuple(plane.shape[:2]) != shape:
            warnings.append(f"{channel}: skipped mismatched shape {tuple(plane.shape[:2])}, expected {shape}")
            continue
        planes.append((channel, plane))
    if first_rgb is not None:
        return first_rgb[..., :3], f"exported_tiff_rgb:{first_channel}", warnings
    if not planes or shape is None:
        raise _no_readable_channels_error(warnings)
    composite = np.zeros(shape + (3,), dtype=dtype)
    fallback_slot = 0
    for channel, plane in planes:
        slot = _channel_rgb_slot(channel)
        if slot is None:
            slot = fallback_slot % 3
            fallback_slot += 1
        composite[..., slot] = plane.astype(dtype, copy=False)
    return composite, "exported_tiff_channels", warnings


def _roi_native_bounds(
    rois: list[dict[str, Any]],
    image_width: int,
    image_height: int,
    padding: int = 0,
) -> tuple[int, int, int, int]:
    xs: list[float] = []
    ys: list[float] = []
    for roi in rois:
        for point in roi.get("points", []):
            try:
                xs.append(float(point.get("x")))
                ys.append(float(point.get("y")))
            except Exception:
                continue
    if not xs or not ys:
        return 0, 0, int(image_width), int(image_height)
    x0 = max(0, int(np.floor(min(xs))) - int(padding))
    y0 = max(0, int(np.floor(min(ys))) - int(padding))
    x1 = min(int(image_width), int(np.ceil(max(xs))) + int(padding) + 1)
    y1 = min(int(image_height), int(np.ceil(max(ys))) + int(padding) + 1)
    if x1 <= x0:
        x1 = min(int(image_width), x0 + 1)
    if y1 <= y0:
        y1 = min(int(image_height), y0 + 1)
    return x0, y0, x1, y1


def _roi_crop_padding(params: dict[str, Any]) -> int:
    def rolling_for(prefix: str) -> int:
        mode = str(params.get(f"{prefix}_background_mode") or params.get("background_mode") or "percentile").lower()
        if mode not in {"rolling", "rolling_ball", "local"}:
            return 0
        return int(float(params.get(f"{prefix}_rolling_radius_px") or params.get("rolling_radius_px") or 0))

    rolling = max(0, rolling_for("sma"), rolling_for("macrophage"), rolling_for("dapi"))
    sigma = max(
        0.0,
        float(params.get("smooth_sigma") or 0),
        float(params.get("sma_smooth_sigma") or 0),
        float(params.get("macrophage_smooth_sigma") or 0),
        float(params.get("dapi_smooth_sigma") or 0),
    )
    return max(8, rolling, int(np.ceil(sigma * 4)))


def _translate_rois(rois: list[dict[str, Any]], x0: int, y0: int) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    for roi in rois:
        copy = dict(roi)
        copy["points"] = [
            {"x": float(point["x"]) - x0, "y": float(point["y"]) - y0}
            for point in roi.get("points", [])
        ]
        translated.append(copy)
    return translated


def _translate_and_scale_rois(
    rois: list[dict[str, Any]],
    x0: int,
    y0: int,
    scale: float,
) -> list[dict[str, Any]]:
    translated = _translate_rois(rois, x0, y0)
    if abs(float(scale) - 1.0) < 1e-9:
        return translated
    for roi in translated:
        roi["points"] = [
            {"x": float(point["x"]) * float(scale), "y": float(point["y"]) * float(scale)}
            for point in roi.get("points", [])
        ]
    return translated


def _roi_shrink_percent(params: dict[str, Any]) -> float:
    for key in ("roi_shrink_percent", "roi_shrink_pct", "roi_inset_percent", "roi_inset_pct"):
        if key not in params:
            continue
        try:
            value = float(params.get(key) or 0)
        except Exception:
            value = 0.0
        if np.isfinite(value):
            return max(0.0, min(90.0, value))
    return 0.0


def _polygon_centroid(points: list[dict[str, Any]]) -> tuple[float, float]:
    clean: list[tuple[float, float]] = []
    for point in points:
        try:
            x = float(point.get("x"))
            y = float(point.get("y"))
        except Exception:
            continue
        if np.isfinite(x) and np.isfinite(y):
            clean.append((x, y))
    if not clean:
        return 0.0, 0.0
    if len(clean) < 3:
        return float(np.mean([p[0] for p in clean])), float(np.mean([p[1] for p in clean]))
    signed_area = 0.0
    cx = 0.0
    cy = 0.0
    for idx, (x0, y0) in enumerate(clean):
        x1, y1 = clean[(idx + 1) % len(clean)]
        cross = x0 * y1 - x1 * y0
        signed_area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    signed_area *= 0.5
    if abs(signed_area) < 1e-9:
        return float(np.mean([p[0] for p in clean])), float(np.mean([p[1] for p in clean]))
    return cx / (6.0 * signed_area), cy / (6.0 * signed_area)


def _shrink_roi(roi: dict[str, Any], percent: float) -> dict[str, Any]:
    copy = dict(roi)
    points = roi.get("points") if isinstance(roi.get("points"), list) else []
    copy["points"] = [
        {"x": float(point.get("x") or 0), "y": float(point.get("y") or 0)}
        for point in points
    ]
    shrink = max(0.0, min(90.0, float(percent or 0.0)))
    if shrink <= 0 or len(copy["points"]) < 3:
        return copy
    scale = 1.0 - shrink / 100.0
    cx, cy = _polygon_centroid(copy["points"])
    copy["points"] = [
        {
            "x": float(cx + (float(point["x"]) - cx) * scale),
            "y": float(cy + (float(point["y"]) - cy) * scale),
        }
        for point in copy["points"]
    ]
    copy["analysis_roi_shrink_percent"] = shrink
    return copy


def _analysis_rois_for_params(clean_rois: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    shrink = _roi_shrink_percent(params)
    return [_shrink_roi(roi, shrink) for roi in clean_rois]


def _analysis_max_region_pixels(params: dict[str, Any]) -> int:
    try:
        value = int(float(params.get("analysis_max_region_pixels", 8_000_000)))
    except Exception:
        value = 8_000_000
    return max(0, value)


def _analysis_params_for_region_scale(params: dict[str, Any], scale: float) -> dict[str, Any]:
    try:
        scale_value = float(scale or 1.0)
    except Exception:
        scale_value = 1.0
    if scale_value <= 0 or abs(scale_value - 1.0) < 1e-9:
        return params
    area_scale = scale_value * scale_value
    out = dict(params)

    def native_int(value: Any) -> int:
        try:
            native = int(round(float(value)))
        except Exception:
            return 0
        return max(0, native)

    def scaled_area(native_value: Any, *, is_max: bool = False) -> int:
        try:
            native = int(round(float(native_value)))
        except Exception:
            native = 0
        if native <= 0:
            return 0 if is_max else 1
        return max(1, int(round(native * area_scale)))

    global_min = params.get("min_positive_area_px", 12)
    global_max = params.get("max_positive_area_px", 0)
    for prefix in ("sma", "macrophage"):
        min_keys = (f"{prefix}_min_area_px", f"{prefix}_min_positive_area_px")
        max_keys = (f"{prefix}_max_area_px", f"{prefix}_max_positive_area_px")
        native_min = next((params[key] for key in min_keys if key in params), global_min)
        native_max = next((params[key] for key in max_keys if key in params), global_max)
        out[f"{prefix}_min_area_px_native"] = native_int(native_min)
        out[f"{prefix}_min_area_px"] = scaled_area(native_min)
        out[f"{prefix}_max_area_px_native"] = native_int(native_max)
        out[f"{prefix}_max_area_px"] = scaled_area(native_max, is_max=True)

    dapi_min = params.get("dapi_min_area_px", 8)
    out["dapi_min_area_px_native"] = native_int(dapi_min)
    out["dapi_min_area_px"] = scaled_area(dapi_min)
    return out


def _rescale_result_counts_for_native_pixels(results: list[dict[str, Any]], scale: float) -> None:
    scale = float(scale or 1.0)
    if scale <= 0 or abs(scale - 1.0) < 1e-9:
        return
    factor = 1.0 / (scale * scale)
    count_keys = (
        "area_px",
        "analysis_area_px",
        "dapi_positive_px",
        "sma_positive_px",
        "macrophage_positive_px",
        "double_positive_px",
    )
    for row in results:
        for key in count_keys:
            if key in row:
                row[key] = int(round(float(row.get(key) or 0) * factor))
        area_px = max(1, int(row.get("area_px") or 0))
        analysis_area_px = max(1, int(row.get("analysis_area_px") or 0))
        row["dapi_positive_fraction_roi"] = float((row.get("dapi_positive_px") or 0) / area_px)
        row["sma_positive_fraction"] = float((row.get("sma_positive_px") or 0) / analysis_area_px)
        row["sma_positive_fraction_roi"] = float((row.get("sma_positive_px") or 0) / area_px)
        row["macrophage_positive_fraction"] = float((row.get("macrophage_positive_px") or 0) / analysis_area_px)
        row["macrophage_positive_fraction_roi"] = float((row.get("macrophage_positive_px") or 0) / area_px)
        row["double_positive_fraction"] = float((row.get("double_positive_px") or 0) / analysis_area_px)
        row["double_positive_fraction_roi"] = float((row.get("double_positive_px") or 0) / area_px)
        row["analysis_scale"] = scale
        row["analysis_pixel_area_scale_factor"] = factor


def _composite_region_from_image_files(
    image_files: dict[str, str],
    box: tuple[int, int, int, int],
    max_pixels: int | None = None,
) -> tuple[np.ndarray, str, list[str], int, int, tuple[int, int, int, int], float]:
    x0, y0, x1, y1 = box
    planes: list[tuple[str, np.ndarray]] = []
    warnings: list[str] = []
    backends: list[str] = []
    dtype = np.uint16
    shape: tuple[int, int] | None = None
    native_w = 0
    native_h = 0
    first_rgb: np.ndarray | None = None
    first_channel = ""
    actual_box: tuple[int, int, int, int] | None = None
    skip_brightfield = len(image_files) > 1 and _has_fluorescence_channels(tuple(image_files.keys()))
    for channel, path in image_files.items():
        if skip_brightfield and _is_brightfield_label(channel):
            continue
        try:
            raw, backend, w, h, item_box, item_scale = _read_project_image_region_for_analysis(
                path,
                x0,
                y0,
                max(1, x1 - x0),
                max(1, y1 - y0),
                max_pixels=max_pixels,
            )
        except Exception as exc:
            warnings.append(f"{channel}: {exc}")
            continue
        if native_w <= 0 or native_h <= 0:
            native_w = int(w)
            native_h = int(h)
            actual_box = item_box
            scale = float(item_scale)
        if len(image_files) == 1 and _channel_rgb_slot(channel) is None and _is_rgb_plane(raw):
            first_rgb = np.asarray(raw)[..., :3]
            first_channel = str(channel)
            backends.append(str(backend))
            break
        plane = _channel_intensity_plane(channel, raw)
        if shape is None:
            shape = tuple(int(v) for v in plane.shape[:2])
            dtype = plane.dtype
        elif tuple(plane.shape[:2]) != shape:
            warnings.append(f"{channel}: skipped mismatched region shape {tuple(plane.shape[:2])}, expected {shape}")
            continue
        planes.append((channel, plane))
        backends.append(str(backend))
    if first_rgb is not None:
        return (
            first_rgb,
            f"exported_tiff_rgb_region:{first_channel}:{'+'.join(sorted(set(backends)))}",
            warnings,
            native_w,
            native_h,
            actual_box or box,
            scale if "scale" in locals() else 1.0,
        )
    if not planes or shape is None:
        raise _no_readable_channels_error(warnings)
    composite = np.zeros(shape + (3,), dtype=dtype)
    fallback_slot = 0
    for channel, plane in planes:
        slot = _channel_rgb_slot(channel)
        if slot is None:
            slot = fallback_slot % 3
            fallback_slot += 1
        composite[..., slot] = plane.astype(dtype, copy=False)
    return (
        composite,
        "exported_tiff_channel_regions:" + "+".join(sorted(set(backends))),
        warnings,
        native_w,
        native_h,
        actual_box or box,
        scale if "scale" in locals() else 1.0,
    )


def _analysis_image_files(image_files: dict[str, str], params: dict[str, Any]) -> dict[str, str]:
    if len(image_files) <= 1:
        return image_files
    needed_slots: set[int] = set()
    for prefix, default_channel in (("sma", "fitc"), ("macrophage", "cy5")):
        slot = _channel_rgb_slot(str(params.get(f"{prefix}_channel") or default_channel))
        if slot is not None:
            needed_slots.add(slot)
    dapi_enabled = str(params.get("dapi_mask_enabled", False)).strip().lower() in {"1", "true", "yes", "y", "on"}
    if dapi_enabled:
        slot = _channel_rgb_slot(str(params.get("dapi_channel") or "dapi"))
        if slot is not None:
            needed_slots.add(slot)
    selected = {
        channel: path
        for channel, path in image_files.items()
        if _channel_rgb_slot(str(channel)) in needed_slots
    }
    return selected or image_files


def _read_data_project_entry_image(entry: dict[str, Any]) -> tuple[np.ndarray, str, list[str]]:
    image_files = _entry_image_files(entry)
    if image_files:
        return _composite_from_image_files(image_files)
    image_path = entry.get("image_path", "")
    if not image_path:
        raise ValueError("Selected project entry has no image path")
    return _read_project_image(image_path)


def _entry_native_dimensions(entry: dict[str, Any]) -> tuple[int, int]:
    records = entry.get("image_records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            shape = record.get("shape")
            if isinstance(shape, (list, tuple)) and len(shape) >= 2:
                try:
                    return int(shape[1]), int(shape[0])
                except Exception:
                    pass
    image_files = _entry_image_files(entry)
    candidates = list(image_files.values())
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        candidates.append(direct)
    for raw_path in candidates:
        try:
            shape = estimate_image_load_size(raw_path).get("shape")
            if isinstance(shape, tuple) and len(shape) >= 2:
                return int(shape[1]), int(shape[0])
        except Exception:
            continue
    raise ValueError("Could not determine original TIFF dimensions for ROI analysis")


def _entry_pixel_calibration(entry: dict[str, Any]) -> dict[str, Any]:
    records = entry.get("image_records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            px_w = _positive_float(record.get("pixel_width_um"))
            px_h = _positive_float(record.get("pixel_height_um")) or px_w
            if px_w is not None and px_h is not None:
                return {
                    "has_physical_scale": True,
                    "pixel_width_um": float(px_w),
                    "pixel_height_um": float(px_h),
                    "pixel_area_um2": float(px_w * px_h),
                    "source": str(record.get("pixel_size_source") or "project image record"),
                }
    image_files = _entry_image_files(entry)
    candidates = list(image_files.values())
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        candidates.append(direct)
    seen: set[str] = set()
    for raw_path in candidates:
        key = str(raw_path)
        if not key or key in seen:
            continue
        seen.add(key)
        calibration = _infer_tiff_pixel_calibration(raw_path)
        if calibration:
            return calibration
    return {"has_physical_scale": False}


def _apply_physical_calibration_to_results(
    results: list[dict[str, Any]],
    calibration: dict[str, Any],
) -> None:
    if not calibration.get("has_physical_scale"):
        return
    pixel_area = _positive_float(calibration.get("pixel_area_um2"))
    pixel_w = _positive_float(calibration.get("pixel_width_um"))
    pixel_h = _positive_float(calibration.get("pixel_height_um")) or pixel_w
    if pixel_area is None or pixel_w is None or pixel_h is None:
        return
    for row in results:
        area_um2 = float(row.get("area_px", 0) or 0) * pixel_area
        analysis_area_um2 = float(row.get("analysis_area_px", 0) or 0) * pixel_area
        row["pixel_width_um"] = float(pixel_w)
        row["pixel_height_um"] = float(pixel_h)
        row["pixel_area_um2"] = float(pixel_area)
        row["area_um2"] = area_um2
        row["area_mm2"] = area_um2 / 1_000_000.0
        row["analysis_area_um2"] = analysis_area_um2
        row["analysis_area_mm2"] = analysis_area_um2 / 1_000_000.0
        roi_area_mm2 = max(area_um2 / 1_000_000.0, 1e-12)
        analysis_area_mm2 = max(analysis_area_um2 / 1_000_000.0, 1e-12)
        for prefix in ("sma", "macrophage"):
            positive_px = float(row.get(f"{prefix}_positive_px", 0) or 0)
            object_count = float(row.get(f"{prefix}_object_count", 0) or 0)
            integrated = float(row.get(f"{prefix}_integrated_density", 0) or 0)
            row[f"{prefix}_positive_area_um2"] = positive_px * pixel_area
            row[f"{prefix}_positive_area_mm2"] = positive_px * pixel_area / 1_000_000.0
            row[f"{prefix}_object_density_per_mm2"] = object_count / roi_area_mm2
            row[f"{prefix}_object_density_analysis_per_mm2"] = object_count / analysis_area_mm2
            row[f"{prefix}_integrated_density_um2"] = integrated * pixel_area
        double_px = float(row.get("double_positive_px", 0) or 0)
        row["double_positive_area_um2"] = double_px * pixel_area
        row["double_positive_area_mm2"] = double_px * pixel_area / 1_000_000.0


def _entry_preview_image_path(entry: dict[str, Any]) -> str:
    direct = str(entry.get("image_path") or entry.get("source_path") or "").strip()
    if direct:
        return direct
    image_files = _entry_image_files(entry)
    for preferred in ("Hoechst", "FITC", "Cy5", "Mito", "Brightfield", "Overview"):
        if preferred in image_files:
            return image_files[preferred]
    return next(iter(image_files.values()), "")


def load_histology_data_project_image_preview(
    project_path: str | Path,
    entry_id: str,
    max_side: int = 1600,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    analysis = _load_data_project_entry_analysis(path, str(entry_id))
    preview_max = max(256, min(int(max_side), 2400))
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, channels = _preview_composite_from_image_files(
            image_files,
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h = _read_project_image_preview(preview_path, max_side=preview_max)
    if not arr.size:
        raise ValueError("Selected project entry has no image path")
    available_channels = _ordered_preview_channel_names(image_files)
    warnings = _clean_preview_warnings([*_entry_warnings(entry), *warnings], channels or available_channels)
    return {
        **entry,
        "backend": backend,
        "available_channels": available_channels,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "rois": analysis.get("rois") if isinstance(analysis.get("rois"), list) else [],
        "analyses": analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else [],
        "warnings": warnings,
    }


def load_histology_data_project_image_region_preview(
    project_path: str | Path,
    entry_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    preview_max = max(256, min(int(max_side), 2600))
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, box, channels = _region_composite_from_image_files(
            image_files,
            x,
            y,
            width,
            height,
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h, box = _read_project_image_region_preview(
            preview_path,
            x,
            y,
            width,
            height,
            max_side=preview_max,
        )
    available_channels = _ordered_preview_channel_names(image_files)
    warnings = _clean_preview_warnings([*_entry_warnings(entry), *warnings], channels or available_channels)
    x0, y0, x1, y1 = box
    return {
        "entry_id": str(entry_id),
        "backend": backend,
        "available_channels": available_channels,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "region_x": int(x0),
        "region_y": int(y0),
        "region_width": int(x1 - x0),
        "region_height": int(y1 - y0),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "warnings": warnings,
    }


def _update_data_project_entry_counts(project_path: Path, entry_id: str) -> None:
    data = _load_data_project_payload(project_path)
    images = [record for record in data.get("images", []) if isinstance(record, dict)]
    analysis = _load_data_project_entry_analysis(project_path, entry_id)
    rois = analysis.get("rois") if isinstance(analysis.get("rois"), list) else []
    analyses = analysis.get("analyses") if isinstance(analysis.get("analyses"), list) else []
    latest = analyses[-1] if analyses and isinstance(analyses[-1], dict) else {}
    for record in images:
        if str(record.get("entry_id")) != str(entry_id):
            continue
        record["roi_count"] = len(rois)
        record["analysis_count"] = len(analyses)
        record["analysis_path"] = str(_data_project_entry_analysis_path(project_path, entry_id))
        record["geojson_path"] = str(_data_project_entry_geojson_path(project_path, entry_id))
        record["latest_analysis_at"] = latest.get("created_at", "") if isinstance(latest, dict) else ""
        record["updated_at"] = _now_iso()
        break
    data["images"] = images
    _write_data_project_payload(project_path, data)


def save_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    append_analysis: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    clean_rois = _clean_rois(rois)
    existing = _load_data_project_entry_analysis(path, str(entry_id))
    analyses = existing.get("analyses") if isinstance(existing.get("analyses"), list) else []
    if analysis:
        analysis = dict(analysis)
        analysis.setdefault("created_at", _now_iso())
        analyses = [*analyses, analysis] if append_analysis else [analysis]
    payload = {
        "version": ANALYSIS_VERSION,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "display_name": entry.get("display_name", entry.get("image_name", "")),
        "image_path": entry.get("image_path", ""),
        "source_path": entry.get("source_path", entry.get("image_path", "")),
        "case_name": entry.get("case_name", ""),
        "associated_files": entry.get("associated_files", []),
        "image_files": entry.get("image_files", {}),
        "image_records": entry.get("image_records", []),
        "sample_id": entry.get("sample_id", ""),
        "case_dir": entry.get("case_dir", ""),
        "physical_rename_dir": entry.get("physical_rename_dir", ""),
        "converted_from_ets": entry.get("converted_from_ets", []),
        "converted_tiff_paths": entry.get("converted_tiff_paths", []),
        "conversion_roles": entry.get("conversion_roles", {}),
        "ets_conversion_count": entry.get("ets_conversion_count", 0),
        "analysis_folder": entry.get("analysis_folder", ""),
        "manifest_path": entry.get("manifest_path", ""),
        "parameters_path": entry.get("parameters_path", ""),
        "roi_measurements_path": entry.get("roi_measurements_path", ""),
        "label_vsi_path": entry.get("label_vsi_path", ""),
        "overview_vsi_path": entry.get("overview_vsi_path", ""),
        "updated_at": _now_iso(),
        "rois": clean_rois,
        "analyses": analyses,
    }
    analysis_path = _data_project_entry_analysis_path(path, str(entry_id))
    _write_json(analysis_path, payload)
    latest_measurements = {}
    if analyses and isinstance(analyses[-1], dict):
        latest_measurements = {str(item.get("roi_id")): item for item in analyses[-1].get("results", [])}
    geojson_path = _data_project_entry_geojson_path(path, str(entry_id))
    _write_json(geojson_path, _geojson(clean_rois, latest_measurements))
    _update_data_project_entry_counts(path, str(entry_id))
    return {
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "index_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "cache_layout": _data_project_cache_layout(path),
        "entry_id": str(entry_id),
        "roi_count": len(clean_rois),
        "analysis_count": len(analyses),
        "analysis_path": str(analysis_path),
        "geojson_path": str(geojson_path),
        "summary_path": str(path),
        "rois": clean_rois,
        "latest_analysis": analyses[-1] if analyses else {},
    }


def _analysis_defaults(parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "dapi_channel": "dapi",
        "dapi_threshold_method": "otsu",
        "dapi_mask_enabled": False,
        "sma_channel": "fitc",
        "sma_invert_signal": "off",
        "sma_threshold_method": "otsu",
        "sma_threshold": 120,
        "macrophage_channel": "cy5",
        "macrophage_invert_signal": "off",
        "macrophage_threshold_method": "otsu",
        "macrophage_threshold": 120,
        "background_mode": "percentile",
        "background_percentile": 10,
        "rolling_radius_px": 35,
        "smooth_sigma": 1.0,
        "threshold_percentile": 97.5,
        "threshold_std_k": 2.0,
        "min_positive_area_px": 12,
        "roi_shrink_percent": 0,
        "summary_group_by": "sample",
        "summary_aggregate_rois_by_entry": True,
        "exclude_zero_observations": False,
        **dict(parameters or {}),
    }


def _analyze_marker_rois(
    arr: np.ndarray,
    clean_rois: list[dict[str, Any]],
    params: dict[str, Any],
) -> tuple[int, int, list[dict[str, Any]]]:
    h, w = arr.shape[:2]
    results: list[dict[str, Any]] = []
    for roi in clean_rois:
        mask = _mask_for_roi(w, h, roi)
        area_px = int(np.count_nonzero(mask))
        analysis_mask, dapi = _dapi_analysis_mask(arr, mask, params)
        sma, sma_positive = _marker_analysis(arr, mask, analysis_mask, params, "sma", "fitc")
        macrophage, macrophage_positive = _marker_analysis(
            arr, mask, analysis_mask, params, "macrophage", "cy5"
        )
        double_positive = np.count_nonzero(sma_positive & macrophage_positive)
        analysis_area_px = int(np.count_nonzero(analysis_mask))
        results.append(
            {
                "roi_id": roi["id"],
                "roi_label": roi["label"],
                "area_px": area_px,
                "area_fraction_image": float(area_px / max(1, w * h)),
                "analysis_area_px": analysis_area_px,
                "dapi_channel": dapi["channel"],
                "dapi_threshold": dapi["threshold"],
                "dapi_threshold_method": dapi["threshold_method"],
                "dapi_positive_px": dapi["positive_px"],
                "dapi_positive_fraction_roi": dapi["positive_fraction_roi"],
                "dapi_object_count": dapi["object_count"],
                "sma_channel": sma["channel"],
                "sma_invert_signal": sma.get("invert_signal", "off"),
                "sma_signal_inverted": bool(sma.get("signal_inverted", False)),
                "sma_background": sma["background"],
                "sma_threshold": sma["threshold"],
                "sma_threshold_method": sma["threshold_method"],
                "sma_min_area_px": sma["min_area_px"],
                "sma_min_area_px_native": sma.get("min_area_px_native", sma["min_area_px"]),
                "sma_max_area_px": sma["max_area_px"],
                "sma_max_area_px_native": sma.get("max_area_px_native", sma["max_area_px"]),
                "sma_opening_px": sma.get("opening_px", 2),
                "sma_mean": sma["mean_corrected"],
                "sma_max": sma["max_corrected"],
                "sma_integrated_density": sma["integrated_density"],
                "sma_positive_px": sma["positive_px"],
                "sma_positive_fraction": sma["positive_fraction"],
                "sma_positive_fraction_roi": sma["positive_fraction_roi"],
                "sma_positive_mean": sma["positive_mean_corrected"],
                "sma_object_count": sma["object_count"],
                "macrophage_channel": macrophage["channel"],
                "macrophage_invert_signal": macrophage.get("invert_signal", "off"),
                "macrophage_signal_inverted": bool(macrophage.get("signal_inverted", False)),
                "macrophage_background": macrophage["background"],
                "macrophage_threshold": macrophage["threshold"],
                "macrophage_threshold_method": macrophage["threshold_method"],
                "macrophage_min_area_px": macrophage["min_area_px"],
                "macrophage_min_area_px_native": macrophage.get("min_area_px_native", macrophage["min_area_px"]),
                "macrophage_max_area_px": macrophage["max_area_px"],
                "macrophage_max_area_px_native": macrophage.get("max_area_px_native", macrophage["max_area_px"]),
                "macrophage_opening_px": macrophage.get("opening_px", 2),
                "macrophage_mean": macrophage["mean_corrected"],
                "macrophage_max": macrophage["max_corrected"],
                "macrophage_integrated_density": macrophage["integrated_density"],
                "macrophage_positive_px": macrophage["positive_px"],
                "macrophage_positive_fraction": macrophage["positive_fraction"],
                "macrophage_positive_fraction_roi": macrophage["positive_fraction_roi"],
                "macrophage_positive_mean": macrophage["positive_mean_corrected"],
                "macrophage_object_count": macrophage["object_count"],
                "double_positive_px": int(double_positive),
                "double_positive_fraction": float(double_positive / max(1, analysis_area_px)),
                "double_positive_fraction_roi": float(double_positive / max(1, area_px)),
            }
        )
    return h, w, results


def _run_histology_data_project_roi_analysis(
    path: Path,
    entry_id: str,
    entry: dict[str, Any],
    clean_rois: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    image_path = str(entry.get("image_path") or "")
    image_files = _entry_image_files(entry)
    analysis_rois = _analysis_rois_for_params(clean_rois, params)
    analysis_region: dict[str, int] = {}
    analysis_regions: list[dict[str, Any]] = []
    if image_files:
        image_files_for_analysis = _analysis_image_files(image_files, params)
        native_w, native_h = _entry_native_dimensions(entry)
        backend_parts: list[str] = []
        warnings: list[str] = []
        results: list[dict[str, Any]] = []
        max_pixels = _analysis_max_region_pixels(params)
        for original_roi, roi in zip(clean_rois, analysis_rois, strict=False):
            padded_box = _roi_native_bounds(
                [roi],
                native_w,
                native_h,
                padding=_roi_crop_padding(params),
            )
            arr, item_backend, item_warnings, item_w, item_h, actual_box, scale = _composite_region_from_image_files(
                image_files_for_analysis,
                padded_box,
                max_pixels=max_pixels,
            )
            backend_parts.append(str(item_backend))
            warnings.extend(item_warnings)
            if scale < 1.0:
                warnings.append(
                    f"{roi.get('label') or roi.get('id')}: ROI region was downsampled to {scale:.4f} "
                    "for large-image positive-area analysis."
                )
            translated_rois = _translate_and_scale_rois([roi], actual_box[0], actual_box[1], scale)
            scale_params = _analysis_params_for_region_scale(params, scale)
            _crop_h, _crop_w, roi_results = _analyze_marker_rois(arr, translated_rois, scale_params)
            _rescale_result_counts_for_native_pixels(roi_results, scale)
            for row in roi_results:
                row["roi_id"] = str(original_roi.get("id") or row.get("roi_id") or "")
                row["roi_label"] = str(original_roi.get("label") or row.get("roi_label") or "")
                row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
            results.extend(roi_results)
            analysis_regions.append(
                {
                    "roi_id": str(original_roi.get("id") or roi.get("id") or ""),
                    "x": int(actual_box[0]),
                    "y": int(actual_box[1]),
                    "width": int(actual_box[2] - actual_box[0]),
                    "height": int(actual_box[3] - actual_box[1]),
                    "analysis_scale": float(scale),
                    "analysis_width": int(arr.shape[1]),
                    "analysis_height": int(arr.shape[0]),
                }
            )
        backend = "+".join(sorted(set(backend_parts))) if backend_parts else "exported_tiff_channel_regions"
        w = int(native_w)
        h = int(native_h)
        w = int(w or native_w)
        h = int(h or native_h)
        for result in results:
            result["area_fraction_image"] = float(result.get("area_px", 0) / max(1, w * h))
        if analysis_regions:
            x0 = min(int(region["x"]) for region in analysis_regions)
            y0 = min(int(region["y"]) for region in analysis_regions)
            x1 = max(int(region["x"]) + int(region["width"]) for region in analysis_regions)
            y1 = max(int(region["y"]) + int(region["height"]) for region in analysis_regions)
            analysis_region = {
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
            }
    else:
        arr, backend, warnings = _read_data_project_entry_image(entry)
        h, w, results = _analyze_marker_rois(arr, analysis_rois, params)
        for row in results:
            row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
    calibration = _entry_pixel_calibration(entry)
    _apply_physical_calibration_to_results(results, calibration)

    analysis = {
        "created_at": _now_iso(),
        "protocol": ETS_PROTOCOL,
        "kind": ETS_DATA_PROJECT_KIND,
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": entry.get("image_name", ""),
        "display_name": entry.get("display_name", entry.get("image_name", "")),
        "image_path": image_path,
        "source_path": entry.get("source_path", image_path),
        "case_name": entry.get("case_name", ""),
        "associated_files": entry.get("associated_files", []),
        "image_files": entry.get("image_files", {}),
        "image_records": entry.get("image_records", []),
        "sample_id": entry.get("sample_id", ""),
        "case_dir": entry.get("case_dir", ""),
        "physical_rename_dir": entry.get("physical_rename_dir", ""),
        "converted_from_ets": entry.get("converted_from_ets", []),
        "converted_tiff_paths": entry.get("converted_tiff_paths", []),
        "conversion_roles": entry.get("conversion_roles", {}),
        "ets_conversion_count": entry.get("ets_conversion_count", 0),
        "analysis_folder": entry.get("analysis_folder", ""),
        "manifest_path": entry.get("manifest_path", ""),
        "parameters_path": entry.get("parameters_path", ""),
        "roi_measurements_path": entry.get("roi_measurements_path", ""),
        "label_vsi_path": entry.get("label_vsi_path", ""),
        "overview_vsi_path": entry.get("overview_vsi_path", ""),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "analysis_region": analysis_region,
        "analysis_regions": analysis_regions,
        "roi_shrink_percent": _roi_shrink_percent(params),
        "calibration": calibration,
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    return {
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


def analyze_histology_data_project_rois(
    project_path: str | Path,
    entry_id: str,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")

    params = _analysis_defaults(parameters)
    payload = _run_histology_data_project_roi_analysis(path, str(entry_id), entry, clean_rois, params)
    analysis = payload["analysis"]
    results = payload["results"]
    backend = payload["backend"]
    w = int(payload["width"])
    h = int(payload["height"])
    warnings = payload["warnings"]
    saved = save_histology_data_project_rois(path, str(entry_id), clean_rois, analysis=analysis)
    return {
        **saved,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


def _saved_or_external_entry_rois(project_path: Path, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    entry_id = str(entry.get("entry_id") or "")
    saved = _load_data_project_entry_analysis(project_path, entry_id) if entry_id else {}
    saved_rois = saved.get("rois") if isinstance(saved.get("rois"), list) else entry.get("rois")
    clean_rois = _clean_rois(saved_rois if isinstance(saved_rois, list) else [])
    if clean_rois:
        return clean_rois, "project"
    clean_rois, external_rois_path = _load_external_entry_rois(project_path, entry)
    if clean_rois:
        return clean_rois, external_rois_path or "external"
    return [], ""


def _select_roi_for_debug(
    rois: list[dict[str, Any]],
    roi_id: str = "",
    roi_index: int = 0,
) -> tuple[int, dict[str, Any]]:
    wanted_id = str(roi_id or "").strip()
    if wanted_id:
        for index, roi in enumerate(rois):
            if str(roi.get("id") or "") == wanted_id:
                return index, roi
        raise ValueError(f"ROI not found: {wanted_id}")
    index = int(roi_index or 0)
    if index < 0 or index >= len(rois):
        raise ValueError(f"ROI index {index} is outside the available ROI range 0-{max(0, len(rois) - 1)}")
    return index, rois[index]


def _roi_points_for_preview(
    roi: dict[str, Any],
    box: tuple[int, int, int, int],
    preview_w: int,
    preview_h: int,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = box
    scale_x = float(preview_w) / max(1.0, float(x1 - x0))
    scale_y = float(preview_h) / max(1.0, float(y1 - y0))
    points: list[tuple[float, float]] = []
    for point in roi.get("points", []):
        try:
            x = (float(point.get("x")) - x0) * scale_x
            y = (float(point.get("y")) - y0) * scale_y
        except Exception:
            continue
        if np.isfinite(x) and np.isfinite(y):
            points.append((x, y))
    return points


def _draw_roi_debug_overlay(
    arr: np.ndarray,
    box: tuple[int, int, int, int],
    original_roi: dict[str, Any],
    adjusted_roi: dict[str, Any],
) -> np.ndarray:
    rgb = _array_to_rgb(arr)
    img = Image.fromarray(rgb, mode="RGB").convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    line_w = max(2, int(round(max(img.size) / 320)))

    def draw_roi(roi: dict[str, Any], line: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
        pts = _roi_points_for_preview(roi, box, img.width, img.height)
        if len(pts) >= 3:
            draw.polygon(pts, fill=fill)
            draw.line([*pts, pts[0]], fill=line, width=line_w, joint="curve")
        elif len(pts) >= 2:
            draw.line(pts, fill=line, width=line_w)
        for x, y in pts:
            r = max(3, line_w + 1)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=line)

    draw_roi(original_roi, (255, 212, 72, 235), (255, 212, 72, 38))
    draw_roi(adjusted_roi, (0, 210, 255, 245), (0, 210, 255, 34))
    img.alpha_composite(overlay)
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def _roi_debug_preview(
    entry: dict[str, Any],
    original_roi: dict[str, Any],
    adjusted_roi: dict[str, Any],
    params: dict[str, Any],
    max_side: int,
    selected_channels: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    preview_max = max(256, min(int(max_side), 1800))
    native_w, native_h = _entry_native_dimensions(entry)
    padding = max(20, _roi_crop_padding(params))
    x0, y0, x1, y1 = _roi_native_bounds(
        [original_roi, adjusted_roi],
        native_w,
        native_h,
        padding=padding,
    )
    image_files = _entry_image_files(entry)
    channels: list[str] = []
    if image_files:
        arr, backend, warnings, w, h, box, channels = _region_composite_from_image_files(
            image_files,
            x0,
            y0,
            max(1, x1 - x0),
            max(1, y1 - y0),
            preview_max,
            selected_channels=selected_channels,
        )
    else:
        preview_path = _entry_preview_image_path(entry)
        if not preview_path:
            raise ValueError("Selected project entry has no image path")
        arr, backend, warnings, w, h, box = _read_project_image_region_preview(
            preview_path,
            x0,
            y0,
            max(1, x1 - x0),
            max(1, y1 - y0),
            max_side=preview_max,
        )
    overlay = _draw_roi_debug_overlay(arr, box, original_roi, adjusted_roi)
    bx0, by0, bx1, by1 = box
    return {
        "backend": backend,
        "preview_channels": channels,
        "width": int(w),
        "height": int(h),
        "region_x": int(bx0),
        "region_y": int(by0),
        "region_width": int(bx1 - bx0),
        "region_height": int(by1 - by0),
        "preview_width": int(overlay.shape[1]),
        "preview_height": int(overlay.shape[0]),
        "img": _png_b64(overlay, max_side=preview_max),
        "warnings": warnings,
    }


def _roi_debug_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    rows = analysis.get("results") if isinstance(analysis.get("results"), list) else []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}

    def marker_block(marker: str) -> dict[str, Any]:
        return {
            "positive_area_ratio": _finite_float(row.get(f"{marker}_positive_fraction")),
            "positive_area_ratio_roi": _finite_float(row.get(f"{marker}_positive_fraction_roi")),
            "positive_px": int(_finite_float(row.get(f"{marker}_positive_px"), 0)),
            "threshold": _finite_float(row.get(f"{marker}_threshold")),
            "threshold_method": str(row.get(f"{marker}_threshold_method") or ""),
            "background": _finite_float(row.get(f"{marker}_background")),
            "mean": _finite_float(row.get(f"{marker}_mean")),
            "max": _finite_float(row.get(f"{marker}_max")),
            "object_count": int(_finite_float(row.get(f"{marker}_object_count"), 0)),
        }

    return {
        "roi_id": str(row.get("roi_id") or ""),
        "roi_label": str(row.get("roi_label") or ""),
        "area_px": int(_finite_float(row.get("area_px"), 0)),
        "analysis_area_px": int(_finite_float(row.get("analysis_area_px"), 0)),
        "dapi_positive_px": int(_finite_float(row.get("dapi_positive_px"), 0)),
        "sma": marker_block("sma"),
        "macrophage": marker_block("macrophage"),
        "double_positive_area_ratio": _finite_float(row.get("double_positive_fraction")),
        "row": row,
    }


def _roi_debug_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {
        "area_px": int(after.get("area_px") or 0) - int(before.get("area_px") or 0),
        "analysis_area_px": int(after.get("analysis_area_px") or 0) - int(before.get("analysis_area_px") or 0),
    }
    for marker in ("sma", "macrophage"):
        before_marker = before.get(marker) if isinstance(before.get(marker), dict) else {}
        after_marker = after.get(marker) if isinstance(after.get(marker), dict) else {}
        delta[marker] = {
            "positive_area_ratio": _finite_float(after_marker.get("positive_area_ratio"))
            - _finite_float(before_marker.get("positive_area_ratio")),
            "positive_px": int(after_marker.get("positive_px") or 0) - int(before_marker.get("positive_px") or 0),
            "threshold": _finite_float(after_marker.get("threshold")) - _finite_float(before_marker.get("threshold")),
            "object_count": int(after_marker.get("object_count") or 0) - int(before_marker.get("object_count") or 0),
        }
    return delta


def debug_histology_data_project_roi(
    project_path: str | Path,
    entry_id: str,
    roi_id: str = "",
    roi_index: int = 0,
    parameters: dict[str, Any] | None = None,
    before_parameters: dict[str, Any] | None = None,
    max_side: int = 900,
    selected_channels: list[str] | tuple[str, ...] | None = None,
    include_preview: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    entry = _find_data_project_entry(path, str(entry_id))
    rois, roi_source = _saved_or_external_entry_rois(path, entry)
    if not rois:
        raise ValueError("No saved ROI annotations are available for the selected image")
    selected_index, roi = _select_roi_for_debug(rois, roi_id=roi_id, roi_index=roi_index)
    after_params = _analysis_defaults(parameters)
    if before_parameters is None:
        before_raw = dict(after_params)
        before_raw["roi_shrink_percent"] = 0
    else:
        before_raw = dict(before_parameters)
    before_params = _analysis_defaults(before_raw)
    before_analysis_payload = _run_histology_data_project_roi_analysis(
        path,
        str(entry_id),
        entry,
        [roi],
        before_params,
    )
    after_analysis_payload = _run_histology_data_project_roi_analysis(
        path,
        str(entry_id),
        entry,
        [roi],
        after_params,
    )
    adjusted_roi = _shrink_roi(roi, _roi_shrink_percent(after_params))
    preview = (
        _roi_debug_preview(
            entry,
            roi,
            adjusted_roi,
            after_params,
            max_side=max_side,
            selected_channels=selected_channels,
        )
        if include_preview
        else {}
    )
    before = _roi_debug_metrics(before_analysis_payload["analysis"])
    after = _roi_debug_metrics(after_analysis_payload["analysis"])
    sample_number = ""
    treatment = ""
    for key in ("image_name", "display_name", "sample_id", "case_name"):
        sample_number, treatment = _parse_image_sample_and_treatment(entry.get(key))
        if treatment:
            break
    warnings = [
        *list(before_analysis_payload.get("warnings") or []),
        *list(after_analysis_payload.get("warnings") or []),
        *list(preview.get("warnings") or []),
    ]
    return {
        "ok": True,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "histology_roi_debug",
        "project_path": str(path),
        "entry_id": str(entry_id),
        "image_name": str(entry.get("image_name") or ""),
        "display_name": str(entry.get("display_name") or entry.get("image_name") or ""),
        "sample_id": str(entry.get("sample_id") or ""),
        "sample_number": sample_number,
        "treatment": treatment,
        "roi_source": roi_source,
        "roi_index": selected_index,
        "roi_id": str(roi.get("id") or ""),
        "roi_label": str(roi.get("label") or f"ROI {selected_index + 1}"),
        "roi": roi,
        "adjusted_roi": adjusted_roi,
        "roi_shrink_percent": _roi_shrink_percent(after_params),
        "parameters": after_params,
        "before_parameters": before_params,
        "before": before,
        "after": after,
        "delta": _roi_debug_delta(before, after),
        "preview": preview,
        "img": preview.get("img", ""),
        "warnings": warnings,
    }


def _batch_timestamp_slug() -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", _now_iso()) or "now"


def _new_project_batch_dir(project_path: Path) -> Path:
    root = _data_project_dir(project_path) / "project_analysis"
    stem = f"saved_roi_batch_{_batch_timestamp_slug()}"
    out_dir = root / stem
    suffix = 2
    while out_dir.exists():
        out_dir = root / f"{stem}_{suffix}"
        suffix += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _scalar_for_table(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    return out if np.isfinite(out) else float(default)


def _extract_sample_group(entry: dict[str, Any], fallback_index: int) -> tuple[str, float]:
    candidates = [
        entry.get("sample_id"),
        entry.get("case_name"),
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("source_name"),
    ]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        leading = re.match(r"^\s*([0-9]+)(?:\b|[-_\s])", text)
        if leading:
            value = leading.group(1)
            return value, float(int(value))
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.search(r"(?:^|[^0-9])([0-9]+)(?:[^0-9]|$)", text)
        if match:
            value = match.group(1)
            return value, float(int(value))
    value = str(fallback_index + 1)
    return value, float(fallback_index + 1)


def _extract_source_letter(entry: dict[str, Any], row: dict[str, Any], roi_index: int) -> str:
    source_map = {
        "CB": "A",
        "DE": "B",
        "HY": "C",
        "PC": "D",
        "SB": "E",
        "SH": "F",
    }
    for raw in (
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("sample_id"),
        entry.get("case_name"),
    ):
        text = str(raw or "").strip()
        match = re.match(r"^\s*[0-9]+\s*[-_\s]+\s*([A-Za-z]+)", text)
        if match:
            token = match.group(1).upper()
            if token in source_map:
                return source_map[token]
    candidates = [
        row.get("roi_label"),
        row.get("roi_id"),
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("sample_id"),
        entry.get("case_name"),
    ]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.search(r"(?:^|[^A-Za-z])([A-Z])(?:[^A-Za-z]|$)", text.upper())
        if match:
            return match.group(1)
    return chr(ord("A") + (int(roi_index) % 26))


TREATMENT_SORT_ORDER = {
    "DE": 1.0,
    "PC": 2.0,
    "SB": 3.0,
    "CB": 4.0,
    "HY": 5.0,
    "SH": 6.0,
}


def _canonical_treatment_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    key = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    aliases = {
        "DE": "DE",
        "D": "DE",
        "PC": "PC",
        "SB": "SB",
        "CB": "CB",
        "HY": "HY",
        "H": "HY",
        "SH": "SH",
    }
    return aliases.get(key, key)


def _parse_image_sample_and_treatment(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^\s*([0-9]+)\s*[-_\s]+\s*([A-Za-z]+)", text)
    if not match:
        return "", ""
    return match.group(1), _canonical_treatment_label(match.group(2))


def _apply_batch_grouping(rows: list[dict[str, Any]], group_by: str) -> None:
    if str(group_by or "").strip().lower() not in {"treatment", "material"}:
        return
    for row in rows:
        sample_number = ""
        treatment = ""
        for key in ("image_name", "display_name", "sample_id", "case_name"):
            sample_number, treatment = _parse_image_sample_and_treatment(row.get(key))
            if treatment:
                break
        if not treatment:
            continue
        row["sample_number"] = sample_number
        row["treatment"] = treatment
        row["sample_group"] = treatment
        row["sample_group_sort"] = TREATMENT_SORT_ORDER.get(treatment.upper(), 999.0)
        if sample_number:
            row["letter"] = sample_number
            row["source_label"] = sample_number


def _apply_marker_inclusion(rows: list[dict[str, Any]], params: dict[str, Any]) -> None:
    exclude_zero = _boolish(
        params.get("exclude_zero_observations", params.get("skip_zero_observations", False)),
        default=False,
    )
    for row in rows:
        for marker in ("sma", "macrophage"):
            value = _finite_float(row.get(_metric_column(marker)))
            include = np.isfinite(value) and (not exclude_zero or value > 0)
            row[f"{marker}_include"] = bool(include)


def _group_sort_key(value: Any) -> tuple[int, float, str]:
    text = str(value or "").strip()
    treatment = _canonical_treatment_label(text)
    if treatment in TREATMENT_SORT_ORDER:
        return (0, TREATMENT_SORT_ORDER[treatment], treatment)
    try:
        return (0, float(text), text)
    except Exception:
        return (1, 0.0, text.lower())


def _metric_column(marker: str) -> str:
    return f"{marker}_positive_area_ratio"


def _normalized_metric_column(marker: str) -> str:
    return f"{marker}_positive_area_ratio_normalized"


def _boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _row_metric_value(row: dict[str, Any], marker: str) -> float:
    for key in (f"{marker}_positive_fraction", f"{marker}_positive_fraction_roi"):
        if key in row:
            return _finite_float(row.get(key))
    return 0.0


def _flatten_batch_row(
    project_path: Path,
    entry: dict[str, Any],
    analysis: dict[str, Any],
    result_row: dict[str, Any],
    entry_index: int,
    roi_index: int,
) -> dict[str, Any]:
    group, group_sort = _extract_sample_group(entry, entry_index)
    letter = _extract_source_letter(entry, result_row, roi_index)
    row: dict[str, Any] = {
        "project_path": str(project_path),
        "entry_id": str(entry.get("entry_id") or analysis.get("entry_id") or ""),
        "entry_index": entry_index + 1,
        "image_name": str(entry.get("image_name") or analysis.get("image_name") or ""),
        "display_name": str(entry.get("display_name") or analysis.get("display_name") or ""),
        "sample_id": str(entry.get("sample_id") or analysis.get("sample_id") or ""),
        "case_name": str(entry.get("case_name") or analysis.get("case_name") or ""),
        "sample_group": group,
        "sample_group_sort": group_sort,
        "letter": letter,
        "source_label": letter,
        "roi_index": roi_index + 1,
        "created_at": str(analysis.get("created_at") or ""),
        "backend": str(analysis.get("backend") or ""),
        "image_width": int(analysis.get("width") or 0),
        "image_height": int(analysis.get("height") or 0),
        "analysis_path": str(entry.get("analysis_path") or ""),
        "geojson_path": str(entry.get("geojson_path") or ""),
    }
    for key, value in result_row.items():
        row[key] = _scalar_for_table(value)
    for marker in ("sma", "macrophage"):
        row[_metric_column(marker)] = _row_metric_value(result_row, marker)
    return row


def _roi_parameter_overrides(params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("roi_parameter_overrides") or params.get("roi_parameters_by_roi")
    return raw if isinstance(raw, dict) else {}


def _roi_parameter_override_candidates(entry_id: str, roi: dict[str, Any], roi_index: int) -> list[str]:
    roi_id = str(roi.get("id") or "").strip()
    roi_label = str(roi.get("label") or "").strip()
    one_based_index = str(int(roi_index) + 1)
    candidates = [
        f"{entry_id}::roi_index::{one_based_index}",
        f"{entry_id}::{one_based_index}",
    ]
    if roi_id:
        candidates.insert(0, f"{entry_id}::roi_id::{roi_id}")
        candidates.insert(1, f"{entry_id}::{roi_id}")
    if roi_label:
        candidates.append(f"{entry_id}::roi_label::{roi_label}")
        candidates.append(f"{entry_id}::{roi_label}")
    return candidates


def _params_for_roi_parameter_override(
    params: dict[str, Any],
    entry_id: str,
    roi: dict[str, Any],
    roi_index: int,
) -> tuple[dict[str, Any], str]:
    overrides = _roi_parameter_overrides(params)
    for key in _roi_parameter_override_candidates(entry_id, roi, roi_index):
        raw = overrides.get(key)
        if not isinstance(raw, dict):
            continue
        merged = dict(params)
        merged.pop("roi_parameter_overrides", None)
        merged.pop("roi_parameters_by_roi", None)
        clean_override = dict(raw)
        clean_override.pop("roi_parameter_overrides", None)
        clean_override.pop("roi_parameters_by_roi", None)
        merged.update(clean_override)
        return _analysis_defaults(merged), key
    return params, ""


def _numeric_mean(values: list[Any]) -> float | None:
    finite: list[float] = []
    for value in values:
        if value in ("", None):
            continue
        try:
            number = float(value)
        except Exception:
            return None
        if np.isfinite(number):
            finite.append(number)
    if not finite:
        return None
    return float(np.mean(finite))


def _aggregate_roi_rows_by_entry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.get("entry_id") or row.get("image_name") or len(order))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)
    out: list[dict[str, Any]] = []
    metadata_keys = {
        "project_path",
        "entry_id",
        "entry_index",
        "image_name",
        "display_name",
        "sample_id",
        "case_name",
        "sample_group",
        "sample_group_sort",
        "letter",
        "source_label",
        "created_at",
        "backend",
        "image_width",
        "image_height",
        "analysis_path",
        "geojson_path",
    }
    for key in order:
        group_rows = grouped[key]
        first = group_rows[0]
        record = {field: first.get(field, "") for field in metadata_keys}
        record["observation_level"] = "image"
        record["roi_count"] = len(group_rows)
        record["n_roi"] = len(group_rows)
        record["roi_id"] = ";".join(str(row.get("roi_id") or "") for row in group_rows)
        record["roi_label"] = f"{len(group_rows)} ROI mean"
        record["roi_labels"] = ";".join(str(row.get("roi_label") or "") for row in group_rows)
        keys = sorted({key for row in group_rows for key in row.keys() if key not in metadata_keys})
        for field in keys:
            if field in {"roi_id", "roi_label", "roi_labels", "roi_index", "observation_level", "source_label"}:
                continue
            mean = _numeric_mean([row.get(field) for row in group_rows])
            if mean is not None:
                record[field] = mean
        for marker in ("sma", "macrophage"):
            metric = _metric_column(marker)
            record[metric] = float(np.mean([_finite_float(row.get(metric)) for row in group_rows]))
        out.append(record)
    return out


def _normalize_batch_rows(
    rows: list[dict[str, Any]],
    normalize_to_group: str,
) -> dict[str, Any]:
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    baseline_group = str(normalize_to_group or "").strip() or "1"
    if baseline_group not in groups and groups:
        baseline_group = groups[0]
    baselines: dict[str, float] = {}
    warnings: list[str] = []
    for marker in ("sma", "macrophage"):
        values = [
            _finite_float(row.get(_metric_column(marker)))
            for row in rows
            if str(row.get("sample_group") or "") == baseline_group
        ]
        finite = [value for value in values if np.isfinite(value)]
        baseline = float(np.mean(finite)) if finite else 0.0
        if baseline <= 0:
            warnings.append(
                f"{marker.upper()} baseline group {baseline_group} has no positive area; normalized values use 1.0 as denominator."
            )
            baseline = 1.0
        baselines[marker] = baseline
        for row in rows:
            row[_normalized_metric_column(marker)] = _finite_float(row.get(_metric_column(marker))) / baseline
    return {
        "normalize_to_group": baseline_group,
        "baseline_values": baselines,
        "warnings": warnings,
    }


def _apply_normalization_to_rows(rows: list[dict[str, Any]], normalization: dict[str, Any]) -> None:
    baselines = normalization.get("baseline_values") if isinstance(normalization, dict) else {}
    if not isinstance(baselines, dict):
        baselines = {}
    for marker in ("sma", "macrophage"):
        baseline = _finite_float(baselines.get(marker), default=1.0)
        if baseline <= 0:
            baseline = 1.0
        for row in rows:
            row[_normalized_metric_column(marker)] = _finite_float(row.get(_metric_column(marker))) / baseline


def _mean_sd_sem(values: list[float]) -> tuple[float, float, float]:
    finite = np.asarray([value for value in values if np.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return 0.0, 0.0, 0.0
    mean = float(np.mean(finite))
    if finite.size <= 1:
        return mean, 0.0, 0.0
    sd = float(np.std(finite, ddof=1))
    sem = float(sd / np.sqrt(finite.size))
    return mean, sd, sem


def _batch_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    summary: list[dict[str, Any]] = []
    for group in groups:
        group_rows = [row for row in rows if str(row.get("sample_group") or "") == group]
        record: dict[str, Any] = {
            "sample_group": group,
            "sample_group_sort": _group_sort_key(group)[1],
            "n_observations": len(group_rows),
            "n_roi": int(sum(max(1, int(_finite_float(row.get("n_roi") or row.get("roi_count"), 1))) for row in group_rows)),
            "n_entries": len({str(row.get("entry_id") or "") for row in group_rows}),
        }
        for marker in ("sma", "macrophage"):
            marker_rows = [
                row for row in group_rows if _boolish(row.get(f"{marker}_include", True), default=True)
            ]
            raw_values = [_finite_float(row.get(_metric_column(marker))) for row in marker_rows]
            norm_values = [_finite_float(row.get(_normalized_metric_column(marker))) for row in marker_rows]
            mean_raw, sd_raw, sem_raw = _mean_sd_sem(raw_values)
            mean_norm, sd_norm, sem_norm = _mean_sd_sem(norm_values)
            record[f"{marker}_n_observations"] = len(marker_rows)
            record[f"{marker}_mean"] = mean_raw
            record[f"{marker}_sd"] = sd_raw
            record[f"{marker}_sem"] = sem_raw
            record[f"{marker}_normalized_mean"] = mean_norm
            record[f"{marker}_normalized_sd"] = sd_norm
            record[f"{marker}_normalized_sem"] = sem_norm
        summary.append(record)
    return summary


def _batch_anova(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats_out: dict[str, Any] = {}
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    for marker in ("sma", "macrophage"):
        grouped = []
        labels = []
        for group in groups:
            values = [
                _finite_float(row.get(_normalized_metric_column(marker)), default=np.nan)
                for row in rows
                if str(row.get("sample_group") or "") == group
                and _boolish(row.get(f"{marker}_include", True), default=True)
            ]
            finite = np.asarray([value for value in values if np.isfinite(value)], dtype=np.float64)
            if finite.size:
                grouped.append(finite)
                labels.append(group)
        result: dict[str, Any] = {
            "marker": marker,
            "group_labels": labels,
            "group_count": len(grouped),
            "n": int(sum(len(values) for values in grouped)),
            "f": None,
            "p": None,
            "reason": "",
        }
        if len(grouped) < 2 or result["n"] <= len(grouped):
            result["reason"] = "Need at least two groups with residual degrees of freedom."
            stats_out[marker] = result
            continue
        try:
            import warnings as py_warnings

            from scipy import stats as scipy_stats  # type: ignore

            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore")
                f_value, p_value = scipy_stats.f_oneway(*grouped)
            if np.isfinite(f_value) and np.isfinite(p_value):
                result["f"] = float(f_value)
                result["p"] = float(p_value)
            else:
                result["reason"] = "ANOVA returned a non-finite statistic."
        except Exception as exc:  # pragma: no cover - scipy is a declared dependency
            result["reason"] = str(exc) or "ANOVA failed."
        stats_out[marker] = result
    return stats_out


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


def analyze_histology_data_project_saved_rois(
    project_path: str | Path,
    parameters: dict[str, Any] | None = None,
    progress: Callable[[float, str], None] | None = None,
    write_outputs: bool = True,
) -> dict[str, Any]:
    path = _normalize_data_project_path(project_path)
    loaded = load_histology_data_project(path)
    entries = [entry for entry in loaded.get("entries", []) if isinstance(entry, dict)]
    params = _analysis_defaults(parameters)
    normalize_to_group = str(
        params.get("summary_normalize_to_group")
        or params.get("normalize_to_group")
        or params.get("normalize_to_sample")
        or "1"
    )
    group_by = str(params.get("summary_group_by") or params.get("group_by") or "sample").strip().lower()
    if group_by in {"treatment", "material"} and normalize_to_group == "1":
        normalize_to_group = "CB"
    roi_rows: list[dict[str, Any]] = []
    analyzed_entries: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    used_roi_parameter_override_keys: set[str] = set()
    total = max(1, len(entries))
    if progress:
        progress(0.01, "Loading saved ROI annotations")
    for entry_index, entry in enumerate(entries):
        entry_id = str(entry.get("entry_id") or "")
        if not entry_id:
            skipped.append({"entry_id": "", "image_name": str(entry.get("image_name") or ""), "reason": "Missing entry id"})
            continue
        saved = _load_data_project_entry_analysis(path, entry_id)
        saved_rois = saved.get("rois") if isinstance(saved.get("rois"), list) else entry.get("rois")
        clean_rois = _clean_rois(saved_rois if isinstance(saved_rois, list) else [])
        roi_source = "project"
        if not clean_rois:
            clean_rois, external_rois_path = _load_external_entry_rois(path, entry)
            if clean_rois:
                roi_source = external_rois_path or "external"
        if not clean_rois:
            skipped.append(
                {
                    "entry_id": entry_id,
                    "image_name": str(entry.get("image_name") or ""),
                    "reason": "No saved ROI annotations",
                }
            )
            continue
        if progress:
            progress(0.05 + 0.78 * entry_index / total, f"Analyzing {entry.get('image_name') or entry_id}")
        try:
            entry_roi_params = [
                _params_for_roi_parameter_override(params, entry_id, roi, roi_index)
                for roi_index, roi in enumerate(clean_rois)
            ]
            entry_has_roi_overrides = any(override_key for _roi_params, override_key in entry_roi_params)
            if entry_has_roi_overrides:
                for roi_index, (roi, (roi_params, override_key)) in enumerate(zip(clean_rois, entry_roi_params, strict=False)):
                    result = _run_histology_data_project_roi_analysis(
                        path,
                        entry_id,
                        entry,
                        [roi],
                        roi_params,
                    )
                    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
                    result_rows = analysis.get("results") if isinstance(analysis.get("results"), list) else result.get("results", [])
                    for result_row in result_rows if isinstance(result_rows, list) else []:
                        if isinstance(result_row, dict):
                            flat = _flatten_batch_row(path, entry, analysis, result_row, entry_index, roi_index)
                            flat["roi_parameter_override_key"] = override_key
                            roi_rows.append(flat)
                    if override_key:
                        used_roi_parameter_override_keys.add(override_key)
                result = {"analysis_path": ""}
            elif write_outputs:
                result = analyze_histology_data_project_rois(path, entry_id, clean_rois, parameters=params)
            else:
                result = _run_histology_data_project_roi_analysis(
                    path,
                    entry_id,
                    entry,
                    clean_rois,
                    params,
                )
        except Exception as exc:
            failures.append(
                {
                    "entry_id": entry_id,
                    "image_name": str(entry.get("image_name") or ""),
                    "reason": str(exc) or exc.__class__.__name__,
                }
            )
            continue
        if not entry_has_roi_overrides:
            analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
            result_rows = analysis.get("results") if isinstance(analysis.get("results"), list) else result.get("results", [])
            for roi_index, result_row in enumerate(result_rows if isinstance(result_rows, list) else []):
                if isinstance(result_row, dict):
                    roi_rows.append(_flatten_batch_row(path, entry, analysis, result_row, entry_index, roi_index))
        analyzed_entries.append(
            {
                "entry_id": entry_id,
                "image_name": str(entry.get("image_name") or ""),
                "roi_count": len(clean_rois),
                "roi_source": roi_source,
                "analysis_path": str(result.get("analysis_path") or ""),
            }
        )
    if not roi_rows:
        detail = "; ".join(item["reason"] for item in [*failures, *skipped][:3])
        suffix = f": {detail}" if detail else ""
        raise ValueError(f"No saved histology ROI could be analyzed{suffix}")
    if progress:
        progress(0.85, "Averaging ROI measurements by image")
    aggregate_by_entry = _boolish(
        params.get("summary_aggregate_rois_by_entry", params.get("aggregate_rois_by_entry", True)),
        default=True,
    )
    observation_rows = _aggregate_roi_rows_by_entry(roi_rows) if aggregate_by_entry else [dict(row) for row in roi_rows]
    observation_level = "image" if aggregate_by_entry else "roi"
    _apply_batch_grouping(observation_rows, group_by)
    _apply_batch_grouping(roi_rows, group_by)
    _apply_marker_inclusion(observation_rows, params)
    _apply_marker_inclusion(roi_rows, params)
    if progress:
        progress(0.88, "Normalizing image measurements")
    normalization = _normalize_batch_rows(observation_rows, normalize_to_group)
    normalization["observation_level"] = observation_level
    _apply_normalization_to_rows(roi_rows, normalization)
    summary = _batch_group_summary(observation_rows)
    stats = _batch_anova(observation_rows)
    if write_outputs:
        out_dir = _new_project_batch_dir(path)
        if progress:
            progress(0.92, "Writing CSV tables and plots")
        used_roi_parameter_override_key_list = sorted(used_roi_parameter_override_keys)
        outputs = _write_batch_outputs(
            out_dir,
            roi_rows,
            observation_rows,
            summary,
            stats,
            normalization,
            params,
            skipped,
            failures,
            observation_level=observation_level,
            roi_parameter_override_keys=used_roi_parameter_override_key_list,
        )
    else:
        used_roi_parameter_override_key_list = sorted(used_roi_parameter_override_keys)
        if progress:
            progress(0.92, "Prepared readout preview without writing output files")
        outputs = {
            "run_dir": "",
            "roi_table_path": "",
            "image_table_path": "",
            "summary_table_path": "",
            "statistics_path": "",
            "manifest_path": "",
            "plots": [],
            "outputs": [],
        }
    warnings = list(normalization.get("warnings") or [])
    warnings.extend(f"{item['image_name'] or item['entry_id']}: {item['reason']}" for item in failures)
    if progress:
        progress(1.0, "Histology saved ROI batch analysis complete")
    return {
        "ok": True,
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "histology_saved_roi_batch_analysis",
        "write_outputs": bool(write_outputs),
        "project_path": str(path),
        "data_dir": str(_data_project_dir(path)),
        "cache_dir": str(_data_project_cache_dir(path)),
        "created_at": _now_iso(),
        "entry_count": len(entries),
        "analyzed_entry_count": len(analyzed_entries),
        "skipped_entry_count": len(skipped),
        "failed_entry_count": len(failures),
        "roi_parameter_override_count": len(used_roi_parameter_override_key_list),
        "roi_parameter_override_keys": used_roi_parameter_override_key_list,
        "observation_level": observation_level,
        "observation_count": len(observation_rows),
        "roi_count": len(roi_rows),
        "sample_count": len(summary),
        "normalization": normalization,
        "statistics": stats,
        "summary": summary,
        "rows": observation_rows,
        "roi_rows": roi_rows,
        "analyzed_entries": analyzed_entries,
        "skipped_entries": skipped,
        "failed_entries": failures,
        "warnings": warnings,
        **outputs,
    }


def _resolve_single_image_path(image_path: str | Path) -> Path:
    raw = str(image_path or "").strip()
    if not raw:
        raise FileNotFoundError("Histology image path is required")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Histology image not found: {path}")
    if not _has_project_image_suffix(path):
        raise ValueError("Select an exported TIFF, PNG, or JPG image file")
    return path


def load_histology_file_image_preview(
    image_path: str | Path,
    max_side: int = 1600,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    preview_max = max(256, min(int(max_side), 2400))
    arr, backend, warnings, w, h = _read_project_image_preview(path, max_side=preview_max)
    channel_label = path.stem
    force_channel_display = _channel_rgb_slot(channel_label) is not None or _is_brightfield_label(channel_label)
    is_rgb = _is_rgb_plane(arr)
    is_mono_rgb = _rgb_channels_are_monochrome(arr) if is_rgb else False
    if force_channel_display or not is_rgb or is_mono_rgb:
        if is_mono_rgb:
            warnings.append(f"{path.name}: RGB channels are identical; source preview is monochrome.")
        elif force_channel_display:
            warnings.append(f"{path.name}: displayed as channel intensity from its filename, not as an RGB photo.")
        else:
            warnings.append(f"{path.name}: source preview is single-channel/monochrome.")
        warnings.append(
            f"{path.name}: preview uses display-only pseudocolor/contrast; analysis uses source intensities."
        )
        arr = _pseudo_color_channel(channel_label, arr)
    return {
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "rois": [],
        "analyses": [],
        "warnings": warnings,
    }


def load_histology_file_image_region_preview(
    image_path: str | Path,
    x: float,
    y: float,
    width: float,
    height: float,
    max_side: int = 1800,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    preview_max = max(256, min(int(max_side), 2600))
    arr, backend, warnings, w, h, box = _read_project_image_region_preview(
        path,
        x,
        y,
        width,
        height,
        max_side=preview_max,
    )
    x0, y0, x1, y1 = box
    return {
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "region_x": int(x0),
        "region_y": int(y0),
        "region_width": int(x1 - x0),
        "region_height": int(y1 - y0),
        "preview_width": int(arr.shape[1]),
        "preview_height": int(arr.shape[0]),
        "img": _png_b64(arr, max_side=preview_max),
        "warnings": warnings,
    }


def analyze_histology_file_rois(
    image_path: str | Path,
    rois: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _resolve_single_image_path(image_path)
    arr, backend, warnings = _read_project_image(path, max_side=1600)
    clean_rois = _clean_rois(rois)
    if not clean_rois:
        raise ValueError("Draw at least one polygon ROI before analysis")
    params = _analysis_defaults(parameters)
    analysis_rois = _analysis_rois_for_params(clean_rois, params)
    h, w, results = _analyze_marker_rois(arr, analysis_rois, params)
    for row in results:
        row["analysis_roi_shrink_percent"] = _roi_shrink_percent(params)
    calibration = _infer_tiff_pixel_calibration(path) or {"has_physical_scale": False}
    _apply_physical_calibration_to_results(results, calibration)
    analysis = {
        "created_at": _now_iso(),
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "single_file_histology_analysis",
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "display_name": path.name,
        "image_path": str(path),
        "source_path": str(path),
        "case_name": _case_name_for_source(path),
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "roi_shrink_percent": _roi_shrink_percent(params),
        "calibration": calibration,
        "parameters": params,
        "results": results,
        "warnings": warnings,
    }
    return {
        "protocol": TIFF_PROJECT_PROTOCOL,
        "kind": "single_file_histology_analysis",
        "entry_id": _source_entry_id(path),
        "image_name": path.name,
        "image_path": str(path),
        "roi_count": len(clean_rois),
        "analysis_count": 1,
        "rois": clean_rois,
        "analysis": analysis,
        "results": results,
        "backend": backend,
        "width": int(w),
        "height": int(h),
        "warnings": warnings,
    }


__all__ = [
    "ETS_INDEX_FILE",
    "ETS_DATA_PROJECT_FILE",
    "ETS_PROJECT_DIR",
    "ETS_PROTOCOL",
    "add_histology_data_project_paths",
    "analyze_histology_file_rois",
    "analyze_histology_data_project_rois",
    "analyze_histology_data_project_saved_rois",
    "create_histology_data_project",
    "debug_histology_data_project_roi",
    "load_histology_data_project",
    "load_histology_data_project_image_preview",
    "load_histology_data_project_image_region_preview",
    "load_histology_file_image_preview",
    "load_histology_file_image_region_preview",
    "rename_histology_data_project_entry",
    "save_histology_data_project_rois",
]
