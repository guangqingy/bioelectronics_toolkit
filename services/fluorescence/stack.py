from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from services.io_guards import assert_tiff_within_limits

LUT_OPTIONS = ["Red", "Blue", "Gray", "Green", "Magenta", "Cyan", "Yellow"]
DENOISE_OPTIONS = ["Off", "Light", "Medium", "Strong"]
BACKGROUND_OPTIONS = ["Off", "Light", "Medium", "Strong"]

DEFAULT_LUT_BY_INDEX = {0: "Red", 1: "Blue", 2: "Gray"}
DEFAULT_DENOISE_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}
DEFAULT_BACKGROUND_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}

_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_CACHE: OrderedDict[tuple[str, int, int, int], np.ndarray] = OrderedDict()
_PAGE_CACHE_BYTES = 0


def _cache_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _page_cache_limits() -> tuple[int, int]:
    return (
        _cache_int("DP_TIFF_PAGE_CACHE_ITEMS", 12),
        _cache_int("DP_TIFF_PAGE_CACHE_BYTES", 256_000_000),
    )


def _page_cache_get(key: tuple[str, int, int, int]) -> np.ndarray | None:
    with _PAGE_CACHE_LOCK:
        arr = _PAGE_CACHE.get(key)
        if arr is None:
            return None
        _PAGE_CACHE.move_to_end(key)
        return arr


def _page_cache_put(key: tuple[str, int, int, int], arr: np.ndarray) -> None:
    global _PAGE_CACHE_BYTES
    max_items, max_bytes = _page_cache_limits()
    if max_items <= 0 or max_bytes <= 0 or int(arr.nbytes) > max_bytes:
        return
    cached = np.asarray(arr)
    cached.setflags(write=False)
    with _PAGE_CACHE_LOCK:
        previous = _PAGE_CACHE.pop(key, None)
        if previous is not None:
            _PAGE_CACHE_BYTES -= int(previous.nbytes)
        _PAGE_CACHE[key] = cached
        _PAGE_CACHE_BYTES += int(cached.nbytes)
        while _PAGE_CACHE and (len(_PAGE_CACHE) > max_items or _PAGE_CACHE_BYTES > max_bytes):
            _old_key, old_arr = _PAGE_CACHE.popitem(last=False)
            _PAGE_CACHE_BYTES -= int(old_arr.nbytes)


def bool_or(value: Any, default: bool = False) -> bool:
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


def int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile


def compute_default_min_max(
    img: np.ndarray,
    low_p: float = 1.0,
    high_p: float = 99.8,
) -> tuple[float, float]:
    arr = np.asarray(img, dtype=np.float32)
    if arr.size == 0:
        return 0.0, 1.0
    vmin = float(np.percentile(arr, low_p))
    vmax = float(np.percentile(arr, high_p))
    if vmax <= vmin:
        vmin = float(np.min(arr))
        vmax = float(np.max(arr))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return vmin, vmax


def convert_to_export_dtype(img: np.ndarray) -> np.ndarray:
    arr = np.asarray(img)
    if np.issubdtype(arr.dtype, np.integer):
        if arr.dtype == np.uint16:
            return arr
        if arr.dtype == np.uint8:
            return arr.astype(np.uint16) * 257
        return np.clip(arr, 0, 65535).astype(np.uint16)
    arr = arr.astype(np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=65535.0, neginf=0.0)
    arr = np.clip(arr, 0.0, 65535.0)
    return arr.astype(np.uint16)


