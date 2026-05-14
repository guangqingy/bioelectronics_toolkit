from __future__ import annotations

import base64
import io
import re as _re2

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

from services.fluorescence import gif as fl_gif
from services.fluorescence import roi as fl_roi
from services.fluorescence import route_helpers as fl_helpers


def build_gif_roi_context(*, image_mod, image_draw_mod, image_font_mod, fig_to_b64, float_or) -> dict:
    _fl_normalize_hex_color = fl_helpers.normalize_hex_color
    _fl_apply_lut = fl_helpers.apply_lut
    _fl_roi_empty_metrics = fl_roi.empty_metrics
    _fl_roi_metrics_from_flat = fl_roi.metrics_from_flat
    _fl_roi_background_mean = fl_roi.background_mean
    _fl_roi_apply_metric_mode = fl_roi.apply_metric_mode

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


    return {
        "_fl_apply_gif_crop": _fl_apply_gif_crop,
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
        "_fl_percent_label": _fl_percent_label,
        "_fl_render_gif_frame": _fl_render_gif_frame,
        "_fl_render_gif_roi_reference_preview": _fl_render_gif_roi_reference_preview,
        "_fl_smooth_heatmap_2d": _fl_smooth_heatmap_2d,
        "_fl_smooth_series_nan": _fl_smooth_series_nan,
    }
