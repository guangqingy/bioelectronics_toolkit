from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.fluorescence.manual_roi import RoiPolygon
from services.fluorescence.marker_roi_core import (
    CHANNEL_NAMES,
    PIXEL_AREA_UM2,
    PIXEL_SIZE_UM,
    MarkerParams,
    _fmt,
    analyze_roi_image,
    clean_mask,
    hex_rgb,
    infer_marker_channels,
    load_roi_payload,
    otsu_threshold,
    overlay_masks,
    polygon_mask,
    segment_dapi,
    segment_macrophage,
    segment_sma,
)
from services.fluorescence.marker_roi_plots import make_mouse_plot, make_normalized_mouse_plot
from services.fluorescence.marker_roi_tables import (
    IMAGE_COLUMNS,
    MARKER_METRICS,
    MOUSE_COLUMNS,
    NORMALIZED_MOUSE_COLUMNS,
    STATS_COLUMNS,
    TUNING_COLUMNS,
    mean_numeric,
    mouse_summary_rows,
    normalized_mouse_rows,
    read_csv_dicts,
    sd,
    stats_rows,
    write_csv,
)
from services.fluorescence.marker_roi_workflow import (
    DEFAULT_OUTPUT_DIRNAME,
    DEFAULT_ROI_JSON,
    analyze,
    run_parameter_tuning,
    tuning_parameter_sets,
)

__all__ = [
    "CHANNEL_NAMES",
    "DEFAULT_OUTPUT_DIRNAME",
    "DEFAULT_ROI_JSON",
    "IMAGE_COLUMNS",
    "MARKER_METRICS",
    "MOUSE_COLUMNS",
    "MarkerParams",
    "NORMALIZED_MOUSE_COLUMNS",
    "PIXEL_AREA_UM2",
    "PIXEL_SIZE_UM",
    "RoiPolygon",
    "STATS_COLUMNS",
    "TUNING_COLUMNS",
    "_fmt",
    "analyze",
    "analyze_roi_image",
    "build_arg_parser",
    "clean_mask",
    "hex_rgb",
    "infer_marker_channels",
    "load_roi_payload",
    "main",
    "make_mouse_plot",
    "make_normalized_mouse_plot",
    "mean_numeric",
    "mouse_summary_rows",
    "normalized_mouse_rows",
    "otsu_threshold",
    "overlay_masks",
    "polygon_mask",
    "read_csv_dicts",
    "run_parameter_tuning",
    "sd",
    "segment_dapi",
    "segment_macrophage",
    "segment_sma",
    "stats_rows",
    "tuning_parameter_sets",
    "write_csv",
]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze fluorescence manual ROIs for DAPI/SMA/macrophage markers.")
    parser.add_argument("--roi-json", default=str(DEFAULT_ROI_JSON), help="Path to fluorescence_manual_rois.json.")
    parser.add_argument("--output-dir", default="", help="Output folder. Defaults to <roi folder>/fluorescence_marker_roi_analysis.")
    parser.add_argument("--pixel-size-um", type=float, default=PIXEL_SIZE_UM, help="Microns per pixel.")
    parser.add_argument("--dapi-channel", type=int, default=0, help="Override DAPI channel, 1-based.")
    parser.add_argument("--sma-channel", type=int, default=0, help="Override SMA channel, 1-based.")
    parser.add_argument("--macrophage-channel", type=int, default=0, help="Override macrophage channel, 1-based.")
    parser.add_argument("--dapi-percentile", type=float, default=97.5, help="Manual DAPI percentile floor.")
    parser.add_argument("--sma-percentile", type=float, default=92.0, help="Manual SMA percentile floor.")
    parser.add_argument("--macrophage-percentile", type=float, default=99.2, help="Manual macrophage percentile floor.")
    parser.add_argument("--macrophage-mad-k", type=float, default=6.0, help="Robust MAD multiplier for macrophage threshold.")
    parser.add_argument("--dapi-min-area-um2", type=float, default=8.0, help="Minimum DAPI nucleus area.")
    parser.add_argument("--dapi-max-area-um2", type=float, default=700.0, help="Maximum DAPI nucleus area.")
    parser.add_argument("--sma-min-area-um2", type=float, default=50.0, help="Minimum SMA connected area.")
    parser.add_argument("--macrophage-min-area-um2", type=float, default=50.0, help="Minimum macrophage object area.")
    parser.add_argument("--no-previews", action="store_true", help="Skip segmentation overlay PNGs.")
    parser.add_argument("--tune-percentiles", action="store_true", help="Run a small percentile tuning sweep.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    params = MarkerParams(
        pixel_size_um=float(args.pixel_size_um),
        dapi_percentile_floor=float(args.dapi_percentile),
        dapi_min_area_um2=float(args.dapi_min_area_um2),
        dapi_max_area_um2=float(args.dapi_max_area_um2),
        sma_percentile_floor=float(args.sma_percentile),
        sma_min_area_um2=float(args.sma_min_area_um2),
        macrophage_percentile_floor=float(args.macrophage_percentile),
        macrophage_mad_k=float(args.macrophage_mad_k),
        macrophage_min_area_um2=float(args.macrophage_min_area_um2),
    )
    override = {
        "dapi": args.dapi_channel,
        "sma": args.sma_channel,
        "macrophage": args.macrophage_channel,
    }
    result = analyze(
        Path(args.roi_json).expanduser(),
        output_dir=output_dir,
        params=params,
        write_previews=not args.no_previews,
        channel_override=override,
    )
    print(f"Output: {result['output_dir']}")
    print(f"Rows: image={result['image_rows']} mouse={result['mouse_rows']} skipped={result['skipped']}")
    print(f"Channels: {result['channels']}")
    if args.tune_percentiles:
        tuning_path = run_parameter_tuning(
            Path(args.roi_json).expanduser(),
            Path(result["output_dir"]),
            params,
            channel_override=override,
        )
        print(f"Tuning: {tuning_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
