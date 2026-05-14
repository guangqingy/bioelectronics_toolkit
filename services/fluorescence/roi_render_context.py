from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Rectangle

from services.fluorescence import roi as fl_roi
from services.fluorescence import route_helpers as fl_helpers


def build_roi_render_context(
    *,
    tifflib,
    has_pil: bool,
    image_mod,
    image_draw_mod,
    image_font_mod,
    fig_to_b64,
    int_or,
    infer_pixel_size_um_from_tiff,
) -> dict:
    _fl_normalize_display_2d = fl_helpers.normalize_display_2d
    _fl_infer_pixel_size_um_from_tiff = infer_pixel_size_um_from_tiff
    _fl_roi_shape_type = fl_roi.shape_type
    _fl_roi_circle_geometry = fl_roi.circle_geometry
    _fl_roi_ring_width_px = fl_roi.ring_width_px

    def _fl_roi_read_first_page(stack_path: str) -> np.ndarray:
        return fl_roi.read_first_page(stack_path, tifflib)

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
                    ax.add_patch(
                        Circle(
                            (cx, cy),
                            r,
                            linewidth=0.85,
                            edgecolor=color,
                            facecolor="none",
                            alpha=0.55,
                        )
                    )
                    r += ring_width
                ax.add_patch(
                    Circle((cx, cy), radius, linewidth=1.7, edgecolor=color, facecolor="none")
                )
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
                bbox={
                    "facecolor": "black",
                    "alpha": 0.65,
                    "edgecolor": "none",
                    "boxstyle": "round,pad=0.25",
                },
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
            ax.text(
                x0,
                y0 - 4,
                text_label,
                color="white",
                fontsize=max(8.0, 8.0 * label_scale),
                va="bottom",
            )

        fig.tight_layout(pad=0.2)
        img_b64 = fig_to_b64(fig)
        return {
            "img": img_b64,
            "pixel_size_um": float(pixel_size_um)
            if pixel_size_um is not None and np.isfinite(pixel_size_um)
            else None,
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
                    draw.ellipse(
                        (cx - rr, cy - rr, cx + rr, cy + rr), outline=color, width=max(1, width - 1)
                    )
                    rr += ring_width
                draw.ellipse(
                    (cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=width
                )
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

    return {
        "_fl_roi_pick_output_dir": _fl_roi_pick_output_dir,
        "_fl_roi_render_gif_frame": _fl_roi_render_gif_frame,
        "_fl_roi_render_reference_preview": _fl_roi_render_reference_preview,
    }
