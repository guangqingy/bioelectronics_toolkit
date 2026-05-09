# EMG Pipeline README

## Purpose

EMG pipelines generate representative waveform figures, overlays, bar comparisons, and heatmaps from EMG CSV exports.

## Upstream GUI Integration

1. Use RHD Viewer (`/emg/rhd`) to export channel CSV files.
2. Use RHD Peak Selector (`/emg/peaks`) to detect peaks and export grouped peak segments.
3. Run Script Panel category `emg` (`/scripts/emg`).

## Script IDs And Targets

- `emg_demo` -> `2025_Subcutaneous/EMG/model_demo_single_peak.py`
- `emg_overlay` -> `2025_Subcutaneous/EMG/model_overlay_mean.py`
- `emg_bar` -> `2025_Subcutaneous/EMG/model_bar_diagram.py`
- `emg_heatmap` -> `2025_Subcutaneous/EMG/model_heatmap.py`

## Parameter Contract (Script Panel -> DP_*)

All EMG model scripts consume `DP_*` parameters directly.

Main keys by tool:

- Demo: `csv_path`, `output_dir`, `t_center`, `window_ms`, `title`, `scale_bar`
- Overlay: `peaks_dir`, `output_dir`, `group`, `pre_ms`, `post_ms`, `show_indiv`, `alpha`
- Bar: `peaks_dir`, `output_dir`, `groups`, `metric`, `stat_test`
- Heatmap: `peaks_dir`, `output_dir`, `row_var`, `col_var`, `value_var`, `colormap`

## Input Expectations

- Demo: one channel CSV with time/value columns.
- Overlay/Bar/Heatmap: grouped peak CSV files and/or summary CSV files under `peaks_dir`.

## Outputs

- PNG main figure
- SVG main figure (signal-only style for downstream layout)
- CSV data table used to render the figure

## Integration Quality

- Status: strong.
- Reason: EMG scripts are explicitly designed for web-parameterized execution.
- Caveat: column naming should remain consistent (`height`, `group`, `channel`, etc.) for best auto-detection.

## Recommended Run Order

1. Detect and group peaks in GUI.
2. Export grouped peaks.
3. Run `emg_overlay` to validate grouped data quality.
4. Run `emg_bar` and `emg_heatmap` for comparisons.
