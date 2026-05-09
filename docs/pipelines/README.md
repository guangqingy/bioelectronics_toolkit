# DataProcess Script Pipeline Readmes

This folder documents how each script pipeline category integrates with the GUI workflow.

## Coverage

- Photocurrent pipelines: [photocurrent_pipeline_README.md](photocurrent_pipeline_README.md)
- EMG pipelines: [emg_pipeline_README.md](emg_pipeline_README.md)
- EChem curve pipelines: [echem_curve_pipeline_README.md](echem_curve_pipeline_README.md)
- Viability pipelines: [viability_pipeline_README.md](viability_pipeline_README.md)
- Web GUI run manifests: [webgui_run_manifest_2026-05-08.md](webgui_run_manifest_2026-05-08.md)
- Web GUI file profiles: [webgui_file_profiles_2026-05-08.md](webgui_file_profiles_2026-05-08.md)
- Web GUI job/API architecture pass: [webgui_second_priority_2026-05-08.md](webgui_second_priority_2026-05-08.md)

## Standards And Audits

- Analysis script standard: [analysis_script_standard.md](analysis_script_standard.md)
- Analysis script audit: [analysis_scripts_audit_2026-05-08.md](analysis_scripts_audit_2026-05-08.md)
- GUI output stability audit: [gui_output_stability_2026-05-08.md](gui_output_stability_2026-05-08.md)

## Integration Summary

- Photocurrent: strong integration. Script panel parameters map to environment variables consumed by pipeline scripts.
- EMG: strong integration. Script panel parameters map directly to standardized EMG model scripts.
- EChem curves: limited integration. Scripts are dataset-specific and mostly self-contained.
- Viability: limited integration. Scripts are dataset-specific and mostly self-contained.

## GUI Entry Points

- Script panel: `/scripts/<category>`
- Categories in current web app: `photocurrent`, `emg`, `echem_curves`, `viability`

## Dataflow At A Glance

- ABF GUI tools produce summary and segment CSV files used by Photocurrent pipelines.
- EMG GUI tools produce channel and grouped peak CSV files used by EMG pipelines.
- EChem curve and Viability pipelines currently target fixed local project datasets.

## Dev Utilities

- `dev_scripts/check_analysis_scripts.py` — audits project analysis scripts for import-safety conventions.
  Run from the repo root: `python3 dev_scripts/check_analysis_scripts.py`

## Maintenance Notes

- Pipeline source of truth: `pipelines/registry.json`.
- Python loader and availability checks: `pipelines/registry.py`.
- The WebGUI page and `/api/pipelines/catalog` both load that registry, so do
  not duplicate script lists in templates or route modules.
- Prefer environment-variable-driven scripts for robust GUI integration.
- Run `python3 -m unittest discover -s tests` after Web GUI route, job, or
  response-contract changes.
