# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move ROI sequence analysis plotting/export assembly into fluorescence ROI
# services and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
from __future__ import annotations

import io
import re as _re2
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from flask import jsonify
from pydantic import ValidationError

from services.matplotlib_utils import new_subplots

from .fluorescence_request_schemas import FluorescenceRoiAnalyzeSequenceRequest
from .jobs import route_response_to_payload
from .request_validation import parse_json_payload, request_schema, validation_error_response


def register_fluorescence_roi_sequence_routes(app, fl):
    err = fl["err"]
    fig_to_b64 = fl["fig_to_b64"]
    float_or = fl["float_or"]
    int_or = fl["int_or"]
    has_tiff = fl["has_tiff"]

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

    @app.route("/api/fluorescence/roi/analyze_sequence", methods=["POST"])
    @request_schema(FluorescenceRoiAnalyzeSequenceRequest)
    def api_fl_roi_analyze_sequence():
        """Sequence-style ROI analysis across selected stack pairs."""
        if not has_tiff:
            return err("tifffile not installed")

        try:
            d = parse_json_payload(FluorescenceRoiAnalyzeSequenceRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        records = d.get("records", [])
        rois = d.get("rois", [])
        metric = d.get("metric", "mean")
        plot_metric = d.get("plot_metric", "bg_normalized")
        bg_mode = d.get("bg_mode", "none")
        bg_roi = d.get("bg_roi", None)
        ref_sequence_raw = str(d.get("ref_sequence", "") or "").strip()
        preview_path_raw = str(d.get("preview_path", "") or "").strip()
        preview_stack = str(d.get("preview_stack", "stack1") or "stack1").strip().lower()
        scale_bar_um = max(0.0, float_or(d.get("scale_bar_um", 0.0), 0.0))
        pixel_size_um_override = float_or(d.get("pixel_size_um"), None)
        if pixel_size_um_override is not None and (
            not np.isfinite(pixel_size_um_override) or pixel_size_um_override <= 0
        ):
            pixel_size_um_override = None
        show_preview_name = _fl_bool(d.get("show_preview_name", True), True)
        show_scale_bar = _fl_bool(d.get("show_scale_bar", True), True)
        scale_bar_label = str(d.get("scale_bar_label", "") or "").strip()
        label_scale = float_or(d.get("label_scale", 2.0), 2.0)
        label_scale = max(0.5, min(4.0, label_scale))

        valid_metrics = {"mean", "top20_mean", "sum", "max", "std"}
        valid_plot_metrics = {"absolute", "bg_subtracted", "bg_normalized", "delta_f_over_f0"}
        if metric not in valid_metrics:
            metric = "mean"
        if plot_metric not in valid_plot_metrics:
            plot_metric = "bg_normalized"

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
                            if ring_width_um is not None
                            and np.isfinite(ring_width_um)
                            and ring_width_um > 0
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
                    m1 = (
                        _fl_roi_metrics_2d(img1, roi)
                        if img1 is not None
                        else _fl_roi_empty_metrics()
                    )
                    m2 = (
                        _fl_roi_metrics_2d(img2, roi)
                        if img2 is not None
                        else _fl_roi_empty_metrics()
                    )

                    raw1 = float(m1.get(metric, np.nan))
                    raw2 = float(m2.get(metric, np.nan))
                    val1 = _fl_roi_apply_metric_mode(
                        raw1, m1.get("area_px", 0), metric, bg1, plot_metric
                    )
                    val2 = _fl_roi_apply_metric_mode(
                        raw2, m2.get("area_px", 0), metric, bg2, plot_metric
                    )
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
                            scale_path = (
                                stack1_path
                                if stack1_path and Path(stack1_path).exists()
                                else stack2_path
                            )
                            if scale_path:
                                if scale_path not in pixel_size_cache:
                                    pixel_size_cache[scale_path] = (
                                        _fl_infer_pixel_size_um_from_tiff(scale_path)
                                    )
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
                        df_out[cr] = [
                            _fl_roi_safe_ratio(a, b) for a, b in zip(y1n, y2n, strict=True)
                        ]

            if plot_metric != "delta_f_over_f0" and ref_idx is not None:
                for roi in roi_specs:
                    key = roi["key"]
                    for col in [f"stack1_value_{key}", f"stack2_value_{key}", f"ratio_{key}"]:
                        if col in df_out.columns:
                            arr = pd.to_numeric(df_out[col], errors="coerce").to_numpy(dtype=float)
                            df_out[col] = _fl_roi_normalize_to_reference(arr, ref_idx)

                seq_vals_for_ref = pd.to_numeric(
                    df_out["sequence_number"], errors="coerce"
                ).to_numpy(dtype=float)
                if 0 <= ref_idx < len(seq_vals_for_ref) and np.isfinite(seq_vals_for_ref[ref_idx]):
                    sv = float(seq_vals_for_ref[ref_idx])
                    ref_sequence_applied = (
                        str(int(round(sv))) if abs(sv - round(sv)) < 1e-9 else f"{sv:g}"
                    )
                else:
                    ref_sequence_applied = str(ref_idx)

            if ref_idx is not None and not ref_sequence_applied:
                seq_vals_for_ref = pd.to_numeric(
                    df_out["sequence_number"], errors="coerce"
                ).to_numpy(dtype=float)
                if 0 <= ref_idx < len(seq_vals_for_ref) and np.isfinite(seq_vals_for_ref[ref_idx]):
                    sv = float(seq_vals_for_ref[ref_idx])
                    ref_sequence_applied = (
                        str(int(round(sv))) if abs(sv - round(sv)) < 1e-9 else f"{sv:g}"
                    )
                else:
                    ref_sequence_applied = str(ref_idx)

            radial_df = pd.DataFrame(radial_rows) if radial_rows else pd.DataFrame()
            if not radial_df.empty:
                order_map = {
                    str(base): i for i, base in enumerate(df_out["base_name"].astype(str).tolist())
                }
                radial_df["_sort_idx"] = (
                    radial_df["base_name"]
                    .astype(str)
                    .map(order_map)
                    .fillna(len(order_map))
                    .astype(int)
                )
                radial_inner_um = pd.to_numeric(
                    radial_df.get("inner_radius_um", np.nan), errors="coerce"
                )
                radial_outer_um = pd.to_numeric(
                    radial_df.get("outer_radius_um", np.nan), errors="coerce"
                )
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

                    y1r = pd.to_numeric(
                        radial_df.loc[idxs, "stack1_value"], errors="coerce"
                    ).to_numpy(dtype=float)
                    y2r = pd.to_numeric(
                        radial_df.loc[idxs, "stack2_value"], errors="coerce"
                    ).to_numpy(dtype=float)
                    radial_df.loc[idxs, "ratio"] = [
                        _fl_roi_safe_ratio(a, b) for a, b in zip(y1r, y2r, strict=True)
                    ]
                    radial_df.loc[idxs, "difference"] = [
                        a - b if np.isfinite(a) and np.isfinite(b) else np.nan
                        for a, b in zip(y1r, y2r, strict=True)
                    ]
                radial_df = radial_df.drop(
                    columns=["_sort_idx", "_ring_inner_key", "_ring_outer_key"]
                )

            x = np.arange(len(df_out))
            seq_vals = pd.to_numeric(df_out["sequence_number"], errors="coerce").to_numpy(
                dtype=float
            )
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

            fig, axes = new_subplots(2, 2, figsize=(11.0, 7.4), dpi=120)
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
                        cands = [
                            str(rec.get("stack2", "") or "").strip(),
                            str(rec.get("stack1", "") or "").strip(),
                        ]
                    else:
                        cands = [
                            str(rec.get("stack1", "") or "").strip(),
                            str(rec.get("stack2", "") or "").strip(),
                        ]
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
