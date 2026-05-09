# Photocurrent Pipeline README

## Purpose

Photocurrent pipelines generate publication-ready plots and CSV summaries from ABF batch outputs.

## Upstream GUI Integration

1. Use ABF Batch Processor GUI (`/abf/batch`) to generate `summary_*.csv` and segment CSV files.
2. Optionally review data in ABF Viewer/Peak tools.
3. Run scripts from Script Panel category `photocurrent` (`/scripts/photocurrent`).

## Script IDs And Targets

- `pc_line_chart` -> `2025_Subcutaneous/Photocurrent/model_line_chart.py` -> `group_2_electrode_line_chart.py`
- `pc_peaks_overlay` -> `2025_Subcutaneous/Photocurrent/model_peaks_overlay.py` -> `group_2_electrode_peaks.py`
- `pc_heatmap` -> `2025_Subcutaneous/Photocurrent/model_heatmap.py` -> `group_2_electrode_bank.py`
- `pc_decay` -> `2025_Subcutaneous/Photocurrent/model_decay_curve.py` -> `group_8_decay_curve.py`
- `pc_longterm` -> `2025_Subcutaneous/Photocurrent/model_longterm_bar.py` -> `group_10_bar_diagram.py`

## Parameter Contract (Script Panel -> DP_*)

The script panel sends parameters as environment variables (`DP_<UPPER_KEY>`).

Common keys:

- `base_dir`
- `output_dir`
- `series` or `materials`
- `x_lin_range`, `x_log_range`
- `pre_ms`, `post_ms`
- `metric`
- `file_str`, `dates`, `material`

## Input Expectations

- Summary CSV files with power and metric columns (`power_density`, `capacitance_peak*`, `integral_charge*`).
- Segment CSV files for overlay/heatmap workflows.
- Folder names matching panel configuration for each script.

## Outputs

Typical outputs include:

- PNG figures
- SVG figure variants
- CSV tables exported with figure data

Output is written to resolved `output_dir` (absolute or relative to `base_dir`).

## Integration Quality

- Status: strong.
- Reason: script panel parameters map to environment-aware script entry points.
- Caveat: dataset folder naming still must match each script's expected conventions.

## Recommended Run Order

1. ABF batch process data.
2. Verify summary/segment files are present.
3. Run `pc_line_chart` first as a sanity check.
4. Run overlay/heatmap/decay/longterm scripts as needed.
