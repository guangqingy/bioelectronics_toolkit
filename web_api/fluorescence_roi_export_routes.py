# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move ROI sequence image/GIF export assembly into fluorescence ROI services
# and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
from __future__ import annotations

import traceback
from pathlib import Path

import numpy as np
from flask import jsonify
from pydantic import ValidationError

from .fluorescence_request_schemas import (
    FluorescenceRoiExportSequenceGifRequest,
    FluorescenceRoiExportSequenceRequest,
)
from .jobs import route_response_to_payload, submit_json_task
from .request_validation import parse_json_payload, request_schema, validation_error_response


def register_fluorescence_roi_export_routes(app, fl):
    err = fl["err"]
    float_or = fl["float_or"]
    int_or = fl["int_or"]
    jobs = fl["jobs"]

    _fl_bool = fl["_fl_bool"]
    _fl_decode_base64_payload = fl["_fl_decode_base64_payload"]
    _fl_frame_to_b64 = fl["_fl_frame_to_b64"]
    _fl_infer_pixel_size_um_from_tiff = fl["_fl_infer_pixel_size_um_from_tiff"]
    _fl_roi_apply_metric_mode = fl["_fl_roi_apply_metric_mode"]
    _fl_roi_background_mean = fl["_fl_roi_background_mean"]
    _fl_roi_circle_geometry = fl["_fl_roi_circle_geometry"]
    _fl_roi_collect_pairs = fl["_fl_roi_collect_pairs"]
    _fl_roi_compute = fl["_fl_roi_compute"]
    _fl_roi_delta_f_over_f0 = fl["_fl_roi_delta_f_over_f0"]
    _fl_roi_empty_metrics = fl["_fl_roi_empty_metrics"]
    _fl_roi_metrics_2d = fl["_fl_roi_metrics_2d"]
    _fl_roi_normalize_to_reference = fl["_fl_roi_normalize_to_reference"]
    _fl_roi_pick_output_dir = fl["_fl_roi_pick_output_dir"]
    _fl_roi_plot_radial_profiles = fl["_fl_roi_plot_radial_profiles"]
    _fl_roi_radial_pair_rows = fl["_fl_roi_radial_pair_rows"]
    _fl_roi_read_first_page = fl["_fl_roi_read_first_page"]
    _fl_roi_render_gif_frame = fl["_fl_roi_render_gif_frame"]
    _fl_roi_render_reference_preview = fl["_fl_roi_render_reference_preview"]
    _fl_roi_resolve_ref_index = fl["_fl_roi_resolve_ref_index"]
    _fl_roi_ring_count = fl["_fl_roi_ring_count"]
    _fl_roi_safe_ratio = fl["_fl_roi_safe_ratio"]
    _fl_roi_sequence_number = fl["_fl_roi_sequence_number"]
    _fl_roi_shape_type = fl["_fl_roi_shape_type"]
    _fl_roi_shared_ylim = fl["_fl_roi_shared_ylim"]
    _fl_sanitize_prefix = fl["_fl_sanitize_prefix"]

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    @app.route("/api/fluorescence/roi/export_sequence", methods=["POST"])
    @request_schema(FluorescenceRoiExportSequenceRequest)
    def api_fl_roi_export_sequence(payload=None):
        """Save ROI sequence analysis outputs to disk (CSV/plot/ROI preview)."""
        try:
            if payload is None:
                d = parse_json_payload(FluorescenceRoiExportSequenceRequest).model_dump()
            else:
                d = FluorescenceRoiExportSequenceRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        records = d.get("records", [])
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "roi_analysis")

        save_csv = _fl_bool(d.get("save_csv", True), True)
        save_plot = _fl_bool(d.get("save_plot", True), True)
        save_preview = _fl_bool(d.get("save_preview", True), True)
        save_radial_csv = _fl_bool(d.get("save_radial_csv", True), True)
        save_radial_plot = _fl_bool(d.get("save_radial_plot", True), True)

        csv_text = str(d.get("csv", "") or "")
        plot_png_b64 = str(d.get("plot_png_b64", "") or "")
        roi_preview_png_b64 = str(d.get("roi_preview_png_b64", "") or "")
        radial_csv_text = str(d.get("radial_csv", "") or "")
        radial_plot_png_b64 = str(d.get("radial_plot_png_b64", "") or "")

        try:
            out_dir = _fl_roi_pick_output_dir(records, output_dir_raw)
            out_dir.mkdir(parents=True, exist_ok=True)

            csv_path = ""
            plot_path = ""
            preview_path = ""
            radial_csv_path = ""
            radial_plot_path = ""

            if save_csv and csv_text.strip():
                p = out_dir / f"{prefix}_metrics.csv"
                p.write_text(csv_text, encoding="utf-8")
                csv_path = str(p)

            if save_plot and plot_png_b64.strip():
                p = out_dir / f"{prefix}_plots_page2_basic.png"
                p.write_bytes(_fl_decode_base64_payload(plot_png_b64))
                plot_path = str(p)

            if save_preview and roi_preview_png_b64.strip():
                p = out_dir / f"{prefix}_roi_reference.png"
                p.write_bytes(_fl_decode_base64_payload(roi_preview_png_b64))
                preview_path = str(p)

            if save_radial_csv and radial_csv_text.strip():
                p = out_dir / f"{prefix}_radial_profile.csv"
                p.write_text(radial_csv_text, encoding="utf-8")
                radial_csv_path = str(p)

            if save_radial_plot and radial_plot_png_b64.strip():
                p = out_dir / f"{prefix}_radial_profile.png"
                p.write_bytes(_fl_decode_base64_payload(radial_plot_png_b64))
                radial_plot_path = str(p)

            if not any([csv_path, plot_path, preview_path, radial_csv_path, radial_plot_path]):
                return err("No output content provided for saving")

            saved_paths = [
                p
                for p in [csv_path, plot_path, preview_path, radial_csv_path, radial_plot_path]
                if p
            ]
            return jsonify(
                {
                    "ok": True,
                    "output_dir": str(out_dir),
                    "prefix": prefix,
                    "csv_path": csv_path,
                    "plot_path": plot_path,
                    "preview_path": preview_path,
                    "radial_csv_path": radial_csv_path,
                    "radial_plot_path": radial_plot_path,
                    "saved_paths": saved_paths,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/export_sequence_job", methods=["POST"])
    @request_schema(FluorescenceRoiExportSequenceRequest)
    def api_fl_roi_export_sequence_job():
        try:
            body = parse_json_payload(FluorescenceRoiExportSequenceRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.roi_export_sequence",
            "Export ROI sequence outputs",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_roi_export_sequence, "Exporting ROI sequence outputs"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/roi/export_sequence"},
        )

    @app.route("/api/fluorescence/roi/export_sequence_gif", methods=["POST"])
    @request_schema(FluorescenceRoiExportSequenceGifRequest)
    def api_fl_roi_export_sequence_gif(payload=None):
        """Save a sequence GIF from selected ROI records with ROI overlays and scale bar."""
        try:
            if payload is None:
                d = parse_json_payload(FluorescenceRoiExportSequenceGifRequest).model_dump()
            else:
                d = FluorescenceRoiExportSequenceGifRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        records = d.get("records", [])
        rois = d.get("rois", [])
        preview_stack = str(d.get("preview_stack", "stack1") or "stack1").strip().lower()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "roi_analysis")

        frame_ms = int_or(d.get("frame_ms", 2000), 2000)
        frame_ms = max(20, frame_ms)
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 0.0), 0.0))
        pixel_size_um_override = float_or(d.get("pixel_size_um"), None)
        if pixel_size_um_override is not None and (
            not np.isfinite(pixel_size_um_override) or pixel_size_um_override <= 0
        ):
            pixel_size_um_override = None
        show_preview_name = _fl_bool(d.get("show_preview_name", True), True)
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        scale_bar_label = str(d.get("scale_bar_label", "") or "").strip()
        label_scale = float_or(d.get("label_scale", 1.0), 1.0)
        label_scale = max(0.5, min(4.0, label_scale))

        if not isinstance(records, list) or not records:
            return err("No records selected")

        try:
            frame_paths = []
            frame_names = []
            for i, rec in enumerate(records):
                if not isinstance(rec, dict):
                    continue
                base = str(rec.get("base", "") or "").strip() or f"frame_{i + 1}"
                s1 = str(rec.get("stack1", "") or "").strip()
                s2 = str(rec.get("stack2", "") or "").strip()
                if preview_stack == "stack2":
                    path = (
                        s2 if s2 and Path(s2).exists() else (s1 if s1 and Path(s1).exists() else "")
                    )
                else:
                    path = (
                        s1 if s1 and Path(s1).exists() else (s2 if s2 and Path(s2).exists() else "")
                    )
                if path:
                    frame_paths.append(path)
                    frame_names.append(base)

            if not frame_paths:
                return err("No valid stack paths found for GIF export")

            pixel_size_um = pixel_size_um_override
            if pixel_size_um is None:
                pixel_size_um = _fl_infer_pixel_size_um_from_tiff(frame_paths[0])

            frames = []
            for p, name in zip(frame_paths, frame_names, strict=True):
                img2d = _fl_roi_read_first_page(p)
                frame = _fl_roi_render_gif_frame(
                    img2d=img2d,
                    frame_name=name,
                    roi_specs=rois if isinstance(rois, list) else [],
                    pixel_size_um=pixel_size_um,
                    scale_bar_um=scale_bar_um,
                    scale_label=scale_bar_label,
                    show_name=show_preview_name,
                    show_scale_bar=show_scale_bar,
                    label_scale=label_scale,
                )
                frames.append(frame)

            if not frames:
                return err("No valid frames generated for GIF")

            out_dir = _fl_roi_pick_output_dir(records, output_dir_raw)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{prefix}_{preview_stack}.gif"
            frames[0].save(
                str(out_path),
                save_all=True,
                append_images=frames[1:],
                duration=frame_ms,
                loop=0,
            )

            return jsonify(
                {
                    "ok": True,
                    "gif_path": str(out_path),
                    "n_frames": len(frames),
                    "frame_ms": frame_ms,
                    "pixel_size_um": float(pixel_size_um)
                    if pixel_size_um is not None and np.isfinite(pixel_size_um)
                    else None,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/export_sequence_gif_job", methods=["POST"])
    @request_schema(FluorescenceRoiExportSequenceGifRequest)
    def api_fl_roi_export_sequence_gif_job():
        try:
            body = parse_json_payload(FluorescenceRoiExportSequenceGifRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.roi_export_sequence_gif",
            "Export ROI sequence GIF",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_roi_export_sequence_gif, "Exporting ROI sequence GIF"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/roi/export_sequence_gif"},
        )
