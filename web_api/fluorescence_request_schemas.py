# LOC budget exception: this module is about 313 lines because it is a
# fluorescence request-schema catalog, not a route module.
# Splitting attempted on 2026-05-14; abandoned because one domain-level schema
# registry keeps OpenAPI request naming and route imports easier to audit.
# Re-evaluate when this file exceeds 500 lines or per-feature schema packages
# are introduced for the fluorescence route modules.
from __future__ import annotations

from typing import Any

from pydantic import Field

from .request_validation import OptFloat, OptInt, RequestModel


class FluorescenceRoiBrowseRequest(RequestModel):
    folder: str = ""


class FluorescenceRoiLoadStackRequest(RequestModel):
    stack_path: str = Field(min_length=1)
    frame: OptInt = 0
    lut: str = "Gray"


class FluorescenceRoiAnalyzeRequest(RequestModel):
    stack1_path: str = ""
    stack2_path: str = ""
    rois: list[Any] = Field(default_factory=list)
    metric: str = "mean"
    frame_interval_s: OptFloat = 1.0
    bg_mode: str = "none"
    bg_roi: Any = None
    plot_metric: str = "absolute"
    img_width: OptInt = 0
    img_height: OptInt = 0


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
    scale_bar_um: OptFloat = 0.0
    pixel_size_um: OptFloat = None
    show_preview_name: bool = True
    show_scale_bar: bool = True
    scale_bar_label: str = ""
    label_scale: OptFloat = 2.0
    img_width: OptInt = 0
    img_height: OptInt = 0


class FluorescenceRoiExportSequenceRequest(RequestModel):
    records: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_csv: bool = True
    save_plot: bool = True
    save_preview: bool = True
    save_radial_csv: bool = True
    save_radial_plot: bool = True
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
    frame_ms: OptFloat = 2000
    scale_bar_um: OptFloat = 0.0
    pixel_size_um: OptFloat = None
    show_preview_name: bool = True
    show_scale_bar: bool = True
    scale_bar_label: str = ""
    label_scale: OptFloat = 1.0


class FluorescenceGifRenderRequest(RequestModel):
    input_path: str = ""
    output_path: str = ""
    fps: OptFloat = 5.0
    lut: str = "Gray"
    scale_bar_um: OptFloat = 10.0
    px_per_um: OptFloat = 3.45
    auto_scale: bool = True
    label_mode: str = "time"
    add_timestamp: bool = True
    slice_spec: Any = ""
    roi_polygons: Any = None
    crop_rects: Any = None
    crop_mode: str = "full"
    crop_roi_label: str = ""
    crop_rect_label: str = ""
    crop_padding_px: OptInt = 0
    show_roi_overlay: bool | None = None


class FluorescenceGifPreviewRequest(FluorescenceGifRenderRequest):
    pass


class FluorescenceGifExportPreviewRequest(FluorescenceGifRenderRequest):
    output_dir: str = ""
    prefix: str = ""
    show_name: bool = True
    show_scale_bar: bool = True
    frame_label: str = ""


class FluorescenceGifMergeRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    output_path: str = ""
    fps: OptFloat = 5.0
    lut: str = "Gray"
    scale_bar_um: OptFloat = 10.0
    px_per_um: OptFloat = 3.45
    auto_scale: bool = True
    label_mode: str = "time"
    add_timestamp: bool = True
    roi_polygons: Any = None
    crop_rects: Any = None
    crop_mode: str = "full"
    crop_roi_label: str = ""
    crop_rect_label: str = ""
    crop_padding_px: OptInt = 0
    show_roi_overlay: bool | None = None


class FluorescenceGifRoiAnalyzeRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    rois: Any = None
    roi_polygons: Any = None
    bg_roi: Any = None
    metric: str = "mean"
    plot_metric: str = "delta_f_over_f0"
    bg_mode: str = "none"
    fps: OptFloat = 5.0
    frame_interval_s: OptFloat = None
    ref_frame: OptInt = 1


class FluorescenceGifRoiExportRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_csv: bool = True
    save_plot: bool = True
    csv: str = ""
    plot_png_b64: str = ""


class FluorescenceGifRoiKymographRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    slice_specs: list[Any] = Field(default_factory=list)
    roi: Any = None
    bg_roi: Any = None
    bg_mode: str = "none"
    value_mode: str = "delta_f_over_f0"
    fps: OptFloat = 5.0
    frame_interval_s: OptFloat = None
    ref_frame: OptInt = 1
    ref_stat: str = "median"
    bins: OptInt = 80
    range_low_pct: OptFloat = 1.0
    range_high_pct: OptFloat = 99.5
    range_min: OptFloat = None
    range_max: OptFloat = None
    smooth_intensity_bins: OptFloat = 1.2
    smooth_time_frames: OptFloat = 0.8
    smooth_lines: bool = True
    overlay_percentiles: Any = Field(default_factory=list)
    overlay_top_means: Any = Field(default_factory=list)
    overlay_peak: bool = False
    overlay_mean: bool = False
    threshold_lines: Any = Field(default_factory=list)


class FluorescenceGifRoiKymographExportRequest(RequestModel):
    tiff_paths: list[Any] = Field(default_factory=list)
    output_dir: str = ""
    prefix: str = ""
    save_heatmap_csv: bool = True
    save_summary_csv: bool = True
    save_plot: bool = True
    heatmap_csv: str = ""
    summary_csv: str = ""
    plot_png_b64: str = ""


class FluorescenceBrowseRequest(RequestModel):
    folder: str = ""


class FluorescencePathRequest(RequestModel):
    path: str = Field(min_length=1)


class FluorescencePreviewFrameRequest(RequestModel):
    path: str = Field(min_length=1)
    frame: OptInt = 0
    lut: str = "Gray"
    p_low: OptFloat = 1.0
    p_high: OptFloat = 99.8
    mode: str = "single"
    z_start: OptInt = None
    z_end: OptInt = None


class FluorescenceStackAutoRangeRequest(RequestModel):
    path: str = Field(min_length=1)
    page_index: OptInt = 0
    background: Any = None
    denoise: Any = None


class FluorescenceStackExportRequest(RequestModel):
    input_path: str = Field(min_length=1)
    settings: Any = None


class FluorescenceStackExportBatchRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)
    use_template: bool = True
    lock_ranges: bool = False
    template_settings: Any = Field(default_factory=list)


class FluorescenceNormalizeRequest(RequestModel):
    input_path: str = Field(min_length=1)
    output_path: str = ""
    low_pct: OptFloat = 1.0
    high_pct: OptFloat = 99.8
    dtype: str = "uint16"


class FluorescenceTiffInfoBatchRequest(RequestModel):
    paths: list[Any] = Field(default_factory=list)


class Fluorescence3dPreviewSliceRequest(RequestModel):
    path: str = Field(min_length=1)
    z: OptInt = 0
    c: OptInt = 0
    t: OptInt = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: OptFloat = 1.0
    p_high: OptFloat = 99.0


class Fluorescence3dVolumeRequest(RequestModel):
    path: str = Field(min_length=1)
    z: OptInt = 0
    c: OptInt = 0
    t: OptInt = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: OptFloat = 1.0
    p_high: OptFloat = 99.0
    channel_mode: str = "composite"
    max_points: OptInt = None
    max_xy: OptInt = None
    max_z: OptInt = None
    threshold_percentile: OptFloat = None
    channel_ranges: Any = Field(default_factory=dict)
    denoise: Any = None
    interlayer_level: str = "middle"
    density_mode: str = "off"
    density_radius_um: OptFloat = None
    density_min_neighbors: OptInt = None
    show_scale_bar: bool = True
    scale_bar_um: OptFloat = 20.0
    output_name: str = ""
    output_dir: str = ""
    overwrite: bool = False


class Fluorescence3dRotationGifRequest(Fluorescence3dVolumeRequest):
    rotation_axis: str = "z"
    rotation_direction: str = "forward"
    gif_frames: OptInt = None
    gif_fps: OptFloat = None
    gif_size: OptInt = None
    gif_points: OptInt = None


class Fluorescence3dDistributionRequest(RequestModel):
    path: str = Field(min_length=1)
    distribution_channel: OptInt = None
    z: OptInt = 0
    c: OptInt = 0
    t: OptInt = 0
    extra_indices: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: OptFloat = 1.0
    p_high: OptFloat = 99.0
    distribution_axis: str = "z"
    distribution_metric: str = "mean"
    denoise: Any = None
    output_name: str = ""
    output_dir: str = ""
    overwrite: bool = False
