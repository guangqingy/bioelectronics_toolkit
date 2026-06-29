from __future__ import annotations

import base64
import csv
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from services.matplotlib_utils import new_subplots


def _num(value: Any, default: Any) -> Any:
    return default if value is None else value


@dataclass(frozen=True)
class Volume3DExportContext:
    denoise_options: list[str]
    bool_value: Callable[[Any, bool], bool]
    clean_choice: Callable[[Any, list[str], str], str]
    apply_optional_denoise: Callable[[np.ndarray, str], np.ndarray]
    sanitize_prefix: Callable[[str, str], str]
    tiff_plane_from_array: Callable[..., np.ndarray]
    tiff_read_array: Callable[[Path], tuple[np.ndarray, str, dict]]
    tiff_series_info: Callable[[Path], dict]
    tiff_volume3d_payload: Callable[..., dict]
    volume3d_html: Callable[[dict], str]
    fig_to_b64: Callable[..., str]


def volume_payload_from_body(
    d: dict, ctx: Volume3DExportContext, *, for_export: bool = False
) -> tuple[Path, dict]:
    path = str(d.get("path", "") or "").strip()
    c = _num(d.get("c"), 0)
    t = _num(d.get("t"), 0)
    extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
    channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
    if channel_mode not in {"composite", "current"}:
        channel_mode = "composite"

    max_points_default = 110000 if for_export else 70000
    max_xy_default = 220 if for_export else 180
    max_z_default = 120 if for_export else 80
    threshold_default = 98.6 if for_export else 98.8
    max_points = _num(d.get("max_points"), max_points_default)
    max_xy = _num(d.get("max_xy"), max_xy_default)
    max_z = _num(d.get("max_z"), max_z_default)
    threshold_percentile = _num(d.get("threshold_percentile"), threshold_default)
    channel_ranges = d.get("channel_ranges") if isinstance(d.get("channel_ranges"), dict) else {}
    denoise_mode = ctx.clean_choice(d.get("denoise"), ctx.denoise_options, "Off")
    interlayer_level = str(d.get("interlayer_level", "middle") or "middle").strip().lower()
    density_mode = str(d.get("density_mode", "off") or "off").strip().lower()
    density_radius_raw = d.get("density_radius_um", None)
    density_radius_um = (
        None
        if density_radius_raw is None or density_radius_raw == ""
        else density_radius_raw
    )
    density_min_raw = d.get("density_min_neighbors", None)
    density_min_neighbors = (
        None if density_min_raw is None or density_min_raw == "" else density_min_raw
    )
    show_scale_bar = ctx.bool_value(d.get("show_scale_bar", True), True)
    scale_bar_um = max(0.0, _num(d.get("scale_bar_um"), 20.0))
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Input TIFF not found: {path}")

    payload = ctx.tiff_volume3d_payload(
        p,
        c=c,
        t=t,
        extra_indices=extra_indices,
        channel_mode=channel_mode,
        max_points=max_points,
        max_xy=max_xy,
        max_z=max_z,
        threshold_percentile=threshold_percentile,
        channel_ranges=channel_ranges,
        denoise_mode=denoise_mode,
        interlayer_level=interlayer_level,
        density_mode=density_mode,
        density_radius_um=density_radius_um,
        density_min_neighbors=density_min_neighbors,
        show_scale_bar=show_scale_bar,
        scale_bar_um=scale_bar_um,
    )
    return p, payload


def export_volume_payload(d: dict, ctx: Volume3DExportContext) -> dict:
    output_name = str(d.get("output_name", "") or "").strip()
    output_dir_raw = str(d.get("output_dir", "") or "").strip()
    overwrite = ctx.bool_value(d.get("overwrite", False), False)
    p, payload = volume_payload_from_body(d, ctx, for_export=True)
    output_dir = _output_dir_for(p, output_dir_raw)
    safe_name = ctx.sanitize_prefix(output_name or p.stem, p.stem)
    out_path = _unique_output_path(output_dir / f"{safe_name}_3d_viewer.html", overwrite)
    out_path.write_text(ctx.volume3d_html(payload), encoding="utf-8")
    return {
        "ok": True,
        "output_path": str(out_path),
        "n_points": payload.get("render", {}).get("n_points", 0),
        "z_sampled": payload.get("dimensions", {}).get("z_sampled", 0),
        "channels_rendered": payload.get("dimensions", {}).get("channels_rendered", []),
        "calibration": payload.get("calibration", {}),
        "outputs": [{"path": str(out_path), "type": "html", "role": "fluorescence_3d_viewer"}],
    }


