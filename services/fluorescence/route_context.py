from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import colormaps

from services.fluorescence import gif as fl_gif
from services.fluorescence import gif_roi_context as fl_gif_roi_context
from services.fluorescence import roi as fl_roi
from services.fluorescence import roi_render_context as fl_roi_render_context
from services.fluorescence import route_helpers as fl_helpers
from services.fluorescence import stack as fl_stack
from services.fluorescence import tiff_volume_context as fl_tiff_volume_context
from services.matplotlib_utils import new_subplots


def build_fluorescence_route_contexts(ctx) -> dict[str, dict]:
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]

    has_tiff = ctx["HAS_TIFF"]
    has_pil = ctx["HAS_PIL"]
    tifflib = ctx.get("tifflib")
    image_mod = ctx.get("Image")
    image_draw_mod = ctx.get("ImageDraw")
    image_font_mod = ctx.get("ImageFont")
    jobs = ctx.get("jobs")

    _fl_apply_lut = fl_helpers.apply_lut
    _fl_select_display_frame = fl_helpers.select_display_frame
    _fl_bool = fl_helpers.parse_bool
    _fl_sanitize_prefix = fl_helpers.sanitize_prefix
    _fl_unit_to_um_scale = fl_helpers.unit_to_um_scale
    _fl_normalize_hex_color = fl_helpers.normalize_hex_color
    _fl_normalize_display_2d = fl_helpers.normalize_display_2d
    _fl_decode_base64_payload = fl_helpers.decode_base64_payload

    def _fl_frame_to_b64(frame: np.ndarray, lut: str, p_low: float, p_high: float) -> str:
        return fl_helpers.frame_to_b64(frame, lut, p_low, p_high, image_mod)

    def _fl_infer_pixel_size_um_from_tiff(path: str) -> float | None:
        return fl_helpers.infer_pixel_size_um_from_tiff(path, has_tiff=has_tiff, tifflib=tifflib)

    _FL_LUT_OPTIONS = ["Red", "Blue", "Gray", "Green", "Magenta", "Cyan", "Yellow"]
    _FL_DENOISE_OPTIONS = ["Off", "Light", "Medium", "Strong"]
    _FL_BACKGROUND_OPTIONS = ["Off", "Light", "Medium", "Strong"]
    _FL_DEFAULT_LUT_BY_INDEX = {0: "Red", 1: "Blue", 2: "Gray"}
    _FL_DEFAULT_DENOISE_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}
    _FL_DEFAULT_BACKGROUND_BY_INDEX = {0: "Off", 1: "Off", 2: "Off"}

    def _fl_read_tiff_as_pages(tiff_path: Path) -> list[np.ndarray]:
        return fl_stack.read_tiff_as_pages(tiff_path, tifflib)

    def _fl_prepare_gif_plane(raw: np.ndarray) -> np.ndarray:
        return fl_gif.prepare_plane(raw)

    def _fl_split_tiff_array_to_gif_planes(arr: np.ndarray) -> list[np.ndarray]:
        return fl_gif.split_tiff_array_to_planes(arr)

    def _fl_tiff_gif_frame_count(tiff_path: Path) -> tuple[int, list[int]]:
        return fl_gif.tiff_frame_count(tiff_path, tifflib)

    _fl_tiff_helpers = fl_tiff_volume_context.build_tiff_volume_context(
        tifflib=tifflib,
        has_tiff=has_tiff,
        int_or=int_or,
        float_or=float_or,
        denoise_options=_FL_DENOISE_OPTIONS,
    )
    _fl_tiff_plane_from_array = _fl_tiff_helpers["_fl_tiff_plane_from_array"]
    _fl_tiff_read_array = _fl_tiff_helpers["_fl_tiff_read_array"]
    _fl_tiff_series_info = _fl_tiff_helpers["_fl_tiff_series_info"]
    _fl_tiff_volume3d_payload = _fl_tiff_helpers["_fl_tiff_volume3d_payload"]
    _fl_volume3d_html = _fl_tiff_helpers["_fl_volume3d_html"]
    _fl_resolve_gif_scale = _fl_tiff_helpers["_fl_resolve_gif_scale"]

    def _fl_parse_slice_spec(slice_spec: object, n_frames: int) -> list[int]:
        return fl_gif.parse_slice_spec(slice_spec, n_frames)

    def _fl_read_selected_gif_planes(tiff_path: Path, indices: list[int]) -> list[np.ndarray]:
        return fl_gif.read_selected_planes(tiff_path, indices, tifflib)

    _fl_gif_roi_helpers = fl_gif_roi_context.build_gif_roi_context(
        image_mod=image_mod,
        image_draw_mod=image_draw_mod,
        image_font_mod=image_font_mod,
        fig_to_b64=fig_to_b64,
        float_or=float_or,
    )
    _fl_apply_gif_crop = _fl_gif_roi_helpers["_fl_apply_gif_crop"]
    _fl_gif_kymo_stat = _fl_gif_roi_helpers["_fl_gif_kymo_stat"]
    _fl_gif_kymo_top_mean = _fl_gif_roi_helpers["_fl_gif_kymo_top_mean"]
    _fl_gif_roi_apply_value = _fl_gif_roi_helpers["_fl_gif_roi_apply_value"]
    _fl_gif_roi_background_mean = _fl_gif_roi_helpers["_fl_gif_roi_background_mean"]
    _fl_gif_roi_make_specs = _fl_gif_roi_helpers["_fl_gif_roi_make_specs"]
    _fl_gif_roi_mask_for = _fl_gif_roi_helpers["_fl_gif_roi_mask_for"]
    _fl_gif_roi_metrics_2d = _fl_gif_roi_helpers["_fl_gif_roi_metrics_2d"]
    _fl_image_to_b64 = _fl_gif_roi_helpers["_fl_image_to_b64"]
    _fl_normalize_gif_polygons = _fl_gif_roi_helpers["_fl_normalize_gif_polygons"]
    _fl_normalize_gif_rects = _fl_gif_roi_helpers["_fl_normalize_gif_rects"]
    _fl_parse_percent_list = _fl_gif_roi_helpers["_fl_parse_percent_list"]
    _fl_percent_label = _fl_gif_roi_helpers["_fl_percent_label"]
    _fl_render_gif_frame = _fl_gif_roi_helpers["_fl_render_gif_frame"]
    _fl_render_gif_roi_reference_preview = _fl_gif_roi_helpers[
        "_fl_render_gif_roi_reference_preview"
    ]
    _fl_smooth_heatmap_2d = _fl_gif_roi_helpers["_fl_smooth_heatmap_2d"]
    _fl_smooth_series_nan = _fl_gif_roi_helpers["_fl_smooth_series_nan"]

    _FL_LUT_OPTIONS = fl_stack.LUT_OPTIONS
    _FL_DENOISE_OPTIONS = fl_stack.DENOISE_OPTIONS
    _FL_BACKGROUND_OPTIONS = fl_stack.BACKGROUND_OPTIONS
    _FL_DEFAULT_LUT_BY_INDEX = fl_stack.DEFAULT_LUT_BY_INDEX
    _FL_DEFAULT_DENOISE_BY_INDEX = fl_stack.DEFAULT_DENOISE_BY_INDEX
    _FL_DEFAULT_BACKGROUND_BY_INDEX = fl_stack.DEFAULT_BACKGROUND_BY_INDEX

    _fl_compute_default_min_max = fl_stack.compute_default_min_max
    _fl_convert_to_export_dtype = fl_stack.convert_to_export_dtype
    _fl_box_blur2d = fl_stack.box_blur2d
    _fl_apply_background_suppression = fl_stack.apply_background_suppression
    _fl_apply_optional_denoise = fl_stack.apply_optional_denoise
    _fl_preprocess_stack_image = fl_stack.preprocess_stack_image
    _fl_compute_auto_range_with_processing = fl_stack.compute_auto_range_with_processing
    _fl_clean_choice = fl_stack.clean_choice
    _fl_to_macro_path = fl_stack.to_macro_path
    _fl_imagej_lut_command = fl_stack.imagej_lut_command
    _fl_build_fiji_macro = fl_stack.build_fiji_macro
    _fl_build_default_settings_for_pages = fl_stack.build_default_settings_for_pages
    _fl_normalize_settings_for_pages = fl_stack.normalize_settings_for_pages
    _fl_build_settings_from_template = fl_stack.build_settings_from_template
    _fl_is_generated_tiff = fl_stack.is_generated_tiff

    def _fl_read_tiff_as_pages(tiff_path: Path) -> list[np.ndarray]:
        return fl_stack.read_tiff_as_pages(tiff_path, tifflib)

    def _fl_export_with_settings(
        tiff_path: Path, pages: list[np.ndarray], settings: list[dict]
    ) -> dict:
        return fl_stack.export_with_settings(tiff_path, pages, settings, tifflib)

    _fl_roi_render_helpers = fl_roi_render_context.build_roi_render_context(
        tifflib=tifflib,
        has_pil=has_pil,
        image_mod=image_mod,
        image_draw_mod=image_draw_mod,
        image_font_mod=image_font_mod,
        fig_to_b64=fig_to_b64,
        int_or=int_or,
        infer_pixel_size_um_from_tiff=_fl_infer_pixel_size_um_from_tiff,
    )
    _fl_roi_pick_output_dir = _fl_roi_render_helpers["_fl_roi_pick_output_dir"]
    _fl_roi_render_gif_frame = _fl_roi_render_helpers["_fl_roi_render_gif_frame"]
    _fl_roi_render_reference_preview = _fl_roi_render_helpers["_fl_roi_render_reference_preview"]

    stack_route_ctx = {
        "err": err,
        "browse_files": browse_files,
        "int_or": int_or,
        "float_or": float_or,
        "has_tiff": has_tiff,
        "has_pil": has_pil,
        "tifflib": tifflib,
        "jobs": jobs,
        "_FL_BACKGROUND_OPTIONS": _FL_BACKGROUND_OPTIONS,
        "_FL_DENOISE_OPTIONS": _FL_DENOISE_OPTIONS,
        "_FL_LUT_OPTIONS": _FL_LUT_OPTIONS,
        "_fl_bool": _fl_bool,
        "_fl_build_default_settings_for_pages": _fl_build_default_settings_for_pages,
        "_fl_build_settings_from_template": _fl_build_settings_from_template,
        "_fl_clean_choice": _fl_clean_choice,
        "_fl_compute_auto_range_with_processing": _fl_compute_auto_range_with_processing,
        "_fl_export_with_settings": _fl_export_with_settings,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_is_generated_tiff": _fl_is_generated_tiff,
        "_fl_normalize_settings_for_pages": _fl_normalize_settings_for_pages,
        "_fl_read_tiff_as_pages": _fl_read_tiff_as_pages,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
        "_fl_select_display_frame": _fl_select_display_frame,
        "_fl_tiff_gif_frame_count": _fl_tiff_gif_frame_count,
    }
    volume_route_ctx = {
        "err": err,
        "int_or": int_or,
        "float_or": float_or,
        "has_tiff": has_tiff,
        "has_pil": has_pil,
        "jobs": jobs,
        "fig_to_b64": fig_to_b64,
        "_FL_DENOISE_OPTIONS": _FL_DENOISE_OPTIONS,
        "_fl_bool": _fl_bool,
        "_fl_clean_choice": _fl_clean_choice,
        "_fl_apply_optional_denoise": _fl_apply_optional_denoise,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
        "_fl_tiff_plane_from_array": _fl_tiff_plane_from_array,
        "_fl_tiff_read_array": _fl_tiff_read_array,
        "_fl_tiff_series_info": _fl_tiff_series_info,
        "_fl_tiff_volume3d_payload": _fl_tiff_volume3d_payload,
        "_fl_volume3d_html": _fl_volume3d_html,
    }

    _fl_roi_shape_type = fl_roi.shape_type
    _fl_roi_empty_metrics = fl_roi.empty_metrics
    _fl_roi_metrics_from_flat = fl_roi.metrics_from_flat
    _fl_roi_circle_geometry = fl_roi.circle_geometry
    _fl_roi_ring_width_px = fl_roi.ring_width_px
    _fl_roi_ring_count = fl_roi.ring_count
    _fl_roi_values_2d = fl_roi.values_2d
    _fl_roi_metrics_2d = fl_roi.metrics_2d
    _fl_roi_safe_ratio = fl_roi.safe_ratio
    _fl_roi_sequence_number = fl_roi.sequence_number
    _fl_roi_background_mean = fl_roi.background_mean
    _fl_roi_apply_metric_mode = fl_roi.apply_metric_mode
    _fl_roi_radial_metrics_2d = fl_roi.radial_metrics_2d
    _fl_roi_radial_pair_rows = fl_roi.radial_pair_rows
    _fl_roi_shared_ylim = fl_roi.shared_ylim

    def _fl_roi_collect_pairs(folder: Path):
        return fl_roi.collect_pairs(folder)

    def _fl_roi_compute(stack_path: str, rois: list, metric: str = "mean"):
        return fl_roi.compute_stack_roi(stack_path, rois, metric, tifflib)

    def _fl_roi_read_first_page(stack_path: str) -> np.ndarray:
        return fl_roi.read_first_page(stack_path, tifflib)

    def _fl_roi_plot_radial_profiles(
        radial_df: pd.DataFrame, roi_specs: list, metric: str, plot_metric: str, has_ref: bool
    ) -> str:
        if radial_df is None or radial_df.empty:
            return ""

        seq_vals_all = pd.to_numeric(radial_df["sequence_number"], errors="coerce").to_numpy(
            dtype=float
        )
        use_sequence_axis = np.isfinite(seq_vals_all).any()
        x_label = "Sequence"
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
        y_label = f"{metric_labels.get(metric, metric)} ({presentation_labels.get(plot_metric, plot_metric)})"
        if has_ref:
            y_label += " (Ref=1)"

        ring_keys = (
            radial_df.assign(
                _ring_inner_key=np.where(
                    pd.to_numeric(
                        radial_df.get("inner_radius_um", np.nan), errors="coerce"
                    ).notna(),
                    pd.to_numeric(radial_df.get("inner_radius_um", np.nan), errors="coerce"),
                    pd.to_numeric(radial_df["inner_radius_px"], errors="coerce"),
                ),
                _ring_outer_key=np.where(
                    pd.to_numeric(
                        radial_df.get("outer_radius_um", np.nan), errors="coerce"
                    ).notna(),
                    pd.to_numeric(radial_df.get("outer_radius_um", np.nan), errors="coerce"),
                    pd.to_numeric(radial_df["outer_radius_px"], errors="coerce"),
                ),
            )[["roi_key", "_ring_inner_key", "_ring_outer_key"]]
            .drop_duplicates()
            .sort_values(["roi_key", "_ring_inner_key"], kind="stable")
        )
        tab20 = colormaps["tab20"]
        ring_color = {
            tuple(row): tab20(i % 20)
            for i, row in enumerate(
                ring_keys[["roi_key", "_ring_inner_key", "_ring_outer_key"]].itertuples(
                    index=False, name=None
                )
            )
        }
        show_legend = len(ring_keys) <= 18

        fig, axes = new_subplots(2, 2, figsize=(11.0, 7.4), dpi=120)
        ax1, ax2, ax3, ax4 = axes.ravel()
        panels = [
            (ax1, "stack1_value", "Stack1 by ring", y_label),
            (ax2, "stack2_value", "Stack2 by ring", y_label),
            (ax3, "ratio", "Stack1 / Stack2 by ring", "Ratio" + (" (Ref=1)" if has_ref else "")),
            (ax4, "difference", "Stack1 - Stack2 by ring", f"{y_label} difference"),
        ]

        radial_plot_df = radial_df.copy()
        inner_um = pd.to_numeric(radial_plot_df.get("inner_radius_um", np.nan), errors="coerce")
        outer_um = pd.to_numeric(radial_plot_df.get("outer_radius_um", np.nan), errors="coerce")
        radial_plot_df["_ring_inner_key"] = np.where(
            inner_um.notna(),
            inner_um,
            pd.to_numeric(radial_plot_df["inner_radius_px"], errors="coerce"),
        )
        radial_plot_df["_ring_outer_key"] = np.where(
            outer_um.notna(),
            outer_um,
            pd.to_numeric(radial_plot_df["outer_radius_px"], errors="coerce"),
        )
        grouped = radial_plot_df.groupby(
            ["roi_key", "roi_label", "_ring_inner_key", "_ring_outer_key"], sort=False
        )
        for ax, y_col, title, panel_ylabel in panels:
            x_tick_pairs = []
            for (roi_key, roi_label, inner_key, outer_key), grp in grouped:
                grp = grp.copy()
                grp["_seq"] = pd.to_numeric(grp["sequence_number"], errors="coerce")
                if use_sequence_axis and grp["_seq"].notna().any():
                    grp = grp.sort_values(["_seq", "base_name"], kind="stable")
                    x = grp["_seq"].to_numpy(dtype=float)
                else:
                    grp = grp.sort_values(["base_name"], kind="stable")
                    x = np.arange(len(grp), dtype=float)
                    x_tick_pairs.extend(
                        (float(i), str(v))
                        for i, v in enumerate(grp["base_name"].astype(str).tolist())
                    )
                y = pd.to_numeric(grp[y_col], errors="coerce").to_numpy(dtype=float)
                if not np.isfinite(x).any() or not np.isfinite(y).any():
                    continue
                inner_um_vals = pd.to_numeric(
                    grp.get("inner_radius_um", pd.Series(dtype=float)), errors="coerce"
                ).dropna()
                outer_um_vals = pd.to_numeric(
                    grp.get("outer_radius_um", pd.Series(dtype=float)), errors="coerce"
                ).dropna()
                if not inner_um_vals.empty and not outer_um_vals.empty:
                    ring_label = (
                        f"{float(inner_um_vals.iloc[0]):g}-{float(outer_um_vals.iloc[0]):g} um"
                    )
                else:
                    ring_label = f"{float(inner_key):g}-{float(outer_key):g} px"
                label = f"{roi_label} {ring_label}".strip()
                ax.plot(
                    x,
                    y,
                    marker="o",
                    ms=3,
                    lw=1.2,
                    alpha=0.86,
                    color=ring_color.get((roi_key, inner_key, outer_key), "#3E6AE1"),
                    label=label if show_legend else None,
                )
            ax.set_title(title)
            ax.set_xlabel(x_label)
            ax.set_ylabel(panel_ylabel)
            ax.grid(True, alpha=0.35)
            ax.tick_params(axis="both", labelsize=8)
            if not use_sequence_axis and x_tick_pairs:
                dedup = []
                seen = set()
                for xv, lab in x_tick_pairs:
                    if xv in seen:
                        continue
                    seen.add(xv)
                    dedup.append((xv, lab))
                ax.set_xticks([x for x, _lab in dedup])
                ax.set_xticklabels([lab for _x, lab in dedup], rotation=45, ha="right", fontsize=7)
            if y_col == "ratio":
                ax.axhline(1.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            if y_col == "difference":
                ax.axhline(0.0, color="#8E8E8E", lw=1.0, ls="--", alpha=0.8)
            if show_legend:
                ax.legend(fontsize=7, frameon=False, loc="best")

        stack_ylim = _fl_roi_shared_ylim(radial_df["stack1_value"], radial_df["stack2_value"])
        if stack_ylim is not None:
            ax1.set_ylim(stack_ylim)
            ax2.set_ylim(stack_ylim)

        fig.tight_layout()
        return fig_to_b64(fig)

    _fl_roi_resolve_ref_index = fl_roi.resolve_ref_index
    _fl_roi_normalize_to_reference = fl_roi.normalize_to_reference
    _fl_roi_delta_f_over_f0 = fl_roi.delta_f_over_f0

    gif_route_ctx = {
        "err": err,
        "fig_to_b64": fig_to_b64,
        "float_or": float_or,
        "int_or": int_or,
        "has_pil": has_pil,
        "has_tiff": has_tiff,
        "jobs": jobs,
        "_fl_apply_gif_crop": _fl_apply_gif_crop,
        "_fl_bool": _fl_bool,
        "_fl_decode_base64_payload": _fl_decode_base64_payload,
        "_fl_gif_kymo_stat": _fl_gif_kymo_stat,
        "_fl_gif_kymo_top_mean": _fl_gif_kymo_top_mean,
        "_fl_gif_roi_apply_value": _fl_gif_roi_apply_value,
        "_fl_gif_roi_background_mean": _fl_gif_roi_background_mean,
        "_fl_gif_roi_make_specs": _fl_gif_roi_make_specs,
        "_fl_gif_roi_mask_for": _fl_gif_roi_mask_for,
        "_fl_gif_roi_metrics_2d": _fl_gif_roi_metrics_2d,
        "_fl_image_to_b64": _fl_image_to_b64,
        "_fl_normalize_gif_polygons": _fl_normalize_gif_polygons,
        "_fl_normalize_gif_rects": _fl_normalize_gif_rects,
        "_fl_parse_percent_list": _fl_parse_percent_list,
        "_fl_parse_slice_spec": _fl_parse_slice_spec,
        "_fl_percent_label": _fl_percent_label,
        "_fl_read_selected_gif_planes": _fl_read_selected_gif_planes,
        "_fl_render_gif_frame": _fl_render_gif_frame,
        "_fl_render_gif_roi_reference_preview": _fl_render_gif_roi_reference_preview,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
        "_fl_roi_delta_f_over_f0": _fl_roi_delta_f_over_f0,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
        "_fl_smooth_heatmap_2d": _fl_smooth_heatmap_2d,
        "_fl_smooth_series_nan": _fl_smooth_series_nan,
        "_fl_tiff_gif_frame_count": _fl_tiff_gif_frame_count,
    }
    roi_route_ctx = {
        "err": err,
        "fig_to_b64": fig_to_b64,
        "float_or": float_or,
        "int_or": int_or,
        "has_pil": has_pil,
        "has_tiff": has_tiff,
        "jobs": jobs,
        "tifflib": tifflib,
        "_fl_bool": _fl_bool,
        "_fl_decode_base64_payload": _fl_decode_base64_payload,
        "_fl_frame_to_b64": _fl_frame_to_b64,
        "_fl_infer_pixel_size_um_from_tiff": _fl_infer_pixel_size_um_from_tiff,
        "_fl_roi_apply_metric_mode": _fl_roi_apply_metric_mode,
        "_fl_roi_background_mean": _fl_roi_background_mean,
        "_fl_roi_circle_geometry": _fl_roi_circle_geometry,
        "_fl_roi_collect_pairs": _fl_roi_collect_pairs,
        "_fl_roi_compute": _fl_roi_compute,
        "_fl_roi_delta_f_over_f0": _fl_roi_delta_f_over_f0,
        "_fl_roi_empty_metrics": _fl_roi_empty_metrics,
        "_fl_roi_metrics_2d": _fl_roi_metrics_2d,
        "_fl_roi_normalize_to_reference": _fl_roi_normalize_to_reference,
        "_fl_roi_pick_output_dir": _fl_roi_pick_output_dir,
        "_fl_roi_plot_radial_profiles": _fl_roi_plot_radial_profiles,
        "_fl_roi_radial_pair_rows": _fl_roi_radial_pair_rows,
        "_fl_roi_read_first_page": _fl_roi_read_first_page,
        "_fl_roi_render_gif_frame": _fl_roi_render_gif_frame,
        "_fl_roi_render_reference_preview": _fl_roi_render_reference_preview,
        "_fl_roi_resolve_ref_index": _fl_roi_resolve_ref_index,
        "_fl_roi_ring_count": _fl_roi_ring_count,
        "_fl_roi_safe_ratio": _fl_roi_safe_ratio,
        "_fl_roi_sequence_number": _fl_roi_sequence_number,
        "_fl_roi_shape_type": _fl_roi_shape_type,
        "_fl_roi_shared_ylim": _fl_roi_shared_ylim,
        "_fl_sanitize_prefix": _fl_sanitize_prefix,
    }
    return {
        "stack": stack_route_ctx,
        "volume": volume_route_ctx,
        "gif": gif_route_ctx,
        "roi": roi_route_ctx,
    }
