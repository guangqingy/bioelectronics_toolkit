from __future__ import annotations

import base64
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import jsonify, request

from .jobs import route_response_to_payload, submit_json_task


def register_fluorescence_gif_basic_routes(app, fl):
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

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

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
    def api_fl_gif_roi_export_preview(payload=None):
        """Save one GIF preview frame with polygon ROI overlays and a labeled scale bar."""
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")

        d = (request.json or {}) if payload is None else payload
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
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_export_preview",
            "Export GIF ROI preview",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_gif_roi_export_preview, "Exporting GIF ROI preview"
            ),
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/gif_roi/export_preview"},
        )

    @app.route("/api/fluorescence/make_gif", methods=["POST"])
    def api_fl_make_gif(payload=None):
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = (request.json or {}) if payload is None else payload
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
        return submit_json_task(
            jobs,
            "fluorescence.make_gif",
            "Generate single-file fluorescence GIF",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_make_gif, "Generating fluorescence GIF"
            ),
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/make_gif"},
        )

    @app.route("/api/fluorescence/merge_gif", methods=["POST"])
    def api_fl_merge_gif(payload=None):
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
        d = (request.json or {}) if payload is None else payload
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
        return submit_json_task(
            jobs,
            "fluorescence.merge_gif",
            "Generate fluorescence GIF",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_merge_gif, "Generating fluorescence GIF"
            ),
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/merge_gif"},
        )
