from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from services.fluorescence.stack_io import import_tifffile
from services.fluorescence.stack_processing import (
    BACKGROUND_OPTIONS,
    DEFAULT_BACKGROUND_BY_INDEX,
    DEFAULT_DENOISE_BY_INDEX,
    DEFAULT_LUT_BY_INDEX,
    DENOISE_OPTIONS,
    LUT_OPTIONS,
    bool_or,
    clean_choice,
    compute_auto_range_with_processing,
    compute_default_min_max,
    convert_to_export_dtype,
    float_or,
    int_or,
    preprocess_stack_image,
)


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

__all__ = [
    "build_default_settings_for_pages",
    "build_fiji_macro",
    "build_settings_from_template",
    "export_with_settings",
    "imagej_lut_command",
    "is_generated_tiff",
    "normalize_settings_for_pages",
    "to_macro_path",
]