def rotation_gif_payload(
    d: dict,
    ctx: Volume3DExportContext,
    *,
    preview: bool = False,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    p, payload = volume_payload_from_body(d, ctx, for_export=not preview)
    axis_raw = str(d.get("rotation_axis", "z") or "z").strip()
    axis_vector, axis_label = rotation_axis_vector(axis_raw)
    direction = str(d.get("rotation_direction", "forward") or "forward").strip().lower()
    frame_default = 24 if preview else 48
    size_default = 420 if preview else 640
    max_points_default = 18000 if preview else 45000
    frame_count = _num(d.get("gif_frames"), frame_default)
    fps = _num(d.get("gif_fps"), 12.0)
    image_size = _num(d.get("gif_size"), size_default)
    max_gif_points = _num(d.get("gif_points"), max_points_default)
    scale_bar_um = max(0.0, _num(d.get("scale_bar_um"), 20.0))
    show_scale_bar = ctx.bool_value(d.get("show_scale_bar", True), True) and scale_bar_um > 0
    gif_bytes = rotation_gif_bytes(
        payload,
        axis_vector=axis_vector,
        direction=direction,
        frame_count=frame_count,
        fps=fps,
        image_size=image_size,
        max_gif_points=max_gif_points,
        show_scale_bar=show_scale_bar,
        scale_bar_um=scale_bar_um,
        cancel_check=cancel_check,
    )
    result = {
        "ok": True,
        "gif_b64": base64.b64encode(gif_bytes).decode() if preview else "",
        "n_points": min(int(payload.get("render", {}).get("n_points", 0)), max_gif_points),
        "frames": max(8, min(120, frame_count)),
        "fps": max(1.0, min(30.0, fps)),
        "axis": axis_label,
        "axis_raw": axis_raw,
        "scale_bar_um": scale_bar_um if show_scale_bar else 0,
    }
    if preview:
        return result

    output_dir_raw = str(d.get("output_dir", "") or "").strip()
    output_dir = _output_dir_for(p, output_dir_raw)
    output_name = str(d.get("output_name", "") or "").strip()
    overwrite = ctx.bool_value(d.get("overwrite", False), False)
    safe_name = ctx.sanitize_prefix(output_name or p.stem, p.stem)
    out_path = _unique_output_path(output_dir / f"{safe_name}_3d_rotation.gif", overwrite)
    out_path.write_bytes(gif_bytes)
    result.update(
        {
            "output_path": str(out_path),
            "outputs": [
                {"path": str(out_path), "type": "gif", "role": "fluorescence_3d_rotation_gif"}
            ],
        }
    )
    return result


def rotation_gif_job_payload(d: dict, ctx: Volume3DExportContext, job_ctx) -> dict:
    """Build an export rotation GIF inside a cancellable background job."""
    job_ctx.set_progress(0.1, "Building 3D rotation GIF")
    result = rotation_gif_payload(d, ctx, preview=False, cancel_check=job_ctx.check_cancelled)
    job_ctx.set_progress(1.0, "3D rotation GIF exported")
    return result


def distribution_payload(d: dict, ctx: Volume3DExportContext) -> dict:
    path = str(d.get("path", "") or "").strip()
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Input TIFF not found: {path}")
    info = ctx.tiff_series_info(p)
    dims = info.get("dimensions", {}) or {}
    z_count = max(1, int(dims.get("z", 1) or 1))
    c_count = max(1, int(dims.get("c", 1) or 1))
    x_count = max(1, int(dims.get("x", 1) or 1))
    y_count = max(1, int(dims.get("y", 1) or 1))
    c = max(0, min(_num(d.get("distribution_channel", d.get("c")), 0), c_count - 1))
    t = _num(d.get("t"), 0)
    extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
    axis = str(d.get("distribution_axis", "z") or "z").strip().lower()
    if axis not in {"x", "y", "z"}:
        axis = "z"
    metric = str(d.get("distribution_metric", "mean") or "mean").strip().lower()
    if metric not in {"mean", "sum", "max"}:
        metric = "mean"
    denoise_mode = ctx.clean_choice(d.get("denoise"), ctx.denoise_options, "Off")
    arr, axes, roles = ctx.tiff_read_array(p)

    if axis == "x":
        values = np.full(x_count, -np.inf if metric == "max" else 0.0, dtype=np.float64)
        counts = np.zeros(x_count, dtype=np.float64)
    elif axis == "y":
        values = np.full(y_count, -np.inf if metric == "max" else 0.0, dtype=np.float64)
        counts = np.zeros(y_count, dtype=np.float64)
    else:
        values = np.zeros(z_count, dtype=np.float64)
        counts = np.ones(z_count, dtype=np.float64)

    for z in range(z_count):
        plane = ctx.tiff_plane_from_array(
            arr, axes, roles, z=z, c=c, t=t, extra_indices=extra_indices
        )
        data = np.asarray(plane, dtype=np.float32)
        data = ctx.apply_optional_denoise(data, denoise_mode)
        if axis == "z":
            if metric == "sum":
                values[z] = float(np.nansum(data))
            elif metric == "max":
                values[z] = float(np.nanmax(data))
            else:
                values[z] = float(np.nanmean(data))
        elif axis == "x":
            if metric == "max":
                values = np.maximum(values, np.nanmax(data, axis=0))
            else:
                values += np.nansum(data, axis=0)
                counts += np.sum(np.isfinite(data), axis=0)
        else:
            if metric == "max":
                values = np.maximum(values, np.nanmax(data, axis=1))
            else:
                values += np.nansum(data, axis=1)
                counts += np.sum(np.isfinite(data), axis=1)

    if metric == "mean" and axis in {"x", "y"}:
        values = np.divide(values, counts, out=np.zeros_like(values), where=counts > 0)
    values = np.where(np.isfinite(values), values, 0.0)

    cal = info.get("calibration", {}) or {}
    pixel_w = float(cal.get("pixel_width_um", 1.0) or 1.0)
    pixel_h = float(cal.get("pixel_height_um", pixel_w) or pixel_w)
    z_um = float(cal.get("z_spacing_um", pixel_w) or pixel_w)
    spacing = {"x": pixel_w, "y": pixel_h, "z": z_um}[axis]
    coords = np.arange(values.size, dtype=np.float64) * spacing
    rows = [
        {
            "axis": axis,
            "index": int(i),
            "coordinate_um": round(float(coord), 6),
            "intensity": round(float(value), 6),
        }
        for i, (coord, value) in enumerate(zip(coords, values, strict=True))
    ]

    fig, ax = new_subplots(figsize=(6.0, 3.2), dpi=140)
    ax.plot(coords, values, color="#3E6AE1", linewidth=1.6)
    ax.set_xlabel(f"{axis.upper()} position (um)")
    ax.set_ylabel(f"{metric.title()} intensity")
    ax.grid(True, alpha=0.22)
    ax.set_title(f"C{c + 1} {axis.upper()} intensity distribution", fontsize=10)
    fig.tight_layout()
    img = ctx.fig_to_b64(fig)

    output_dir_raw = str(d.get("output_dir", "") or "").strip()
    output_name = str(d.get("output_name", "") or "").strip()
    overwrite = ctx.bool_value(d.get("overwrite", False), False)
    output_dir = _output_dir_for(p, output_dir_raw)
    safe_name = ctx.sanitize_prefix(output_name or p.stem, p.stem)
    csv_path = _unique_output_path(
        output_dir / f"{safe_name}_C{c + 1}_{axis}_{metric}_distribution.csv", overwrite
    )
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["axis", "index", "coordinate_um", "intensity"])
        writer.writeheader()
        writer.writerows(rows)
    return {
        "ok": True,
        "plot": img,
        "rows": rows,
        "csv_path": str(csv_path),
        "axis": axis,
        "metric": metric,
        "channel": c,
        "outputs": [
            {"path": str(csv_path), "type": "csv", "role": "fluorescence_3d_intensity_distribution"}
        ],
    }


