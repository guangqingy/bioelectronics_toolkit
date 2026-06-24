from __future__ import annotations

import base64
import io
import re
from typing import Iterable

import numpy as np

from services.fluorescence import gif as fl_gif
from services.output_naming import sanitize_name_part


def apply_lut(gray8: np.ndarray, lut: str) -> np.ndarray:
    return fl_gif.apply_lut(gray8, lut)


def frame_to_b64(frame: np.ndarray, lut: str, p_low: float, p_high: float, image_mod) -> str:
    arr = frame.astype(np.float32)
    lo, hi = np.percentile(arr, [p_low, p_high])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        hi = lo + 1.0
    gray8 = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    rgb = apply_lut(gray8, lut)
    img = image_mod.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def select_display_frame(
    stack: np.ndarray,
    frame_idx: int,
    mode: str,
    z_start: int | None,
    z_end: int | None,
) -> tuple[np.ndarray, dict]:
    arr = np.asarray(stack)
    mode = str(mode or "single").strip().lower()
    if arr.ndim == 2:
        return arr, {"mode": "single", "frame": 0, "z_start": 0, "z_end": 0}

    n = int(arr.shape[0])
    frame_idx = max(0, min(int(frame_idx), n - 1))
    z0 = 0 if z_start is None else max(0, min(int(z_start), n - 1))
    z1 = n - 1 if z_end is None else max(0, min(int(z_end), n - 1))
    if z1 < z0:
        z0, z1 = z1, z0

    slab = arr[z0 : z1 + 1]
    if mode == "max":
        return np.nanmax(slab, axis=0), {
            "mode": "max",
            "frame": frame_idx,
            "z_start": z0,
            "z_end": z1,
        }
    if mode == "mean":
        return np.nanmean(slab, axis=0), {
            "mode": "mean",
            "frame": frame_idx,
            "z_start": z0,
            "z_end": z1,
        }

    return arr[frame_idx], {
        "mode": "single",
        "frame": frame_idx,
        "z_start": frame_idx,
        "z_end": frame_idx,
    }


def parse_bool(value, default: bool = False) -> bool:
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


def sanitize_prefix(prefix: str, fallback: str = "roi_sequence_analysis") -> str:
    return sanitize_name_part(prefix, fallback)


def rational_to_float(value) -> float | None:
    try:
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                return None
            num = float(value[0])
            den = float(value[1])
            if abs(den) < 1e-12:
                return None
            return num / den
        return float(value)
    except Exception:
        return None


def unit_to_um_scale(unit: str | None) -> float | None:
    if not unit:
        return None
    text = unit.strip().lower()
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


def normalize_hex_color(color: object, fallback: str = "#f2f2f2") -> str:
    text = str(color or "").strip()
    if not text:
        text = fallback
    if not text.startswith("#"):
        text = "#" + text
    if re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return fallback
    return text.lower()


def infer_pixel_size_um_from_tiff(path: str, *, tifflib) -> float | None:
    if not path:
        return None
    try:
        with tifflib.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            tags = page.tags

            xres_tag = tags.get("XResolution")
            unit_tag = tags.get("ResolutionUnit")
            xres = rational_to_float(xres_tag.value) if xres_tag is not None else None
            unit_value = unit_tag.value if unit_tag is not None else None
            if xres is not None and xres > 0 and unit_value is not None:
                if int(unit_value) == 2:
                    return 25400.0 / xres
                if int(unit_value) == 3:
                    return 10000.0 / xres

            ome_xml = tf.ome_metadata
            if ome_xml:
                m_val = re.search(r'PhysicalSizeX="([0-9eE+\-.]+)"', ome_xml)
                m_unit = re.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                if m_val:
                    px_val = float(m_val.group(1))
                    unit_str = m_unit.group(1) if m_unit else "um"
                    scale = unit_to_um_scale(unit_str)
                    if scale is not None and px_val > 0:
                        return px_val * scale
    except Exception:
        return None
    return None


def normalize_display_2d(img2d: np.ndarray, low_p: float = 1.0, high_p: float = 99.8) -> np.ndarray:
    arr = np.asarray(img2d, dtype=np.float32)
    if arr.size == 0:
        return np.zeros_like(arr, dtype=np.float32)
    lo, hi = np.percentile(arr, [low_p, high_p])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.min(arr)), float(np.max(arr))
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.float32)
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0)


def decode_base64_payload(payload: str) -> bytes:
    text = str(payload or "")
    if not text:
        return b""
    if "," in text and "base64" in text[:64].lower():
        text = text.split(",", 1)[1]
    return base64.b64decode(text)


def iter_with_job_progress(
    items: Iterable,
    job_ctx=None,
    *,
    start: float = 0.2,
    span: float = 0.7,
    label: str = "Processing item",
):
    """Yield indexed items while honoring background-job cancellation."""
    seq = items if hasattr(items, "__len__") else list(items)
    total = max(1, len(seq))
    for idx, item in enumerate(seq, start=1):
        if job_ctx is not None:
            job_ctx.check_cancelled()
            job_ctx.set_progress(start + span * ((idx - 1) / total), f"{label} {idx}/{total}")
        yield idx, item
