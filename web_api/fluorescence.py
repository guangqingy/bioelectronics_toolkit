import base64
import html
import json
import io
import re as _re2
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Circle, Rectangle

from services.fluorescence import gif as fl_gif
from services.fluorescence import roi as fl_roi
from services.fluorescence import stack as fl_stack
from .path_policy import sanitize_name_part


def register_fluorescence_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]

    has_tiff = ctx["HAS_TIFF"]
    has_pil = ctx["HAS_PIL"]
    tifflib = ctx.get("tifflib")
    image_mod = ctx.get("Image")
    image_draw_mod = ctx.get("ImageDraw")
    image_font_mod = ctx.get("ImageFont")
    jobs = ctx.get("jobs")

    def _fl_apply_lut(gray8: np.ndarray, lut: str) -> np.ndarray:
        """Convert single-channel uint8 -> RGB array using a named LUT."""
        return fl_gif.apply_lut(gray8, lut)

    def _fl_frame_to_b64(frame: np.ndarray, lut: str, p_low: float, p_high: float) -> str:
        """Render one TIFF frame with LUT -> base64 PNG."""
        arr = frame.astype(np.float32)
        lo, hi = np.percentile(arr, [p_low, p_high])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 1.0
        gray8 = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
        rgb = _fl_apply_lut(gray8, lut)
        img = image_mod.fromarray(rgb, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    def _fl_select_display_frame(stack: np.ndarray, frame_idx: int, mode: str, z_start: int | None, z_end: int | None) -> tuple[np.ndarray, dict]:
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
            return np.nanmax(slab, axis=0), {"mode": "max", "frame": frame_idx, "z_start": z0, "z_end": z1}
        if mode == "mean":
            return np.nanmean(slab, axis=0), {"mode": "mean", "frame": frame_idx, "z_start": z0, "z_end": z1}

        return arr[frame_idx], {"mode": "single", "frame": frame_idx, "z_start": frame_idx, "z_end": frame_idx}

    def _fl_bool(v, default=False) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        s = str(v).strip().lower()
        if s in {"1", "true", "yes", "y", "on"}:
            return True
        if s in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _fl_sanitize_prefix(prefix: str, fallback: str = "roi_sequence_analysis") -> str:
        return sanitize_name_part(prefix, fallback)

    def _fl_rational_to_float(v) -> float | None:
        try:
            if isinstance(v, (tuple, list)):
                if len(v) != 2:
                    return None
                num = float(v[0])
                den = float(v[1])
                if abs(den) < 1e-12:
                    return None
                return num / den
            return float(v)
        except Exception:
            return None

    def _fl_unit_to_um_scale(unit: str | None) -> float | None:
        if not unit:
            return None
        u = unit.strip().lower()
        if u in {"um", "micron", "microns", "micrometer", "micrometers"}:
            return 1.0
        if u in {"nm", "nanometer", "nanometers"}:
            return 1e-3
        if u in {"mm", "millimeter", "millimeters"}:
            return 1e3
        if u in {"cm", "centimeter", "centimeters"}:
            return 1e4
        if u in {"m", "meter", "meters"}:
            return 1e6
        if u in {"in", "inch", "inches"}:
            return 25400.0
        return None

    def _fl_infer_pixel_size_um_from_tiff(path: str) -> float | None:
        if not has_tiff or not path:
            return None
        try:
            with tifflib.TiffFile(str(path)) as tf:
                page = tf.pages[0]
                tags = page.tags

                xres_tag = tags.get("XResolution")
                unit_tag = tags.get("ResolutionUnit")
                xres = _fl_rational_to_float(xres_tag.value) if xres_tag is not None else None
                unit_value = unit_tag.value if unit_tag is not None else None
                if xres is not None and xres > 0 and unit_value is not None:
                    if int(unit_value) == 2:
                        return 25400.0 / xres
                    if int(unit_value) == 3:
                        return 10000.0 / xres

                ome_xml = tf.ome_metadata
                if ome_xml:
                    m_val = _re2.search(r'PhysicalSizeX="([0-9eE+\\-.]+)"', ome_xml)
                    m_unit = _re2.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                    if m_val:
                        px_val = float(m_val.group(1))
                        unit_str = m_unit.group(1) if m_unit else "um"
                        scale = _fl_unit_to_um_scale(unit_str)
                        if scale is not None and px_val > 0:
                            return px_val * scale
        except Exception:
            return None
        return None

    def _fl_normalize_display_2d(img2d: np.ndarray, low_p: float = 1.0, high_p: float = 99.8) -> np.ndarray:
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

    def _fl_decode_base64_payload(payload: str) -> bytes:
        s = str(payload or "")
        if not s:
            return b""
        if "," in s and "base64" in s[:64].lower():
            s = s.split(",", 1)[1]
        return base64.b64decode(s)

    _FL_LUT_OPTIONS = ["Red", "Blue", "Gray", "Green", "Magenta", "Cyan", "Yellow"]
    _FL_DENOISE_OPTIONS = ["Off", "Light", "Medium", "Strong"]
    _FL_BACKGROUND_OPTIONS = ["Off", "Light", "Medium", "Strong"]
    _FL_DEFAULT_LUT_BY_INDEX = {0: "Red", 1: "Blue", 2: "Gray"}
    _FL_DEFAULT_DENOISE_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}
    _FL_DEFAULT_BACKGROUND_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}

    def _fl_read_tiff_as_pages(tiff_path: Path) -> list[np.ndarray]:
        arr = tifflib.imread(str(tiff_path))
        arr = np.asarray(arr)
        if arr.ndim == 2:
            return [arr]
        if arr.ndim == 3:
            return [arr[i] for i in range(arr.shape[0])]
        raise ValueError(
            f"Unsupported TIFF shape: {arr.shape}. "
            "Only grayscale TIFF or multi-page grayscale TIFF is supported."
        )

    def _fl_prepare_gif_plane(raw: np.ndarray) -> np.ndarray:
        return fl_gif.prepare_plane(raw)

    def _fl_split_tiff_array_to_gif_planes(arr: np.ndarray) -> list[np.ndarray]:
        return fl_gif.split_tiff_array_to_planes(arr)

    def _fl_tiff_gif_frame_count(tiff_path: Path) -> tuple[int, list[int]]:
        return fl_gif.tiff_frame_count(tiff_path, tifflib)

    def _fl_positive_float(v) -> float | None:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(x) or x <= 0:
            return None
        return x

    def _fl_metadata_z_spacing_um(payload: object) -> tuple[float | None, str]:
        if not isinstance(payload, dict):
            return None, ""

        calibration = payload.get("calibration")
        if isinstance(calibration, dict):
            z_um = _fl_positive_float(calibration.get("z_spacing_um"))
            if z_um is not None:
                return z_um, "metadata calibration.z_spacing_um"

        xml_meta = payload.get("leica_xml_metadata")
        dims = xml_meta.get("dimensions") if isinstance(xml_meta, dict) else None
        if isinstance(dims, list):
            for dim in dims:
                if not isinstance(dim, dict) or str(dim.get("DimID", "")) != "3":
                    continue
                n = _fl_positive_float(dim.get("NumberOfElements"))
                length = _fl_positive_float(abs(float(dim.get("Length", 0.0))) if dim.get("Length") is not None else None)
                unit = str(dim.get("Unit", "") or "").strip().lower()
                if n is not None and length is not None and n > 1:
                    scale = _fl_unit_to_um_scale(unit)
                    if scale is not None:
                        return (length * scale) / max(1, n - 1), "metadata Leica DimID 3 Length"
        return None, ""

    def _fl_tiff_calibration(tiff_path: Path) -> dict:
        pixel_um = None
        pixel_source = ""
        z_um = None
        z_source = ""

        meta_path = _fl_find_gif_metadata_json(tiff_path)
        if meta_path is not None:
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                pixel_um, pixel_source = _fl_metadata_pixel_size_um(payload)
                z_um, z_source = _fl_metadata_z_spacing_um(payload)
            except Exception:
                pass

        try:
            with tifflib.TiffFile(str(tiff_path)) as tf:
                ij = tf.imagej_metadata or {}
                if z_um is None and isinstance(ij, dict):
                    z_um = _fl_positive_float(ij.get("spacing"))
                    if z_um is not None:
                        z_source = "ImageJ spacing"

                ome_xml = tf.ome_metadata
                if ome_xml:
                    if pixel_um is None:
                        m_val = _re2.search(r'PhysicalSizeX="([0-9eE+\\-.]+)"', ome_xml)
                        m_unit = _re2.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                        if m_val:
                            px_val = _fl_positive_float(m_val.group(1))
                            unit_str = m_unit.group(1) if m_unit else "um"
                            scale = _fl_unit_to_um_scale(unit_str)
                            if px_val is not None and scale is not None:
                                pixel_um = px_val * scale
                                pixel_source = "OME PhysicalSizeX"
                    if z_um is None:
                        m_val = _re2.search(r'PhysicalSizeZ="([0-9eE+\\-.]+)"', ome_xml)
                        m_unit = _re2.search(r'PhysicalSizeZUnit="([^"]+)"', ome_xml)
                        if m_val:
                            z_val = _fl_positive_float(m_val.group(1))
                            unit_str = m_unit.group(1) if m_unit else "um"
                            scale = _fl_unit_to_um_scale(unit_str)
                            if z_val is not None and scale is not None:
                                z_um = z_val * scale
                                z_source = "OME PhysicalSizeZ"
        except Exception:
            pass

        if pixel_um is None:
            pixel_um = _fl_infer_pixel_size_um_from_tiff(str(tiff_path))
            if pixel_um is not None:
                pixel_source = "TIFF resolution metadata"

        pixel_um = _fl_positive_float(pixel_um) or 1.0
        z_um = _fl_positive_float(z_um) or pixel_um
        if not z_source:
            z_source = "fallback to XY pixel size"
        if not pixel_source:
            pixel_source = "fallback 1 um/px"
        return {
            "pixel_width_um": float(pixel_um),
            "pixel_height_um": float(pixel_um),
            "z_spacing_um": float(z_um),
            "pixel_source": pixel_source,
            "z_source": z_source,
            "metadata_path": str(meta_path) if meta_path is not None else "",
        }

    def _fl_clean_tiff_axes(axes: object, shape: tuple[int, ...]) -> str:
        raw = str(axes or "")
        if len(raw) == len(shape):
            return raw
        if len(shape) == 2:
            return "YX"
        if len(shape) == 3:
            if shape[-1] in {3, 4}:
                return "YXS"
            return "ZYX"
        if len(shape) == 4:
            if shape[-1] in {3, 4}:
                return "ZYXS"
            return "ZCYX"
        if len(shape) == 5:
            return "TZCYX"
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return letters[: max(0, len(shape) - 2)] + "YX"

    def _fl_tiff_axis_roles(axes: str, shape: tuple[int, ...]) -> dict:
        axis_list = list(axes)
        y_axis = axes.rfind("Y")
        x_axis = axes.rfind("X")
        if y_axis < 0 or x_axis < 0:
            y_axis = max(0, len(shape) - 2)
            x_axis = max(0, len(shape) - 1)

        z_axis = axes.find("Z")
        if z_axis < 0:
            for i, size in enumerate(shape):
                if i not in {y_axis, x_axis} and axis_list[i] not in {"C", "S"} and int(size) > 1:
                    z_axis = i
                    break

        c_axis = axes.find("C")
        t_axis = axes.find("T")
        s_axis = axes.find("S")
        if t_axis == z_axis:
            t_axis = -1
        extras = []
        for i, label in enumerate(axis_list):
            if i in {y_axis, x_axis, z_axis, c_axis, t_axis, s_axis}:
                continue
            extras.append({"axis": label or f"D{i}", "index": i, "count": int(shape[i])})
        return {"y": y_axis, "x": x_axis, "z": z_axis, "c": c_axis, "t": t_axis, "s": s_axis, "extras": extras}

    def _fl_tiff_series_info(tiff_path: Path) -> dict:
        with tifflib.TiffFile(str(tiff_path)) as tf:
            series = tf.series[0]
            shape = tuple(int(v) for v in series.shape)
            axes = _fl_clean_tiff_axes(getattr(series, "axes", ""), shape)
            dtype = str(series.dtype)
        roles = _fl_tiff_axis_roles(axes, shape)
        y_count = int(shape[roles["y"]]) if roles["y"] >= 0 else int(shape[-2])
        x_count = int(shape[roles["x"]]) if roles["x"] >= 0 else int(shape[-1])
        z_axis = roles["z"]
        c_axis = roles["c"]
        t_axis = roles["t"]
        z_count = int(shape[z_axis]) if z_axis >= 0 else 1
        c_count = int(shape[c_axis]) if c_axis >= 0 else 1
        t_count = int(shape[t_axis]) if t_axis >= 0 else 1
        cal = _fl_tiff_calibration(tiff_path)
        return {
            "path": str(tiff_path),
            "name": tiff_path.name,
            "shape": list(shape),
            "axes": axes,
            "dtype": dtype,
            "dimensions": {
                "x": x_count,
                "y": y_count,
                "z": z_count,
                "c": c_count,
                "t": t_count,
                "extras": roles["extras"],
            },
            "axis_roles": {k: v for k, v in roles.items() if k != "extras"},
            "calibration": cal,
            "can_3d": z_count > 1,
        }

    def _fl_tiff_read_array(tiff_path: Path) -> tuple[np.ndarray, str, dict]:
        with tifflib.TiffFile(str(tiff_path)) as tf:
            series = tf.series[0]
            arr = np.asarray(series.asarray())
            axes = _fl_clean_tiff_axes(getattr(series, "axes", ""), arr.shape)
        return arr, axes, _fl_tiff_axis_roles(axes, tuple(arr.shape))

    def _fl_tiff_plane_from_array(
        arr: np.ndarray,
        axes: str,
        roles: dict,
        z: int = 0,
        c: int = 0,
        t: int = 0,
        extra_indices: dict | None = None,
    ) -> np.ndarray:
        shape = tuple(arr.shape)
        indexer = []
        extra_indices = extra_indices if isinstance(extra_indices, dict) else {}
        for i, _size in enumerate(shape):
            if i == roles.get("y") or i == roles.get("x"):
                indexer.append(slice(None))
            elif i == roles.get("z"):
                indexer.append(max(0, min(int(z or 0), shape[i] - 1)))
            elif i == roles.get("c"):
                indexer.append(max(0, min(int(c or 0), shape[i] - 1)))
            elif i == roles.get("t"):
                indexer.append(max(0, min(int(t or 0), shape[i] - 1)))
            elif i == roles.get("s"):
                indexer.append(slice(None))
            else:
                raw = extra_indices.get(str(i), extra_indices.get(axes[i], 0))
                indexer.append(max(0, min(int(raw or 0), shape[i] - 1)))
        plane = np.asarray(arr[tuple(indexer)])
        plane = np.squeeze(plane)
        if plane.ndim == 3 and plane.shape[-1] in {3, 4}:
            return np.mean(plane[..., :3].astype(np.float32), axis=-1)
        if plane.ndim != 2:
            plane = plane.reshape((-1, plane.shape[-2], plane.shape[-1]))[0]
        return plane

    def _fl_volume_lut_rgb(lut_name: str) -> tuple[float, float, float]:
        name = str(lut_name or "").strip().lower()
        if name == "red":
            return 1.0, 0.05, 0.05
        if name == "green":
            return 0.05, 1.0, 0.12
        if name == "blue":
            return 0.12, 0.25, 1.0
        if name == "cyan":
            return 0.05, 1.0, 1.0
        if name == "magenta":
            return 1.0, 0.08, 1.0
        if name == "yellow":
            return 1.0, 0.86, 0.05
        return 0.95, 0.95, 0.95

    def _fl_normalize_hex_color(color: object, fallback: str = "#f2f2f2") -> str:
        s = str(color or "").strip()
        if not s:
            s = fallback
        if not s.startswith("#"):
            s = "#" + s
        if _re2.fullmatch(r"#[0-9a-fA-F]{3}", s):
            s = "#" + "".join(ch * 2 for ch in s[1:])
        if not _re2.fullmatch(r"#[0-9a-fA-F]{6}", s):
            return fallback
        return s.lower()

    def _fl_hex_color_to_rgb(color: object, fallback: str = "#f2f2f2") -> tuple[float, float, float]:
        s = _fl_normalize_hex_color(color, fallback).lstrip("#")
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def _fl_volume_indices(count: int, max_count: int) -> list[int]:
        count = max(1, int(count or 1))
        max_count = max(1, int(max_count or count))
        if count <= max_count:
            return list(range(count))
        return sorted({int(x) for x in np.linspace(0, count - 1, max_count)})

    def _fl_plane_points_3d(
        arr: np.ndarray,
        z_index: int,
        c_index: int,
        z_count: int,
        c_count: int,
        xy_step: int,
        per_plane_quota: int,
        threshold_percentile: float,
        range_low_percentile: float,
        range_high_percentile: float,
        calibration: dict,
        lut_rgb: tuple[float, float, float],
        denoise_mode: str = "Off",
    ) -> tuple[list[float], list[float]]:
        data = np.asarray(arr, dtype=np.float32)
        if data.size == 0:
            return [], []
        view = data[::xy_step, ::xy_step]
        view = _fl_apply_optional_denoise(view, denoise_mode)
        finite = view[np.isfinite(view)]
        if finite.size == 0:
            return [], []

        range_low_percentile = max(0.0, min(99.0, float(range_low_percentile)))
        range_high_percentile = max(range_low_percentile + 0.1, min(100.0, float(range_high_percentile)))
        lo = float(np.percentile(finite, range_low_percentile))
        hi = float(np.percentile(finite, range_high_percentile))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.min(finite)), float(np.max(finite))
        if hi <= lo:
            return [], []

        norm = np.clip((view - lo) / (hi - lo), 0.0, 1.0)
        threshold_percentile = max(0.0, min(99.95, float(threshold_percentile)))
        thr = float(np.percentile(finite, threshold_percentile))
        ys, xs = np.where(view >= thr)
        if ys.size == 0:
            flat = view.reshape(-1)
            k = min(max(1, per_plane_quota), flat.size)
            selected = np.argpartition(flat, -k)[-k:]
            ys, xs = np.unravel_index(selected, view.shape)
        if ys.size > per_plane_quota:
            intensities = norm[ys, xs]
            k = max(1, int(per_plane_quota))
            keep = np.argpartition(intensities, -k)[-k:]
            ys = ys[keep]
            xs = xs[keep]

        pixel_w = _fl_positive_float(calibration.get("pixel_width_um")) or 1.0
        pixel_h = _fl_positive_float(calibration.get("pixel_height_um")) or pixel_w
        z_spacing = _fl_positive_float(calibration.get("z_spacing_um")) or pixel_w
        h, w = data.shape
        cx = (w - 1) * pixel_w / 2.0
        cy = (h - 1) * pixel_h / 2.0
        cz = (max(1, z_count) - 1) * z_spacing / 2.0
        channel_offset = 0.0
        if c_count > 1:
            channel_offset = (c_index - (c_count - 1) / 2.0) * z_spacing * 0.08

        positions: list[float] = []
        colors: list[float] = []
        for y_s, x_s in zip(ys, xs):
            brightness = float(norm[y_s, x_s])
            if brightness <= 0:
                continue
            x_px = int(x_s) * xy_step
            y_px = int(y_s) * xy_step
            positions.extend(
                [
                    round(x_px * pixel_w - cx, 4),
                    round(cy - y_px * pixel_h, 4),
                    round(z_index * z_spacing - cz + channel_offset, 4),
                ]
            )
            colors.extend(
                [
                    round(min(1.0, max(0.0, lut_rgb[0] * brightness)), 4),
                    round(min(1.0, max(0.0, lut_rgb[1] * brightness)), 4),
                    round(min(1.0, max(0.0, lut_rgb[2] * brightness)), 4),
                ]
            )
        return positions, colors

    def _fl_channel_render_range(channel_ranges: object, channel: int, default_threshold: float, default_color: str) -> dict:
        default = {
            "enabled": True,
            "low": 1.0,
            "high": 99.7,
            "signal": max(0.0, min(99.95, float(default_threshold))),
            "color": _fl_normalize_hex_color(default_color),
        }
        if not isinstance(channel_ranges, dict):
            return default
        raw = channel_ranges.get(str(channel), channel_ranges.get(channel, {}))
        if not isinstance(raw, dict):
            return default
        low = float_or(raw.get("low"), default["low"])
        high = float_or(raw.get("high"), default["high"])
        signal = float_or(raw.get("signal"), default["signal"])
        low = max(0.0, min(99.0, low))
        high = max(low + 0.1, min(100.0, high))
        signal = max(0.0, min(99.95, signal))
        color = _fl_normalize_hex_color(raw.get("color"), default["color"])
        enabled = _fl_bool(raw.get("enabled", True), True)
        return {"enabled": enabled, "low": low, "high": high, "signal": signal, "color": color}

    def _fl_tiff_volume3d_payload(
        tiff_path: Path,
        c: int = 0,
        t: int = 0,
        extra_indices: dict | None = None,
        channel_mode: str = "composite",
        max_points: int = 70000,
        max_xy: int = 180,
        max_z: int = 80,
        threshold_percentile: float = 98.8,
        channel_ranges: dict | None = None,
        denoise_mode: str = "Off",
        show_scale_bar: bool = True,
        scale_bar_um: float = 20.0,
    ) -> dict:
        info = _fl_tiff_series_info(tiff_path)
        dims = info.get("dimensions", {}) or {}
        z_count = max(1, int(dims.get("z", 1) or 1))
        c_count = max(1, int(dims.get("c", 1) or 1))
        x_count = max(1, int(dims.get("x", 1) or 1))
        y_count = max(1, int(dims.get("y", 1) or 1))
        if z_count < 2:
            raise ValueError("This TIFF has only one readable stack plane; 3D stacking needs Z/slices > 1.")

        arr, axes, roles = _fl_tiff_read_array(tiff_path)
        max_points = max(1000, min(250000, int(max_points or 70000)))
        max_xy = max(48, min(512, int(max_xy or 180)))
        max_z = max(2, min(200, int(max_z or 80)))
        xy_step = max(1, int(np.ceil(max(x_count, y_count) / float(max_xy))))
        z_indices = _fl_volume_indices(z_count, max_z)
        if str(channel_mode or "composite").strip().lower() == "current" or c_count <= 1:
            candidate_channels = [max(0, min(int(c or 0), c_count - 1))]
            channel_mode = "current"
        else:
            candidate_channels = list(range(c_count))
            channel_mode = "composite"

        fallback_colors = ["#3b82f6", "#22c55e", "#ef4444", "#d946ef", "#06b6d4", "#eab308"]
        denoise_mode = _fl_clean_choice(denoise_mode, _FL_DENOISE_OPTIONS, "Off")
        calibration = info.get("calibration", {}) or {}
        positions: list[float] = []
        colors: list[float] = []
        channel_settings: dict[str, dict] = {}
        channel_render_settings: dict[int, dict] = {}
        channels: list[int] = []
        for channel in candidate_channels:
            default_color = fallback_colors[channel % len(fallback_colors)] if channel_mode == "composite" else "#f2f2f2"
            chan_range = _fl_channel_render_range(channel_ranges, channel, threshold_percentile, default_color)
            channel_settings[str(channel)] = {
                "enabled": bool(chan_range["enabled"]),
                "low": chan_range["low"],
                "high": chan_range["high"],
                "signal": chan_range["signal"],
                "color": chan_range["color"],
            }
            if chan_range["enabled"]:
                channel_render_settings[channel] = chan_range
                channels.append(channel)
        if not channels:
            raise ValueError("Select at least one channel for 3D rendering/export.")
        plane_quota = max(12, int(np.ceil(max_points / max(1, len(z_indices) * len(channels)))))
        for z in z_indices:
            for channel in channels:
                plane = _fl_tiff_plane_from_array(arr, axes, roles, z=z, c=channel, t=t, extra_indices=extra_indices)
                default_color = fallback_colors[channel % len(fallback_colors)] if channel_mode == "composite" else "#f2f2f2"
                chan_range = channel_render_settings[channel]
                p, col = _fl_plane_points_3d(
                    arr=plane,
                    z_index=int(z),
                    c_index=int(channel),
                    z_count=z_count,
                    c_count=c_count,
                    xy_step=xy_step,
                    per_plane_quota=plane_quota,
                    threshold_percentile=chan_range["signal"],
                    range_low_percentile=chan_range["low"],
                    range_high_percentile=chan_range["high"],
                    calibration=calibration,
                    lut_rgb=_fl_hex_color_to_rgb(chan_range["color"], default_color),
                    denoise_mode=denoise_mode,
                )
                positions.extend(p)
                colors.extend(col)

        n_points = len(positions) // 3
        if n_points > max_points:
            idx = np.linspace(0, n_points - 1, max_points, dtype=np.int64)
            pos_arr = np.asarray(positions, dtype=np.float32).reshape(-1, 3)[idx]
            col_arr = np.asarray(colors, dtype=np.float32).reshape(-1, 3)[idx]
            positions = np.round(pos_arr.reshape(-1), 4).tolist()
            colors = np.round(col_arr.reshape(-1), 4).tolist()
            n_points = len(positions) // 3
        if n_points <= 0:
            raise ValueError("No bright voxels were found for 3D rendering. Try lowering Signal %.") 

        scale_bar_um = max(0.0, float(scale_bar_um or 0.0))

        return {
            "title": tiff_path.stem,
            "source_tiff": str(tiff_path),
            "dimensions": {
                "x": x_count,
                "y": y_count,
                "z": z_count,
                "c": c_count,
                "t": max(1, int(dims.get("t", 1) or 1)),
                "z_sampled": len(z_indices),
                "channels_rendered": channels,
            },
            "calibration": {
                "pixel_width_um": float(calibration.get("pixel_width_um", 1.0)),
                "pixel_height_um": float(calibration.get("pixel_height_um", calibration.get("pixel_width_um", 1.0))),
                "z_spacing_um": float(calibration.get("z_spacing_um", calibration.get("pixel_width_um", 1.0))),
                "pixel_source": calibration.get("pixel_source", ""),
                "z_source": calibration.get("z_source", ""),
            },
            "render": {
                "type": "point_cloud",
                "positions": positions,
                "colors": colors,
                "n_points": n_points,
                "xy_step": xy_step,
                "z_indices": z_indices,
                "channel_mode": channel_mode,
                "threshold_percentile": threshold_percentile,
                "channel_settings": channel_settings,
                "denoise": denoise_mode,
                "show_scale_bar": bool(show_scale_bar and scale_bar_um > 0),
                "scale_bar_um": scale_bar_um,
                "point_size": max(0.35, min(4.0, float(calibration.get("pixel_width_um", 1.0)) * xy_step * 0.9)),
            },
        }

    def _fl_volume3d_html(volume_payload: dict) -> str:
        payload_json = json.dumps(volume_payload, ensure_ascii=False).replace("</", "<\\/")
        title = html.escape(str(volume_payload.get("title", "TIFF 3D Viewer") or "TIFF 3D Viewer"))
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title} - 3D Volume</title>
<script type="importmap">
{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}}}
</script>
<style>
html,body{{margin:0;height:100%;background:#08090c;color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#viewer{{position:fixed;inset:0}}
#hud{{position:fixed;left:14px;top:14px;max-width:min(460px,calc(100vw - 28px));background:rgba(8,9,12,.72);border:1px solid rgba(255,255,255,.16);backdrop-filter:blur(12px);border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.5}}
#title{{font-weight:700;font-size:13px;margin-bottom:3px}}
#hint{{color:#b7beca}}
</style>
</head>
<body>
<div id="viewer"></div>
<div id="hud">
  <div id="title"></div>
  <div id="meta"></div>
  <div id="hint">Mouse drag: rotate · Wheel: zoom · Right drag: pan</div>
</div>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
const data = {payload_json};
const el = document.getElementById('viewer');
document.getElementById('title').textContent = data.title || 'TIFF 3D Viewer';
document.getElementById('meta').textContent = `${{data.render.n_points}} points · Z ${{data.dimensions.z}} · C ${{data.dimensions.c}} · ${{data.calibration.pixel_width_um.toFixed(4)}} um/px · Z step ${{data.calibration.z_spacing_um.toFixed(4)}} um`;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x08090c);
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 100000);
const renderer = new THREE.WebGLRenderer({{antialias:true}});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
el.appendChild(renderer.domElement);
const geom = new THREE.BufferGeometry();
geom.setAttribute('position', new THREE.Float32BufferAttribute(data.render.positions, 3));
geom.setAttribute('color', new THREE.Float32BufferAttribute(data.render.colors, 3));
geom.computeBoundingSphere();
const mat = new THREE.PointsMaterial({{size:data.render.point_size || 1, vertexColors:true, transparent:true, opacity:0.92, sizeAttenuation:true}});
scene.add(new THREE.Points(geom, mat));
const sphere = geom.boundingSphere || new THREE.Sphere(new THREE.Vector3(), 100);
scene.add(new THREE.AxesHelper(Math.max(20, sphere.radius * 0.65)));
const grid = new THREE.GridHelper(Math.max(20, sphere.radius * 2.2), 10, 0x2c3445, 0x151923);
grid.rotation.x = Math.PI / 2;
scene.add(grid);
function makeLabelSprite(text){{
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  canvas.width = 256;
  canvas.height = 64;
  ctx.font = '28px -apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif';
  ctx.fillStyle = 'rgba(0,0,0,0.72)';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = 'rgba(255,255,255,0.7)';
  ctx.strokeRect(1, 1, canvas.width - 2, canvas.height - 2);
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({{map:texture, transparent:true}}));
  sprite.scale.set(Math.max(18, sphere.radius * 0.42), Math.max(4.5, sphere.radius * 0.105), 1);
  return sprite;
}}
function addScaleBar(){{
  const len = Number(data.render.scale_bar_um || 0);
  if (!data.render.show_scale_bar || !Number.isFinite(len) || len <= 0) return;
  const radius = Math.max(10, sphere.radius || 100);
  const barLen = len;
  const x0 = sphere.center.x - radius * 0.82;
  const y0 = sphere.center.y - radius * 0.86;
  const z0 = sphere.center.z - radius * 0.86;
  const pts = [new THREE.Vector3(x0, y0, z0), new THREE.Vector3(x0 + barLen, y0, z0)];
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({{color:0xffffff, linewidth:3}}));
  scene.add(line);
  const labelText = `${{len.toFixed(len >= 10 ? 0 : 1).replace(/\\.0$/, '')}} µm`;
  const label = makeLabelSprite(labelText);
  label.position.set(x0 + barLen / 2, y0 + radius * 0.055, z0);
  scene.add(label);
}}
addScaleBar();
camera.position.set(sphere.center.x + sphere.radius * 1.6, sphere.center.y + sphere.radius * 1.25, sphere.center.z + sphere.radius * 1.8);
camera.near = Math.max(0.01, sphere.radius / 1000);
camera.far = Math.max(1000, sphere.radius * 12);
camera.updateProjectionMatrix();
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(sphere.center);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.update();
scene.add(new THREE.AmbientLight(0xffffff, 1.0));
function resize(){{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}}
window.addEventListener('resize', resize);
function animate(){{
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}}
animate();
</script>
</body>
</html>"""

    def _fl_parse_slice_spec(slice_spec: object, n_frames: int) -> list[int]:
        return fl_gif.parse_slice_spec(slice_spec, n_frames)

    def _fl_read_selected_gif_planes(tiff_path: Path, indices: list[int]) -> list[np.ndarray]:
        return fl_gif.read_selected_planes(tiff_path, indices, tifflib)

    def _fl_json_float(v) -> float | None:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(x) or x <= 0:
            return None
        return x

    def _fl_find_gif_metadata_json(tiff_path: Path) -> Path | None:
        candidates = [
            tiff_path.with_name(f"{tiff_path.stem}_metadata.json"),
            tiff_path.with_suffix(".json"),
        ]
        if tiff_path.stem.endswith("_selected_stacks"):
            base = tiff_path.stem[: -len("_selected_stacks")]
            candidates.append(tiff_path.with_name(f"{base}_metadata.json"))
        for p in candidates:
            if p.exists() and p.is_file():
                return p
        return None

    def _fl_metadata_pixel_size_um(payload: object) -> tuple[float | None, str]:
        if not isinstance(payload, dict):
            return None, ""

        calibration = payload.get("calibration")
        if isinstance(calibration, dict):
            px_um = _fl_json_float(calibration.get("pixel_width_um"))
            if px_um is not None:
                return px_um, "metadata calibration.pixel_width_um"
            px_per_um = _fl_json_float(calibration.get("x_pixels_per_um"))
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata calibration.x_pixels_per_um"

        scale = payload.get("readlif_scale")
        if isinstance(scale, list) and scale:
            try:
                px_per_um = _fl_json_float(abs(float(scale[0])) if scale[0] is not None else None)
            except (TypeError, ValueError):
                px_per_um = None
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata readlif_scale[0]"

        scale_n = payload.get("readlif_scale_n")
        if isinstance(scale_n, dict):
            px_per_um = _fl_json_float(scale_n.get("1"))
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata readlif_scale_n.1"

        xml_meta = payload.get("leica_xml_metadata")
        dims = xml_meta.get("dimensions") if isinstance(xml_meta, dict) else None
        if isinstance(dims, list):
            for dim in dims:
                if not isinstance(dim, dict) or str(dim.get("DimID", "")) != "1":
                    continue
                n = _fl_json_float(dim.get("NumberOfElements"))
                length = _fl_json_float(abs(float(dim.get("Length", 0.0))) if dim.get("Length") is not None else None)
                unit = str(dim.get("Unit", "") or "").strip().lower()
                if n is not None and length is not None:
                    scale = _fl_unit_to_um_scale(unit)
                    if scale is not None:
                        return (length * scale) / n, "metadata Leica DimID 1 Length"

        return None, ""

    def _fl_resolve_gif_scale(tiff_path: Path, auto_scale: bool, manual_px_per_um: float) -> dict:
        manual = max(0.01, float(manual_px_per_um or 3.45))
        if auto_scale:
            meta_path = _fl_find_gif_metadata_json(tiff_path)
            if meta_path is not None:
                try:
                    px_um, source = _fl_metadata_pixel_size_um(json.loads(meta_path.read_text(encoding="utf-8")))
                    if px_um is not None:
                        return {
                            "pixels_per_um": 1.0 / px_um,
                            "pixel_size_um": px_um,
                            "source": source,
                            "metadata_path": str(meta_path),
                        }
                except Exception:
                    pass

            px_um = _fl_infer_pixel_size_um_from_tiff(str(tiff_path))
            if px_um is not None and px_um > 0:
                return {
                    "pixels_per_um": 1.0 / px_um,
                    "pixel_size_um": px_um,
                    "source": "TIFF resolution metadata",
                    "metadata_path": "",
                }

        return {
            "pixels_per_um": manual,
            "pixel_size_um": 1.0 / manual,
            "source": "manual px/um",
            "metadata_path": "",
        }

    def _fl_hex_to_rgb(color: object, fallback=(255, 215, 0)) -> tuple[int, int, int]:
        s = str(color or "").strip()
        if s.startswith("#"):
            s = s[1:]
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return fallback
        try:
            return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            return fallback

    def _fl_normalize_gif_polygons(raw_polygons: object) -> list[dict]:
        if not isinstance(raw_polygons, list):
            return []
        out: list[dict] = []
        for i, poly in enumerate(raw_polygons):
            if not isinstance(poly, dict):
                continue
            points_raw = poly.get("points")
            if not isinstance(points_raw, list):
                continue
            points = []
            for pt in points_raw[:200]:
                if not isinstance(pt, dict):
                    continue
                try:
                    x = float(pt.get("x"))
                    y = float(pt.get("y"))
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(x) or not np.isfinite(y):
                    continue
                points.append((float(x), float(y)))
            if len(points) < 3:
                continue
            label = str(poly.get("label", f"ROI {i + 1}") or f"ROI {i + 1}").strip()[:40]
            color_hex = _fl_normalize_hex_color(poly.get("color"), "#ffd166")
            out.append(
                {
                    "label": label or f"ROI {i + 1}",
                    "color": _fl_hex_to_rgb(color_hex),
                    "color_hex": color_hex,
                    "points": points,
                }
            )
        return out

    def _fl_normalize_gif_rects(raw_rects: object) -> list[dict]:
        if not isinstance(raw_rects, list):
            return []
        out: list[dict] = []
        for i, rect in enumerate(raw_rects):
            if not isinstance(rect, dict):
                continue
            try:
                x = float(rect.get("x", 0))
                y = float(rect.get("y", 0))
                w = float(rect.get("width", rect.get("w", 0)))
                h = float(rect.get("height", rect.get("h", 0)))
            except (TypeError, ValueError):
                continue
            if not all(np.isfinite(v) for v in [x, y, w, h]):
                continue
            if w < 0:
                x += w
                w = abs(w)
            if h < 0:
                y += h
                h = abs(h)
            if w < 2 or h < 2:
                continue
            label = str(rect.get("label", f"ROI2 {i + 1}") or f"ROI2 {i + 1}").strip()[:40]
            color_hex = _fl_normalize_hex_color(rect.get("color"), "#38bdf8")
            out.append(
                {
                    "label": label or f"ROI2 {i + 1}",
                    "color": _fl_hex_to_rgb(color_hex),
                    "color_hex": color_hex,
                    "x": float(x),
                    "y": float(y),
                    "width": float(w),
                    "height": float(h),
                }
            )
        return out

    def _fl_gif_polygon_mask(shape: tuple[int, int], points: list[tuple[float, float]]) -> np.ndarray:
        h, w = int(shape[0]), int(shape[1])
        if h <= 0 or w <= 0 or len(points) < 3:
            return np.zeros((max(0, h), max(0, w)), dtype=bool)

        pts = []
        for x, y in points:
            px = max(0, min(w - 1, float(x)))
            py = max(0, min(h - 1, float(y)))
            pts.append((px, py))
        mask_img = image_mod.new("L", (w, h), 0)
        image_draw_mod.Draw(mask_img).polygon(pts, outline=1, fill=1)
        return np.asarray(mask_img, dtype=bool)

    def _fl_gif_roi_make_specs(raw_rois: object, fallback_prefix: str = "ROI") -> list[dict]:
        specs = []
        used_keys = set()
        for idx, roi in enumerate(_fl_normalize_gif_polygons(raw_rois)):
            label = str(roi.get("label", f"{fallback_prefix} {idx + 1}") or f"{fallback_prefix} {idx + 1}").strip()
            key = _re2.sub(r"[^a-zA-Z0-9]+", "_", label.lower()).strip("_") or f"roi_{idx + 1}"
            if key in used_keys:
                suffix = 2
                while f"{key}_{suffix}" in used_keys:
                    suffix += 1
                key = f"{key}_{suffix}"
            used_keys.add(key)
            specs.append(
                {
                    "label": label,
                    "key": key,
                    "color": roi.get("color_hex", "#3E6AE1"),
                    "points": roi.get("points", []),
                }
            )
        return specs

    def _fl_gif_roi_mask_for(mask_cache: dict, roi: dict, shape: tuple[int, int]) -> np.ndarray:
        cache_key = (roi.get("key", ""), int(shape[0]), int(shape[1]))
        if cache_key not in mask_cache:
            mask_cache[cache_key] = _fl_gif_polygon_mask(shape, roi.get("points", []))
        return mask_cache[cache_key]

    def _fl_gif_roi_metrics_2d(img2d: np.ndarray, roi: dict, mask_cache: dict) -> dict:
        arr = np.asarray(img2d, dtype=np.float64)
        if arr.ndim != 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            return _fl_roi_empty_metrics()
        mask = _fl_gif_roi_mask_for(mask_cache, roi, (arr.shape[0], arr.shape[1]))
        if mask.shape != arr.shape:
            return _fl_roi_empty_metrics()
        return _fl_roi_metrics_from_flat(arr[mask])

    def _fl_gif_roi_background_mean(img2d: np.ndarray, bg_mode: str, bg_roi: dict | None, mask_cache: dict) -> float:
        if bg_mode == "roi" and bg_roi:
            return float(_fl_gif_roi_metrics_2d(img2d, bg_roi, mask_cache).get("mean", np.nan))
        return _fl_roi_background_mean(img2d, bg_mode, None)

    def _fl_gif_roi_apply_value(raw_val: float, area_px: int, metric: str, bg_mean: float, plot_metric: str) -> float:
        if plot_metric == "delta_f_over_f0":
            if np.isfinite(bg_mean):
                return _fl_roi_apply_metric_mode(raw_val, area_px, metric, bg_mean, "bg_subtracted")
            return float(raw_val)
        return _fl_roi_apply_metric_mode(raw_val, area_px, metric, bg_mean, plot_metric)

    def _fl_gif_kymo_stat(vals: np.ndarray, stat: str) -> float:
        finite = np.asarray(vals, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return float("nan")
        if stat == "median":
            return float(np.median(finite))
        if stat == "p90":
            return float(np.percentile(finite, 90.0))
        if stat == "p99":
            return float(np.percentile(finite, 99.0))
        return float(np.mean(finite))

    def _fl_gif_kymo_top_mean(vals: np.ndarray, top_fraction: float) -> float:
        finite = np.asarray(vals, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size == 0:
            return float("nan")
        top_fraction = max(0.001, min(1.0, float(top_fraction)))
        q = 100.0 * (1.0 - top_fraction)
        thr = float(np.percentile(finite, q))
        top = finite[finite >= thr]
        return float(np.mean(top)) if top.size else float("nan")

    def _fl_gif_kymo_correct_values(img2d: np.ndarray, roi: dict, bg_mode: str, bg_roi: dict | None, mask_cache: dict) -> tuple[np.ndarray, float]:
        arr = np.asarray(img2d, dtype=np.float64)
        if arr.ndim != 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            return np.asarray([], dtype=np.float64), float("nan")
        mask = _fl_gif_roi_mask_for(mask_cache, roi, (arr.shape[0], arr.shape[1]))
        vals = np.asarray(arr[mask], dtype=np.float64).ravel() if mask.shape == arr.shape else np.asarray([], dtype=np.float64)
        bg_mean = _fl_gif_roi_background_mean(arr, bg_mode, bg_roi, mask_cache)
        if np.isfinite(bg_mean):
            vals = vals - float(bg_mean)
        return vals, bg_mean

    def _fl_gaussian_kernel1d(sigma: float) -> np.ndarray:
        sigma = float(sigma or 0.0)
        if not np.isfinite(sigma) or sigma <= 0:
            return np.asarray([1.0], dtype=np.float64)
        radius = max(1, int(np.ceil(sigma * 3.0)))
        x = np.arange(-radius, radius + 1, dtype=np.float64)
        k = np.exp(-(x * x) / (2.0 * sigma * sigma))
        s = float(np.sum(k))
        if s <= 0 or not np.isfinite(s):
            return np.asarray([1.0], dtype=np.float64)
        return k / s

    def _fl_convolve_axis_edge(arr: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
        data = np.asarray(arr, dtype=np.float64)
        k = np.asarray(kernel, dtype=np.float64)
        if data.size == 0 or k.size <= 1:
            return data.copy()
        radius = k.size // 2
        pad = [(0, 0)] * data.ndim
        pad[axis] = (radius, radius)
        padded = np.pad(data, pad, mode="edge")
        moved = np.moveaxis(padded, axis, 0)
        out = np.empty_like(np.moveaxis(data, axis, 0), dtype=np.float64)
        for idx in np.ndindex(moved.shape[1:]):
            out[(slice(None),) + idx] = np.convolve(moved[(slice(None),) + idx], k, mode="valid")
        return np.moveaxis(out, 0, axis)

    def _fl_smooth_heatmap_2d(hist_pct_arr: np.ndarray, intensity_sigma: float, time_sigma: float) -> np.ndarray:
        sm = np.asarray(hist_pct_arr, dtype=np.float64)
        if sm.size == 0:
            return sm.copy()
        k_i = _fl_gaussian_kernel1d(intensity_sigma)
        k_t = _fl_gaussian_kernel1d(time_sigma)
        if k_i.size > 1:
            sm = _fl_convolve_axis_edge(sm, k_i, axis=1)
        if k_t.size > 1 and sm.shape[0] > 1:
            sm = _fl_convolve_axis_edge(sm, k_t, axis=0)
        return np.clip(sm, 0.0, None)

    def _fl_smooth_series_nan(vals: np.ndarray, sigma: float) -> np.ndarray:
        arr = np.asarray(vals, dtype=np.float64)
        k = _fl_gaussian_kernel1d(sigma)
        if arr.size == 0 or k.size <= 1:
            return arr.copy()
        radius = k.size // 2
        finite = np.isfinite(arr)
        filled = np.where(finite, arr, 0.0)
        weights = finite.astype(np.float64)
        filled_pad = np.pad(filled, (radius, radius), mode="edge")
        weights_pad = np.pad(weights, (radius, radius), mode="edge")
        numerator = np.convolve(filled_pad, k, mode="valid")
        denominator = np.convolve(weights_pad, k, mode="valid")
        out = np.full_like(arr, np.nan, dtype=np.float64)
        good = denominator > 1e-12
        out[good] = numerator[good] / denominator[good]
        return out

    def _fl_percent_label(v: float) -> str:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return f"{x:g}".replace(".", "p")

    def _fl_parse_percent_list(raw: object, max_items: int = 8, lower_exclusive: float = 0.0, upper_inclusive: float = 100.0) -> list[float]:
        values = []
        if isinstance(raw, list):
            tokens = raw
        elif isinstance(raw, str):
            tokens = [x for x in _re2.split(r"[,;\s]+", raw.strip()) if x]
        else:
            tokens = []
        seen = set()
        for token in tokens[: max_items * 2]:
            x = float_or(token, None)
            if x is None or not np.isfinite(x):
                continue
            if not (float(x) > lower_exclusive and float(x) <= upper_inclusive):
                continue
            key = round(float(x), 6)
            if key in seen:
                continue
            seen.add(key)
            values.append(float(x))
            if len(values) >= max_items:
                break
        return values

    def _fl_draw_gif_polygons(img, roi_polygons: list[dict]):
        if not roi_polygons:
            return
        draw = image_draw_mod.Draw(img)
        w_img, h_img = img.size
        line_w = max(2, int(round(min(w_img, h_img) * 0.004)))
        dot_r = max(2, int(round(line_w * 1.3)))
        font = image_font_mod.load_default()
        for poly in roi_polygons:
            pts = []
            for x, y in poly.get("points", []):
                px = max(0, min(w_img - 1, int(round(x))))
                py = max(0, min(h_img - 1, int(round(y))))
                pts.append((px, py))
            if len(pts) < 3:
                continue
            color = tuple(poly.get("color") or (255, 215, 0))
            label = str(poly.get("label", "ROI") or "ROI")
            draw.line(pts + [pts[0]], fill=color, width=line_w, joint="curve")
            for px, py in pts:
                draw.ellipse((px - dot_r, py - dot_r, px + dot_r, py + dot_r), fill=color)
            lx, ly = pts[0]
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            bx = max(0, min(w_img - tw - 8, lx + 4))
            by = max(0, min(h_img - th - 8, ly + 4))
            draw.rectangle((bx, by, bx + tw + 6, by + th + 5), fill=(0, 0, 0))
            draw.text((bx + 3, by + 2), label, fill=color, font=font)

    def _fl_gif_crop_box_for(
        shape: tuple[int, int],
        roi_polygons: list[dict],
        crop_mode: str,
        crop_roi_label: str,
        crop_padding_px: int,
    ) -> tuple[int, int, int, int] | None:
        mode = str(crop_mode or "full").strip().lower()
        if mode in {"", "none", "full", "full_frame", "frame"}:
            return None
        h, w = int(shape[0]), int(shape[1])
        if h <= 1 or w <= 1:
            return None

        polygons = roi_polygons or []
        if mode in {"roi", "selected_roi", "selected"}:
            label = str(crop_roi_label or "").strip()
            if label:
                polygons = [p for p in polygons if str(p.get("label", "") or "") == label]
            else:
                polygons = polygons[:1]
            if not polygons:
                raise ValueError("Choose a polygon ROI for cropped GIF export")
        elif mode in {"all", "all_roi", "all_rois", "rois"}:
            if not polygons:
                raise ValueError("Draw at least one polygon ROI before using ROI crop")
        else:
            return None

        xs = []
        ys = []
        for poly in polygons:
            for x, y in poly.get("points", []):
                if np.isfinite(x) and np.isfinite(y):
                    xs.append(float(x))
                    ys.append(float(y))
        if not xs or not ys:
            raise ValueError("Selected crop ROI has no valid points")

        pad = max(0, int(crop_padding_px or 0))
        x0 = max(0, int(np.floor(min(xs))) - pad)
        y0 = max(0, int(np.floor(min(ys))) - pad)
        x1 = min(w, int(np.ceil(max(xs))) + pad + 1)
        y1 = min(h, int(np.ceil(max(ys))) + pad + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise ValueError("Selected crop region is too small")
        return x0, y0, x1, y1

    def _fl_gif_rect_crop_box_for(
        shape: tuple[int, int],
        crop_rects: list[dict],
        crop_mode: str,
        crop_rect_label: str,
        crop_padding_px: int,
    ) -> tuple[tuple[int, int, int, int] | None, str]:
        mode = str(crop_mode or "full").strip().lower()
        if mode not in {"rect", "selected_rect", "roi2", "crop_rect"}:
            return None, ""
        h, w = int(shape[0]), int(shape[1])
        if h <= 1 or w <= 1:
            return None, ""
        rects = crop_rects or []
        label = str(crop_rect_label or "").strip()
        if label:
            rects = [r for r in rects if str(r.get("label", "") or "") == label]
        else:
            rects = rects[:1]
        if not rects:
            raise ValueError("Draw one ROI2 crop rectangle before using rectangle crop")
        rect = rects[0]
        pad = max(0, int(crop_padding_px or 0))
        x0 = max(0, int(np.floor(float(rect["x"]))) - pad)
        y0 = max(0, int(np.floor(float(rect["y"]))) - pad)
        x1 = min(w, int(np.ceil(float(rect["x"]) + float(rect["width"]))) + pad)
        y1 = min(h, int(np.ceil(float(rect["y"]) + float(rect["height"]))) + pad)
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise ValueError("Selected ROI2 crop rectangle is too small")
        return (x0, y0, x1, y1), str(rect.get("label", "") or "")

    def _fl_shift_gif_polygons_for_crop(
        roi_polygons: list[dict],
        crop_box: tuple[int, int, int, int] | None,
    ) -> list[dict]:
        if not crop_box:
            return roi_polygons or []
        x0, y0, x1, y1 = crop_box
        shifted = []
        for poly in roi_polygons or []:
            pts = poly.get("points", [])
            if not pts:
                continue
            xs = [float(x) for x, _y in pts if np.isfinite(x)]
            ys = [float(y) for _x, y in pts if np.isfinite(y)]
            if not xs or not ys:
                continue
            if max(xs) < x0 or min(xs) > x1 - 1 or max(ys) < y0 or min(ys) > y1 - 1:
                continue
            shifted.append({**poly, "points": [(float(x) - x0, float(y) - y0) for x, y in pts]})
        return shifted

    def _fl_apply_gif_crop(
        plane: np.ndarray,
        roi_polygons: list[dict],
        crop_rects: list[dict],
        crop_mode: str,
        crop_roi_label: str,
        crop_rect_label: str,
        crop_padding_px: int,
    ) -> tuple[np.ndarray, list[dict], dict]:
        arr = np.asarray(plane)
        if arr.ndim != 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            raise ValueError(f"Unsupported TIFF plane shape for crop: {arr.shape}")
        crop_box, rect_label = _fl_gif_rect_crop_box_for(arr.shape, crop_rects, crop_mode, crop_rect_label, crop_padding_px)
        if not crop_box:
            crop_box = _fl_gif_crop_box_for(arr.shape, roi_polygons, crop_mode, crop_roi_label, crop_padding_px)
        if not crop_box:
            return arr, roi_polygons or [], {"mode": "full", "x": 0, "y": 0, "width": int(arr.shape[1]), "height": int(arr.shape[0])}
        x0, y0, x1, y1 = crop_box
        return (
            arr[y0:y1, x0:x1],
            _fl_shift_gif_polygons_for_crop(roi_polygons, crop_box),
            {
                "mode": str(crop_mode or "full"),
                "roi_label": str(crop_roi_label or ""),
                "rect_label": rect_label,
                "padding_px": int(max(0, crop_padding_px or 0)),
                "x": int(x0),
                "y": int(y0),
                "width": int(x1 - x0),
                "height": int(y1 - y0),
            },
        )

    def _fl_render_gif_frame(
        plane: np.ndarray,
        lut: str,
        frame_idx: int,
        fps: float,
        scale_bar_um: float,
        pixels_per_um: float,
        add_timestamp: bool,
        roi_polygons: list[dict] | None = None,
        label_mode: str = "time",
    ):
        img = fl_gif.render_frame(
            plane,
            lut=lut,
            frame_idx=frame_idx,
            fps=fps,
            scale_bar_um=scale_bar_um,
            pixels_per_um=pixels_per_um,
            add_timestamp=add_timestamp,
            label_mode=label_mode,
            image_module=image_mod,
            image_draw_module=image_draw_mod,
            image_font_module=image_font_mod,
        )
        _fl_draw_gif_polygons(img, roi_polygons or [])
        return img

    def _fl_render_gif_roi_reference_preview(
        plane: np.ndarray,
        lut: str,
        frame_label: str,
        roi_polygons: list[dict],
        show_name: bool,
        show_scale_bar: bool,
        scale_bar_um: float,
        pixels_per_um: float,
    ) -> str:
        arr = np.asarray(plane).astype(np.float32)
        if arr.ndim != 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            raise ValueError(f"Unsupported preview plane shape: {arr.shape}")

        h, w = arr.shape
        lo, hi = np.percentile(arr, [1.0, 99.8])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.min(arr)), float(np.max(arr))
        if hi <= lo:
            hi = lo + 1.0
        gray8 = np.clip((arr - lo) / max(hi - lo, 1.0) * 255, 0, 255).astype(np.uint8)
        rgb = _fl_apply_lut(gray8, lut)

        fig_w = max(4.8, min(10.0, w / 180.0))
        fig_h = max(4.0, fig_w * (h / max(w, 1)))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
        ax.set_axis_off()
        ax.imshow(rgb, interpolation="nearest")

        for poly in roi_polygons:
            pts = poly.get("points", [])
            if len(pts) < 3:
                continue
            xs = [max(0, min(w - 1, float(x))) for x, _y in pts]
            ys = [max(0, min(h - 1, float(y))) for _x, y in pts]
            xs_closed = xs + [xs[0]]
            ys_closed = ys + [ys[0]]
            color = poly.get("color_hex", "#ffd166")
            label = str(poly.get("label", "ROI") or "ROI")
            ax.plot(xs_closed, ys_closed, color=color, lw=1.7, solid_joinstyle="round")
            ax.scatter(xs, ys, s=10, color=color, edgecolors="black", linewidths=0.25, zorder=3)
            ax.text(
                xs[0] + 4,
                ys[0] + 13,
                label,
                color=color,
                fontsize=8.5,
                weight="bold",
                va="top",
                bbox={"facecolor": "black", "alpha": 0.62, "edgecolor": "none", "boxstyle": "round,pad=0.22"},
            )

        if show_name:
            label = str(frame_label or "").strip()
            if label:
                ax.text(
                    10,
                    16,
                    label,
                    color="white",
                    fontsize=8.5,
                    va="top",
                    bbox={"facecolor": "black", "alpha": 0.65, "edgecolor": "none", "boxstyle": "round,pad=0.25"},
                )

        if show_scale_bar and scale_bar_um > 0 and pixels_per_um > 0:
            bar_px = int(round(scale_bar_um * pixels_per_um))
            bar_px = max(1, min(bar_px, int(0.6 * w)))
            pad = max(8, int(min(h, w) * 0.02))
            bar_thick = max(3, int(min(h, w) * 0.006))
            x0 = max(pad, int(0.05 * w))
            y0 = h - pad - bar_thick
            label_text = f"{scale_bar_um:g} um"
            ax.add_patch(
                Rectangle(
                    (x0 - pad, y0 - (bar_thick + 18)),
                    bar_px + 2 * pad,
                    bar_thick + 22,
                    facecolor="black",
                    edgecolor="none",
                    alpha=0.65,
                )
            )
            ax.add_patch(Rectangle((x0, y0), bar_px, bar_thick, facecolor="white", edgecolor="none"))
            ax.text(x0, y0 - 4, label_text, color="white", fontsize=8.0, va="bottom")

        fig.tight_layout(pad=0.15)
        return fig_to_b64(fig)

    def _fl_image_to_b64(img, fmt: str = "PNG") -> str:
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    _FL_LUT_OPTIONS = fl_stack.LUT_OPTIONS
    _FL_DENOISE_OPTIONS = fl_stack.DENOISE_OPTIONS
    _FL_BACKGROUND_OPTIONS = fl_stack.BACKGROUND_OPTIONS
    _FL_DEFAULT_LUT_BY_INDEX = fl_stack.DEFAULT_LUT_BY_INDEX
    _FL_DEFAULT_DENOISE_BY_INDEX = fl_stack.DEFAULT_DENOISE_BY_INDEX
    _FL_DEFAULT_BACKGROUND_BY_INDEX = fl_stack.DEFAULT_BACKGROUND_BY_INDEX

    _fl_compute_default_min_max = fl_stack.compute_default_min_max
    _fl_convert_to_export_dtype = fl_stack.convert_to_export_dtype
    _fl_box_blur2d = fl_stack.box_blur2d
    _fl_apply_background_suppression = fl_stack.apply_background_suppression
    _fl_apply_optional_denoise = fl_stack.apply_optional_denoise
    _fl_preprocess_stack_image = fl_stack.preprocess_stack_image
    _fl_compute_auto_range_with_processing = fl_stack.compute_auto_range_with_processing
    _fl_clean_choice = fl_stack.clean_choice
    _fl_to_macro_path = fl_stack.to_macro_path
    _fl_imagej_lut_command = fl_stack.imagej_lut_command
    _fl_build_fiji_macro = fl_stack.build_fiji_macro
    _fl_build_default_settings_for_pages = fl_stack.build_default_settings_for_pages
    _fl_normalize_settings_for_pages = fl_stack.normalize_settings_for_pages
    _fl_build_settings_from_template = fl_stack.build_settings_from_template
    _fl_is_generated_tiff = fl_stack.is_generated_tiff

    def _fl_read_tiff_as_pages(tiff_path: Path) -> list[np.ndarray]:
        return fl_stack.read_tiff_as_pages(tiff_path, tifflib)

    def _fl_export_with_settings(tiff_path: Path, pages: list[np.ndarray], settings: list[dict]) -> dict:
        return fl_stack.export_with_settings(tiff_path, pages, settings, tifflib)

    def _fl_roi_pick_output_dir(records: list, output_dir_raw: str = "") -> Path:
        recs = records if isinstance(records, list) else []

        anchor = None
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            for key in ("stack1", "stack2"):
                p = str(rec.get(key, "") or "").strip()
                if p:
                    anchor = Path(p).parent
                    break
            if anchor is not None:
                break

        out_raw = str(output_dir_raw or "").strip()
        if out_raw:
            p_out = Path(out_raw).expanduser()
            if not p_out.is_absolute() and anchor is not None:
                p_out = anchor / p_out
            elif not p_out.is_absolute():
                p_out = Path.cwd() / p_out
            return p_out

        if anchor is not None:
            return anchor
        return Path.cwd()

    def _fl_roi_render_reference_preview(
        preview_path: str,
        roi_specs: list,
        show_name: bool,
        show_scale_bar: bool,
        scale_bar_um: float,
        scale_label: str,
        pixel_size_um_override: float | None,
        label_scale: float,
    ) -> dict:
        if not preview_path or not Path(preview_path).exists():
            return {"img": "", "pixel_size_um": None, "path": ""}

        img2d = _fl_roi_read_first_page(preview_path)
        disp = _fl_normalize_display_2d(img2d)
        h, w = disp.shape

        fig_w = max(4.8, min(9.0, w / 180.0))
        fig_h = max(4.2, fig_w * (h / max(w, 1)))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=140)
        ax.set_axis_off()
        ax.imshow(disp, cmap="gray", interpolation="nearest")

        fs_roi = max(8.0, 9.0 * label_scale)
        for roi in roi_specs:
            color = roi.get("color", "#3E6AE1")
            label = str(roi.get("label", "ROI")).strip() or "ROI"
            if _fl_roi_shape_type(roi) == "concentric":
                cx, cy, radius, _x1, y1, _x2, _y2, ring_width = _fl_roi_circle_geometry(roi)
                ring_width = _fl_roi_ring_width_px(roi)
                if radius <= 0:
                    continue
                r = ring_width
                while r < radius:
                    ax.add_patch(Circle((cx, cy), r, linewidth=0.85, edgecolor=color, facecolor="none", alpha=0.55))
                    r += ring_width
                ax.add_patch(Circle((cx, cy), radius, linewidth=1.7, edgecolor=color, facecolor="none"))
                ax.plot([cx - 5, cx + 5], [cy, cy], color=color, lw=1.1)
                ax.plot([cx, cx], [cy - 5, cy + 5], color=color, lw=1.1)
                ax.text(cx + 4, max(0, cy - radius - 4), label, color=color, fontsize=fs_roi)
            else:
                x1 = int_or(roi.get("x1", 0), 0)
                y1 = int_or(roi.get("y1", 0), 0)
                x2 = int_or(roi.get("x2", 0), 0)
                y2 = int_or(roi.get("y2", 0), 0)
                if x2 <= x1 or y2 <= y1:
                    continue
                ax.add_patch(
                    Rectangle(
                        (x1, y1),
                        x2 - x1,
                        y2 - y1,
                        linewidth=1.6,
                        edgecolor=color,
                        facecolor="none",
                    )
                )
                ax.text(x1, max(0, y1 - 4), label, color=color, fontsize=fs_roi)

        if show_name:
            preview_name = Path(preview_path).name
            fs_name = max(8.0, 9.0 * label_scale)
            ax.text(
                10,
                16,
                preview_name,
                color="white",
                fontsize=fs_name,
                va="top",
                bbox={"facecolor": "black", "alpha": 0.65, "edgecolor": "none", "boxstyle": "round,pad=0.25"},
            )

        pixel_size_um = pixel_size_um_override
        if pixel_size_um is None or not np.isfinite(pixel_size_um) or pixel_size_um <= 0:
            pixel_size_um = _fl_infer_pixel_size_um_from_tiff(preview_path)

        if show_scale_bar and pixel_size_um is not None and pixel_size_um > 0 and scale_bar_um > 0:
            bar_px = int(round(scale_bar_um / pixel_size_um))
            bar_px = max(1, min(bar_px, int(0.6 * w)))
            pad = max(8, int(min(h, w) * 0.02))
            bar_thick = max(3, int(min(h, w) * 0.006 * max(0.6, label_scale)))
            x0 = max(pad, int(0.05 * w))
            y0 = h - pad - bar_thick
            text_label = str(scale_label or "").strip() or f"{scale_bar_um:g} um"

            ax.add_patch(
                Rectangle(
                    (x0 - pad, y0 - (bar_thick + 18)),
                    bar_px + 2 * pad,
                    bar_thick + 22,
                    facecolor="black",
                    edgecolor="none",
                    alpha=0.65,
                )
            )
            ax.add_patch(
                Rectangle(
                    (x0, y0),
                    bar_px,
                    bar_thick,
                    facecolor="white",
                    edgecolor="none",
                )
            )
            ax.text(x0, y0 - 4, text_label, color="white", fontsize=max(8.0, 8.0 * label_scale), va="bottom")

        fig.tight_layout(pad=0.2)
        img_b64 = fig_to_b64(fig)
        return {
            "img": img_b64,
            "pixel_size_um": float(pixel_size_um) if pixel_size_um is not None and np.isfinite(pixel_size_um) else None,
            "path": str(preview_path),
        }

    def _fl_get_pil_font(size_px: int):
        if not has_pil or image_font_mod is None:
            return None
        size_px = max(10, int(size_px))
        for font_name in ["DejaVuSans-Bold.ttf", "Arial.ttf"]:
            try:
                return image_font_mod.truetype(font_name, size_px)
            except Exception:
                continue
        try:
            return image_font_mod.load_default()
        except Exception:
            return None

    def _fl_measure_pil_text(draw, text: str, font, stroke_w: int = 0) -> tuple[int, int]:
        if hasattr(draw, "textbbox"):
            b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
            return int(b[2] - b[0]), int(b[3] - b[1])
        w, h = draw.textsize(text, font=font)
        return int(w), int(h)

    def _fl_roi_render_gif_frame(
        img2d: np.ndarray,
        frame_name: str,
        roi_specs: list,
        pixel_size_um: float | None,
        scale_bar_um: float,
        scale_label: str,
        show_name: bool,
        show_scale_bar: bool,
        label_scale: float,
    ):
        img_disp = (_fl_normalize_display_2d(img2d) * 255.0).astype(np.uint8)
        pil_img = image_mod.fromarray(img_disp, mode="L").convert("RGB")
        draw = image_draw_mod.Draw(pil_img)
        w, h = pil_img.size

        fs = max(12, int(min(w, h) * 0.018 * max(0.6, label_scale)))
        font_main = _fl_get_pil_font(fs)
        font_small = _fl_get_pil_font(max(10, int(fs * 0.9)))
        stroke_w = max(1, int(fs * 0.12))
        pad = max(8, int(min(w, h) * 0.012))

        for roi in roi_specs:
            label = str(roi.get("label", "ROI")).strip() or "ROI"
            color = str(roi.get("color", "#3E6AE1"))
            width = max(2, int(2 * max(0.7, label_scale)))
            if _fl_roi_shape_type(roi) == "concentric":
                cx, cy, radius, _x1, _y1, _x2, _y2, ring_width = _fl_roi_circle_geometry(roi)
                ring_width = _fl_roi_ring_width_px(roi)
                if radius <= 0:
                    continue
                rr = ring_width
                while rr < radius:
                    draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=color, width=max(1, width - 1))
                    rr += ring_width
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=width)
                cross = max(4, min(12, int(radius * 0.08)))
                draw.line((cx - cross, cy, cx + cross, cy), fill=color, width=width)
                draw.line((cx, cy - cross, cx, cy + cross), fill=color, width=width)
                label_pos = (cx + 3, max(0, cy - radius - max(12, int(12 * label_scale))))
            else:
                x1 = int_or(roi.get("x1", 0), 0)
                y1 = int_or(roi.get("y1", 0), 0)
                x2 = int_or(roi.get("x2", 0), 0)
                y2 = int_or(roi.get("y2", 0), 0)
                if x2 <= x1 or y2 <= y1:
                    continue
                draw.rectangle((x1, y1, x2, y2), outline=color, width=width)
                label_pos = (x1 + 3, max(0, y1 - max(12, int(12 * label_scale))))
            draw.text(
                label_pos,
                label,
                fill=color,
                font=font_small,
                stroke_width=max(1, int(stroke_w * 0.8)),
                stroke_fill=(0, 0, 0),
            )

        if show_name:
            title = str(frame_name or "Frame")
            tw, th = _fl_measure_pil_text(draw, title, font_main, stroke_w)
            bx0, by0 = pad, pad
            bx1 = bx0 + tw + 2 * pad
            by1 = by0 + th + 2 * max(4, pad // 2)
            draw.rectangle((bx0, by0, bx1, by1), fill=(0, 0, 0))
            draw.text(
                (bx0 + pad, by0 + max(2, pad // 3)),
                title,
                fill=(255, 255, 255),
                font=font_main,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
            )

        if show_scale_bar and pixel_size_um is not None and pixel_size_um > 0 and scale_bar_um > 0:
            bar_px = int(round(scale_bar_um / pixel_size_um))
            bar_px = max(1, min(bar_px, int(0.6 * w)))
            bar_thick = max(3, int(min(w, h) * 0.004 * max(0.6, label_scale)))
            label_text = str(scale_label or "").strip() or f"{scale_bar_um:g} um"
            _, text_h = _fl_measure_pil_text(draw, label_text, font_small, stroke_w)

            x0 = max(pad, int(0.05 * w))
            y0 = h - pad - bar_thick
            y_txt = y0 - text_h - max(6, pad // 2)
            x1 = x0 + bar_px

            draw.rectangle(
                (
                    x0 - pad,
                    max(0, y_txt - max(4, pad // 2)),
                    min(w - 1, x1 + pad),
                    min(h - 1, y0 + bar_thick + max(4, pad // 2)),
                ),
                fill=(0, 0, 0),
            )
            draw.rectangle((x0, y0, x1, y0 + bar_thick), fill=(255, 255, 255))
            draw.text(
                (x0, y_txt),
                label_text,
                fill=(255, 255, 255),
                font=font_small,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
            )

        return pil_img

    from .fluorescence_stack_routes import register_fluorescence_stack_routes
    from .fluorescence_3d_routes import register_fluorescence_3d_routes

    stack_route_ctx = {
        "err": err,
        "browse_files": browse_files,
        "int_or": int_or,
        "float_or": float_or,
        "has_tiff": has_tiff,
        "has_pil": has_pil,
        "tifflib": tifflib,
        "jobs": jobs,
        "_FL_BACKGROUND_OPTIONS": _FL_BACKGROUND_OPTIONS,
        "_FL_DENOISE_OPTIONS": _FL_DENOISE_OPTIONS,
        "_FL_LUT_OPTIONS": _FL_LUT_OPTIONS,
        "_fl_bool": _fl_bool,
        "_fl_build_default_settings_for_pages": _fl_build_default_settings_for_pages,
        "_fl_build_settings_from_template": _fl_build_settings_from_template,
        "_fl_clean_choice": _fl_clean_choice,
        "_fl_compute_auto_range_with_processing": _fl_compute_auto_range_with_processing,
        "_fl_export_with_settings": _fl_export_with_settings,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_is_generated_tiff": _fl_is_generated_tiff,
        "_fl_normalize_settings_for_pages": _fl_normalize_settings_for_pages,
        "_fl_read_tiff_as_pages": _fl_read_tiff_as_pages,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
        "_fl_select_display_frame": _fl_select_display_frame,
        "_fl_tiff_gif_frame_count": _fl_tiff_gif_frame_count,
    }
    volume_route_ctx = {
        "err": err,
        "int_or": int_or,
        "float_or": float_or,
        "has_tiff": has_tiff,
        "has_pil": has_pil,
        "jobs": jobs,
        "_FL_DENOISE_OPTIONS": _FL_DENOISE_OPTIONS,
        "_fl_bool": _fl_bool,
        "_fl_clean_choice": _fl_clean_choice,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
        "_fl_tiff_plane_from_array": _fl_tiff_plane_from_array,
        "_fl_tiff_read_array": _fl_tiff_read_array,
        "_fl_tiff_series_info": _fl_tiff_series_info,
        "_fl_tiff_volume3d_payload": _fl_tiff_volume3d_payload,
        "_fl_volume3d_html": _fl_volume3d_html,
    }
    register_fluorescence_stack_routes(app, stack_route_ctx)
    register_fluorescence_3d_routes(app, volume_route_ctx)

    _fl_roi_shape_type = fl_roi.shape_type
    _fl_roi_empty_metrics = fl_roi.empty_metrics
    _fl_roi_metrics_from_flat = fl_roi.metrics_from_flat
    _fl_roi_circle_geometry = fl_roi.circle_geometry
    _fl_roi_ring_width_px = fl_roi.ring_width_px
    _fl_roi_ring_count = fl_roi.ring_count
    _fl_roi_values_2d = fl_roi.values_2d
    _fl_roi_metrics_2d = fl_roi.metrics_2d
    _fl_roi_safe_ratio = fl_roi.safe_ratio
    _fl_roi_sequence_number = fl_roi.sequence_number
    _fl_roi_background_mean = fl_roi.background_mean
    _fl_roi_apply_metric_mode = fl_roi.apply_metric_mode
    _fl_roi_radial_metrics_2d = fl_roi.radial_metrics_2d
    _fl_roi_radial_pair_rows = fl_roi.radial_pair_rows
    _fl_roi_shared_ylim = fl_roi.shared_ylim

    def _fl_roi_collect_pairs(folder: Path):
        return fl_roi.collect_pairs(folder)

    def _fl_roi_compute(stack_path: str, rois: list, metric: str = "mean"):
        return fl_roi.compute_stack_roi(stack_path, rois, metric, tifflib)

    def _fl_roi_read_first_page(stack_path: str) -> np.ndarray:
        return fl_roi.read_first_page(stack_path, tifflib)

    def _fl_roi_plot_radial_profiles(radial_df: pd.DataFrame, roi_specs: list, metric: str, plot_metric: str, has_ref: bool) -> str:
        if radial_df is None or radial_df.empty:
            return ""

        seq_vals_all = pd.to_numeric(radial_df["sequence_number"], errors="coerce").to_numpy(dtype=float)
        use_sequence_axis = np.isfinite(seq_vals_all).any()
        x_label = "Sequence"
        metric_labels = {
            "mean": "Mean",
            "top20_mean": "Top20 Mean",
            "sum": "Sum",
            "max": "Max",
            "std": "Std",
        }
        presentation_labels = {
            "absolute": "Absolute",
            "bg_subtracted": "BG-subtracted",
            "bg_normalized": "BG-normalized",
            "delta_f_over_f0": "DeltaF/F0",
        }
        y_label = f"{metric_labels.get(metric, metric)} ({presentation_labels.get(plot_metric, plot_metric)})"
        if has_ref:
            y_label += " (Ref=1)"

        ring_keys = (
            radial_df.assign(
                _ring_inner_key=np.where(
                    pd.to_numeric(radial_df.get("inner_radius_um", np.nan), errors="coerce").notna(),
                    pd.to_numeric(radial_df.get("inner_radius_um", np.nan), errors="coerce"),
                    pd.to_numeric(radial_df["inner_radius_px"], errors="coerce"),
                ),
                _ring_outer_key=np.where(
                    pd.to_numeric(radial_df.get("outer_radius_um", np.nan), errors="coerce").notna(),
                    pd.to_numeric(radial_df.get("outer_radius_um", np.nan), errors="coerce"),
                    pd.to_numeric(radial_df["outer_radius_px"], errors="coerce"),
                ),
            )[["roi_key", "_ring_inner_key", "_ring_outer_key"]]
            .drop_duplicates()
            .sort_values(["roi_key", "_ring_inner_key"], kind="stable")
        )
        ring_color = {
            tuple(row): plt.cm.tab20(i % 20)
            for i, row in enumerate(ring_keys[["roi_key", "_ring_inner_key", "_ring_outer_key"]].itertuples(index=False, name=None))
        }
        show_legend = len(ring_keys) <= 18

        fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.4), dpi=120)
        ax1, ax2, ax3, ax4 = axes.ravel()
        panels = [
            (ax1, "stack1_value", "Stack1 by ring", y_label),
            (ax2, "stack2_value", "Stack2 by ring", y_label),
            (ax3, "ratio", "Stack1 / Stack2 by ring", "Ratio" + (" (Ref=1)" if has_ref else "")),
            (ax4, "difference", "Stack1 - Stack2 by ring", f"{y_label} difference"),
        ]

        radial_plot_df = radial_df.copy()
        inner_um = pd.to_numeric(radial_plot_df.get("inner_radius_um", np.nan), errors="coerce")
        outer_um = pd.to_numeric(radial_plot_df.get("outer_radius_um", np.nan), errors="coerce")
        radial_plot_df["_ring_inner_key"] = np.where(inner_um.notna(), inner_um, pd.to_numeric(radial_plot_df["inner_radius_px"], errors="coerce"))
        radial_plot_df["_ring_outer_key"] = np.where(outer_um.notna(), outer_um, pd.to_numeric(radial_plot_df["outer_radius_px"], errors="coerce"))
        grouped = radial_plot_df.groupby(["roi_key", "roi_label", "_ring_inner_key", "_ring_outer_key"], sort=False)
        for ax, y_col, title, panel_ylabel in panels:
            x_tick_pairs = []
            for (roi_key, roi_label, inner_key, outer_key), grp in grouped:
                grp = grp.copy()
                grp["_seq"] = pd.to_numeric(grp["sequence_number"], errors="coerce")
                if use_sequence_axis and grp["_seq"].notna().any():
                    grp = grp.sort_values(["_seq", "base_name"], kind="stable")
                    x = grp["_seq"].to_numpy(dtype=float)
                else:
                    grp = grp.sort_values(["base_name"], kind="stable")
                    x = np.arange(len(grp), dtype=float)
                    x_tick_pairs.extend((float(i), str(v)) for i, v in enumerate(grp["base_name"].astype(str).tolist()))
                y = pd.to_numeric(grp[y_col], errors="coerce").to_numpy(dtype=float)
                if not np.isfinite(x).any() or not np.isfinite(y).any():
                    continue
                inner_um_vals = pd.to_numeric(grp.get("inner_radius_um", pd.Series(dtype=float)), errors="coerce").dropna()
                outer_um_vals = pd.to_numeric(grp.get("outer_radius_um", pd.Series(dtype=float)), errors="coerce").dropna()
                if not inner_um_vals.empty and not outer_um_vals.empty:
                    ring_label = f"{float(inner_um_vals.iloc[0]):g}-{float(outer_um_vals.iloc[0]):g} um"
                else:
                    ring_label = f"{float(inner_key):g}-{float(outer_key):g} px"
                label = f"{roi_label} {ring_label}".strip()
                ax.plot(
                    x,
                    y,
                    marker="o",
                    ms=3,
                    lw=1.2,
                    alpha=0.86,
                    color=ring_color.get((roi_key, inner_key, outer_key), "#3E6AE1"),
                    label=label if show_legend else None,
                )
            ax.set_title(title)
            ax.set_xlabel(x_label)
            ax.set_ylabel(panel_ylabel)
            ax.grid(True, alpha=0.35)
            ax.tick_params(axis="both", labelsize=8)
            if not use_sequence_axis and x_tick_pairs:
                dedup = []
                seen = set()
                for xv, lab in x_tick_pairs:
                    if xv in seen:
                        continue
                    seen.add(xv)
                    dedup.append((xv, lab))
                ax.set_xticks([x for x, _lab in dedup])
                ax.set_xticklabels([lab for _x, lab in dedup], rotation=45, ha="right", fontsize=7)
            if y_col == "ratio":
                ax.axhline(1.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            if y_col == "difference":
                ax.axhline(0.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            if show_legend:
                ax.legend(fontsize=7, frameon=False, loc="best")

        stack_ylim = _fl_roi_shared_ylim(radial_df["stack1_value"], radial_df["stack2_value"])
        if stack_ylim is not None:
            ax1.set_ylim(stack_ylim)
            ax2.set_ylim(stack_ylim)

        fig.tight_layout()
        return fig_to_b64(fig)

    _fl_roi_resolve_ref_index = fl_roi.resolve_ref_index
    _fl_roi_normalize_to_reference = fl_roi.normalize_to_reference
    _fl_roi_delta_f_over_f0 = fl_roi.delta_f_over_f0

    from .fluorescence_gif_routes import register_fluorescence_gif_routes
    from .fluorescence_roi_routes import register_fluorescence_roi_routes

    gif_route_ctx = {
        "err": err,
        "fig_to_b64": fig_to_b64,
        "float_or": float_or,
        "int_or": int_or,
        "has_pil": has_pil,
        "has_tiff": has_tiff,
        "jobs": jobs,
        "_fl_apply_gif_crop": _fl_apply_gif_crop,
        "_fl_bool": _fl_bool,
        "_fl_decode_base64_payload": _fl_decode_base64_payload,
        "_fl_gif_kymo_stat": _fl_gif_kymo_stat,
        "_fl_gif_kymo_top_mean": _fl_gif_kymo_top_mean,
        "_fl_gif_roi_apply_value": _fl_gif_roi_apply_value,
        "_fl_gif_roi_background_mean": _fl_gif_roi_background_mean,
        "_fl_gif_roi_make_specs": _fl_gif_roi_make_specs,
        "_fl_gif_roi_mask_for": _fl_gif_roi_mask_for,
        "_fl_gif_roi_metrics_2d": _fl_gif_roi_metrics_2d,
        "_fl_image_to_b64": _fl_image_to_b64,
        "_fl_normalize_gif_polygons": _fl_normalize_gif_polygons,
        "_fl_normalize_gif_rects": _fl_normalize_gif_rects,
        "_fl_parse_percent_list": _fl_parse_percent_list,
        "_fl_parse_slice_spec": _fl_parse_slice_spec,
        "_fl_percent_label": _fl_percent_label,
        "_fl_read_selected_gif_planes": _fl_read_selected_gif_planes,
        "_fl_render_gif_frame": _fl_render_gif_frame,
        "_fl_render_gif_roi_reference_preview": _fl_render_gif_roi_reference_preview,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
        "_fl_roi_delta_f_over_f0": _fl_roi_delta_f_over_f0,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
        "_fl_smooth_heatmap_2d": _fl_smooth_heatmap_2d,
        "_fl_smooth_series_nan": _fl_smooth_series_nan,
        "_fl_tiff_gif_frame_count": _fl_tiff_gif_frame_count,
    }
    roi_route_ctx = {
        "err": err,
        "fig_to_b64": fig_to_b64,
        "float_or": float_or,
        "int_or": int_or,
        "has_pil": has_pil,
        "has_tiff": has_tiff,
        "jobs": jobs,
        "tifflib": tifflib,
        "_fl_bool": _fl_bool,
        "_fl_decode_base64_payload": _fl_decode_base64_payload,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_infer_pixel_size_um_from_tiff": _fl_infer_pixel_size_um_from_tiff,
        "_fl_roi_apply_metric_mode": _fl_roi_apply_metric_mode,
        "_fl_roi_background_mean": _fl_roi_background_mean,
        "_fl_roi_circle_geometry": _fl_roi_circle_geometry,
        "_fl_roi_collect_pairs": _fl_roi_collect_pairs,
        "_fl_roi_compute": _fl_roi_compute,
        "_fl_roi_delta_f_over_f0": _fl_roi_delta_f_over_f0,
        "_fl_roi_empty_metrics": _fl_roi_empty_metrics,
        "_fl_roi_metrics_2d": _fl_roi_metrics_2d,
        "_fl_roi_normalize_to_reference": _fl_roi_normalize_to_reference,
        "_fl_roi_pick_output_dir": _fl_roi_pick_output_dir,
        "_fl_roi_plot_radial_profiles": _fl_roi_plot_radial_profiles,
        "_fl_roi_radial_pair_rows": _fl_roi_radial_pair_rows,
        "_fl_roi_read_first_page": _fl_roi_read_first_page,
        "_fl_roi_render_gif_frame": _fl_roi_render_gif_frame,
        "_fl_roi_render_reference_preview": _fl_roi_render_reference_preview,
        "_fl_roi_resolve_ref_index": _fl_roi_resolve_ref_index,
        "_fl_roi_ring_count": _fl_roi_ring_count,
        "_fl_roi_safe_ratio": _fl_roi_safe_ratio,
        "_fl_roi_sequence_number": _fl_roi_sequence_number,
        "_fl_roi_shape_type": _fl_roi_shape_type,
        "_fl_roi_shared_ylim": _fl_roi_shared_ylim,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
    }
    register_fluorescence_gif_routes(app, gif_route_ctx)
    register_fluorescence_roi_routes(app, roi_route_ctx)