def rotation_axis_vector(axis: object) -> tuple[np.ndarray, str]:
    raw = str(axis or "z").strip().lower()
    text = raw.replace(" ", "").replace("*", "")
    if text in {"x", "+x"}:
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float32), "x"
    if text in {"y", "+y"}:
        return np.asarray([0.0, 1.0, 0.0], dtype=np.float32), "y"
    if text in {"z", "+z"}:
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float32), "z"
    if "," in text:
        try:
            parts = [float(part) for part in text.split(",")]
        except ValueError as exc:
            raise ValueError(f"Invalid rotation axis: {raw}") from exc
        if len(parts) != 3:
            raise ValueError(f"Invalid rotation axis: {raw}")
        vec = np.asarray(parts, dtype=np.float32)
    else:
        vec = np.zeros(3, dtype=np.float32)
        consumed = [False] * len(text)
        for match in re.finditer(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)?)([xyz])", text):
            coeff_raw, axis_name = match.groups()
            if coeff_raw in {"", "+"}:
                coeff = 1.0
            elif coeff_raw == "-":
                coeff = -1.0
            else:
                coeff = float(coeff_raw)
            vec[{"x": 0, "y": 1, "z": 2}[axis_name]] += coeff
            for i in range(match.start(), match.end()):
                consumed[i] = True
        if any(not ok for ok in consumed):
            raise ValueError(f"Invalid rotation axis: {raw}")
    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise ValueError(f"Invalid rotation axis: {raw}")
    vec = vec / norm
    label_parts = []
    for coeff, axis_name in zip(vec, ("x", "y", "z"), strict=True):
        if abs(float(coeff)) > 1e-4:
            label_parts.append(f"{float(coeff):.3g}{axis_name}")
    return vec.astype(np.float32), "+".join(label_parts).replace("+-", "-") or "z"


