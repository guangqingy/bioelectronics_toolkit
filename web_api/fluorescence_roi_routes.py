from __future__ import annotations

import io
import re as _re2
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import jsonify, request

from .jobs import route_response_to_payload, submit_json_task


def register_fluorescence_roi_routes(app, fl):
    # Transitional adapter: ROI helpers still live in fluorescence.py while route
    # bodies are moved into this focused registration module. The next refactor
    # should promote those helpers into services/fluorescence/roi.py.
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
    def api_fl_roi_browse():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        folder = d.get("folder", "")
        p = Path(folder)
        if not p.is_dir():
            return err(f"Not a directory: {folder}")
        pairs = _fl_roi_collect_pairs(p)
        return jsonify({"pairs": pairs})

    @app.route("/api/fluorescence/roi/load_stack", methods=["POST"])
    def api_fl_roi_load_stack():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        stack_path = d.get("stack_path", "")
        frame_idx = int_or(d.get("frame", 0), 0)
        lut = d.get("lut", "Gray")
        try:
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
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/analyze", methods=["POST"])
    def api_fl_roi_analyze():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
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

    @app.route("/api/fluorescence/roi/analyze_sequence", methods=["POST"])
    def api_fl_roi_analyze_sequence():
        """Sequence-style ROI analysis across selected stack pairs."""
        if not has_tiff:
            return err("tifffile not installed")

        d = request.json or {}
        records = d.get("records", [])
        rois = d.get("rois", [])
        metric = d.get("metric", "mean")
        plot_metric = d.get("plot_metric", "absolute")
        bg_mode = d.get("bg_mode", "none")
        bg_roi = d.get("bg_roi", None)
        ref_sequence_raw = str(d.get("ref_sequence", "") or "").strip()
        preview_path_raw = str(d.get("preview_path", "") or "").strip()
        preview_stack = str(d.get("preview_stack", "stack1") or "stack1").strip().lower()
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 0.0), 0.0))
        pixel_size_um_override = float_or(d.get("pixel_size_um"), None)
        if pixel_size_um_override is not None and (not np.isfinite(pixel_size_um_override) or pixel_size_um_override <= 0):
            pixel_size_um_override = None
        show_preview_name = _fl_bool(d.get("show_preview_name", True), True)
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        scale_bar_label = str(d.get("scale_bar_label", "") or "").strip()
        label_scale = float_or(d.get("label_scale", 1.0), 1.0)
        label_scale = max(0.5, min(4.0, label_scale))

        valid_metrics = {"mean", "top20_mean", "sum", "max", "std"}
        valid_plot_metrics = {"absolute", "bg_subtracted", "bg_normalized", "delta_f_over_f0"}
        if metric not in valid_metrics:
            metric = "mean"
        if plot_metric not in valid_plot_metrics:
            plot_metric = "absolute"

        if not isinstance(records, list) or not records:
            return err("No records selected")
        if not isinstance(rois, list) or not rois:
            return err("No ROIs defined")

        try:
            roi_specs = []
            used_keys = set()
            for idx, r in enumerate(rois):
                label = str(r.get("label", f"ROI {idx + 1}")).strip() or f"ROI {idx + 1}"
                key = _re2.sub(r"[^a-zA-Z0-9]+", "_", label.lower()).strip("_")
                if not key:
                    key = f"roi_{idx + 1}"
                if key in used_keys:
                    suffix = 2
                    while f"{key}_{suffix}" in used_keys:
                        suffix += 1
                    key = f"{key}_{suffix}"
                used_keys.add(key)
                shape_type = _fl_roi_shape_type(r)
                spec = {
                    "label": label,
                    "key": key,
                    "color": r.get("color", "#3E6AE1"),
                    "type": shape_type,
                    "x1": int_or(r.get("x1", 0), 0),
                    "y1": int_or(r.get("y1", 0), 0),
                    "x2": int_or(r.get("x2", 0), 0),
                    "y2": int_or(r.get("y2", 0), 0),
                }
                if shape_type == "concentric":
                    cx, cy, radius, x1, y1, x2, y2, ring_width = _fl_roi_circle_geometry(r)
                    ring_width_um = float_or(r.get("ring_width_um"), None)
                    ring_count = _fl_roi_ring_count(r)
                    spec.update(
                        {
                            "cx": cx,
                            "cy": cy,
                            "radius": radius,
                            "ring_width_px": ring_width,
                            "ring_width_um": ring_width_um
                            if ring_width_um is not None and np.isfinite(ring_width_um) and ring_width_um > 0
                            else None,
                            "ring_count": ring_count,
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                        }
                    )
                roi_specs.append(spec)

            rows = []
            radial_rows = []
            pixel_size_cache = {}
            for ridx, rec in enumerate(records):
                if not isinstance(rec, dict):
                    continue

                stack1_path = str(rec.get("stack1", "") or "").strip()
                stack2_path = str(rec.get("stack2", "") or "").strip()
                base_name = str(rec.get("base", "") or "").strip()
                if not base_name:
                    src = stack1_path or stack2_path
                    base_name = Path(src).stem if src else f"record_{ridx + 1}"

                img1 = None
                img2 = None
                if stack1_path and Path(stack1_path).exists():
                    img1 = _fl_roi_read_first_page(stack1_path)
                if stack2_path and Path(stack2_path).exists():
                    img2 = _fl_roi_read_first_page(stack2_path)
                if img1 is None and img2 is None:
                    continue

                bg1 = _fl_roi_background_mean(img1, bg_mode, bg_roi) if img1 is not None else np.nan
                bg2 = _fl_roi_background_mean(img2, bg_mode, bg_roi) if img2 is not None else np.nan

                sequence_number = _fl_roi_sequence_number(base_name)
                row = {
                    "base_name": base_name,
                    "sequence_number": sequence_number,
                    "stack1_path": stack1_path,
                    "stack2_path": stack2_path,
                    "background_mode": bg_mode,
                    "stack1_bg_mean": float(bg1) if np.isfinite(bg1) else np.nan,
                    "stack2_bg_mean": float(bg2) if np.isfinite(bg2) else np.nan,
                }

                for roi in roi_specs:
                    m1 = _fl_roi_metrics_2d(img1, roi) if img1 is not None else _fl_roi_empty_metrics()
                    m2 = _fl_roi_metrics_2d(img2, roi) if img2 is not None else _fl_roi_empty_metrics()

                    raw1 = float(m1.get(metric, np.nan))
                    raw2 = float(m2.get(metric, np.nan))
                    val1 = _fl_roi_apply_metric_mode(raw1, m1.get("area_px", 0), metric, bg1, plot_metric)
                    val2 = _fl_roi_apply_metric_mode(raw2, m2.get("area_px", 0), metric, bg2, plot_metric)
                    ratio = _fl_roi_safe_ratio(val1, val2)

                    key = roi["key"]
                    row[f"stack1_raw_{metric}_{key}"] = raw1
                    row[f"stack2_raw_{metric}_{key}"] = raw2
                    row[f"stack1_value_{key}"] = val1
                    row[f"stack2_value_{key}"] = val2
                    row[f"ratio_{key}"] = ratio

                    if roi.get("type") == "concentric":
                        radial_pixel_size_um = pixel_size_um_override
                        if radial_pixel_size_um is None:
                            scale_path = stack1_path if stack1_path and Path(stack1_path).exists() else stack2_path
                            if scale_path:
                                if scale_path not in pixel_size_cache:
                                    pixel_size_cache[scale_path] = _fl_infer_pixel_size_um_from_tiff(scale_path)
                                radial_pixel_size_um = pixel_size_cache.get(scale_path)
                        radial_rows.extend(
                            _fl_roi_radial_pair_rows(
                                img1,
                                img2,
                                roi,
                                metric,
                                bg1,
                                bg2,
                                plot_metric,
                                radial_pixel_size_um,
                                base_name,
                                sequence_number,
                            )
                        )

                rows.append(row)

            if not rows:
                return err("No valid data found in selected records")

            df_out = pd.DataFrame(rows)

            seq_num = pd.to_numeric(df_out["sequence_number"], errors="coerce")
            df_out["_sort_seq"] = np.where(np.isfinite(seq_num), seq_num, np.inf)
            df_out["_sort_idx"] = np.arange(len(df_out))
            df_out = df_out.sort_values(["_sort_seq", "_sort_idx"], kind="stable")
            df_out = df_out.drop(columns=["_sort_seq", "_sort_idx"])

            ref_idx = _fl_roi_resolve_ref_index(df_out, ref_sequence_raw)
            ref_sequence_applied = ""
            if plot_metric == "delta_f_over_f0":
                for roi in roi_specs:
                    key = roi["key"]
                    c1 = f"stack1_value_{key}"
                    c2 = f"stack2_value_{key}"
                    cr = f"ratio_{key}"
                    if c1 in df_out.columns:
                        y1 = pd.to_numeric(df_out[c1], errors="coerce").to_numpy(dtype=float)
                        df_out[c1] = _fl_roi_delta_f_over_f0(y1, ref_idx)
                    if c2 in df_out.columns:
                        y2 = pd.to_numeric(df_out[c2], errors="coerce").to_numpy(dtype=float)
                        df_out[c2] = _fl_roi_delta_f_over_f0(y2, ref_idx)
                    if c1 in df_out.columns and c2 in df_out.columns:
                        y1n = pd.to_numeric(df_out[c1], errors="coerce").to_numpy(dtype=float)
                        y2n = pd.to_numeric(df_out[c2], errors="coerce").to_numpy(dtype=float)
                        df_out[cr] = [_fl_roi_safe_ratio(a, b) for a, b in zip(y1n, y2n)]

            if plot_metric != "delta_f_over_f0" and ref_idx is not None:
                for roi in roi_specs:
                    key = roi["key"]
                    for col in [f"stack1_value_{key}", f"stack2_value_{key}", f"ratio_{key}"]:
                        if col in df_out.columns:
                            arr = pd.to_numeric(df_out[col], errors="coerce").to_numpy(dtype=float)
                            df_out[col] = _fl_roi_normalize_to_reference(arr, ref_idx)

                seq_vals_for_ref = pd.to_numeric(df_out["sequence_number"], errors="coerce").to_numpy(dtype=float)
                if 0 <= ref_idx < len(seq_vals_for_ref) and np.isfinite(seq_vals_for_ref[ref_idx]):
                    sv = float(seq_vals_for_ref[ref_idx])
                    ref_sequence_applied = str(int(round(sv))) if abs(sv - round(sv)) < 1e-9 else f"{sv:g}"
                else:
                    ref_sequence_applied = str(ref_idx)

            if ref_idx is not None and not ref_sequence_applied:
                seq_vals_for_ref = pd.to_numeric(df_out["sequence_number"], errors="coerce").to_numpy(dtype=float)
                if 0 <= ref_idx < len(seq_vals_for_ref) and np.isfinite(seq_vals_for_ref[ref_idx]):
                    sv = float(seq_vals_for_ref[ref_idx])
                    ref_sequence_applied = str(int(round(sv))) if abs(sv - round(sv)) < 1e-9 else f"{sv:g}"
                else:
                    ref_sequence_applied = str(ref_idx)

            radial_df = pd.DataFrame(radial_rows) if radial_rows else pd.DataFrame()
            if not radial_df.empty:
                order_map = {str(base): i for i, base in enumerate(df_out["base_name"].astype(str).tolist())}
                radial_df["_sort_idx"] = radial_df["base_name"].astype(str).map(order_map).fillna(len(order_map)).astype(int)
                radial_inner_um = pd.to_numeric(radial_df.get("inner_radius_um", np.nan), errors="coerce")
                radial_outer_um = pd.to_numeric(radial_df.get("outer_radius_um", np.nan), errors="coerce")
                radial_df["_ring_inner_key"] = np.where(
                    radial_inner_um.notna(),
                    radial_inner_um,
                    pd.to_numeric(radial_df["inner_radius_px"], errors="coerce"),
                )
                radial_df["_ring_outer_key"] = np.where(
                    radial_outer_um.notna(),
                    radial_outer_um,
                    pd.to_numeric(radial_df["outer_radius_px"], errors="coerce"),
                )
                radial_df = radial_df.sort_values(
                    ["roi_key", "_ring_inner_key", "_sort_idx"],
                    kind="stable",
                )
                ref_base = ""
                if ref_idx is not None and 0 <= ref_idx < len(df_out):
                    ref_base = str(df_out.iloc[ref_idx].get("base_name", ""))

                for (_roi_key, _inner, _outer), grp in radial_df.groupby(
                    ["roi_key", "_ring_inner_key", "_ring_outer_key"],
                    sort=False,
                ):
                    idxs = grp.index
                    ref_pos = None
                    if ref_base:
                        hits = np.where(grp["base_name"].astype(str).to_numpy() == ref_base)[0]
                        if hits.size > 0:
                            ref_pos = int(hits[0])
                    if plot_metric == "delta_f_over_f0":
                        for col in ["stack1_value", "stack2_value"]:
                            arr = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
                            radial_df.loc[idxs, col] = _fl_roi_delta_f_over_f0(arr, ref_pos)
                    elif ref_idx is not None and ref_pos is not None:
                        for col in ["stack1_value", "stack2_value", "ratio"]:
                            arr = pd.to_numeric(grp[col], errors="coerce").to_numpy(dtype=float)
                            radial_df.loc[idxs, col] = _fl_roi_normalize_to_reference(arr, ref_pos)

                    y1r = pd.to_numeric(radial_df.loc[idxs, "stack1_value"], errors="coerce").to_numpy(dtype=float)
                    y2r = pd.to_numeric(radial_df.loc[idxs, "stack2_value"], errors="coerce").to_numpy(dtype=float)
                    radial_df.loc[idxs, "ratio"] = [_fl_roi_safe_ratio(a, b) for a, b in zip(y1r, y2r)]
                    radial_df.loc[idxs, "difference"] = [
                        a - b if np.isfinite(a) and np.isfinite(b) else np.nan
                        for a, b in zip(y1r, y2r)
                    ]
                radial_df = radial_df.drop(columns=["_sort_idx", "_ring_inner_key", "_ring_outer_key"])

            x = np.arange(len(df_out))
            seq_vals = pd.to_numeric(df_out["sequence_number"], errors="coerce").to_numpy(dtype=float)
            x_labels = []
            for i, sv in enumerate(seq_vals):
                if np.isfinite(sv):
                    if abs(sv - round(sv)) < 1e-9:
                        x_labels.append(str(int(round(sv))))
                    else:
                        x_labels.append(f"{sv:g}")
                else:
                    x_labels.append(str(i + 1))

            metric_labels = {
                "mean": "Mean",
                "top20_mean": "Top20 Mean",
                "sum": "Sum",
                "max": "Max",
                "std": "Std",
            }
            presentation_labels = {
                "absolute": "Absolute",
                "bg_subtracted": "BG-subtracted",
                "bg_normalized": "BG-normalized",
                "delta_f_over_f0": "DeltaF/F0",
            }
            signal_ylabel = f"{metric_labels.get(metric, metric)} ({presentation_labels.get(plot_metric, plot_metric)})"
            if ref_idx is not None:
                signal_ylabel += " (Ref=1)"

            fig, axes = plt.subplots(2, 2, figsize=(11.0, 7.4), dpi=120)
            ax1, ax2, ax3, ax4 = axes.ravel()
            stack1_plot_values = []
            stack2_plot_values = []

            for roi in roi_specs:
                key = roi["key"]
                label = roi["label"]
                col = roi["color"]
                c1 = f"stack1_value_{key}"
                c2 = f"stack2_value_{key}"
                cr = f"ratio_{key}"
                if c1 not in df_out.columns or c2 not in df_out.columns or cr not in df_out.columns:
                    continue
                y1 = pd.to_numeric(df_out[c1], errors="coerce").to_numpy(dtype=float)
                y2 = pd.to_numeric(df_out[c2], errors="coerce").to_numpy(dtype=float)
                yr = pd.to_numeric(df_out[cr], errors="coerce").to_numpy(dtype=float)
                yd = y1 - y2

                ax1.plot(x, y1, marker="o", lw=1.3, color=col, label=label)
                ax2.plot(x, y2, marker="o", lw=1.3, color=col, label=label)
                ax3.plot(x, yr, marker="o", lw=1.3, color=col, label=label)
                ax4.plot(x, yd, marker="o", lw=1.3, color=col, label=label)
                stack1_plot_values.append(y1)
                stack2_plot_values.append(y2)

            ax1.set_title("Stack1 by ROI")
            ax2.set_title("Stack2 by ROI")
            ax3.set_title("Stack1 / Stack2 by ROI")
            ax4.set_title("Stack1 - Stack2 by ROI")

            ax1.set_ylabel(signal_ylabel)
            ax2.set_ylabel(signal_ylabel)
            ax3.set_ylabel("Ratio" + (" (Ref=1)" if ref_idx is not None else ""))
            ax4.set_ylabel(f"{signal_ylabel} difference")

            ax3.axhline(1.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            ax4.axhline(0.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            stack_ylim = _fl_roi_shared_ylim(*stack1_plot_values, *stack2_plot_values)
            if stack_ylim is not None:
                ax1.set_ylim(stack_ylim)
                ax2.set_ylim(stack_ylim)

            for ax in [ax1, ax2, ax3, ax4]:
                ax.set_xlabel("Sequence")
                ax.set_xticks(x)
                ax.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=8)
                ax.grid(True, alpha=0.35)
                ax.tick_params(axis="y", labelsize=8)
                ax.legend(fontsize=8, frameon=False, loc="best")

            fig.tight_layout()
            img_b64 = fig_to_b64(fig)

            radial_img_b64 = ""
            radial_csv_str = ""
            if not radial_df.empty:
                radial_img_b64 = _fl_roi_plot_radial_profiles(
                    radial_df,
                    roi_specs,
                    metric,
                    plot_metric,
                    ref_idx is not None,
                )
                radial_buf = io.StringIO()
                radial_df.to_csv(radial_buf, index=False)
                radial_csv_str = radial_buf.getvalue()

            preview_path = preview_path_raw
            if (not preview_path or not Path(preview_path).exists()) and isinstance(records, list):
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    if preview_stack == "stack2":
                        cands = [str(rec.get("stack2", "") or "").strip(), str(rec.get("stack1", "") or "").strip()]
                    else:
                        cands = [str(rec.get("stack1", "") or "").strip(), str(rec.get("stack2", "") or "").strip()]
                    chosen = next((p for p in cands if p and Path(p).exists()), "")
                    if chosen:
                        preview_path = chosen
                        break

            preview_pack = _fl_roi_render_reference_preview(
                preview_path=preview_path,
                roi_specs=roi_specs,
                show_name=show_preview_name,
                show_scale_bar=show_scale_bar,
                scale_bar_um=scale_bar_um,
                scale_label=scale_bar_label,
                pixel_size_um_override=pixel_size_um_override,
                label_scale=label_scale,
            )
            default_output_dir = str(_fl_roi_pick_output_dir(records, ""))

            buf = io.StringIO()
            df_out.to_csv(buf, index=False)
            csv_str = buf.getvalue()

            return jsonify(
                {
                    "img": img_b64,
                    "csv": csv_str,
                    "n_records": int(len(df_out)),
                    "n_rois": int(len(roi_specs)),
                    "metric": metric,
                    "plot_metric": plot_metric,
                    "ref_sequence": ref_sequence_raw,
                    "ref_sequence_applied": ref_sequence_applied,
                    "columns": list(df_out.columns),
                    "radial_img": radial_img_b64,
                    "radial_csv": radial_csv_str,
                    "n_radial_rows": int(len(radial_df)) if not radial_df.empty else 0,
                    "roi_preview_img": preview_pack.get("img", ""),
                    "roi_preview_path": preview_pack.get("path", ""),
                    "roi_preview_pixel_size_um": preview_pack.get("pixel_size_um", None),
                    "default_output_dir": default_output_dir,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/export_sequence", methods=["POST"])
    def api_fl_roi_export_sequence(payload=None):
        """Save ROI sequence analysis outputs to disk (CSV/plot/ROI preview)."""
        d = (request.json or {}) if payload is None else payload
        records = d.get("records", [])
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "roi_sequence_analysis")

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

            saved_paths = [p for p in [csv_path, plot_path, preview_path, radial_csv_path, radial_plot_path] if p]
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
    def api_fl_roi_export_sequence_job():
        return submit_json_task(
            jobs,
            "fluorescence.roi_export_sequence",
            "Export ROI sequence outputs",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_roi_export_sequence, "Exporting ROI sequence outputs"
            ),
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/roi/export_sequence"},
        )

    @app.route("/api/fluorescence/roi/export_sequence_gif", methods=["POST"])
    def api_fl_roi_export_sequence_gif(payload=None):
        """Save a sequence GIF from selected ROI records with ROI overlays and scale bar."""
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")

        d = (request.json or {}) if payload is None else payload
        records = d.get("records", [])
        rois = d.get("rois", [])
        preview_stack = str(d.get("preview_stack", "stack1") or "stack1").strip().lower()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        prefix = _fl_sanitize_prefix(d.get("prefix", ""), "roi_sequence_analysis")

        frame_ms = int_or(d.get("frame_ms", 2000), 2000)
        frame_ms = max(20, frame_ms)
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 0.0), 0.0))
        pixel_size_um_override = float_or(d.get("pixel_size_um"), None)
        if pixel_size_um_override is not None and (not np.isfinite(pixel_size_um_override) or pixel_size_um_override <= 0):
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
                    path = s2 if s2 and Path(s2).exists() else (s1 if s1 and Path(s1).exists() else "")
                else:
                    path = s1 if s1 and Path(s1).exists() else (s2 if s2 and Path(s2).exists() else "")
                if path:
                    frame_paths.append(path)
                    frame_names.append(base)

            if not frame_paths:
                return err("No valid stack paths found for GIF export")

            pixel_size_um = pixel_size_um_override
            if pixel_size_um is None:
                pixel_size_um = _fl_infer_pixel_size_um_from_tiff(frame_paths[0])

            frames = []
            for p, name in zip(frame_paths, frame_names):
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
                    "pixel_size_um": float(pixel_size_um) if pixel_size_um is not None and np.isfinite(pixel_size_um) else None,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/roi/export_sequence_gif_job", methods=["POST"])
    def api_fl_roi_export_sequence_gif_job():
        return submit_json_task(
            jobs,
            "fluorescence.roi_export_sequence_gif",
            "Export ROI sequence GIF",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_roi_export_sequence_gif, "Exporting ROI sequence GIF"
            ),
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/roi/export_sequence_gif"},
        )
