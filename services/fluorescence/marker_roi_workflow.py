from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from services.fluorescence.manual_roi import RoiPolygon, load_tiff_channels
from services.fluorescence.marker_roi_core import (
    CHANNEL_NAMES,
    MarkerParams,
    _fmt,
    analyze_roi_image,
    infer_marker_channels,
    load_roi_payload,
)
from services.fluorescence.marker_roi_plots import make_mouse_plot, make_normalized_mouse_plot
from services.fluorescence.marker_roi_tables import (
    IMAGE_COLUMNS,
    MOUSE_COLUMNS,
    NORMALIZED_MOUSE_COLUMNS,
    STATS_COLUMNS,
    TUNING_COLUMNS,
    mouse_summary_rows,
    normalized_mouse_rows,
    read_csv_dicts,
    stats_rows,
    write_csv,
)

DEFAULT_ROI_JSON = Path("fluorescence_manual_rois.json")
DEFAULT_OUTPUT_DIRNAME = "fluorescence_marker_roi_analysis"


def tuning_parameter_sets(base: MarkerParams) -> list[tuple[str, MarkerParams]]:
    return [
        ("current", base),
        (
            "sma_p90",
            MarkerParams(**{**base.__dict__, "sma_percentile_floor": 90.0}),
        ),
        (
            "sma_p94",
            MarkerParams(**{**base.__dict__, "sma_percentile_floor": 94.0}),
        ),
        (
            "mac_p98_8",
            MarkerParams(**{**base.__dict__, "macrophage_percentile_floor": 98.8}),
        ),
        (
            "mac_p99_5",
            MarkerParams(**{**base.__dict__, "macrophage_percentile_floor": 99.5}),
        ),
        (
            "dapi_p96_5",
            MarkerParams(**{**base.__dict__, "dapi_percentile_floor": 96.5}),
        ),
        (
            "dapi_p98_5",
            MarkerParams(**{**base.__dict__, "dapi_percentile_floor": 98.5}),
        ),
    ]


def run_parameter_tuning(
    roi_json: Path,
    output_dir: Path,
    base_params: MarkerParams,
    channel_override: dict[str, int] | None = None,
) -> Path:
    tuning_dir = output_dir / "parameter_tuning_runs"
    summary_rows: list[dict[str, str]] = []
    for label, params in tuning_parameter_sets(base_params):
        run_dir = tuning_dir / label
        analyze(
            roi_json,
            output_dir=run_dir,
            params=params,
            write_previews=False,
            channel_override=channel_override,
        )
        stat_rows = read_csv_dicts(run_dir / "fluorescence_marker_device_vs_control_stats.csv")
        for stat_row in stat_rows:
            try:
                control_mean = float(stat_row.get("control_mean", "nan"))
                device_mean = float(stat_row.get("device_mean", "nan"))
            except Exception:
                control_mean = device_mean = float("nan")
            ratio = device_mean / control_mean if np.isfinite(control_mean) and abs(control_mean) > 1e-12 else float("nan")
            score = abs(math.log(ratio)) if np.isfinite(ratio) and ratio > 0 else float("nan")
            summary_rows.append(
                {
                    "label": label,
                    "dapi_percentile": _fmt(params.dapi_percentile_floor),
                    "sma_percentile": _fmt(params.sma_percentile_floor),
                    "macrophage_percentile": _fmt(params.macrophage_percentile_floor),
                    "macrophage_mad_k": _fmt(params.macrophage_mad_k),
                    "macrophage_min_area_um2": _fmt(params.macrophage_min_area_um2),
                    "metric": stat_row.get("metric", ""),
                    "control_mean": _fmt(control_mean),
                    "device_mean": _fmt(device_mean),
                    "device_to_control": _fmt(ratio),
                    "student_p": stat_row.get("student_p", ""),
                    "normalization_score": _fmt(score),
                }
            )
    tuning_path = output_dir / "fluorescence_marker_parameter_tuning.csv"
    write_csv(tuning_path, summary_rows, TUNING_COLUMNS)
    return tuning_path


def analyze(
    roi_json: Path,
    output_dir: Path | None = None,
    params: MarkerParams | None = None,
    write_previews: bool = True,
    channel_override: dict[str, int] | None = None,
) -> dict[str, Path | int | dict[str, int]]:
    params = params or MarkerParams()
    payload = load_roi_payload(roi_json)
    first_image = next((item for item in payload.get("images", []) if item.get("image_path")), None)
    channel_count = None
    if first_image is not None:
        image_path = Path(first_image.get("image_path", ""))
        if image_path.is_file():
            channel_count = int(load_tiff_channels(image_path).shape[0])
    channels = infer_marker_channels(payload, channel_count=channel_count)
    if channel_override:
        channels.update({key: int(value) for key, value in channel_override.items() if value})
    display_colors = payload.get("display", {}).get("channel_colors") or None
    output_dir = output_dir or (roi_json.parent / DEFAULT_OUTPUT_DIRNAME)
    preview_dir = output_dir / "segmentation_previews" if write_previews else None

    image_rows: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in payload.get("images", []):
        rois = [RoiPolygon.from_dict(roi_data) for roi_data in item.get("rois", []) if roi_data.get("kind", "signal") == "signal"]
        if not rois:
            skipped.append({"image_name": str(item.get("image_name", "")), "reason": "no signal ROI"})
            continue
        image_path = Path(item.get("image_path", ""))
        if not image_path.is_file():
            skipped.append({"image_name": str(item.get("image_name", "")), "reason": "missing TIFF"})
            continue
        for roi in rois:
            image_rows.append(
                analyze_roi_image(
                    image_path,
                    roi,
                    channels=channels,
                    params=params,
                    preview_dir=preview_dir,
                    display_colors=display_colors,
                )
            )

    mouse_rows = mouse_summary_rows(image_rows)
    stat_rows = stats_rows(mouse_rows)
    normalized_rows = normalized_mouse_rows(mouse_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "fluorescence_marker_image_summary.csv"
    mouse_path = output_dir / "fluorescence_marker_mouse_summary.csv"
    normalized_path = output_dir / "fluorescence_marker_mouse_summary_normalized_to_control.csv"
    stats_path = output_dir / "fluorescence_marker_device_vs_control_stats.csv"
    skipped_path = output_dir / "fluorescence_marker_skipped_images.csv"
    plot_path = output_dir / "fluorescence_marker_mouse_points.png"
    normalized_plot_path = output_dir / "fluorescence_marker_mouse_points_normalized_to_control.png"
    write_csv(image_path, image_rows, IMAGE_COLUMNS)
    write_csv(mouse_path, mouse_rows, MOUSE_COLUMNS)
    write_csv(normalized_path, normalized_rows, NORMALIZED_MOUSE_COLUMNS)
    write_csv(stats_path, stat_rows, STATS_COLUMNS)
    write_csv(skipped_path, skipped, ["image_name", "reason"])
    if mouse_rows:
        make_mouse_plot(mouse_rows, stat_rows, plot_path)
        make_normalized_mouse_plot(normalized_rows, normalized_plot_path)
    settings = {
        "pixel_size_um": params.pixel_size_um,
        "pixel_area_um2": params.pixel_area_um2,
        "channels": {CHANNEL_NAMES[key]: value for key, value in channels.items()},
        "parameters": params.__dict__,
    }
    (output_dir / "fluorescence_marker_analysis_settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return {
        "output_dir": output_dir,
        "image_rows": len(image_rows),
        "mouse_rows": len(mouse_rows),
        "skipped": len(skipped),
        "channels": channels,
    }