def rotation_matrix(axis: str | np.ndarray, angle: float) -> np.ndarray:
    if isinstance(axis, str):
        axis_vec, _label = rotation_axis_vector(axis)
    else:
        axis_vec = np.asarray(axis, dtype=np.float32)
        norm = float(np.linalg.norm(axis_vec))
        axis_vec = axis_vec / max(norm, 1e-8)
    c = math.cos(angle)
    s = math.sin(angle)
    x, y, z = [float(v) for v in axis_vec]
    one_c = 1.0 - c
    return np.asarray(
        [
            [c + x * x * one_c, x * y * one_c - z * s, x * z * one_c + y * s],
            [y * x * one_c + z * s, c + y * y * one_c, y * z * one_c - x * s],
            [z * x * one_c - y * s, z * y * one_c + x * s, c + z * z * one_c],
        ],
        dtype=np.float32,
    )


def rotation_gif_bytes(
    volume_payload: dict,
    *,
    axis_vector: np.ndarray,
    direction: str,
    frame_count: int,
    fps: float,
    image_size: int,
    max_gif_points: int,
    show_scale_bar: bool,
    scale_bar_um: float,
    cancel_check: Callable[[], None] | None = None,
) -> bytes:
    from PIL import Image, ImageDraw

    render = volume_payload.get("render", {}) or {}
    positions = np.asarray(render.get("positions") or [], dtype=np.float32).reshape(-1, 3)
    colors = np.asarray(render.get("colors") or [], dtype=np.float32).reshape(-1, 3)
    sign = -1.0 if direction in {"reverse", "ccw", "counterclockwise"} else 1.0
    frame_count = max(8, min(120, int(frame_count or 48)))
    fps = max(1.0, min(30.0, float(fps or 12.0)))
    image_size = max(280, min(1100, int(image_size or 640)))
    max_gif_points = max(1000, min(90000, int(max_gif_points or 40000)))
    if positions.size == 0 or colors.size == 0:
        raise ValueError("No 3D points available for rotation GIF.")
    if positions.shape[0] > max_gif_points:
        idx = np.linspace(0, positions.shape[0] - 1, max_gif_points, dtype=np.int64)
        positions = positions[idx]
        colors = colors[idx]

    extent = float(np.max(np.ptp(positions, axis=0))) if positions.size else 1.0
    extent = max(extent, 1.0)
    scale = image_size * 0.76 / extent
    point_radius = max(1, int(round(image_size / 520)))
    tilt = rotation_matrix("x", -0.55) @ rotation_matrix("y", 0.22)
    rgb = np.clip(colors * 255.0, 0, 255).astype(np.uint8)
    frames = []
    for i in range(frame_count):
        if cancel_check is not None:
            cancel_check()
        angle = sign * (2.0 * math.pi * i / frame_count)
        rotated = positions @ (rotation_matrix(axis_vector, angle).T)
        view = rotated @ tilt.T
        xs = (view[:, 0] * scale + image_size / 2.0).astype(np.int32)
        ys = (image_size / 2.0 - view[:, 1] * scale).astype(np.int32)
        depth_order = np.argsort(view[:, 2])
        img = Image.new("RGB", (image_size, image_size), (8, 9, 12))
        draw = ImageDraw.Draw(img, "RGBA")
        for idx in depth_order:
            x = int(xs[idx])
            y = int(ys[idx])
            if (
                x < -point_radius
                or y < -point_radius
                or x > image_size + point_radius
                or y > image_size + point_radius
            ):
                continue
            color = tuple(int(v) for v in rgb[idx])
            alpha = 175 + int(70 * max(0.0, min(1.0, float(np.mean(colors[idx])))))
            if point_radius <= 1:
                draw.point((x, y), fill=(*color, alpha))
            else:
                draw.ellipse(
                    (x - point_radius, y - point_radius, x + point_radius, y + point_radius),
                    fill=(*color, alpha),
                )
        if show_scale_bar:
            _draw_gif_scale_bar(draw, image_size, scale, scale_bar_um)
        frames.append(img)

    buf = io.BytesIO()
    duration = int(round(1000.0 / fps))
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=False,
    )
    return buf.getvalue()