def box_blur2d(img: np.ndarray, radius: int) -> np.ndarray:
    arr = np.asarray(img, dtype=np.float32)
    if radius <= 0:
        return arr
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D image for denoise, got shape {arr.shape}")

    h, w = arr.shape
    max_r = max(0, min(h, w) // 2 - 1)
    r = min(radius, max_r)
    if r <= 0:
        return arr

    k = 2 * r + 1

    pad_x = np.pad(arr, ((0, 0), (r, r)), mode="reflect")
    cs_x = np.cumsum(pad_x, axis=1, dtype=np.float64)
    cs_x = np.pad(cs_x, ((0, 0), (1, 0)), mode="constant", constant_values=0.0)
    sum_x = cs_x[:, k:] - cs_x[:, :-k]
    blur_x = (sum_x / float(k)).astype(np.float32)

    pad_y = np.pad(blur_x, ((r, r), (0, 0)), mode="reflect")
    cs_y = np.cumsum(pad_y, axis=0, dtype=np.float64)
    cs_y = np.pad(cs_y, ((1, 0), (0, 0)), mode="constant", constant_values=0.0)
    sum_y = cs_y[k:, :] - cs_y[:-k, :]
    return (sum_y / float(k)).astype(np.float32)


def apply_background_suppression(img: np.ndarray, background_mode: str) -> np.ndarray:
    mode = str(background_mode).strip().lower()
    arr = np.asarray(img, dtype=np.float32)
    if mode in {"", "off", "none"}:
        return arr
    if mode == "light":
        radius = 15
    elif mode == "medium":
        radius = 25
    elif mode == "strong":
        radius = 40
    else:
        return arr

    bg = box_blur2d(arr, radius)
    corrected = arr - bg
    corrected = np.clip(corrected, 0.0, None)
    return corrected.astype(np.float32)


def apply_optional_denoise(img: np.ndarray, denoise_mode: str) -> np.ndarray:
    mode = str(denoise_mode).strip().lower()
    arr = np.asarray(img, dtype=np.float32)
    if mode in {"", "off", "none"}:
        return arr
    if mode == "light":
        radius, blend = 1, 0.55
    elif mode == "medium":
        radius, blend = 2, 0.75
    elif mode == "strong":
        radius, blend = 3, 1.0
    else:
        return arr

    blur = box_blur2d(arr, radius)
    return ((1.0 - blend) * arr + blend * blur).astype(np.float32)


def preprocess_stack_image(
    img: np.ndarray,
    background_mode: str,
    denoise_mode: str,
) -> np.ndarray:
    x = apply_background_suppression(img, background_mode)
    return apply_optional_denoise(x, denoise_mode)


def compute_auto_range_with_processing(
    img: np.ndarray,
    background_mode: str,
    denoise_mode: str,
) -> tuple[float, float]:
    proc = preprocess_stack_image(img, background_mode, denoise_mode)
    return compute_default_min_max(proc)


def clean_choice(raw: Any, options: list[str], default: str) -> str:
    text = str(raw or "").strip().lower()
    for option in options:
        if text == option.lower():
            return option
    return default


def split_tiff_array_to_pages(arr: np.ndarray) -> list[np.ndarray]:
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return [arr]
    if arr.ndim == 3:
        return [np.asarray(arr[i]) for i in range(arr.shape[0])]
    raise ValueError(
        f"Unsupported TIFF shape: {arr.shape}. "
        "Only grayscale TIFF or multi-page grayscale TIFF is supported."
    )


def read_tiff_as_pages(tiff_path: Path, tifflib_module: Any = None) -> list[np.ndarray]:
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(tiff_path, tifflib)
    with tifflib.TiffFile(str(tiff_path)) as tif:
        if len(tif.pages) == 1:
            return split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
        return [np.asarray(page.asarray()) for page in tif.pages]


def tiff_stack_info(tiff_path: Path | str, tifflib_module: Any = None) -> dict[str, Any]:
    tifflib = import_tifffile(tifflib_module)
    estimate = assert_tiff_within_limits(tiff_path, tifflib)
    shape = estimate.shape
    if len(shape) == 2:
        n_frames, h, w = 1, int(shape[0]), int(shape[1])
    elif len(shape) >= 3:
        n_frames = int(shape[0])
        h = int(shape[-2])
        w = int(shape[-1])
    else:
        raise ValueError(f"Unsupported TIFF shape: {shape}")
    return {
        "n_frames": n_frames,
        "height": h,
        "width": w,
        "dtype": estimate.dtype,
        "shape": list(shape),
        "estimated_bytes": estimate.estimated_bytes,
    }


def read_tiff_page(
    tiff_path: Path | str,
    page_index: int = 0,
    tifflib_module: Any = None,
) -> tuple[np.ndarray, int, int]:
    tifflib = import_tifffile(tifflib_module)
    assert_tiff_within_limits(tiff_path, tifflib)
    p = Path(tiff_path)
    stat = p.stat()
    resolved = str(p.resolve())
    with tifflib.TiffFile(str(p)) as tif:
        if len(tif.pages) == 1:
            pages = split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
            n_frames = len(pages)
            idx = max(0, min(int(page_index), n_frames - 1))
            return np.asarray(pages[idx]), idx, n_frames
        n_frames = len(tif.pages)
        idx = max(0, min(int(page_index), n_frames - 1))
        key = (resolved, int(stat.st_mtime_ns), int(stat.st_size), idx)
        cached = _page_cache_get(key)
        if cached is not None:
            return cached, idx, n_frames
        arr = np.asarray(tif.pages[idx].asarray())
        _page_cache_put(key, arr)
        return arr, idx, n_frames


def _read_open_tiff_page(
    tif,
    idx: int,
    *,
    cache_prefix: tuple[str, int, int],
) -> tuple[np.ndarray, int]:
    if len(tif.pages) == 1:
        pages = split_tiff_array_to_pages(np.asarray(tif.pages[0].asarray()))
        n_frames = len(pages)
        actual = max(0, min(int(idx), n_frames - 1))
        return np.asarray(pages[actual]), n_frames
    n_frames = len(tif.pages)
    actual = max(0, min(int(idx), n_frames - 1))
    key = (*cache_prefix, actual)
    cached = _page_cache_get(key)
    if cached is not None:
        return cached, n_frames
    arr = np.asarray(tif.pages[actual].asarray())
    _page_cache_put(key, arr)
    return arr, n_frames


def select_display_frame_from_tiff(
    tiff_path: Path | str,
    frame_idx: int,
    mode: str,
    z_start: int | None,
    z_end: int | None,
    tifflib_module: Any = None,
) -> tuple[np.ndarray, dict]:
    info = tiff_stack_info(tiff_path, tifflib_module)
    n = int(info["n_frames"])
    frame_idx = max(0, min(int(frame_idx), n - 1))
    mode_clean = str(mode or "single").strip().lower()
    if mode_clean not in {"max", "mean"}:
        frame, idx, _n = read_tiff_page(tiff_path, frame_idx, tifflib_module)
        return frame, {"mode": "single", "frame": idx, "z_start": idx, "z_end": idx}

    z0 = 0 if z_start is None else max(0, min(int(z_start), n - 1))
    z1 = n - 1 if z_end is None else max(0, min(int(z_end), n - 1))
    if z1 < z0:
        z0, z1 = z1, z0

    p = Path(tiff_path)
    stat = p.stat()
    cache_prefix = (str(p.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    tifflib = import_tifffile(tifflib_module)
    with tifflib.TiffFile(str(p)) as tif:
        if mode_clean == "max":
            acc = None
            for idx in range(z0, z1 + 1):
                frame, _n = _read_open_tiff_page(tif, idx, cache_prefix=cache_prefix)
                arr = np.asarray(frame, dtype=np.float32)
                acc = arr if acc is None else np.maximum(acc, arr)
            out = acc
        else:
            total = None
            count = 0
            for idx in range(z0, z1 + 1):
                frame, _n = _read_open_tiff_page(tif, idx, cache_prefix=cache_prefix)
                arr = np.asarray(frame, dtype=np.float64)
                total = arr if total is None else total + arr
                count += 1
            out = (total / max(1, count)).astype(np.float32) if total is not None else None

    if out is None:
        out = read_tiff_page(tiff_path, frame_idx, tifflib_module)[0]
    return out, {"mode": mode_clean, "frame": frame_idx, "z_start": z0, "z_end": z1}


def to_macro_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def imagej_lut_command(lut_name: str) -> str:
    name = str(lut_name or "Gray").strip().lower()
    if name == "red":
        return 'run("Red");'
    if name == "blue":
        return 'run("Blue");'
    if name == "gray":
        return 'run("Grays");'
    if name == "green":
        return 'run("Green");'
    if name == "magenta":
        return 'run("Magenta");'
    if name == "cyan":
        return 'run("Cyan");'
    if name == "yellow":
        return 'run("Yellow");'
    return 'run("Grays");'


def build_fiji_macro(tiff_path: Path, included_settings: list[dict]) -> str:
    n_channels = len(included_settings)
    lines = [f'open("{to_macro_path(tiff_path)}");']
    if n_channels > 1:
        lines.append(
            f'run("Stack to Hyperstack...", '
            f'"order=xyczt(default) channels={n_channels} slices=1 frames=1 display=Composite");'
        )
    else:
        lines.append('run("Make Composite");')

    for i, settings in enumerate(included_settings, start=1):
        lines.append(f"Stack.setChannel({i});")
        lines.append(imagej_lut_command(str(settings.get("lut", "Gray"))))
        lines.append(
            f"setMinAndMax({float(settings.get('min', 0.0)):.6f}, "
            f"{float(settings.get('max', 1.0)):.6f});"
        )

    return "\n".join(lines) + "\n"


def build_default_settings_for_pages(pages: list[np.ndarray]) -> list[dict]:
    settings: list[dict] = []
    for i, page in enumerate(pages):
        data_min = float(np.min(page))
        data_max = float(np.max(page))
        if data_max <= data_min:
            data_max = data_min + 1.0
        vmin, vmax = compute_default_min_max(page)
        settings.append(
            {
                "include": bool(i < 3),
                "page_index": i,
                "lut": DEFAULT_LUT_BY_INDEX.get(i, "Gray"),
                "background": DEFAULT_BACKGROUND_BY_INDEX.get(i, "Off"),
                "denoise": DEFAULT_DENOISE_BY_INDEX.get(i, "Off"),
                "min": float(vmin),
                "max": float(vmax),
                "default_min": float(vmin),
                "default_max": float(vmax),
                "data_min": float(data_min),
                "data_max": float(data_max),
            }
        )
    return settings


def normalize_settings_for_pages(
    pages: list[np.ndarray],
    raw_settings: Any,
) -> list[dict]:
    defaults = build_default_settings_for_pages(pages)
    if not isinstance(raw_settings, list):
        return defaults

    mapped = {}
    for settings in raw_settings:
        if not isinstance(settings, dict):
            continue
        idx = int_or(settings.get("page_index", -1), -1)
        if idx >= 0:
            mapped[idx] = settings

    out: list[dict] = []
    for default_settings in defaults:
        idx = int(default_settings["page_index"])
        settings = mapped.get(idx)
        if settings is None:
            out.append(default_settings)
            continue

        include = bool_or(settings.get("include"), default_settings["include"])
        lut = clean_choice(settings.get("lut"), LUT_OPTIONS, default_settings["lut"])
        background = clean_choice(
            settings.get("background"),
            BACKGROUND_OPTIONS,
            default_settings["background"],
        )
        denoise = clean_choice(
            settings.get("denoise"),
            DENOISE_OPTIONS,
            default_settings["denoise"],
        )
        min_v = float_or(settings.get("min", default_settings["min"]), default_settings["min"])
        max_v = float_or(settings.get("max", default_settings["max"]), default_settings["max"])
        if max_v <= min_v:
            max_v = min_v + 1.0

        normalized = dict(default_settings)
        normalized["include"] = include
        normalized["lut"] = lut
        normalized["background"] = background
        normalized["denoise"] = denoise
        normalized["min"] = float(min_v)
        normalized["max"] = float(max_v)
        out.append(normalized)

    return out


def build_settings_from_template(
    pages: list[np.ndarray],
    template_settings: Any,
    lock_ranges: bool,
) -> list[dict]:
    defaults = build_default_settings_for_pages(pages)
    if not isinstance(template_settings, list):
        return defaults

    template_map = {}
    for settings in template_settings:
        if not isinstance(settings, dict):
            continue
        idx = int_or(settings.get("page_index", -1), -1)
        if idx >= 0:
            template_map[idx] = settings

    out: list[dict] = []
    for default_settings in defaults:
        index = int(default_settings["page_index"])
        template = template_map.get(index)
        if template is None:
            out.append(default_settings)
            continue

        normalized = dict(default_settings)
        normalized["include"] = bool_or(template.get("include"), normalized["include"])
        normalized["lut"] = clean_choice(template.get("lut"), LUT_OPTIONS, normalized["lut"])
        normalized["background"] = clean_choice(
            template.get("background"),
            BACKGROUND_OPTIONS,
            normalized["background"],
        )
        normalized["denoise"] = clean_choice(
            template.get("denoise"),
            DENOISE_OPTIONS,
            normalized["denoise"],
        )

        if lock_ranges:
            min_v = float_or(template.get("min", normalized["min"]), normalized["min"])
            max_v = float_or(template.get("max", normalized["max"]), normalized["max"])
            if max_v <= min_v:
                max_v = min_v + 1.0
            normalized["min"] = float(min_v)
            normalized["max"] = float(max_v)
        else:
            vmin, vmax = compute_auto_range_with_processing(
                pages[index],
                normalized["background"],
                normalized["denoise"],
            )
            normalized["min"] = float(vmin)
            normalized["max"] = float(vmax)
        out.append(normalized)

    return out


def export_with_settings(
    tiff_path: Path,
    pages: list[np.ndarray],
    settings: list[dict],
    tifflib_module: Any = None,
) -> dict:
    tifflib = import_tifffile(tifflib_module)
    included = [settings_item for settings_item in settings if bool(settings_item.get("include"))]
    if not included:
        raise ValueError("Please include at least one stack.")

    base_dir = tiff_path.parent
    base_name = tiff_path.stem

    exported_stack_files: list[Path] = []
    selected_pages: list[np.ndarray] = []

    for settings_item in included:
        page_index = int_or(settings_item.get("page_index", -1), -1)
        if page_index < 0 or page_index >= len(pages):
            raise ValueError(f"Invalid page index {page_index} for file: {tiff_path.name}")

        lut_name = str(settings_item.get("lut", "Gray")).strip().lower()
        page_number = page_index + 1

        processed = preprocess_stack_image(
            pages[page_index],
            background_mode=str(settings_item.get("background", "Off")),
            denoise_mode=str(settings_item.get("denoise", "Off")),
        )
        page_data = convert_to_export_dtype(processed)
        selected_pages.append(page_data)

        out_stack = base_dir / f"{base_name}_stack{page_number}_{lut_name}.tif"
        tifflib.imwrite(str(out_stack), page_data)
        exported_stack_files.append(out_stack)

    stack_arr = selected_pages[0] if len(selected_pages) == 1 else np.stack(selected_pages, axis=0)

    out_tiff = base_dir / f"{base_name}_selected_stacks.tif"
    tifflib.imwrite(str(out_tiff), stack_arr)

    out_macro = base_dir / f"{base_name}_open_in_fiji.ijm"
    out_macro.write_text(build_fiji_macro(out_tiff, included), encoding="utf-8")

    out_json = base_dir / f"{base_name}_display_settings.json"
    out_json.write_text(json.dumps(included, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "stack_files": [str(path) for path in exported_stack_files],
        "combined_tiff": str(out_tiff),
        "macro": str(out_macro),
        "json": str(out_json),
    }


def is_generated_tiff(path: Path) -> bool:
    name = path.stem.lower()
    if name.endswith("_selected_stacks"):
        return True
    if "_stack" in name:
        return True
    return False
