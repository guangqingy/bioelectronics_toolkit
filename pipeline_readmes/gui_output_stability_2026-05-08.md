# GUI output stability check - 2026-05-08

Scope: `Pipelines` web GUI, excluding temp scripts.

## Backend/API checks

- `python -m py_compile web_api/scripts_panel.py web_app.py`: passed
- `python pipeline_readmes/check_analysis_scripts.py`: 122 scripts checked, 0 blocking issues
- Flask template smoke test:
  - `/scripts/photocurrent`: 200
  - `/scripts/emg`: 200
  - `/scripts/echem_curves`: 200
  - `/scripts/viability`: 200
- Local server smoke test:
  - `http://127.0.0.1:7433/scripts/photocurrent`: 200
  - `http://127.0.0.1:7433/scripts/emg`: 200

## GUI pipeline output checks

All current GUI pipeline entries returned `ok=True` through `/api/scripts/run` and produced updated artifacts visible to the GUI manifest.

| Category | Script ID | Updated artifacts detected |
| --- | --- | ---: |
| Photocurrent | `pc_line_chart` | 16 |
| Photocurrent | `pc_peaks_overlay` | 3 |
| Photocurrent | `pc_heatmap` | 8 |
| Photocurrent | `pc_decay` | 5 |
| Photocurrent | `pc_longterm` | 52 |
| EMG | `emg_demo` | 3 |
| EMG | `emg_overlay` | 3 |
| EMG | `emg_bar` | 3 |
| EMG | `emg_heatmap` | 3 |
| Echem Curves | `echem_pc_curve` | 5 |
| Echem Curves | `echem_pv_curve` | 5 |
| Cell Viability | `viab_watershed_area` | 8 |
| Cell Viability | `viab_stardist_auto` | 8 |

## Notes

- EMG tests used synthetic temporary fixtures under `/tmp/dataprocess_gui_emg_suite`.
- Photocurrent, electrochemistry, and viability tests used the current Subcutaneous data already present in the repository.
- The viability scripts complete successfully but still emit Matplotlib deprecation warnings from `get_cmap`; these warnings do not block file output.
- Playwright is not installed in this environment, so this pass used Flask render/API/HTTP smoke tests rather than browser screenshots.
