# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Split kymograph preview/export workflow helpers into services/fluorescence/
# and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
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

from .fluorescence_request_schemas import (
    FluorescenceGifRoiKymographExportRequest,
    FluorescenceGifRoiKymographRequest,
)
from .jobs import route_response_to_payload, submit_json_task
from .path_policy import resolve_output_dir
from .request_validation import parse_json_payload, request_schema, validation_error_response


def register_fluorescence_gif_kymograph_routes(app, fl):
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

    @app.route("/api/fluorescence/gif_roi/kymograph", methods=["POST"])
    @request_schema(FluorescenceGifRoiKymographRequest)
    def api_fl_gif_roi_kymograph(payload=None):
        """Build a time-vs-intensity distribution kymograph for one polygon ROI."""
        try:
            if payload is None:
                d = parse_json_payload(FluorescenceGifRoiKymographRequest).model_dump()
            else:
                d = FluorescenceGifRoiKymographRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        tiff_paths = d.get("tiff_paths") or []
        slice_specs = d.get("slice_specs") or []
        roi_specs = (
            _fl_gif_roi_make_specs([d.get("roi")], "ROI") if isinstance(d.get("roi"), dict) else []
        )
        bg_specs = (
            _fl_gif_roi_make_specs([d.get("bg_roi")], "BG")
            if isinstance(d.get("bg_roi"), dict)
            else []
        )
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
        smooth_intensity_bins = max(
            0.0, min(8.0, float_or(d.get("smooth_intensity_bins", 1.2), 1.2))
        )
        smooth_time_frames = max(0.0, min(8.0, float_or(d.get("smooth_time_frames", 0.8), 0.8)))
        smooth_lines = _fl_bool(d.get("smooth_lines", True), True)
        overlay_percentiles = _fl_parse_percent_list(
            d.get("overlay_percentiles", []),
            max_items=8,
            lower_exclusive=0.0,
            upper_inclusive=100.0,
        )
        overlay_top_means = _fl_parse_percent_list(
            d.get("overlay_top_means", []), max_items=6, lower_exclusive=0.0, upper_inclusive=100.0
        )
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

                    mask = _fl_gif_roi_mask_for(mask_cache, roi, shape)
                    vals_raw = (
                        np.asarray(img2d[mask], dtype=np.float64).ravel()
                        if mask.shape == img2d.shape
                        else np.asarray([], dtype=np.float64)
                    )
                    bg_mean = _fl_gif_roi_background_mean(img2d, bg_mode, bg_roi, mask_cache)
                    vals_corrected = (
                        vals_raw - float(bg_mean) if np.isfinite(bg_mean) else vals_raw.copy()
                    )

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
                data_span = (
                    float(np.nanmax(finite_sample) - np.nanmin(finite_sample))
                    if finite_sample.size
                    else 0.0
                )
                eps = max(1e-12, abs(data_span) * 1e-9)
                if not np.isfinite(f0_value) or abs(f0_value) <= eps:
                    return err(
                        "Reference F0 is too close to zero for DeltaF/F0; use another ref frame/stat or BG-subtracted mode"
                    )
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
            overlay_palette = [
                "#f8fafc",
                "#38bdf8",
                "#a3e635",
                "#fbbf24",
                "#fb7185",
                "#c084fc",
                "#2dd4bf",
                "#f472b6",
            ]
            if overlay_peak:
                overlay_specs.append(
                    {
                        "col": "display_peak_bin_intensity",
                        "label": "peak bin",
                        "color": "#ffffff",
                        "lw": 1.5,
                    }
                )
            if overlay_mean:
                overlay_specs.append(
                    {"col": "mean", "label": "mean", "color": "#22d3ee", "lw": 1.35}
                )
            for pct in overlay_percentiles:
                label = _fl_percent_label(pct)
                overlay_specs.append(
                    {
                        "col": f"p{label}",
                        "label": f"p{pct:g}",
                        "color": overlay_palette[(len(overlay_specs)) % len(overlay_palette)],
                        "lw": 1.3,
                    }
                )
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

            for meta, vals, bg_mean in zip(frame_meta, values_by_frame, bg_means, strict=True):
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
                        summary[f"p{_fl_percent_label(pct)}"] = float(
                            np.percentile(finite, float(pct))
                        )
                    for pct in overlay_top_means:
                        summary[f"top{_fl_percent_label(pct)}_mean"] = _fl_gif_kymo_top_mean(
                            finite, float(pct) / 100.0
                        )
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
            hist_display_arr = _fl_smooth_heatmap_2d(
                hist_pct_arr, smooth_intensity_bins, smooth_time_frames
            )
            if hist_display_arr.size and centers.size:
                display_peak_idx = np.argmax(hist_display_arr, axis=1)
                summary_df["display_peak_bin_intensity"] = [
                    float(centers[int(i)]) for i in display_peak_idx
                ]
                summary_df["display_peak_bin_fraction_pct"] = [
                    float(hist_display_arr[row_i, int(bin_i)])
                    for row_i, bin_i in enumerate(display_peak_idx)
                ]
            else:
                summary_df["display_peak_bin_intensity"] = np.nan
                summary_df["display_peak_bin_fraction_pct"] = np.nan
            if smooth_lines and smooth_time_frames > 0:
                smooth_cols = [
                    "mean",
                    "median",
                    "p90",
                    "p99",
                    "top5_mean",
                    "top1_mean",
                    "display_peak_bin_intensity",
                ]
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

            frame_axis = pd.to_numeric(summary_df["global_frame"], errors="coerce").to_numpy(
                dtype=float
            )
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
            fig, ax = new_subplots(figsize=(9.4, 4.8), dpi=140, constrained_layout=True)
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
                interpolation="bilinear"
                if (smooth_intensity_bins > 0 or smooth_time_frames > 0)
                else "nearest",
            )
            for spec in overlay_specs:
                col = spec["col"]
                if col in summary_df.columns:
                    plot_col = (
                        f"{col}_display"
                        if smooth_lines and f"{col}_display" in summary_df.columns
                        else col
                    )
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

            default_output_dir = (
                str(paths[0].parent)
                if paths
                else str(resolve_output_dir("", "", "fluorescence_gif_kymograph"))
            )
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
    @request_schema(FluorescenceGifRoiKymographRequest)
    def api_fl_gif_roi_kymograph_job():
        try:
            body = parse_json_payload(FluorescenceGifRoiKymographRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_kymograph",
            "Build GIF ROI kymograph",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_gif_roi_kymograph, "Building GIF ROI kymograph"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/kymograph"},
        )

    @app.route("/api/fluorescence/gif_roi/kymograph_export", methods=["POST"])
    @request_schema(FluorescenceGifRoiKymographExportRequest)
    def api_fl_gif_roi_kymograph_export(payload=None):
        """Save selected-ROI kymograph plot and data to disk."""
        try:
            if payload is None:
                d = parse_json_payload(FluorescenceGifRoiKymographExportRequest).model_dump()
            else:
                d = FluorescenceGifRoiKymographExportRequest.model_validate(payload).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
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
                    out_dir = (anchor / out_dir) if anchor is not None else resolve_output_dir("", out_dir, "fluorescence_gif_kymograph")
            else:
                out_dir = anchor or resolve_output_dir("", "", "fluorescence_gif_kymograph")
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
    @request_schema(FluorescenceGifRoiKymographExportRequest)
    def api_fl_gif_roi_kymograph_export_job():
        try:
            body = parse_json_payload(FluorescenceGifRoiKymographExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_kymograph_export",
            "Export GIF ROI kymograph outputs",
            lambda job_ctx, body: _response_task(
                job_ctx,
                body,
                api_fl_gif_roi_kymograph_export,
                "Exporting GIF ROI kymograph outputs",
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/kymograph_export"},
        )
