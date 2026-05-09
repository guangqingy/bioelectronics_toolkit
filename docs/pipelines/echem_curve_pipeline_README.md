# EChem Curve Pipeline README

## Purpose

EChem curve pipelines provide fixed-format photocurrent and photovoltage curve generation for predefined datasets.

## Script IDs And Targets

- `echem_pc_curve` -> `2025_Subcutaneous/Electrochemistry/model_photocurrent_curve.py`
- `echem_pv_curve` -> `2025_Subcutaneous/Electrochemistry/model_photovoltage_curve.py`

## Current Integration Model

These scripts are mostly self-contained research scripts:

- The script panel runs `model_*` wrappers for consistent entry style.
- The wrappers call underlying source scripts (`group_1_photocurrent_curve.py`, `group_1_photovoltage_curve.py`).
- They use hardcoded base directories and expected dataset layouts.
- Script panel currently calls them without parameter forms.
- They run if local project data matches the script assumptions.

## Input Expectations

- Device/chamber-index folder structure under each material.
- Summary CSV naming conventions exactly as expected by each script.

## Outputs

- PNG figures
- SVG plot and legend assets
- CSV tables for figure data

## Integration Quality

- Status: limited.
- Reason: not fully environment-driven yet.
- Practical implication: these scripts are stable for known datasets but not generic across arbitrary folder layouts.

## Recommended Usage

1. Use these scripts when your data follows the existing lab project structure.
2. If a new project layout is needed, create a wrapper that reads `DP_BASE_DIR`/`DP_OUTPUT_DIR` and remaps paths.
3. Keep Script Panel descriptions aligned with actual script assumptions.

## Optional Future Upgrade

To make EChem pipelines fully GUI-integrated:

- Add env parsing (`DP_*`) in both EChem scripts.
- Add explicit parameter fields in `web_templates/scripts.html` for EChem category.
- Add input validation and readable error messages for missing folders.
