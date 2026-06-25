from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from services.histology_analysis import _array_to_rgb, _scale_to_uint8
from services.histology_tiff_project import TIFF_SUFFIXES, load_image_for_analysis

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None


def _as_2d_channel(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] <= 4:
        return data[..., :3].mean(axis=-1).astype(data.dtype, copy=False)
    while data.ndim > 2:
        data = np.max(data, axis=0)
    return data


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

__all__ = [
    "_as_2d_channel",
    "_read_project_image",
    "_read_project_image_preview",
    "_read_project_image_region_for_analysis",
    "_read_project_image_region_preview",
]