def _output_dir_for(p: Path, output_dir_raw: str) -> Path:
    output_dir = (
        Path(output_dir_raw).expanduser() if output_dir_raw else p.parent / f"{p.stem}_3d_exports"
    )
    if not output_dir.is_absolute():
        output_dir = p.parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _unique_output_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    n = 2
    while path.exists():
        path = path.with_name(f"{stem}_{n}{suffix}")
        n += 1
    return path


def _draw_gif_scale_bar(draw, image_size: int, scale_px_per_um: float, scale_bar_um: float) -> None:
    if scale_bar_um <= 0 or scale_px_per_um <= 0:
        return
    from PIL import ImageFont

    bar_px = int(round(scale_bar_um * scale_px_per_um))
    if bar_px < 6:
        return
    max_bar = int(image_size * 0.42)
    if bar_px > max_bar:
        bar_px = max_bar
        scale_bar_um = bar_px / scale_px_per_um
    margin = max(18, int(image_size * 0.055))
    x0 = margin
    y0 = image_size - margin
    thickness = max(3, int(image_size * 0.008))
    label = f"{scale_bar_um:g} um"
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    text_h = bbox[3] - bbox[1]
    draw.line((x0, y0, x0 + bar_px, y0), fill=(0, 0, 0), width=thickness + 2)
    draw.line((x0, y0, x0 + bar_px, y0), fill=(255, 255, 255), width=thickness)
    draw.text(
        (x0, y0 - text_h - max(5, thickness)),
        label,
        fill=(255, 255, 255),
        font=font,
        stroke_width=2,
        stroke_fill=(0, 0, 0),
    )
