from __future__ import annotations

import io
import re as _re2
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.matplotlib_utils import new_subplots


def build_gif_roi_kymograph_payload(
    data: dict[str, Any],
    *,
    helpers: dict[str, Any],
    resolve_output_dir: Callable[[object, object, str], Path],
) -> dict[str, Any]:
    fig_to_b64 = helpers["fig_to_b64"]
    float_or = helpers["float_or"]
    int_or = helpers["int_or"]

    _fl_bool = helpers["_fl_bool"]
    _fl_gif_kymo_stat = helpers["_fl_gif_kymo_stat"]
    _fl_gif_kymo_top_mean = helpers["_fl_gif_kymo_top_mean"]
    _fl_gif_roi_background_mean = helpers["_fl_gif_roi_background_mean"]
    _fl_gif_roi_make_specs = helpers["_fl_gif_roi_make_specs"]
    _fl_gif_roi_mask_for = helpers["_fl_gif_roi_mask_for"]
    _fl_parse_percent_list = helpers["_fl_parse_percent_list"]
    _fl_parse_slice_spec = helpers["_fl_parse_slice_spec"]
    _fl_percent_label = helpers["_fl_percent_label"]
    _fl_read_selected_gif_planes = helpers["_fl_read_selected_gif_planes"]
    _fl_smooth_heatmap_2d = helpers["_fl_smooth_heatmap_2d"]
    _fl_smooth_series_nan = helpers["_fl_smooth_series_nan"]
    _fl_tiff_gif_frame_count = helpers["_fl_tiff_gif_frame_count"]

    tiff_paths = data.get("tiff_paths") or []
    slice_specs = data.get("slice_specs") or []
    roi_specs = (
        _fl_gif_roi_make_specs([data.get("roi")], "ROI")
        if isinstance(data.get("roi"), dict)
        else []
    )
    bg_specs = (
        _fl_gif_roi_make_specs([data.get("bg_roi")], "BG")
        if isinstance(data.get("bg_roi"), dict)
        else []
    )
    roi = roi_specs[0] if roi_specs else None
    bg_roi = bg_specs[0] if bg_specs else None

    bg_mode = str(data.get("bg_mode", "none") or "none").strip()
    value_mode = str(data.get("value_mode", "delta_f_over_f0") or "delta_f_over_f0").strip()
    fps = max(0.1, float_or(data.get("fps", 5.0), 5.0))
    frame_interval_s = float_or(data.get("frame_interval_s"), None)
    if frame_interval_s is None or not np.isfinite(frame_interval_s) or frame_interval_s <= 0:
        frame_interval_s = 1.0 / fps
    ref_frame_raw = int_or(data.get("ref_frame", 1), 1)
    ref_stat = str(data.get("ref_stat", "median") or "median").strip().lower()
    bins = max(8, min(240, int_or(data.get("bins", 80), 80)))
    low_pct = max(0.0, min(49.0, float_or(data.get("range_low_pct", 1.0), 1.0)))
    high_pct = max(51.0, min(100.0, float_or(data.get("range_high_pct", 99.5), 99.5)))
    if high_pct <= low_pct:
        high_pct = min(100.0, low_pct + 1.0)
    range_min = float_or(data.get("range_min"), None)
    range_max = float_or(data.get("range_max"), None)
    smooth_intensity_bins = max(
        0.0, min(8.0, float_or(data.get("smooth_intensity_bins", 1.2), 1.2))
    )
    smooth_time_frames = max(0.0, min(8.0, float_or(data.get("smooth_time_frames", 0.8), 0.8)))
    smooth_lines = _fl_bool(data.get("smooth_lines", True), True)
    overlay_percentiles = _fl_parse_percent_list(
        data.get("overlay_percentiles", []),
        max_items=8,
        lower_exclusive=0.0,
        upper_inclusive=100.0,
    )
    overlay_top_means = _fl_parse_percent_list(
        data.get("overlay_top_means", []),
        max_items=6,
        lower_exclusive=0.0,
        upper_inclusive=100.0,
    )
    overlay_peak = _fl_bool(data.get("overlay_peak", False), False)
    overlay_mean = _fl_bool(data.get("overlay_mean", False), False)
    threshold_lines_raw = data.get("threshold_lines", [])
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
        raise ValueError("Select a background ROI or change background mode")
    if value_mode == "bg_subtracted" and bg_mode == "none":
        raise ValueError("Choose a background mode before using BG-subtracted kymography")

    if not isinstance(tiff_paths, list) or not tiff_paths:
        raise ValueError("tiff_paths must be a non-empty list")
    if roi is None:
        raise ValueError("Select one closed polygon ROI for kymography")

    paths = []
    for raw in tiff_paths:
        path = Path(str(raw or "").strip())
        if not path.exists():
            raise ValueError(f"TIFF not found: {raw}")
        paths.append(path)

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

        for order_idx, (source_idx, plane) in enumerate(zip(selected_indices, planes, strict=True)):
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
        raise ValueError("No valid frames found for kymography")

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
        for values in corrected_by_frame:
            finite = values[np.isfinite(values)]
            if finite.size:
                baseline_chunks.append(finite[:: max(1, finite.size // 5000)])
        if not baseline_chunks:
            raise ValueError("Selected ROI has no finite pixels")
        finite_sample = np.concatenate(baseline_chunks)
        data_span = (
            float(np.nanmax(finite_sample) - np.nanmin(finite_sample))
            if finite_sample.size
            else 0.0
        )
        eps = max(1e-12, abs(data_span) * 1e-9)
        if not np.isfinite(f0_value) or abs(f0_value) <= eps:
            raise ValueError(
                "Reference F0 is too close to zero for DeltaF/F0; use another ref frame/stat or BG-subtracted mode"
            )
        values_by_frame = [(values - f0_value) / f0_value for values in corrected_by_frame]

    sample_chunks = []
    for values in values_by_frame:
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            continue
        step = max(1, finite.size // 5000)
        sample_chunks.append(finite[::step])
    if not sample_chunks:
        raise ValueError("Selected ROI has no finite pixels")
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
        overlay_specs.append({"col": "mean", "label": "mean", "color": "#22d3ee", "lw": 1.35})
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

    for meta, values, bg_mean in zip(frame_meta, values_by_frame, bg_means, strict=True):
        finite = values[np.isfinite(values)]
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
                f"{col}_display" if smooth_lines and f"{col}_display" in summary_df.columns else col
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
    return {
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
