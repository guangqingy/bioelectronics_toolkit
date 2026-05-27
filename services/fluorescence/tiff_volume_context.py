from __future__ import annotations

import html
import json
import re as _re2
from pathlib import Path

import numpy as np

from services.fluorescence import route_helpers as fl_helpers
from services.fluorescence import stack as fl_stack
from services.fluorescence import tiff_metadata_context as fl_tiff_metadata_context


def build_tiff_volume_context(
    *, tifflib, has_tiff: bool, int_or, float_or, denoise_options: list[str]
) -> dict:
    _FL_DENOISE_OPTIONS = list(denoise_options or [])
    _fl_clean_choice = fl_stack.clean_choice
    _fl_apply_optional_denoise = fl_stack.apply_optional_denoise
    _fl_bool = fl_helpers.parse_bool
    _fl_unit_to_um_scale = fl_helpers.unit_to_um_scale

    _fl_tiff_metadata = fl_tiff_metadata_context.build_tiff_metadata_context(
        tifflib=tifflib,
        has_tiff=has_tiff,
    )
    _fl_positive_float = _fl_tiff_metadata["_fl_positive_float"]
    _fl_tiff_plane_from_array = _fl_tiff_metadata["_fl_tiff_plane_from_array"]
    _fl_tiff_read_array = _fl_tiff_metadata["_fl_tiff_read_array"]
    _fl_tiff_series_info = _fl_tiff_metadata["_fl_tiff_series_info"]
    _fl_resolve_gif_scale = _fl_tiff_metadata["_fl_resolve_gif_scale"]

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

    def _fl_hex_color_to_rgb(
        color: object, fallback: str = "#f2f2f2"
    ) -> tuple[float, float, float]:
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
        z_position: float | None = None,
        brightness_scale: float = 1.0,
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
        range_high_percentile = max(
            range_low_percentile + 0.1, min(100.0, float(range_high_percentile))
        )
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
        z_coord = float(z_index if z_position is None else z_position)
        brightness_scale = max(0.0, min(1.0, float(brightness_scale or 1.0)))
        for y_s, x_s in zip(ys, xs, strict=True):
            brightness = float(norm[y_s, x_s]) * brightness_scale
            if brightness <= 0:
                continue
            x_px = int(x_s) * xy_step
            y_px = int(y_s) * xy_step
            positions.extend(
                [
                    round(x_px * pixel_w - cx, 4),
                    round(cy - y_px * pixel_h, 4),
                    round(z_coord * z_spacing - cz + channel_offset, 4),
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

    def _fl_clean_volume_level(level: object, default: str = "middle") -> str:
        text = str(level or default).strip().lower()
        aliases = {
            "fast": "low",
            "balanced": "middle",
            "medium": "middle",
            "mid": "middle",
            "med": "middle",
            "normal": "middle",
            "hi": "high",
        }
        text = aliases.get(text, text)
        if text not in {"low", "middle", "high"}:
            return default
        return text

    def _fl_interlayer_settings(level: object) -> dict:
        clean = _fl_clean_volume_level(level, "middle")
        table = {
            "low": {"steps": 1, "brightness": 0.55},
            "middle": {"steps": 2, "brightness": 0.68},
            "high": {"steps": 3, "brightness": 0.78},
        }
        return {"level": clean, **table[clean]}

    def _fl_density_filter_settings(
        density_mode: object,
        density_radius_um: object,
        density_min_neighbors: object,
        calibration: dict,
        xy_step: int,
    ) -> dict:
        mode = str(density_mode or "off").strip().lower()
        aliases = {
            "none": "off",
            "false": "off",
            "0": "off",
            "medium": "middle",
            "mid": "middle",
            "med": "middle",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"off", "low", "middle", "high", "custom"}:
            mode = "off"

        pixel_w = _fl_positive_float(calibration.get("pixel_width_um")) or 1.0
        pixel_h = _fl_positive_float(calibration.get("pixel_height_um")) or pixel_w
        base_um = max(pixel_w, pixel_h) * max(1, int(xy_step or 1))
        preset = {
            "off": {"radius": 0.0, "neighbors": 0},
            "low": {"radius": base_um * 1.8, "neighbors": 2},
            "middle": {"radius": base_um * 2.8, "neighbors": 3},
            "high": {"radius": base_um * 4.0, "neighbors": 5},
            "custom": {"radius": base_um * 2.8, "neighbors": 3},
        }[mode]

        radius = float_or(density_radius_um, preset["radius"])
        neighbors = int_or(density_min_neighbors, preset["neighbors"])
        radius = max(0.0, min(500.0, float(radius or 0.0)))
        neighbors = max(0, min(100, int(neighbors or 0)))
        if mode == "off" or radius <= 0 or neighbors <= 1:
            return {"mode": "off", "radius_um": 0.0, "min_neighbors": 0}
        return {"mode": mode, "radius_um": radius, "min_neighbors": neighbors}

    def _fl_density_filter_points(
        positions: list[float],
        colors: list[float],
        radius_um: float,
        min_neighbors: int,
    ) -> tuple[list[float], list[float], int]:
        n_points = len(positions) // 3
        if n_points <= 0 or radius_um <= 0 or min_neighbors <= 1:
            return positions, colors, 0

        pos = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
        col = np.asarray(colors, dtype=np.float32).reshape(-1, 3)
        finite = np.all(np.isfinite(pos), axis=1)
        if not np.all(finite):
            pos = pos[finite]
            col = col[finite]
        if pos.shape[0] == 0:
            return [], [], n_points

        cell_size = max(float(radius_um), 1e-6)
        origin = np.min(pos, axis=0)
        cells = np.floor((pos - origin) / cell_size).astype(np.int32)
        unique_cells, inverse, counts = np.unique(
            cells, axis=0, return_inverse=True, return_counts=True
        )
        count_map = {
            tuple(int(v) for v in cell): int(count)
            for cell, count in zip(unique_cells, counts, strict=True)
        }
        offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
        density_by_cell = np.zeros(unique_cells.shape[0], dtype=np.int32)
        for i, cell in enumerate(unique_cells):
            cx, cy, cz = (int(cell[0]), int(cell[1]), int(cell[2]))
            total = 0
            for dx, dy, dz in offsets:
                total += count_map.get((cx + dx, cy + dy, cz + dz), 0)
            density_by_cell[i] = total

        keep = density_by_cell[inverse] >= int(min_neighbors)
        if not np.any(keep):
            return positions, colors, 0
        removed = int(pos.shape[0] - int(np.sum(keep)))
        if removed <= 0 and np.all(finite):
            return positions, colors, 0
        return (
            np.round(pos[keep].reshape(-1), 4).tolist(),
            np.round(col[keep].reshape(-1), 4).tolist(),
            removed + int(n_points - pos.shape[0]),
        )

    def _fl_channel_render_range(
        channel_ranges: object, channel: int, default_threshold: float, default_color: str
    ) -> dict:
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
        interlayer_level: str = "middle",
        density_mode: str = "off",
        density_radius_um: float | None = None,
        density_min_neighbors: int | None = None,
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
            raise ValueError(
                "This TIFF has only one readable stack plane; 3D stacking needs Z/slices > 1."
            )

        arr, axes, roles = _fl_tiff_read_array(tiff_path)
        max_points = max(1000, min(250000, int(max_points or 70000)))
        max_xy = max(48, min(512, int(max_xy or 180)))
        max_z = max(2, min(200, int(max_z or 80)))
        xy_step = max(1, int(np.ceil(max(x_count, y_count) / float(max_xy))))
        z_indices = _fl_volume_indices(z_count, max_z)
        interlayer = _fl_interlayer_settings(interlayer_level)
        interlayer_steps = int(interlayer["steps"])
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
            default_color = (
                fallback_colors[channel % len(fallback_colors)]
                if channel_mode == "composite"
                else "#f2f2f2"
            )
            chan_range = _fl_channel_render_range(
                channel_ranges, channel, threshold_percentile, default_color
            )
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
        rendered_plane_count = len(z_indices) + max(0, len(z_indices) - 1) * interlayer_steps
        plane_quota = max(
            12, int(np.ceil(max_points / max(1, rendered_plane_count * len(channels))))
        )
        for channel in channels:
            for z_i, z in enumerate(z_indices):
                plane = _fl_tiff_plane_from_array(
                    arr, axes, roles, z=z, c=channel, t=t, extra_indices=extra_indices
                )
                default_color = (
                    fallback_colors[channel % len(fallback_colors)]
                    if channel_mode == "composite"
                    else "#f2f2f2"
                )
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
                if interlayer_steps <= 0 or z_i >= len(z_indices) - 1:
                    continue
                z_next = int(z_indices[z_i + 1])
                if z_next == int(z):
                    continue
                next_plane = _fl_tiff_plane_from_array(
                    arr,
                    axes,
                    roles,
                    z=z_next,
                    c=channel,
                    t=t,
                    extra_indices=extra_indices,
                )
                prev_plane = np.asarray(plane, dtype=np.float32)
                next_plane = np.asarray(next_plane, dtype=np.float32)
                for step in range(1, interlayer_steps + 1):
                    frac = step / float(interlayer_steps + 1)
                    mix = prev_plane * (1.0 - frac) + next_plane * frac
                    z_pos = float(z) + (float(z_next) - float(z)) * frac
                    p, col = _fl_plane_points_3d(
                        arr=mix,
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
                        z_position=z_pos,
                        brightness_scale=float(interlayer["brightness"]),
                    )
                    positions.extend(p)
                    colors.extend(col)

        n_points = len(positions) // 3
        density_filter = _fl_density_filter_settings(
            density_mode,
            density_radius_um,
            density_min_neighbors,
            calibration,
            xy_step,
        )
        density_removed = 0
        if density_filter["mode"] != "off":
            positions, colors, density_removed = _fl_density_filter_points(
                positions,
                colors,
                density_filter["radius_um"],
                density_filter["min_neighbors"],
            )
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
                "pixel_height_um": float(
                    calibration.get("pixel_height_um", calibration.get("pixel_width_um", 1.0))
                ),
                "z_spacing_um": float(
                    calibration.get("z_spacing_um", calibration.get("pixel_width_um", 1.0))
                ),
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
                "interlayer_level": interlayer["level"],
                "interlayer_steps": interlayer_steps,
                "interlayer_brightness": interlayer["brightness"],
                "density_filter": density_filter,
                "density_removed": density_removed,
                "show_scale_bar": bool(show_scale_bar and scale_bar_um > 0),
                "scale_bar_um": scale_bar_um,
                "point_size": max(
                    0.35, min(4.0, float(calibration.get("pixel_width_um", 1.0)) * xy_step * 0.9)
                ),
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
    const density = data.render.density_filter || {{}};
    const densityText = density.mode && density.mode !== 'off' ? ` · Density ${{density.mode}}` : '';
    document.getElementById('meta').textContent = `${{data.render.n_points}} points · Z ${{data.dimensions.z}} · C ${{data.dimensions.c}} · Fill ${{data.render.interlayer_level || 'middle'}}${{densityText}} · ${{data.calibration.pixel_width_um.toFixed(4)}} um/px · Z step ${{data.calibration.z_spacing_um.toFixed(4)}} um`;
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

    return {
        "_fl_tiff_plane_from_array": _fl_tiff_plane_from_array,
        "_fl_tiff_read_array": _fl_tiff_read_array,
        "_fl_tiff_series_info": _fl_tiff_series_info,
        "_fl_tiff_volume3d_payload": _fl_tiff_volume3d_payload,
        "_fl_volume3d_html": _fl_volume3d_html,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
    }
