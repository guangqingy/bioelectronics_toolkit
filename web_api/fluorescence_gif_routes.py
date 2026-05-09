from __future__ import annotations

import base64
import io
import re as _re2
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import jsonify, request

from .jobs import submit_flask_route_job


def register_fluorescence_gif_routes(app, fl):
    # Transitional adapter: route-only GIF helpers still live in fluorescence.py
    # while core TIFF/frame/GIF primitives are shared through services.
    err = fl["err"]
    fig_to_b64 = fl["fig_to_b64"]
    float_or = fl["float_or"]
    int_or = fl["int_or"]
    has_pil = fl["has_pil"]
    has_tiff = fl["has_tiff"]
    jobs = fl["jobs"]

    _fl_apply_gif_crop = fl["_fl_apply_gif_crop"]
    _fl_bool = fl["_fl_bool"]
    _fl_decode_base64_payload = fl["_fl_decode_base64_payload"]
    _fl_gif_kymo_stat = fl["_fl_gif_kymo_stat"]
    _fl_gif_kymo_top_mean = fl["_fl_gif_kymo_top_mean"]
    _fl_gif_roi_apply_value = fl["_fl_gif_roi_apply_value"]
    _fl_gif_roi_background_mean = fl["_fl_gif_roi_background_mean"]
    _fl_gif_roi_make_specs = fl["_fl_gif_roi_make_specs"]
    _fl_gif_roi_mask_for = fl["_fl_gif_roi_mask_for"]
    _fl_gif_roi_metrics_2d = fl["_fl_gif_roi_metrics_2d"]
    _fl_image_to_b64 = fl["_fl_image_to_b64"]
    _fl_normalize_gif_polygons = fl["_fl_normalize_gif_polygons"]
    _fl_normalize_gif_rects = fl["_fl_normalize_gif_rects"]
    _fl_parse_percent_list = fl["_fl_parse_percent_list"]
    _fl_parse_slice_spec = fl["_fl_parse_slice_spec"]
    _fl_percent_label = fl["_fl_percent_label"]
    _fl_read_selected_gif_planes = fl["_fl_read_selected_gif_planes"]
    _fl_render_gif_frame = fl["_fl_render_gif_frame"]
    _fl_render_gif_roi_reference_preview = fl["_fl_render_gif_roi_reference_preview"]
    _fl_resolve_gif_scale = fl["_fl_resolve_gif_scale"]
    _fl_roi_delta_f_over_f0 = fl["_fl_roi_delta_f_over_f0"]
    _fl_sanitize_prefix = fl["_fl_sanitize_prefix"]
    _fl_smooth_heatmap_2d = fl["_fl_smooth_heatmap_2d"]
    _fl_smooth_series_nan = fl["_fl_smooth_series_nan"]
    _fl_tiff_gif_frame_count = fl["_fl_tiff_gif_frame_count"]

    @app.route("/api/fluorescence/gif_preview", methods=["POST"])
    def api_fl_gif_preview():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        input_path_str = str(d.get("input_path", "") or "").strip()
        if not input_path_str:
            return err("input_path is required")
        fps = max(0.1, float_or(d.get("fps", 5.0), 5.0))
        lut = d.get("lut", "Gray")
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 10.0), 10.0))
        manual_px_per_um = max(0.01, float_or(d.get("px_per_um", 3.45), 3.45))
        auto_scale = _fl_bool(d.get("auto_scale"), True)
        label_mode = str(d.get("label_mode", "time") or "time").strip().lower()
        add_timestamp = _fl_bool(d.get("add_timestamp", True), True) and label_mode not in {"none", "off", "no"}
        slice_spec = d.get("slice_spec", "")
        roi_polygons = _fl_normalize_gif_polygons(d.get("roi_polygons"))
        crop_rects = _fl_normalize_gif_rects(d.get("crop_rects"))
        crop_mode = str(d.get("crop_mode", "full") or "full").strip().lower()
        crop_roi_label = str(d.get("crop_roi_label", "") or "").strip()
        crop_rect_label = str(d.get("crop_rect_label", "") or "").strip()
        crop_padding_px = max(0, int_or(d.get("crop_padding_px", 0), 0))
        show_roi_overlay = _fl_bool(d.get("show_roi_overlay"), crop_mode in {"", "none", "full", "full_frame", "frame"})
        try:
            p_in = Path(input_path_str)
            if not p_in.exists():
                return err(f"Input file not found: {input_path_str}")

            n_available, _shape = _fl_tiff_gif_frame_count(p_in)
            selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
            first_idx = selected_indices[0]
            plane = _fl_read_selected_gif_planes(p_in, [first_idx])[0]
            plane, draw_polygons, crop_info = _fl_apply_gif_crop(
                plane,
                roi_polygons,
                crop_rects,
                crop_mode,
                crop_roi_label,
                crop_rect_label,
                crop_padding_px,
            )
            if not show_roi_overlay:
                draw_polygons = []
            scale_info = _fl_resolve_gif_scale(p_in, auto_scale, manual_px_per_um)
            img = _fl_render_gif_frame(
                plane,
                lut=lut,
                frame_idx=0,
                fps=fps,
                scale_bar_um=scale_bar_um,
                pixels_per_um=scale_info["pixels_per_um"],
                add_timestamp=add_timestamp,
                roi_polygons=draw_polygons,
                label_mode=label_mode,
            )
            img_h, img_w = np.asarray(plane).shape[-2], np.asarray(plane).shape[-1]
            return jsonify(
                {
                    "ok": True,
                    "img": _fl_image_to_b64(img, "PNG"),
                    "frame": first_idx + 1,
                    "n_frames": n_available,
                    "selected_slices": len(selected_indices),
                    "width": int(img_w),
                    "height": int(img_h),
                    "roi_polygons": len(roi_polygons),
                    "show_roi_overlay": bool(show_roi_overlay),
                    "crop": crop_info,
                    "pixel_size_um": scale_info["pixel_size_um"],
                    "pixels_per_um": scale_info["pixels_per_um"],
                    "scale_source": scale_info["source"],
                    "metadata_path": scale_info["metadata_path"],
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/export_preview", methods=["POST"])
    def api_fl_gif_roi_export_preview():
        """Save one GIF preview frame with polygon ROI overlays and a labeled scale bar."""
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")

        d = request.json or {}
        input_path_str = str(d.get("input_path", "") or "").strip()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "gif_roi_reference")
        slice_spec = d.get("slice_spec", "")
        lut = d.get("lut", "Gray")
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 10.0), 10.0))
        manual_px_per_um = max(0.01, float_or(d.get("px_per_um", 3.45), 3.45))
        auto_scale = _fl_bool(d.get("auto_scale"), True)
        show_name = _fl_bool(d.get("show_name", True), True)
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        roi_polygons = _fl_normalize_gif_polygons(d.get("roi_polygons"))
        crop_rects = _fl_normalize_gif_rects(d.get("crop_rects"))
        crop_mode = str(d.get("crop_mode", "full") or "full").strip().lower()
        crop_roi_label = str(d.get("crop_roi_label", "") or "").strip()
        crop_rect_label = str(d.get("crop_rect_label", "") or "").strip()
        crop_padding_px = max(0, int_or(d.get("crop_padding_px", 0), 0))

        if not input_path_str:
            return err("input_path is required")
        if not roi_polygons:
            return err("Draw and close at least one polygon ROI first")

        try:
            p_in = Path(input_path_str)
            if not p_in.exists():
                return err(f"Input file not found: {input_path_str}")

            n_available, _shape = _fl_tiff_gif_frame_count(p_in)
            selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
            first_idx = selected_indices[0]
            plane = _fl_read_selected_gif_planes(p_in, [first_idx])[0]
            plane, draw_polygons, crop_info = _fl_apply_gif_crop(
                plane,
                roi_polygons,
                crop_rects,
                crop_mode,
                crop_roi_label,
                crop_rect_label,
                crop_padding_px,
            )
            scale_info = _fl_resolve_gif_scale(p_in, auto_scale, manual_px_per_um)
            frame_label = str(d.get("frame_label", "") or "").strip()
            if not frame_label:
                frame_label = f"{p_in.name} | slice {first_idx + 1}"

            img_b64 = _fl_render_gif_roi_reference_preview(
                plane=plane,
                lut=lut,
                frame_label=frame_label,
                roi_polygons=draw_polygons,
                show_name=show_name,
                show_scale_bar=show_scale_bar,
                scale_bar_um=scale_bar_um,
                pixels_per_um=scale_info["pixels_per_um"],
            )

            if output_dir_raw:
                out_dir = Path(output_dir_raw).expanduser()
                if not out_dir.is_absolute():
                    out_dir = p_in.parent / out_dir
            else:
                out_dir = p_in.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{prefix}.png"
            out_path.write_bytes(_fl_decode_base64_payload(img_b64))

            return jsonify(
                {
                    "ok": True,
                    "img": img_b64,
                    "output_path": str(out_path),
                    "output_dir": str(out_dir),
                    "frame": first_idx + 1,
                    "n_frames": n_available,
                    "selected_slices": len(selected_indices),
                    "roi_polygons": len(roi_polygons),
                    "crop": crop_info,
                    "scale_bar_um": scale_bar_um,
                    "pixel_size_um": scale_info["pixel_size_um"],
                    "pixels_per_um": scale_info["pixels_per_um"],
                    "scale_source": scale_info["source"],
                    "metadata_path": scale_info["metadata_path"],
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/export_preview_job", methods=["POST"])
    def api_fl_gif_roi_export_preview_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/gif_roi/export_preview",
            "fluorescence.gif_roi_export_preview",
            "Export GIF ROI preview",
            api_fl_gif_roi_export_preview,
            request.json or {},
        )

    @app.route("/api/fluorescence/make_gif", methods=["POST"])
    def api_fl_make_gif():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        input_path_str = d.get("input_path", "")
        output_path_str = d.get("output_path", "")
        fps = max(0.1, float_or(d.get("fps", 5.0), 5.0))
        lut = d.get("lut", "Gray")
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 10.0), 10.0))
        manual_px_per_um = max(0.01, float_or(d.get("px_per_um", 3.45), 3.45))
        auto_scale = _fl_bool(d.get("auto_scale"), True)
        label_mode = str(d.get("label_mode", "time") or "time").strip().lower()
        add_timestamp = _fl_bool(d.get("add_timestamp", True), True) and label_mode not in {"none", "off", "no"}
        slice_spec = d.get("slice_spec", "")
        roi_polygons = _fl_normalize_gif_polygons(d.get("roi_polygons"))
        crop_rects = _fl_normalize_gif_rects(d.get("crop_rects"))
        crop_mode = str(d.get("crop_mode", "full") or "full").strip().lower()
        crop_roi_label = str(d.get("crop_roi_label", "") or "").strip()
        crop_rect_label = str(d.get("crop_rect_label", "") or "").strip()
        crop_padding_px = max(0, int_or(d.get("crop_padding_px", 0), 0))
        show_roi_overlay = _fl_bool(d.get("show_roi_overlay"), crop_mode in {"", "none", "full", "full_frame", "frame"})
        try:
            if not str(input_path_str or "").strip():
                return err("input_path is required")
            p_in = Path(input_path_str)
            if not p_in.exists():
                return err(f"Input file not found: {input_path_str}")
            if not output_path_str:
                suffix = "_slices.gif" if str(slice_spec or "").strip() else ".gif"
                output_path_str = str(p_in.with_name(f"{p_in.stem}{suffix}"))
            elif not Path(output_path_str).is_absolute():
                output_path_str = str(p_in.parent / output_path_str)

            n_available, _shape = _fl_tiff_gif_frame_count(p_in)
            selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
            stack = _fl_read_selected_gif_planes(p_in, selected_indices)
            scale_info = _fl_resolve_gif_scale(p_in, auto_scale, manual_px_per_um)

            frames_pil = []
            crop_info = None
            for i, plane in enumerate(stack):
                plane, draw_polygons, crop_info = _fl_apply_gif_crop(
                    plane,
                    roi_polygons,
                    crop_rects,
                    crop_mode,
                    crop_roi_label,
                    crop_rect_label,
                    crop_padding_px,
                )
                if not show_roi_overlay:
                    draw_polygons = []
                frames_pil.append(
                    _fl_render_gif_frame(
                        plane,
                        lut=lut,
                        frame_idx=i,
                        fps=fps,
                        scale_bar_um=scale_bar_um,
                        pixels_per_um=scale_info["pixels_per_um"],
                        add_timestamp=add_timestamp,
                        roi_polygons=draw_polygons,
                        label_mode=label_mode,
                    )
                )

            if not frames_pil:
                return err("No frames generated")

            duration_ms = int(round(1000.0 / fps))
            Path(output_path_str).parent.mkdir(parents=True, exist_ok=True)
            frames_pil[0].save(
                output_path_str,
                save_all=True,
                append_images=frames_pil[1:],
                duration=duration_ms,
                loop=0,
            )

            preview_b64 = _fl_image_to_b64(frames_pil[0], "PNG")
            gif_b64 = ""
            try:
                out_file = Path(output_path_str)
                if out_file.stat().st_size <= 30 * 1024 * 1024:
                    gif_b64 = base64.b64encode(out_file.read_bytes()).decode()
            except Exception:
                gif_b64 = ""
            return jsonify(
                {
                    "ok": True,
                    "output_path": output_path_str,
                    "n_frames": len(frames_pil),
                    "selected_slices": len(selected_indices),
                    "roi_polygons": len(roi_polygons),
                    "show_roi_overlay": bool(show_roi_overlay),
                    "crop": crop_info or {},
                    "preview": preview_b64,
                    "gif_preview": gif_b64,
                    "pixel_size_um": scale_info["pixel_size_um"],
                    "pixels_per_um": scale_info["pixels_per_um"],
                    "scale_source": scale_info["source"],
                    "metadata_path": scale_info["metadata_path"],
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/make_gif_job", methods=["POST"])
    def api_fl_make_gif_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/make_gif",
            "fluorescence.make_gif",
            "Generate single-file fluorescence GIF",
            api_fl_make_gif,
            request.json or {},
        )

    @app.route("/api/fluorescence/merge_gif", methods=["POST"])
    def api_fl_merge_gif():
        """Merge one or more TIFF stacks into a single animated GIF.

        Request body:
            tiff_paths  : list[str]   — ordered list of TIFF file paths
            slice_specs : list[str]   — one-based slice ranges per TIFF, e.g. "1-20,25,30-40:2"
            fps         : float       — frames per second (default 5)
            lut         : str         — colour LUT name (default "Gray")
            scale_bar_um: float       — scale bar length in µm (0 = off)
            px_per_um   : float       — pixels per µm (default 3.45)
            auto_scale  : bool        — infer scale from sidecar JSON/TIFF metadata
            label_mode  : str         — frame, time, or none
            show_roi_overlay: bool    — draw polygon ROI overlays when true
            output_path : str         — destination .gif path (optional)
        """
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        tiff_paths = d.get("tiff_paths") or []
        if not tiff_paths:
            return err("tiff_paths must be a non-empty list")
        slice_specs = d.get("slice_specs") or []
        fps = max(0.1, float_or(d.get("fps", 5.0), 5.0))
        lut = d.get("lut", "Gray")
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 10.0), 10.0))
        manual_px_per_um = max(0.01, float_or(d.get("px_per_um", 3.45), 3.45))
        auto_scale = _fl_bool(d.get("auto_scale"), True)
        label_mode = str(d.get("label_mode", "time") or "time").strip().lower()
        add_timestamp = _fl_bool(d.get("add_timestamp", True), True) and label_mode not in {"none", "off", "no"}
        roi_polygons = _fl_normalize_gif_polygons(d.get("roi_polygons"))
        crop_rects = _fl_normalize_gif_rects(d.get("crop_rects"))
        crop_mode = str(d.get("crop_mode", "full") or "full").strip().lower()
        crop_roi_label = str(d.get("crop_roi_label", "") or "").strip()
        crop_rect_label = str(d.get("crop_rect_label", "") or "").strip()
        crop_padding_px = max(0, int_or(d.get("crop_padding_px", 0), 0))
        show_roi_overlay = _fl_bool(d.get("show_roi_overlay"), crop_mode in {"", "none", "full", "full_frame", "frame"})
        output_path_str = (d.get("output_path") or "").strip()
        has_slice_selection = any(str(s or "").strip().lower() not in {"", "all", "*"} for s in slice_specs)

        try:
            # Validate all paths first
            paths = []
            for raw in tiff_paths:
                p = Path(str(raw).strip())
                if not p.exists():
                    return err(f"TIFF not found: {raw}")
                paths.append(p)

            # Derive default output path from first TIFF
            if not output_path_str:
                crop_suffix = "_crop" if crop_mode not in {"", "none", "full", "full_frame", "frame"} else ""
                suffix = f"_slices{crop_suffix}.gif" if has_slice_selection else f"{crop_suffix}.gif"
                output_path_str = str(paths[0].with_name(f"{paths[0].stem}{suffix}"))
            elif not Path(output_path_str).is_absolute():
                output_path_str = str(paths[0].parent / output_path_str)

            frames_pil = []
            global_frame_idx = 0
            selected_total = 0
            scale_sources = []
            first_crop_info = None

            for path_idx, tiff_path in enumerate(paths):
                n_available, _shape = _fl_tiff_gif_frame_count(tiff_path)
                slice_spec = slice_specs[path_idx] if path_idx < len(slice_specs) else ""
                selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
                selected_total += len(selected_indices)
                scale_info = _fl_resolve_gif_scale(tiff_path, auto_scale, manual_px_per_um)
                scale_sources.append(
                    {
                        "path": str(tiff_path),
                        "pixel_size_um": scale_info["pixel_size_um"],
                        "pixels_per_um": scale_info["pixels_per_um"],
                        "source": scale_info["source"],
                        "metadata_path": scale_info["metadata_path"],
                    }
                )

                for plane in _fl_read_selected_gif_planes(tiff_path, selected_indices):
                    plane, draw_polygons, crop_info = _fl_apply_gif_crop(
                        plane,
                        roi_polygons,
                        crop_rects,
                        crop_mode,
                        crop_roi_label,
                        crop_rect_label,
                        crop_padding_px,
                    )
                    if not show_roi_overlay:
                        draw_polygons = []
                    if first_crop_info is None:
                        first_crop_info = crop_info
                    frames_pil.append(
                        _fl_render_gif_frame(
                            plane,
                            lut=lut,
                            frame_idx=global_frame_idx,
                            fps=fps,
                            scale_bar_um=scale_bar_um,
                            pixels_per_um=scale_info["pixels_per_um"],
                            add_timestamp=add_timestamp,
                            roi_polygons=draw_polygons,
                            label_mode=label_mode,
                        )
                    )
                    global_frame_idx += 1

            if not frames_pil:
                return err("No frames generated from the provided TIFFs")

            duration_ms = int(round(1000.0 / fps))
            Path(output_path_str).parent.mkdir(parents=True, exist_ok=True)
            frames_pil[0].save(
                output_path_str,
                save_all=True,
                append_images=frames_pil[1:],
                duration=duration_ms,
                loop=0,
            )

            preview_b64 = _fl_image_to_b64(frames_pil[0], "PNG")
            gif_b64 = ""
            try:
                out_file = Path(output_path_str)
                if out_file.stat().st_size <= 30 * 1024 * 1024:
                    gif_b64 = base64.b64encode(out_file.read_bytes()).decode()
            except Exception:
                gif_b64 = ""
            return jsonify(
                {
                    "ok": True,
                    "output_path": output_path_str,
                    "n_frames": len(frames_pil),
                    "selected_slices": selected_total,
                    "roi_polygons": len(roi_polygons),
                    "show_roi_overlay": bool(show_roi_overlay),
                    "crop": first_crop_info or {},
                    "preview": preview_b64,
                    "gif_preview": gif_b64,
                    "scale_sources": scale_sources,
                    "scale_source": scale_sources[0]["source"] if scale_sources else "",
                    "pixel_size_um": scale_sources[0]["pixel_size_um"] if scale_sources else None,
                    "pixels_per_um": scale_sources[0]["pixels_per_um"] if scale_sources else None,
                    "metadata_path": scale_sources[0]["metadata_path"] if scale_sources else "",
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/merge_gif_job", methods=["POST"])
    def api_fl_merge_gif_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/merge_gif",
            "fluorescence.merge_gif",
            "Generate fluorescence GIF",
            api_fl_merge_gif,
            request.json or {},
        )

    @app.route("/api/fluorescence/gif_roi/analyze", methods=["POST"])
    def api_fl_gif_roi_analyze():
        """Analyze polygon ROI fluorescence across the GIF queue timeline."""
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")

        d = request.json or {}
        tiff_paths = d.get("tiff_paths") or []
        slice_specs = d.get("slice_specs") or []
        roi_specs = _fl_gif_roi_make_specs(d.get("rois", d.get("roi_polygons", [])))
        bg_specs = _fl_gif_roi_make_specs([d.get("bg_roi")], "BG") if isinstance(d.get("bg_roi"), dict) else []
        bg_roi = bg_specs[0] if bg_specs else None

        metric = str(d.get("metric", "mean") or "mean").strip()
        plot_metric = str(d.get("plot_metric", "delta_f_over_f0") or "delta_f_over_f0").strip()
        bg_mode = str(d.get("bg_mode", "none") or "none").strip()
        fps = max(0.1, float_or(d.get("fps", 5.0), 5.0))
        frame_interval_s = float_or(d.get("frame_interval_s"), None)
        if frame_interval_s is None or not np.isfinite(frame_interval_s) or frame_interval_s <= 0:
            frame_interval_s = 1.0 / fps
        ref_frame_raw = int_or(d.get("ref_frame", 1), 1)

        valid_metrics = {"mean", "top20_mean", "sum", "max", "std"}
        valid_plot_metrics = {"absolute", "bg_subtracted", "bg_normalized", "delta_f_over_f0"}
        valid_bg_modes = {"none", "corner_br", "corner_tl", "roi"}
        if metric not in valid_metrics:
            metric = "mean"
        if plot_metric not in valid_plot_metrics:
            plot_metric = "delta_f_over_f0"
        if bg_mode not in valid_bg_modes:
            bg_mode = "none"
        if bg_mode == "roi" and bg_roi is None:
            return err("Select a background ROI or change background mode")

        if not isinstance(tiff_paths, list) or not tiff_paths:
            return err("tiff_paths must be a non-empty list")
        if not roi_specs:
            return err("Draw at least one closed polygon ROI")

        try:
            paths = []
            for raw in tiff_paths:
                p = Path(str(raw or "").strip())
                if not p.exists():
                    return err(f"TIFF not found: {raw}")
                paths.append(p)

            rows = []
            mask_cache: dict = {}
            global_frame = 0
            selected_total = 0
            warnings = []
            first_shape: tuple[int, int] | None = None

            for path_idx, tiff_path in enumerate(paths):
                n_available, _shape = _fl_tiff_gif_frame_count(tiff_path)
                slice_spec = slice_specs[path_idx] if path_idx < len(slice_specs) else ""
                selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
                selected_total += len(selected_indices)
                planes = _fl_read_selected_gif_planes(tiff_path, selected_indices)

                for order_idx, (source_idx, plane) in enumerate(zip(selected_indices, planes)):
                    img2d = np.asarray(plane, dtype=np.float64)
                    if img2d.ndim != 2:
                        img2d = np.squeeze(img2d)
                    if img2d.ndim != 2:
                        continue

                    shape = (int(img2d.shape[0]), int(img2d.shape[1]))
                    if first_shape is None:
                        first_shape = shape
                    elif shape != first_shape and len(warnings) < 4:
                        warnings.append(
                            f"{tiff_path.name} slice {source_idx + 1} has shape {shape[1]}x{shape[0]}, "
                            f"different from first frame {first_shape[1]}x{first_shape[0]}"
                        )

                    bg_mean = _fl_gif_roi_background_mean(img2d, bg_mode, bg_roi, mask_cache)
                    row = {
                        "global_frame": global_frame + 1,
                        "time_s": float(global_frame * frame_interval_s),
                        "source_file": str(tiff_path),
                        "source_name": tiff_path.name,
                        "source_slice": int(source_idx + 1),
                        "source_order": int(order_idx + 1),
                        "background_mode": bg_mode,
                        "background_mean": float(bg_mean) if np.isfinite(bg_mean) else np.nan,
                    }

                    for roi in roi_specs:
                        m = _fl_gif_roi_metrics_2d(img2d, roi, mask_cache)
                        raw_val = float(m.get(metric, np.nan))
                        area_px = int(m.get("area_px", 0))
                        val = _fl_gif_roi_apply_value(raw_val, area_px, metric, bg_mean, plot_metric)
                        key = roi["key"]
                        row[f"{key}_raw_{metric}"] = raw_val
                        row[f"{key}_area_px"] = area_px
                        row[f"{key}_value"] = val

                    rows.append(row)
                    global_frame += 1

            if not rows:
                return err("No valid frames found for ROI time analysis")

            df_out = pd.DataFrame(rows)
            ref_idx = max(0, min(int(ref_frame_raw) - 1, len(df_out) - 1))
            ref_frame_applied = int(ref_idx + 1)

            if plot_metric == "delta_f_over_f0":
                for roi in roi_specs:
                    col = f"{roi['key']}_value"
                    if col in df_out.columns:
                        arr = pd.to_numeric(df_out[col], errors="coerce").to_numpy(dtype=float)
                        df_out[col] = _fl_roi_delta_f_over_f0(arr, ref_idx)

            metric_labels = {
                "mean": "Mean",
                "top20_mean": "Top-20% Mean",
                "sum": "Sum",
                "max": "Max",
                "std": "Std Dev",
            }
            presentation_labels = {
                "absolute": "Fluorescence",
                "bg_subtracted": "BG-subtracted fluorescence",
                "bg_normalized": "F / F_BG",
                "delta_f_over_f0": "DeltaF/F0",
            }
            y_label = f"{presentation_labels.get(plot_metric, plot_metric)} ({metric_labels.get(metric, metric)})"

            fig, ax = plt.subplots(figsize=(9.5, 4.6), dpi=130)
            x = pd.to_numeric(df_out["time_s"], errors="coerce").to_numpy(dtype=float)
            for roi in roi_specs:
                col = f"{roi['key']}_value"
                if col not in df_out.columns:
                    continue
                y = pd.to_numeric(df_out[col], errors="coerce").to_numpy(dtype=float)
                ax.plot(x, y, lw=1.5, marker="o", markersize=2.8, color=roi.get("color", "#3E6AE1"), label=roi["label"])

            if plot_metric == "delta_f_over_f0":
                ax.axhline(0.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
                if 0 <= ref_idx < len(x) and np.isfinite(x[ref_idx]):
                    ax.axvline(float(x[ref_idx]), color="#C7C7C7", lw=1.0, ls=":", alpha=0.85)
            elif plot_metric == "bg_normalized":
                ax.axhline(1.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)

            ax.set_xlabel("Time (s)")
            ax.set_ylabel(y_label)
            ax.set_title("ROI Fluorescence Over Time")
            ax.grid(True, alpha=0.35)
            ax.legend(fontsize=8.5, frameon=False, loc="best")
            fig.tight_layout()
            img_b64 = fig_to_b64(fig)

            buf = io.StringIO()
            df_out.to_csv(buf, index=False)
            csv_str = buf.getvalue()

            default_output_dir = str(paths[0].parent) if paths else str(Path.cwd())
            return jsonify(
                {
                    "ok": True,
                    "img": img_b64,
                    "csv": csv_str,
                    "n_frames": int(len(df_out)),
                    "selected_slices": int(selected_total),
                    "n_rois": int(len(roi_specs)),
                    "metric": metric,
                    "plot_metric": plot_metric,
                    "bg_mode": bg_mode,
                    "fps": float(fps),
                    "frame_interval_s": float(frame_interval_s),
                    "ref_frame": int(ref_frame_raw),
                    "ref_frame_applied": ref_frame_applied,
                    "columns": list(df_out.columns),
                    "default_output_dir": default_output_dir,
                    "warnings": warnings,
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/analyze_job", methods=["POST"])
    def api_fl_gif_roi_analyze_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/gif_roi/analyze",
            "fluorescence.gif_roi_analysis",
            "Analyze GIF ROI time series",
            api_fl_gif_roi_analyze,
            request.json or {},
        )

    @app.route("/api/fluorescence/gif_roi/export", methods=["POST"])
    def api_fl_gif_roi_export():
        """Save GIF ROI time-analysis CSV and/or plot PNG to disk."""
        d = request.json or {}
        tiff_paths = d.get("tiff_paths") or []
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "gif_roi_time_analysis")
        save_csv = _fl_bool(d.get("save_csv", True), True)
        save_plot = _fl_bool(d.get("save_plot", True), True)
        csv_text = str(d.get("csv", "") or "")
        plot_png_b64 = str(d.get("plot_png_b64", "") or "")

        try:
            anchor = None
            if isinstance(tiff_paths, list):
                for raw in tiff_paths:
                    p = Path(str(raw or "").strip())
                    if p.exists():
                        anchor = p.parent
                        break

            if output_dir_raw:
                out_dir = Path(output_dir_raw).expanduser()
                if not out_dir.is_absolute():
                    out_dir = (anchor or Path.cwd()) / out_dir
            else:
                out_dir = anchor or Path.cwd()
            out_dir.mkdir(parents=True, exist_ok=True)

            saved_paths = []
            csv_path = ""
            plot_path = ""
            if save_csv and csv_text.strip():
                p = out_dir / f"{prefix}.csv"
                p.write_text(csv_text, encoding="utf-8")
                csv_path = str(p)
                saved_paths.append(csv_path)
            if save_plot and plot_png_b64.strip():
                p = out_dir / f"{prefix}.png"
                p.write_bytes(_fl_decode_base64_payload(plot_png_b64))
                plot_path = str(p)
                saved_paths.append(plot_path)

            if not saved_paths:
                return err("No GIF ROI outputs to save")

            return jsonify(
                {
                    "ok": True,
                    "output_dir": str(out_dir),
                    "csv_path": csv_path,
                    "plot_path": plot_path,
                    "saved_paths": saved_paths,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/export_job", methods=["POST"])
    def api_fl_gif_roi_export_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/gif_roi/export",
            "fluorescence.gif_roi_export",
            "Export GIF ROI time outputs",
            api_fl_gif_roi_export,
            request.json or {},
        )

    @app.route("/api/fluorescence/gif_roi/kymograph", methods=["POST"])
    def api_fl_gif_roi_kymograph():
        """Build a time-vs-intensity distribution kymograph for one polygon ROI."""
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")

        d = request.json or {}
        tiff_paths = d.get("tiff_paths") or []
        slice_specs = d.get("slice_specs") or []
        roi_specs = _fl_gif_roi_make_specs([d.get("roi")], "ROI") if isinstance(d.get("roi"), dict) else []
        bg_specs = _fl_gif_roi_make_specs([d.get("bg_roi")], "BG") if isinstance(d.get("bg_roi"), dict) else []
        roi = roi_specs[0] if roi_specs else None
        bg_roi = bg_specs[0] if bg_specs else None

        bg_mode = str(d.get("bg_mode", "none") or "none").strip()
        value_mode = str(d.get("value_mode", "delta_f_over_f0") or "delta_f_over_f0").strip()
        fps = max(0.1, float_or(d.get("fps", 5.0), 5.0))
        frame_interval_s = float_or(d.get("frame_interval_s"), None)
        if frame_interval_s is None or not np.isfinite(frame_interval_s) or frame_interval_s <= 0:
            frame_interval_s = 1.0 / fps
        ref_frame_raw = int_or(d.get("ref_frame", 1), 1)
        ref_stat = str(d.get("ref_stat", "median") or "median").strip().lower()
        bins = max(8, min(240, int_or(d.get("bins", 80), 80)))
        low_pct = max(0.0, min(49.0, float_or(d.get("range_low_pct", 1.0), 1.0)))
        high_pct = max(51.0, min(100.0, float_or(d.get("range_high_pct", 99.5), 99.5)))
        if high_pct <= low_pct:
            high_pct = min(100.0, low_pct + 1.0)
        range_min = float_or(d.get("range_min"), None)
        range_max = float_or(d.get("range_max"), None)
        smooth_intensity_bins = max(0.0, min(8.0, float_or(d.get("smooth_intensity_bins", 1.2), 1.2)))
        smooth_time_frames = max(0.0, min(8.0, float_or(d.get("smooth_time_frames", 0.8), 0.8)))
        smooth_lines = _fl_bool(d.get("smooth_lines", True), True)
        overlay_percentiles = _fl_parse_percent_list(d.get("overlay_percentiles", []), max_items=8, lower_exclusive=0.0, upper_inclusive=100.0)
        overlay_top_means = _fl_parse_percent_list(d.get("overlay_top_means", []), max_items=6, lower_exclusive=0.0, upper_inclusive=100.0)
        overlay_peak = _fl_bool(d.get("overlay_peak", False), False)
        overlay_mean = _fl_bool(d.get("overlay_mean", False), False)
        threshold_lines_raw = d.get("threshold_lines", [])
        threshold_lines = []
        if isinstance(threshold_lines_raw, list):
            for raw in threshold_lines_raw[:12]:
                th = float_or(raw, None)
                if th is not None and np.isfinite(th):
                    threshold_lines.append(float(th))
        elif isinstance(threshold_lines_raw, str):
            for token in _re2.split(r"[,;\s]+", threshold_lines_raw.strip())[:12]:
                if not token:
                    continue
                th = float_or(token, None)
                if th is not None and np.isfinite(th):
                    threshold_lines.append(float(th))

        valid_bg_modes = {"none", "corner_br", "corner_tl", "roi"}
        valid_value_modes = {"absolute", "bg_subtracted", "delta_f_over_f0"}
        if bg_mode not in valid_bg_modes:
            bg_mode = "none"
        if value_mode not in valid_value_modes:
            value_mode = "delta_f_over_f0"
        if ref_stat not in {"mean", "median", "p90", "p99"}:
            ref_stat = "median"
        if bg_mode == "roi" and bg_roi is None:
            return err("Select a background ROI or change background mode")
        if value_mode == "bg_subtracted" and bg_mode == "none":
            return err("Choose a background mode before using BG-subtracted kymography")

        if not isinstance(tiff_paths, list) or not tiff_paths:
            return err("tiff_paths must be a non-empty list")
        if roi is None:
            return err("Select one closed polygon ROI for kymography")

        try:
            paths = []
            for raw in tiff_paths:
                p = Path(str(raw or "").strip())
                if not p.exists():
                    return err(f"TIFF not found: {raw}")
                paths.append(p)

            corrected_by_frame: list[np.ndarray] = []
            raw_by_frame: list[np.ndarray] = []
            bg_means: list[float] = []
            frame_meta: list[dict] = []
            mask_cache: dict = {}
            selected_total = 0
            global_frame = 0
            warnings = []
            first_shape: tuple[int, int] | None = None

            for path_idx, tiff_path in enumerate(paths):
                n_available, _shape = _fl_tiff_gif_frame_count(tiff_path)
                slice_spec = slice_specs[path_idx] if path_idx < len(slice_specs) else ""
                selected_indices = _fl_parse_slice_spec(slice_spec, n_available)
                selected_total += len(selected_indices)
                planes = _fl_read_selected_gif_planes(tiff_path, selected_indices)

                for order_idx, (source_idx, plane) in enumerate(zip(selected_indices, planes)):
                    img2d = np.asarray(plane, dtype=np.float64)
                    if img2d.ndim != 2:
                        img2d = np.squeeze(img2d)
                    if img2d.ndim != 2:
                        continue

                    shape = (int(img2d.shape[0]), int(img2d.shape[1]))
                    if first_shape is None:
                        first_shape = shape
                    elif shape != first_shape and len(warnings) < 4:
                        warnings.append(
                            f"{tiff_path.name} slice {source_idx + 1} has shape {shape[1]}x{shape[0]}, "
                            f"different from first frame {first_shape[1]}x{first_shape[0]}"
                        )

                    mask = _fl_gif_roi_mask_for(mask_cache, roi, shape)
                    vals_raw = np.asarray(img2d[mask], dtype=np.float64).ravel() if mask.shape == img2d.shape else np.asarray([], dtype=np.float64)
                    bg_mean = _fl_gif_roi_background_mean(img2d, bg_mode, bg_roi, mask_cache)
                    vals_corrected = vals_raw - float(bg_mean) if np.isfinite(bg_mean) else vals_raw.copy()

                    raw_by_frame.append(vals_raw)
                    corrected_by_frame.append(vals_corrected)
                    bg_means.append(float(bg_mean) if np.isfinite(bg_mean) else np.nan)
                    frame_meta.append(
                        {
                            "global_frame": global_frame + 1,
                            "time_s": float(global_frame * frame_interval_s),
                            "source_file": str(tiff_path),
                            "source_name": tiff_path.name,
                            "source_slice": int(source_idx + 1),
                            "source_order": int(order_idx + 1),
                        }
                    )
                    global_frame += 1

            if not corrected_by_frame:
                return err("No valid frames found for kymography")

            ref_idx = max(0, min(int(ref_frame_raw) - 1, len(corrected_by_frame) - 1))
            ref_frame_applied = int(ref_idx + 1)
            f0_value = float("nan")

            if value_mode == "absolute":
                values_by_frame = [v.astype(np.float64, copy=False) for v in raw_by_frame]
            elif value_mode == "bg_subtracted":
                values_by_frame = [v.astype(np.float64, copy=False) for v in corrected_by_frame]
            else:
                f0_value = _fl_gif_kymo_stat(corrected_by_frame[ref_idx], ref_stat)
                baseline_chunks = []
                for v in corrected_by_frame:
                    finite = v[np.isfinite(v)]
                    if finite.size:
                        baseline_chunks.append(finite[:: max(1, finite.size // 5000)])
                if not baseline_chunks:
                    return err("Selected ROI has no finite pixels")
                finite_sample = np.concatenate(baseline_chunks)
                data_span = float(np.nanmax(finite_sample) - np.nanmin(finite_sample)) if finite_sample.size else 0.0
                eps = max(1e-12, abs(data_span) * 1e-9)
                if not np.isfinite(f0_value) or abs(f0_value) <= eps:
                    return err("Reference F0 is too close to zero for DeltaF/F0; use another ref frame/stat or BG-subtracted mode")
                values_by_frame = [(v - f0_value) / f0_value for v in corrected_by_frame]

            sample_chunks = []
            for vals in values_by_frame:
                finite = vals[np.isfinite(vals)]
                if finite.size == 0:
                    continue
                step = max(1, finite.size // 5000)
                sample_chunks.append(finite[::step])
            if not sample_chunks:
                return err("Selected ROI has no finite pixels")
            sample = np.concatenate(sample_chunks)

            if (
                range_min is not None
                and range_max is not None
                and np.isfinite(range_min)
                and np.isfinite(range_max)
                and range_max > range_min
            ):
                vmin, vmax = float(range_min), float(range_max)
            else:
                vmin, vmax = np.percentile(sample, [low_pct, high_pct])
                vmin, vmax = float(vmin), float(vmax)
            if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
                vmin, vmax = float(np.nanmin(sample)), float(np.nanmax(sample))
            if vmax <= vmin:
                vmax = vmin + 1.0

            edges = np.linspace(vmin, vmax, bins + 1)
            centers = (edges[:-1] + edges[1:]) / 2.0
            hist_counts = []
            hist_pct = []
            summary_rows = []
            overlay_specs = []
            overlay_palette = ["#f8fafc", "#38bdf8", "#a3e635", "#fbbf24", "#fb7185", "#c084fc", "#2dd4bf", "#f472b6"]
            if overlay_peak:
                overlay_specs.append({"col": "display_peak_bin_intensity", "label": "peak bin", "color": "#ffffff", "lw": 1.5})
            if overlay_mean:
                overlay_specs.append({"col": "mean", "label": "mean", "color": "#22d3ee", "lw": 1.35})
            for pct in overlay_percentiles:
                label = _fl_percent_label(pct)
                overlay_specs.append({"col": f"p{label}", "label": f"p{pct:g}", "color": overlay_palette[(len(overlay_specs)) % len(overlay_palette)], "lw": 1.3})
            for pct in overlay_top_means:
                label = _fl_percent_label(pct)
                overlay_specs.append(
                    {
                        "col": f"top{label}_mean",
                        "label": f"top {pct:g}% mean",
                        "color": overlay_palette[(len(overlay_specs)) % len(overlay_palette)],
                        "lw": 1.3,
                    }
                )

            for meta, vals, bg_mean in zip(frame_meta, values_by_frame, bg_means):
                finite = vals[np.isfinite(vals)]
                counts, _ = np.histogram(finite, bins=edges)
                denom = max(1, int(finite.size))
                hist_counts.append(counts)
                hist_pct.append(counts.astype(np.float64) / float(denom) * 100.0)
                peak_idx = int(np.argmax(counts)) if counts.size else 0
                peak_count = int(counts[peak_idx]) if counts.size else 0
                peak_fraction_pct = float(peak_count) / float(denom) * 100.0
                peak_bin_intensity = float(centers[peak_idx]) if centers.size else np.nan

                if finite.size:
                    summary = {
                        **meta,
                        "roi_label": roi["label"],
                        "value_mode": value_mode,
                        "background_mode": bg_mode,
                        "background_mean": bg_mean,
                        "area_px": int(finite.size),
                        "mean": float(np.mean(finite)),
                        "median": float(np.median(finite)),
                        "std": float(np.std(finite)),
                        "min": float(np.min(finite)),
                        "max": float(np.max(finite)),
                        "p90": float(np.percentile(finite, 90.0)),
                        "p99": float(np.percentile(finite, 99.0)),
                        "top5_mean": _fl_gif_kymo_top_mean(finite, 0.05),
                        "top1_mean": _fl_gif_kymo_top_mean(finite, 0.01),
                        "peak_bin_intensity": peak_bin_intensity,
                        "peak_bin_fraction_pct": peak_fraction_pct,
                        "peak_bin_count": peak_count,
                    }
                    for pct in overlay_percentiles:
                        summary[f"p{_fl_percent_label(pct)}"] = float(np.percentile(finite, float(pct)))
                    for pct in overlay_top_means:
                        summary[f"top{_fl_percent_label(pct)}_mean"] = _fl_gif_kymo_top_mean(finite, float(pct) / 100.0)
                else:
                    summary = {
                        **meta,
                        "roi_label": roi["label"],
                        "value_mode": value_mode,
                        "background_mode": bg_mode,
                        "background_mean": bg_mean,
                        "area_px": 0,
                        "mean": np.nan,
                        "median": np.nan,
                        "std": np.nan,
                        "min": np.nan,
                        "max": np.nan,
                        "p90": np.nan,
                        "p99": np.nan,
                        "top5_mean": np.nan,
                        "top1_mean": np.nan,
                        "peak_bin_intensity": np.nan,
                        "peak_bin_fraction_pct": np.nan,
                        "peak_bin_count": 0,
                    }
                    for pct in overlay_percentiles:
                        summary[f"p{_fl_percent_label(pct)}"] = np.nan
                    for pct in overlay_top_means:
                        summary[f"top{_fl_percent_label(pct)}_mean"] = np.nan
                summary_rows.append(summary)

            hist_counts_arr = np.asarray(hist_counts, dtype=np.int64)
            hist_pct_arr = np.asarray(hist_pct, dtype=np.float64)
            summary_df = pd.DataFrame(summary_rows)
            hist_display_arr = _fl_smooth_heatmap_2d(hist_pct_arr, smooth_intensity_bins, smooth_time_frames)
            if hist_display_arr.size and centers.size:
                display_peak_idx = np.argmax(hist_display_arr, axis=1)
                summary_df["display_peak_bin_intensity"] = [float(centers[int(i)]) for i in display_peak_idx]
                summary_df["display_peak_bin_fraction_pct"] = [
                    float(hist_display_arr[row_i, int(bin_i)]) for row_i, bin_i in enumerate(display_peak_idx)
                ]
            else:
                summary_df["display_peak_bin_intensity"] = np.nan
                summary_df["display_peak_bin_fraction_pct"] = np.nan
            if smooth_lines and smooth_time_frames > 0:
                smooth_cols = ["mean", "median", "p90", "p99", "top5_mean", "top1_mean", "display_peak_bin_intensity"]
                smooth_cols.extend([spec["col"] for spec in overlay_specs])
                for col in dict.fromkeys(smooth_cols):
                    if col in summary_df.columns:
                        summary_df[f"{col}_display"] = _fl_smooth_series_nan(
                            pd.to_numeric(summary_df[col], errors="coerce").to_numpy(dtype=float),
                            smooth_time_frames,
                        )

            heat_rows = []
            for frame_i, meta in enumerate(frame_meta):
                for bin_i in range(bins):
                    heat_rows.append(
                        {
                            **meta,
                            "roi_label": roi["label"],
                            "value_mode": value_mode,
                            "bin_index": bin_i + 1,
                            "bin_left": float(edges[bin_i]),
                            "bin_right": float(edges[bin_i + 1]),
                            "bin_center": float(centers[bin_i]),
                            "pixel_count": int(hist_counts_arr[frame_i, bin_i]),
                            "pixel_fraction_pct": float(hist_pct_arr[frame_i, bin_i]),
                            "smoothed_pixel_fraction_pct": float(hist_display_arr[frame_i, bin_i]),
                        }
                    )
            heatmap_df = pd.DataFrame(heat_rows)

            frame_axis = pd.to_numeric(summary_df["global_frame"], errors="coerce").to_numpy(dtype=float)
            if len(frame_axis) == 1:
                y0, y1 = 0.5, 1.5
            else:
                y0 = float(np.nanmin(frame_axis) - 0.5)
                y1 = float(np.nanmax(frame_axis) + 0.5)

            value_labels = {
                "absolute": "Intensity",
                "bg_subtracted": "BG-subtracted intensity",
                "delta_f_over_f0": "DeltaF/F0",
            }
            fig, ax = plt.subplots(figsize=(9.4, 4.8), dpi=140, constrained_layout=True)
            positive = hist_display_arr[hist_display_arr > 0]
            vmax_color = float(np.percentile(positive, 99.0)) if positive.size else 1.0
            im = ax.imshow(
                hist_display_arr,
                origin="lower",
                aspect="auto",
                extent=[float(edges[0]), float(edges[-1]), y0, y1],
                cmap="magma",
                vmin=0.0,
                vmax=max(vmax_color, 1e-9),
                interpolation="bilinear" if (smooth_intensity_bins > 0 or smooth_time_frames > 0) else "nearest",
            )
            for spec in overlay_specs:
                col = spec["col"]
                if col in summary_df.columns:
                    plot_col = f"{col}_display" if smooth_lines and f"{col}_display" in summary_df.columns else col
                    ax.plot(
                        pd.to_numeric(summary_df[plot_col], errors="coerce").to_numpy(dtype=float),
                        frame_axis,
                        color=spec["color"],
                        lw=float(spec.get("lw", 1.35)),
                        label=spec["label"],
                        clip_on=True,
                    )
            if value_mode == "delta_f_over_f0":
                ax.axvline(0.0, color="#d8d8d8", lw=0.9, ls="--", alpha=0.75)
            for th in threshold_lines:
                if float(edges[0]) <= th <= float(edges[-1]):
                    ax.axvline(th, color="#111827", lw=2.6, ls=(0, (4, 3)), alpha=0.45)
                    ax.axvline(th, color="#f8fafc", lw=1.15, ls=(0, (4, 3)), alpha=0.95)
                    ax.text(
                        th,
                        y1,
                        f"{th:g}",
                        color="#f5f5f5",
                        fontsize=7.5,
                        rotation=90,
                        ha="right",
                        va="top",
                        alpha=0.95,
                    )
            ax.set_xlim(float(edges[0]), float(edges[-1]))
            ax.set_ylim(y0, y1)
            ax.set_xlabel(value_labels.get(value_mode, value_mode))
            ax.set_ylabel("Frame #")
            ax.set_title(f"{roi['label']} Frame-Intensity Distribution")
            if overlay_specs:
                leg = ax.legend(
                    fontsize=7.8,
                    frameon=True,
                    loc="upper right",
                    ncol=1,
                    handlelength=1.8,
                    labelspacing=0.35,
                    borderpad=0.35,
                )
                leg.get_frame().set_facecolor((0.02, 0.02, 0.03, 0.72))
                leg.get_frame().set_edgecolor((1.0, 1.0, 1.0, 0.2))
                for txt in leg.get_texts():
                    txt.set_color("white")
            cbar = fig.colorbar(im, ax=ax, pad=0.012, shrink=0.96)
            cbar.set_label("Pixels in bin (%)")
            img_b64 = fig_to_b64(fig)

            heat_buf = io.StringIO()
            heatmap_df.to_csv(heat_buf, index=False)
            summary_buf = io.StringIO()
            summary_df.to_csv(summary_buf, index=False)

            default_output_dir = str(paths[0].parent) if paths else str(Path.cwd())
            return jsonify(
                {
                    "ok": True,
                    "img": img_b64,
                    "heatmap_csv": heat_buf.getvalue(),
                    "summary_csv": summary_buf.getvalue(),
                    "n_frames": int(len(frame_meta)),
                    "selected_slices": int(selected_total),
                    "roi_label": roi["label"],
                    "value_mode": value_mode,
                    "bg_mode": bg_mode,
                    "fps": float(fps),
                    "frame_interval_s": float(frame_interval_s),
                    "bins": int(bins),
                    "range_min": float(vmin),
                    "range_max": float(vmax),
                    "smooth_intensity_bins": float(smooth_intensity_bins),
                    "smooth_time_frames": float(smooth_time_frames),
                    "smooth_lines": bool(smooth_lines),
                    "overlay_peak": bool(overlay_peak),
                    "overlay_mean": bool(overlay_mean),
                    "overlay_percentiles": overlay_percentiles,
                    "overlay_top_means": overlay_top_means,
                    "threshold_lines": threshold_lines,
                    "ref_frame": int(ref_frame_raw),
                    "ref_frame_applied": ref_frame_applied,
                    "ref_stat": ref_stat,
                    "f0_value": float(f0_value) if np.isfinite(f0_value) else None,
                    "default_output_dir": default_output_dir,
                    "warnings": warnings,
                }
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/kymograph_job", methods=["POST"])
    def api_fl_gif_roi_kymograph_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/gif_roi/kymograph",
            "fluorescence.gif_roi_kymograph",
            "Build GIF ROI kymograph",
            api_fl_gif_roi_kymograph,
            request.json or {},
        )

    @app.route("/api/fluorescence/gif_roi/kymograph_export", methods=["POST"])
    def api_fl_gif_roi_kymograph_export():
        """Save selected-ROI kymograph plot and data to disk."""
        d = request.json or {}
        tiff_paths = d.get("tiff_paths") or []
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "gif_roi_kymograph")
        save_heatmap_csv = _fl_bool(d.get("save_heatmap_csv", True), True)
        save_summary_csv = _fl_bool(d.get("save_summary_csv", True), True)
        save_plot = _fl_bool(d.get("save_plot", True), True)
        heatmap_csv = str(d.get("heatmap_csv", "") or "")
        summary_csv = str(d.get("summary_csv", "") or "")
        plot_png_b64 = str(d.get("plot_png_b64", "") or "")

        try:
            anchor = None
            if isinstance(tiff_paths, list):
                for raw in tiff_paths:
                    p = Path(str(raw or "").strip())
                    if p.exists():
                        anchor = p.parent
                        break

            if output_dir_raw:
                out_dir = Path(output_dir_raw).expanduser()
                if not out_dir.is_absolute():
                    out_dir = (anchor or Path.cwd()) / out_dir
            else:
                out_dir = anchor or Path.cwd()
            out_dir.mkdir(parents=True, exist_ok=True)

            saved_paths = []
            heatmap_path = ""
            summary_path = ""
            plot_path = ""
            if save_heatmap_csv and heatmap_csv.strip():
                p = out_dir / f"{prefix}_heatmap.csv"
                p.write_text(heatmap_csv, encoding="utf-8")
                heatmap_path = str(p)
                saved_paths.append(heatmap_path)
            if save_summary_csv and summary_csv.strip():
                p = out_dir / f"{prefix}_summary.csv"
                p.write_text(summary_csv, encoding="utf-8")
                summary_path = str(p)
                saved_paths.append(summary_path)
            if save_plot and plot_png_b64.strip():
                p = out_dir / f"{prefix}.png"
                p.write_bytes(_fl_decode_base64_payload(plot_png_b64))
                plot_path = str(p)
                saved_paths.append(plot_path)

            if not saved_paths:
                return err("No kymograph outputs to save")

            return jsonify(
                {
                    "ok": True,
                    "output_dir": str(out_dir),
                    "heatmap_csv_path": heatmap_path,
                    "summary_csv_path": summary_path,
                    "plot_path": plot_path,
                    "saved_paths": saved_paths,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/kymograph_export_job", methods=["POST"])
    def api_fl_gif_roi_kymograph_export_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/gif_roi/kymograph_export",
            "fluorescence.gif_roi_kymograph_export",
            "Export GIF ROI kymograph outputs",
            api_fl_gif_roi_kymograph_export,
            request.json or {},
        )
