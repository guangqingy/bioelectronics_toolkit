# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move GIF ROI analysis/export workflow helpers into services/fluorescence/ and
# track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
from __future__ import annotations

import io
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from flask import jsonify
from pydantic import ValidationError

from services.matplotlib_utils import new_subplots

from .fluorescence_request_schemas import (
    FluorescenceGifRoiAnalyzeRequest,
    FluorescenceGifRoiExportRequest,
)
from .jobs import route_response_to_payload, submit_json_task
from .path_policy import resolve_output_dir
from .request_validation import parse_json_payload, request_schema, validation_error_response


def register_fluorescence_gif_roi_analysis_routes(app, fl):
    err = fl["err"]
    fig_to_b64 = fl["fig_to_b64"]
    float_or = fl["float_or"]
    int_or = fl["int_or"]
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

    @app.route("/api/fluorescence/gif_roi/analyze", methods=["POST"])
    @request_schema(FluorescenceGifRoiAnalyzeRequest)
    def api_fl_gif_roi_analyze(payload=None):
        """Analyze polygon ROI fluorescence across the GIF queue timeline."""
        try:
            if payload is None:
                body = parse_json_payload(FluorescenceGifRoiAnalyzeRequest).model_dump()
            else:
                body = FluorescenceGifRoiAnalyzeRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        tiff_paths = body.get("tiff_paths") or []
        slice_specs = body.get("slice_specs") or []
        roi_specs = _fl_gif_roi_make_specs(body.get("rois", body.get("roi_polygons", [])))
        bg_specs = (
            _fl_gif_roi_make_specs([body.get("bg_roi")], "BG")
            if isinstance(body.get("bg_roi"), dict)
            else []
        )
        bg_roi = bg_specs[0] if bg_specs else None

        metric = str(body.get("metric", "mean") or "mean").strip()
        plot_metric = str(body.get("plot_metric", "delta_f_over_f0") or "delta_f_over_f0").strip()
        bg_mode = str(body.get("bg_mode", "none") or "none").strip()
        fps = max(0.1, float_or(body.get("fps", 5.0), 5.0))
        frame_interval_s = float_or(body.get("frame_interval_s"), None)
        if frame_interval_s is None or not np.isfinite(frame_interval_s) or frame_interval_s <= 0:
            frame_interval_s = 1.0 / fps
        ref_frame_raw = int_or(body.get("ref_frame", 1), 1)

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

                for order_idx, (source_idx, plane) in enumerate(
                    zip(selected_indices, planes, strict=True)
                ):
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
                        val = _fl_gif_roi_apply_value(
                            raw_val, area_px, metric, bg_mean, plot_metric
                        )
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

            fig, ax = new_subplots(figsize=(9.5, 4.6), dpi=130)
            x = pd.to_numeric(df_out["time_s"], errors="coerce").to_numpy(dtype=float)
            for roi in roi_specs:
                col = f"{roi['key']}_value"
                if col not in df_out.columns:
                    continue
                y = pd.to_numeric(df_out[col], errors="coerce").to_numpy(dtype=float)
                ax.plot(
                    x,
                    y,
                    lw=1.5,
                    marker="o",
                    markersize=2.8,
                    color=roi.get("color", "#3E6AE1"),
                    label=roi["label"],
                )

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

            default_output_dir = (
                str(paths[0].parent)
                if paths
                else str(resolve_output_dir("", "", "fluorescence_gif_roi"))
            )
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
    @request_schema(FluorescenceGifRoiAnalyzeRequest)
    def api_fl_gif_roi_analyze_job():
        try:
            body = parse_json_payload(FluorescenceGifRoiAnalyzeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_analysis",
            "Analyze GIF ROI time series",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_gif_roi_analyze, "Analyzing GIF ROI time series"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/analyze"},
        )

    @app.route("/api/fluorescence/gif_roi/export", methods=["POST"])
    @request_schema(FluorescenceGifRoiExportRequest)
    def api_fl_gif_roi_export(payload=None):
        """Save GIF ROI time-analysis CSV and/or plot PNG to disk."""
        try:
            if payload is None:
                body = parse_json_payload(FluorescenceGifRoiExportRequest).model_dump()
            else:
                body = FluorescenceGifRoiExportRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        tiff_paths = body.get("tiff_paths") or []
        output_dir_raw = str(body.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(body.get("prefix", ""), "gif_roi_time_analysis")
        save_csv = _fl_bool(body.get("save_csv", True), True)
        save_plot = _fl_bool(body.get("save_plot", True), True)
        csv_text = str(body.get("csv", "") or "")
        plot_png_b64 = str(body.get("plot_png_b64", "") or "")

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
                    out_dir = (anchor / out_dir) if anchor is not None else resolve_output_dir("", out_dir, "fluorescence_gif_roi")
            else:
                out_dir = anchor or resolve_output_dir("", "", "fluorescence_gif_roi")
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
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/export_job", methods=["POST"])
    @request_schema(FluorescenceGifRoiExportRequest)
    def api_fl_gif_roi_export_job():
        try:
            body = parse_json_payload(FluorescenceGifRoiExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_export",
            "Export GIF ROI time outputs",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_gif_roi_export, "Exporting GIF ROI time outputs"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/export"},
        )
