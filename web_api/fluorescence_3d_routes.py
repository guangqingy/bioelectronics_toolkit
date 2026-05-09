from __future__ import annotations

import traceback
from pathlib import Path

from flask import jsonify, request

from .jobs import submit_flask_route_job


def register_fluorescence_3d_routes(app, fl):
    err = fl["err"]
    int_or = fl["int_or"]
    float_or = fl["float_or"]
    has_tiff = fl["has_tiff"]
    has_pil = fl["has_pil"]
    jobs = fl["jobs"]

    _FL_DENOISE_OPTIONS = fl["_FL_DENOISE_OPTIONS"]
    _fl_bool = fl["_fl_bool"]
    _fl_clean_choice = fl["_fl_clean_choice"]
    _fl_frame_to_b64 = fl["_fl_frame_to_b64"]
    _fl_sanitize_prefix = fl["_fl_sanitize_prefix"]
    _fl_tiff_plane_from_array = fl["_fl_tiff_plane_from_array"]
    _fl_tiff_read_array = fl["_fl_tiff_read_array"]
    _fl_tiff_series_info = fl["_fl_tiff_series_info"]
    _fl_tiff_volume3d_payload = fl["_fl_tiff_volume3d_payload"]
    _fl_volume3d_html = fl["_fl_volume3d_html"]

    @app.route("/api/fluorescence/3d/tiff_info", methods=["POST"])
    def api_fl_3d_tiff_info():
        if not has_tiff:
            return err("tifffile is required")
        path = str((request.json or {}).get("path", "") or "").strip()
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            return jsonify({"ok": True, "info": _fl_tiff_series_info(p)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/tiff_info_batch", methods=["POST"])
    def api_fl_3d_tiff_info_batch():
        if not has_tiff:
            return err("tifffile is required")
        paths = (request.json or {}).get("paths") or []
        info = {}
        for raw in paths:
            p = Path(str(raw or "").strip())
            if not p.exists():
                info[str(raw)] = {"error": "not found"}
                continue
            try:
                info[str(raw)] = _fl_tiff_series_info(p)
            except Exception as exc:
                info[str(raw)] = {"error": str(exc)}
        return jsonify({"ok": True, "info": info})

    @app.route("/api/fluorescence/3d/preview_slice", methods=["POST"])
    def api_fl_3d_preview_slice():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        z = int_or(d.get("z", 0), 0)
        c = int_or(d.get("c", 0), 0)
        t = int_or(d.get("t", 0), 0)
        extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
        lut = str(d.get("lut", "Gray") or "Gray")
        p_low = max(0.0, min(49.0, float_or(d.get("p_low", 1.0), 1.0)))
        p_high = max(51.0, min(100.0, float_or(d.get("p_high", 99.0), 99.0)))
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            arr, axes, roles = _fl_tiff_read_array(p)
            plane = _fl_tiff_plane_from_array(arr, axes, roles, z=z, c=c, t=t, extra_indices=extra_indices)
            b64 = _fl_frame_to_b64(plane, lut, p_low, p_high)
            return jsonify({"ok": True, "img": b64, "z": z, "c": c, "t": t})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/volume", methods=["POST"])
    def api_fl_3d_volume():
        if not has_tiff:
            return err("tifffile is required")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        c = int_or(d.get("c", 0), 0)
        t = int_or(d.get("t", 0), 0)
        extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
        channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
        if channel_mode not in {"composite", "current"}:
            channel_mode = "composite"
        max_points = int_or(d.get("max_points", 70000), 70000)
        max_xy = int_or(d.get("max_xy", 180), 180)
        max_z = int_or(d.get("max_z", 80), 80)
        threshold_percentile = float_or(d.get("threshold_percentile", 98.8), 98.8)
        channel_ranges = d.get("channel_ranges") if isinstance(d.get("channel_ranges"), dict) else {}
        denoise_mode = _fl_clean_choice(d.get("denoise"), _FL_DENOISE_OPTIONS, "Off")
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 20.0), 20.0))
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            payload = _fl_tiff_volume3d_payload(
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
                show_scale_bar=show_scale_bar,
                scale_bar_um=scale_bar_um,
            )
            return jsonify({"ok": True, "volume": payload})
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume", methods=["POST"])
    def api_fl_3d_export_volume():
        if not has_tiff:
            return err("tifffile is required")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        output_name = str(d.get("output_name", "") or "").strip()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        overwrite = _fl_bool(d.get("overwrite", True), True)
        c = int_or(d.get("c", 0), 0)
        t = int_or(d.get("t", 0), 0)
        extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
        channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
        if channel_mode not in {"composite", "current"}:
            channel_mode = "composite"
        max_points = int_or(d.get("max_points", 110000), 110000)
        max_xy = int_or(d.get("max_xy", 220), 220)
        max_z = int_or(d.get("max_z", 120), 120)
        threshold_percentile = float_or(d.get("threshold_percentile", 98.6), 98.6)
        channel_ranges = d.get("channel_ranges") if isinstance(d.get("channel_ranges"), dict) else {}
        denoise_mode = _fl_clean_choice(d.get("denoise"), _FL_DENOISE_OPTIONS, "Off")
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 20.0), 20.0))
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            payload = _fl_tiff_volume3d_payload(
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
                show_scale_bar=show_scale_bar,
                scale_bar_um=scale_bar_um,
            )
            output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else p.parent / f"{p.stem}_3d_exports"
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = _fl_sanitize_prefix(output_name or p.stem, p.stem)
            out_path = output_dir / f"{safe_name}_3d_viewer.html"
            if not overwrite:
                stem = out_path.stem
                suffix = out_path.suffix
                n = 2
                while out_path.exists():
                    out_path = output_dir / f"{stem}_{n}{suffix}"
                    n += 1
            out_path.write_text(_fl_volume3d_html(payload), encoding="utf-8")
            return jsonify(
                {
                    "ok": True,
                    "output_path": str(out_path),
                    "n_points": payload.get("render", {}).get("n_points", 0),
                    "z_sampled": payload.get("dimensions", {}).get("z_sampled", 0),
                    "channels_rendered": payload.get("dimensions", {}).get("channels_rendered", []),
                    "calibration": payload.get("calibration", {}),
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume_job", methods=["POST"])
    def api_fl_3d_export_volume_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/3d/export_volume",
            "fluorescence.export_volume3d",
            "Export fluorescence 3D viewer",
            api_fl_3d_export_volume,
            request.json or {},
        )

