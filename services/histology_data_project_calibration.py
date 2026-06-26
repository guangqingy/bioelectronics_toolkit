from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from services.histology_tiff_project import TIFF_SUFFIXES

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - declared project dependency, kept defensive
    tifffile = None


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
