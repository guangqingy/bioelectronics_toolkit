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
    plot_metric: str = "absolute"
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
    label_scale: Any = 1.0
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
