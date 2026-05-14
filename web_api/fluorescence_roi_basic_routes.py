from __future__ import annotations

import traceback
import io
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import jsonify
from pydantic import ValidationError

from .fluorescence_request_schemas import (
    FluorescenceRoiAnalyzeRequest,
    FluorescenceRoiBrowseRequest,
    FluorescenceRoiLoadStackRequest,
)
from .jobs import route_response_to_payload, submit_json_task
from .request_validation import parse_json_payload, request_schema, validation_error_response


def register_fluorescence_roi_basic_routes(app, fl):
    err = fl["err"]
    fig_to_b64 = fl["fig_to_b64"]
    float_or = fl["float_or"]
    int_or = fl["int_or"]
    has_pil = fl["has_pil"]
    has_tiff = fl["has_tiff"]
    jobs = fl["jobs"]
    tifflib = fl["tifflib"]

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

    @app.route("/api/fluorescence/roi/browse", methods=["POST"])
    @request_schema(FluorescenceRoiBrowseRequest)
    def api_fl_roi_browse():
        if not has_tiff:
            return err("tifffile not installed")
        try:
            folder = parse_json_payload(FluorescenceRoiBrowseRequest).folder
        except ValidationError as exc:
            return validation_error_response(exc)
        p = Path(folder)
        if not p.is_dir():
            return err(f"Not a directory: {folder}")
        pairs = _fl_roi_collect_pairs(p)
        return jsonify({"pairs": pairs})

    @app.route("/api/fluorescence/roi/load_stack", methods=["POST"])
    @request_schema(FluorescenceRoiLoadStackRequest)
    def api_fl_roi_load_stack():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        try:
            payload = parse_json_payload(FluorescenceRoiLoadStackRequest)
            stack_path = payload.stack_path
            frame_idx = int_or(payload.frame, 0)
            lut = payload.lut
            stack = tifflib.imread(stack_path)
            if stack.ndim == 2:
                stack = stack[np.newaxis, ...]
            n_frames, h, w = stack.shape[0], stack.shape[-2], stack.shape[-1]
            frame_idx = max(0, min(frame_idx, n_frames - 1))
            b64 = _fl_frame_to_b64(stack[frame_idx], lut, 1.0, 99.5)
            return jsonify(
                {
                    "img": b64,
                    "n_frames": n_frames,
                    "height": h,
                    "width": w,
                    "dtype": str(stack.dtype),
                    "frame": frame_idx,
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/analyze", methods=["POST"])
    @request_schema(FluorescenceRoiAnalyzeRequest)
    def api_fl_roi_analyze():
        if not has_tiff:
            return err("tifffile not installed")
        try:
            d = parse_json_payload(FluorescenceRoiAnalyzeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        stack1_path = d.get("stack1_path", "")
        stack2_path = d.get("stack2_path", "")
        rois = d.get("rois", [])
        metric = d.get("metric", "mean")
        frame_interval_s = float_or(d.get("frame_interval_s", 1.0), 1.0)
        bg_mode = d.get("bg_mode", "none")
        bg_roi = d.get("bg_roi", None)
        plot_metric = d.get("plot_metric", "absolute")
        roi_colors = {r["label"]: r.get("color", "#3E6AE1") for r in rois}

        if not stack1_path and not stack2_path:
            return err("No stack path provided")
        if not rois:
            return err("No ROIs defined")

        try:
            results_all = {}
            n_frames = 0
            if stack1_path and Path(stack1_path).exists():
                res1, n1 = _fl_roi_compute(stack1_path, rois, metric)
                n_frames = max(n_frames, n1)
                for k, v in res1.items():
                    results_all[f"{k} (S1)"] = v
            if stack2_path and Path(stack2_path).exists():
                res2, n2 = _fl_roi_compute(stack2_path, rois, metric)
                n_frames = max(n_frames, n2)
                for k, v in res2.items():
                    results_all[f"{k} (S2)"] = v

            bg_roi_to_use = None
            if bg_mode in ("corner_br", "corner_tl"):
                # Prefer caller-supplied dimensions (from load_stack response)
                # to avoid re-reading the TIFF just to get image size.
                img_w = int_or(d.get("img_width", 0), 0)
                img_h = int_or(d.get("img_height", 0), 0)
                if img_w <= 0 or img_h <= 0:
                    first_stack = (
                        stack1_path
                        if (stack1_path and Path(stack1_path).exists())
                        else stack2_path
                    )
                    if first_stack and Path(first_stack).exists():
                        with tifflib.TiffFile(first_stack) as tif:
                            page = tif.pages[0].asarray()
                        img_h, img_w = int(page.shape[-2]), int(page.shape[-1])
                if img_w > 0 and img_h > 0:
                    sz = max(4, min(40, img_h // 4, img_w // 4))
                    if bg_mode == "corner_br":
                        bg_roi_to_use = {"x1": img_w - sz, "y1": img_h - sz, "x2": img_w, "y2": img_h}
                    else:
                        bg_roi_to_use = {"x1": 0, "y1": 0, "x2": sz, "y2": sz}
            elif bg_mode == "roi" and bg_roi:
                bg_roi_to_use = bg_roi

            if bg_roi_to_use and plot_metric in ("bg_subtracted", "bg_normalized"):
                bg_rois = [{"label": "_BG", **bg_roi_to_use}]
                if stack1_path and Path(stack1_path).exists():
                    bg1, _ = _fl_roi_compute(stack1_path, bg_rois, metric)
                    bg1_vals = bg1["_BG"]
                    for k in list(results_all.keys()):
                        if "(S1)" in k:
                            if plot_metric == "bg_subtracted":
                                results_all[k] = [a - b for a, b in zip(results_all[k], bg1_vals)]
                            else:
                                results_all[k] = [a / b if b and b != 0 else float("nan") for a, b in zip(results_all[k], bg1_vals)]
                if stack2_path and Path(stack2_path).exists():
                    bg2, _ = _fl_roi_compute(stack2_path, bg_rois, metric)
                    bg2_vals = bg2["_BG"]
                    for k in list(results_all.keys()):
                        if "(S2)" in k:
                            if plot_metric == "bg_subtracted":
                                results_all[k] = [a - b for a, b in zip(results_all[k], bg2_vals)]
                            else:
                                results_all[k] = [a / b if b and b != 0 else float("nan") for a, b in zip(results_all[k], bg2_vals)]

            t_axis = np.arange(n_frames) * frame_interval_s

            metric_labels = {
                "mean": "Mean",
                "top20_mean": "Top-20% Mean",
                "sum": "Sum",
                "max": "Max",
                "std": "Std Dev",
            }
            presentation_labels = {
                "absolute": "Fluorescence",
                "bg_subtracted": "DeltaF (BG-subtracted)",
                "bg_normalized": "F / F_BG",
                "delta_f_over_f0": "DeltaF/F0",
            }
            y_label = f"{presentation_labels.get(plot_metric, 'Fluorescence')} ({metric_labels.get(metric, metric)})"

            fallback_colors = [
                "#1f77b4",
                "#ff7f0e",
                "#2ca02c",
                "#d62728",
                "#9467bd",
                "#8c564b",
                "#e377c2",
                "#17becf",
                "#bcbd22",
                "#7f7f7f",
            ]
            fig, ax = plt.subplots(figsize=(9, 4))
            for i, (label, vals) in enumerate(results_all.items()):
                base_label = label.replace(" (S1)", "").replace(" (S2)", "")
                color = roi_colors.get(base_label, fallback_colors[i % len(fallback_colors)])
                ls = "-" if "(S1)" in label or "(S2)" not in label else "--"
                ax.plot(t_axis, vals, color=color, lw=1.4, ls=ls, label=label)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel(y_label)
            ax.legend(fontsize=9, frameon=False)
            ax.grid(True, alpha=0.35)
            fig.tight_layout()
            img_b64 = fig_to_b64(fig)

            df_out = pd.DataFrame({"time_s": t_axis})
            for label, vals in results_all.items():
                df_out[label] = vals
            buf = io.StringIO()
            df_out.to_csv(buf, index=False)
            csv_str = buf.getvalue()

            return jsonify(
                {
                    "img": img_b64,
                    "n_frames": n_frames,
                    "metric": metric,
                    "plot_metric": plot_metric,
                    "csv": csv_str,
                    "traces": {k: v for k, v in results_all.items()},
                }
            )
        except Exception:
            return err(traceback.format_exc())
