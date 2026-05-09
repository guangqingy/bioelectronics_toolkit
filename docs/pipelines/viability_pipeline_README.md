# Viability Pipeline README

## Purpose

Viability pipelines generate Control-vs-Device bar analyses for live/dead quantification datasets.

## Script IDs And Targets

- `viab_watershed_area` -> `2025_Subcutaneous/Cell_Number_Counting/model_watershed_area_ratio.py`
- `viab_stardist_auto` -> `2025_Subcutaneous/Cell_Number_Counting/model_stardist_auto_ratio.py`

## Current Integration Model

These two scripts are dataset-specific:

- The script panel runs `model_*` wrappers for consistency.
- `group_1_live_ratio.py` (wrapped by `model_watershed_area_ratio.py`) uses watershed-based area quantification from `Live_and_Dead_20250902/result.txt`.
- `group_2_live_ratio.py` (wrapped by `model_stardist_auto_ratio.py`) uses StarDist auto-recognition outputs from `live_and_dead_20251103/control.csv` and `device.csv`.
- Script panel runs them as fixed workflows with no parameter form.

## Outputs

Each script exports:

- multiple PNG bar figures (with and without significance annotations)
- matching CSV files for plotted values

## Integration Quality

- Status: limited.
- Reason: scripts currently do not consume `DP_*` overrides and rely on fixed relative paths.
- Practical implication: use them only for the predefined project folders unless refactored.

## Recommended Usage

1. Keep expected source files in their original folder structure.
2. Run from Script Panel category `viability`.
3. Check generated `plots_*` subfolder under the dataset directory.

## Optional Future Upgrade

To make Viability pipelines fully generic:

- Add env parsing for `DP_BASE_DIR`, `DP_OUTPUT_DIR`, and input file names.
- Add parameter controls in `web_templates/scripts.html` for viability scripts.
- Add schema validation for incoming CSV/TXT formats before plotting.
