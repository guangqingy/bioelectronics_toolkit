from __future__ import annotations

import html
import json
import math
from typing import Callable

import numpy as np

from services.fluorescence import lif_dimensions, lif_export


def lut_rgb(lut_name: str) -> tuple[float, float, float]:
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


def volume_indices(count: int, max_count: int) -> list[int]:
    count = max(1, int(count or 1))
    max_count = max(1, int(max_count or count))
    if count <= max_count:
        return list(range(count))
    return sorted({int(x) for x in np.linspace(0, count - 1, max_count)})


def plane_points(
    arr: np.ndarray,
    z_index: int,
    c_index: int,
    z_count: int,
    c_count: int,
    xy_step: int,
    per_plane_quota: int,
    threshold_percentile: float,
    calibration: dict,
    lut_rgb_value: tuple[float, float, float],
) -> tuple[list[float], list[float]]:
    data = np.asarray(arr, dtype=np.float32)
    if data.size == 0:
        return [], []

    view = data[::xy_step, ::xy_step]
    if view.size == 0:
        return [], []

    finite = view[np.isfinite(view)]
    if finite.size == 0:
        return [], []

    lo = float(np.percentile(finite, 1.0))
    hi = float(np.percentile(finite, 99.7))
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

    pixel_w = lif_dimensions.positive_float(calibration.get("pixel_width_um")) or 1.0
    pixel_h = lif_dimensions.positive_float(calibration.get("pixel_height_um")) or pixel_w
    z_spacing = lif_dimensions.positive_float(calibration.get("z_spacing_um")) or pixel_w
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
                round(min(1.0, max(0.0, lut_rgb_value[0] * brightness)), 4),
                round(min(1.0, max(0.0, lut_rgb_value[1] * brightness)), 4),
                round(min(1.0, max(0.0, lut_rgb_value[2] * brightness)), 4),
            ]
        )
    return positions, colors


def build_volume3d_payload(
    lif,
    record: dict,
    *,
    get_plane_by_dimensions: Callable,
    display_array: Callable,
    requested_dims: dict | None = None,
    t: int = 0,
    m: int = 0,
    c: int = 0,
    channel_mode: str = "composite",
    max_points: int = 70000,
    max_xy: int = 180,
    max_z: int = 80,
    threshold_percentile: float = 98.8,
) -> dict:
    image_index = int(record.get("index", 0))
    image = lif.get_image(image_index)
    plan = lif_export.export_plan(record)
    counts = plan.get("counts", {}) or {}
    z_count = max(1, int(counts.get("z", 1) or 1))
    c_count = max(1, int(counts.get("c", record.get("channels", 1)) or 1))
    dims = record.get("dimensions", {}) or {}
    x_count = max(1, int(dims.get("x", counts.get("x", 1)) or 1))
    y_count = max(1, int(dims.get("y", counts.get("y", 1)) or 1))

    if z_count < 2:
        raise ValueError("This subfile has only one Z slice; 3D z-stack viewing needs Z > 1.")

    max_points = max(1000, min(250000, int(max_points or 70000)))
    max_xy = max(48, min(512, int(max_xy or 180)))
    max_z = max(2, min(200, int(max_z or 80)))
    xy_step = max(1, int(math.ceil(max(x_count, y_count) / float(max_xy))))
    z_indices = volume_indices(z_count, max_z)

    if str(channel_mode or "composite").lower() == "current":
        channels = [max(0, min(int(c), c_count - 1))]
    else:
        channels = list(range(c_count))

    channel_luts = record.get("channel_lut_names", []) or []
    positions: list[float] = []
    colors: list[float] = []
    plane_quota = max(12, int(math.ceil(max_points / max(1, len(z_indices) * len(channels)))))
    z_dim = plan.get("z_dimension") or {}
    z_dim_id = int(z_dim.get("id", 3) or 3) if z_dim else 3
    base_dims = {3: 0, 4: int(t or 0), 10: int(m or 0)}
    for key, value in (requested_dims or {}).items():
        try:
            base_dims[int(key)] = int(value)
        except Exception:
            continue

    calibration = lif_dimensions.oriented_calibration(record.get("calibration", {}) or {}, record.get("scan_orientation", {}) or {})
    fallback_channel_luts = ["Red", "Green", "Blue", "Magenta", "Cyan", "Yellow"]
    for z in z_indices:
        dim_values = dict(base_dims)
        dim_values[z_dim_id] = int(z)
        for channel in channels:
            arr = display_array(get_plane_by_dimensions(image, c=channel, dimension_values=dim_values), record)
            lut_name = channel_luts[channel] if channel < len(channel_luts) else "Gray"
            if channel_mode != "current" and str(lut_name or "").strip().lower() in {"", "gray"} and c_count > 1:
                lut_name = fallback_channel_luts[channel % len(fallback_channel_luts)]
            p, col = plane_points(
                arr=arr,
                z_index=int(z),
                c_index=int(channel),
                z_count=z_count,
                c_count=c_count,
                xy_step=xy_step,
                per_plane_quota=plane_quota,
                threshold_percentile=threshold_percentile,
                calibration=calibration,
                lut_rgb_value=lut_rgb(lut_name),
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
        raise ValueError("No bright voxels were found for 3D rendering. Try lowering the threshold.")

    pixel_w = lif_dimensions.positive_float(calibration.get("pixel_width_um")) or 1.0
    pixel_h = lif_dimensions.positive_float(calibration.get("pixel_height_um")) or pixel_w
    z_spacing = lif_dimensions.positive_float(calibration.get("z_spacing_um")) or pixel_w
    return {
        "title": record.get("full_name") or record.get("name") or f"Image {image_index + 1}",
        "image_index": image_index,
        "source_name": record.get("full_name", ""),
        "dimensions": {
            "x": x_count,
            "y": y_count,
            "z": z_count,
            "c": c_count,
            "z_sampled": len(z_indices),
            "channels_rendered": channels,
        },
        "calibration": {
            "pixel_width_um": pixel_w,
            "pixel_height_um": pixel_h,
            "z_spacing_um": z_spacing,
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
            "point_size": max(0.35, min(4.0, pixel_w * xy_step * 0.9)),
        },
    }


def volume3d_html(volume_payload: dict) -> str:
    payload_json = json.dumps(volume_payload, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(str(volume_payload.get("title", "Leica LIF 3D Viewer") or "Leica LIF 3D Viewer"))
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
  <div id="hint">Mouse drag: rotate | Wheel: zoom | Right drag: pan</div>
</div>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
const data = {payload_json};
const el = document.getElementById('viewer');
document.getElementById('title').textContent = data.title || 'Leica LIF 3D Viewer';
document.getElementById('meta').textContent = `${{data.render.n_points}} points | Z ${{data.dimensions.z}} | C ${{data.dimensions.c}} | ${{data.calibration.pixel_width_um.toFixed(4)}} um/px | Z step ${{data.calibration.z_spacing_um.toFixed(4)}} um`;
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
const points = new THREE.Points(geom, mat);
scene.add(points);
const sphere = geom.boundingSphere || new THREE.Sphere(new THREE.Vector3(), 100);
const axes = new THREE.AxesHelper(Math.max(20, sphere.radius * 0.65));
scene.add(axes);
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
