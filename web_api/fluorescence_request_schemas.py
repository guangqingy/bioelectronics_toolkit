# LOC budget exception: this module is about 313 lines because it is a
# fluorescence request-schema catalog, not a route module.
# Splitting attempted on 2026-05-14; abandoned because one domain-level schema
# registry keeps OpenAPI request naming and route imports easier to audit.
# Re-evaluate when this file exceeds 500 lines or per-feature schema packages
# are introduced for the fluorescence route modules.
from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import RequestModel


class FluorescenceRoiBrowseRequest(RequestModel):
    folder: str = ""


class FluorescenceRoiLoadStackRequest(RequestModel):
    stack_path: str = Field(min_length=1)
    frame: Any = 0
    lut: str = "Gray"


class FluorescenceRoiAnalyzeRequest(RequestModel):
    stack1_path: str = ""
    stack2_path: str = ""
    rois: list[Any] = Field(default_factory=list)
    metric: str = "mean"
    frame_interval_s: Any = 1.0
    bg_mode: str = "none"
    bg_roi: Any = None
    plot_metric: str = "absolute"
    img_width: Any = 0
    img_height: Any = 0


class FluorescenceRoiAnalyzeSequenceRequest(RequestModel):
    records: list[Any] = Field(default_factory=list)
    rois: list[Any] = Field(default_factory=list)
    metric: str = "mean"
    plot_metric: str = "bg_normalized"
    bg_mode: str = "none"
    bg_roi: Any = None
    ref_sequence: str = ""
    preview_path: str = ""
    preview_stack: str = "stack1"
    scale_bar_um: Any = 0.0
    pixel_size_um: Any = None
    show_preview_name: Any = True
    show_scale_bar: Any = True
    scale_bar_label: str = ""
    label_scale: Any = 2.0
    img_width: Any = 0
    img_height: Any = 0


class FluorescenceRoiExportSequenceRequest(RequestModel):
    records: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_csv: Any = True
    save_plot: Any = True
    save_preview: Any = True
    save_radial_csv: Any = True
    save_radial_plot: Any = True
    csv: str = ""
    plot_png_b64: str = ""
    roi_preview_png_b64: str = ""
    radial_csv: str = ""
    radial_plot_png_b64: str = ""


class FluorescenceRoiExportSequenceGifRequest(RequestModel):
    records: list[Any] = Field(default_factory=list)
    rois: list[Any] = Field(default_factory=list)
    preview_stack: str = "stack1"
    output_dir: str = ""
    prefix: str = ""
    frame_ms: Any = 2000
    scale_bar_um: Any = 0.0
    pixel_size_um: Any = None
    show_preview_name: Any = True
    show_scale_bar: Any = True
    scale_bar_label: str = ""
    label_scale: Any = 1.0


class FluorescenceGifRenderRequest(RequestModel):
    input_path: str = ""
    output_path: str = ""
    fps: Any = 5.0
    lut: str = "Gray"
    scale_bar_um: Any = 10.0
    px_per_um: Any = 3.45
    auto_scale: Any = True
    label_mode: str = "time"
    add_timestamp: Any = True
    slice_spec: Any = ""
    roi_polygons: Any = None
    crop_rects: Any = None
    crop_mode: str = "full"
    crop_roi_label: str = ""
    crop_rect_label: str = ""
    crop_padding_px: Any = 0
    show_roi_overlay: Any = None


class FluorescenceGifPreviewRequest(FluorescenceGifRenderRequest):
    pass


class FluorescenceGifExportPreviewRequest(FluorescenceGifRenderRequest):
    output_dir: str = ""
    prefix: str = ""
    show_name: Any = True
    show_scale_bar: Any = True
    frame_label: str = ""


class FluorescenceGifMergeRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    output_path: str = ""
    fps: Any = 5.0
    lut: str = "Gray"
    scale_bar_um: Any = 10.0
    px_per_um: Any = 3.45
    auto_scale: Any = True
    label_mode: str = "time"
    add_timestamp: Any = True
    roi_polygons: Any = None
    crop_rects: Any = None
    crop_mode: str = "full"
    crop_roi_label: str = ""
    crop_rect_label: str = ""
    crop_padding_px: Any = 0
    show_roi_overlay: Any = None


class FluorescenceGifRoiAnalyzeRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    rois: Any = None
    roi_polygons: Any = None
    bg_roi: Any = None
    metric: str = "mean"
    plot_metric: str = "delta_f_over_f0"
    bg_mode: str = "none"
    fps: Any = 5.0
    frame_interval_s: Any = None
    ref_frame: Any = 1


class FluorescenceGifRoiExportRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_csv: Any = True
    save_plot: Any = True
    csv: str = ""
    plot_png_b64: str = ""


class FluorescenceGifRoiKymographRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    roi: Any = None
    bg_roi: Any = None
    bg_mode: str = "none"
    value_mode: str = "delta_f_over_f0"
    fps: Any = 5.0
    frame_interval_s: Any = None
    ref_frame: Any = 1
    ref_stat: str = "median"
    bins: Any = 80
    range_low_pct: Any = 1.0
    range_high_pct: Any = 99.5
    range_min: Any = None
    range_max: Any = None
    smooth_intensity_bins: Any = 1.2
    smooth_time_frames: Any = 0.8
    smooth_lines: Any = True
    overlay_percentiles: Any = Field(default_factory=list)
    overlay_top_means: Any = Field(default_factory=list)
    overlay_peak: Any = False
    overlay_mean: Any = False
    threshold_lines: Any = Field(default_factory=list)


class FluorescenceGifRoiKymographExportRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_heatmap_csv: Any = True
    save_summary_csv: Any = True
    save_plot: Any = True
    heatmap_csv: str = ""
    summary_csv: str = ""
    plot_png_b64: str = ""


class FluorescenceBrowseRequest(RequestModel):
    folder: str = ""


class FluorescencePathRequest(RequestModel):
    path: str = Field(min_length=1)


class FluorescencePreviewFrameRequest(RequestModel):
    path: str = Field(min_length=1)
    frame: Any = 0
    lut: str = "Gray"
    p_low: Any = 1.0
    p_high: Any = 99.8
    mode: str = "single"
    z_start: Any = None
    z_end: Any = None


class FluorescenceStackAutoRangeRequest(RequestModel):
    path: str = Field(min_length=1)
    page_index: Any = 0
    background: Any = None
    denoise: Any = None


class FluorescenceStackExportRequest(RequestModel):
    input_path: str = Field(min_length=1)
    settings: Any = None


class FluorescenceStackExportBatchRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)
    use_template: Any = True
    lock_ranges: Any = False
    template_settings: Any = Field(default_factory=list)


class FluorescenceNormalizeRequest(RequestModel):
    input_path: str = Field(min_length=1)
    output_path: str = ""
    low_pct: Any = 1.0
    high_pct: Any = 99.8
    dtype: str = "uint16"


class FluorescenceTiffInfoBatchRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)


class Fluorescence3dPreviewSliceRequest(RequestModel):
    path: str = Field(min_length=1)
    z: Any = 0
    c: Any = 0
    t: Any = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: Any = 1.0
    p_high: Any = 99.0


class Fluorescence3dVolumeRequest(RequestModel):
    path: str = Field(min_length=1)
    z: Any = 0
    c: Any = 0
    t: Any = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: Any = 1.0
    p_high: Any = 99.0
    channel_mode: str = "composite"
    max_points: Any = None
    max_xy: Any = None
    max_z: Any = None
    threshold_percentile: Any = None
    channel_ranges: Any = Field(default_factory=dict)
    denoise: Any = None
    interlayer_level: str = "middle"
    density_mode: str = "off"
    density_radius_um: Any = None
    density_min_neighbors: Any = None
    show_scale_bar: Any = True
    scale_bar_um: Any = 20.0
    output_name: str = ""
    output_dir: str = ""
    overwrite: Any = False


class Fluorescence3dRotationGifRequest(Fluorescence3dVolumeRequest):
    rotation_axis: str = "z"
    rotation_direction: str = "forward"
    gif_frames: Any = None
    gif_fps: Any = None
    gif_size: Any = None
    gif_points: Any = None


class Fluorescence3dDistributionRequest(RequestModel):
    path: str = Field(min_length=1)
    distribution_channel: Any = None
    z: Any = 0
    c: Any = 0
    t: Any = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: Any = 1.0
    p_high: Any = 99.0
    distribution_axis: str = "z"
    distribution_metric: str = "mean"
    denoise: Any = None
    output_name: str = ""
    output_dir: str = ""
    overwrite: Any = False
