# Analysis Python Scripts Audit

Date: 2026-05-08

Scope:
- Included analysis-oriented `.py` files under this workspace.
- Excluded GUI/web-interface and maintained package modules by filename/folder
  patterns: `*gui*.py`, viewer/app/panel/selector/designer scripts,
  `desktop_apps/`, `services/`, `pipelines/`, `tests/`, `vendor/`,
  `web_api/`, `web_static/`, virtual environments, build/dist, and
  `__pycache__`.
- Total checked: 120 scripts.
- Syntax status: no Python syntax errors found by AST parsing or `py_compile`.

Reference style from `2025_Subcutaneous`:
- Use `Path(__file__).resolve().parent` or explicit environment/CLI inputs for paths.
- Keep editable configuration near the top.
- Create output directories inside the execution path, not at import time.
- Export reproducible figure data as CSV alongside PNG/SVG/PDF.
- Put analysis execution inside `main()` or an explicit `if __name__ == "__main__":` guarded `run_all()`.
- Prefer deterministic file discovery with `sorted(...)`.
- Use clear status messages and keep processing other panels after per-panel failures.

## Current Status

Script shape after normalization:
- 106 scripts with a `main()` function.
- 9 wrapper `model_*` scripts that call a target group script via `runpy`.
- 7 scripts with an existing guarded `run_all()` style entrypoint.

Main stability result:
- No checked analysis script has a syntax error.
- 0 checked non-GUI scripts lack a main guard.
- Helper/library-style modules now have no-op explanatory `main()` entrypoints rather than direct analysis behavior.
- Global output-directory creation side effects were removed from checked non-GUI analysis scripts. Remaining top-level `OUT_DIR.mkdir(...)` calls are only in GUI scripts.

## Project Counts

| Project | Checked scripts | Current shape |
| --- | ---: | --- |
| `2025_Hydrophobic` | 17 | All now use `main()` guards. |
| `2025_Pixeless` | 12 | All now use `main()` guards. |
| `2025_Subcellular_Stimulation` | 16 | 10 `main()`, 6 guarded `run_all()`. |
| `2025_Subcutaneous` | 75 | 65 `main()`, 9 wrappers, 1 guarded `run_all()`. |
| root utilities | 2 | Both have `main()` guards. |

## Normalized In This Pass

The following groups were changed without altering analysis algorithms, plotting parameters, path constants, panel definitions, or output filenames:

- Hydrophobic Photocurrent: 17 scripts.
- Pixeless Electrochemistry: 6 scripts.
- Pixeless Photocurrent: `group_2_material_peaks.py`.
- Subcellular Stimulation Photocurrent: `group_1_material_peaks.py`.
- Subcellular Stimulation FL image scripts with existing guarded `run_all()`: output directory creation moved into the guarded execution path.
- Subcutaneous Cell Number Counting, EMG, Electrochemistry, Histology, and Photocurrent group scripts with explicit execution blocks.
- Subcutaneous `temp*.py` scripts: guarded for safer execution, but still marked for cleanup below.
- Helper modules now print a short usage note when run directly:
  - `2025_Subcellular_Stimulation/FL_Image/mito_mos_vs_none_layer_compare.py`
  - `2025_Subcutaneous/EMG/emg_model_utils.py`
  - `vendor/intan/importrhdutilities.py`

Mechanical changes applied:
- Wrapped bottom execution blocks in `def main() -> None`.
- Added `if __name__ == "__main__": main()`.
- Moved top-level `OUT_DIR.mkdir(...)`, `OUT_BASE.mkdir(...)`, and related output-folder creation into `main()` or guarded `run_all()`.
- Corrected the active `2025_Subcellular_Stimulation/Photocurrent/group_1_material_peaks.py` header and peak-description comment.
- Added `2025_Subcutaneous/Photocurrent/temp_scripts_index.md` to document legacy temp scripts without moving them.
- Added `dev_scripts/check_analysis_scripts.py` so the audit can be rerun.
- Added `docs/pipelines/analysis_script_standard.md` as the standard format for future non-GUI analysis scripts.

## Remaining Cleanup

These are not syntax or import-safety blockers, but they should still be cleaned up:

- Rename or archive temporary scripts:
  - `2025_Pixeless/Machine_Learning/test.py`
  - `2025_Subcutaneous/Photocurrent/temp1-1.py`
  - `2025_Subcutaneous/Photocurrent/temp1-2.py`
  - `2025_Subcutaneous/Photocurrent/temp1.py`
  - `2025_Subcutaneous/Photocurrent/temp2-2.py`
  - `2025_Subcutaneous/Photocurrent/temp2-3.py`
  - `2025_Subcutaneous/Photocurrent/temp2.py`
  - `2025_Subcutaneous/Photocurrent/temp4.py`
  - `2025_Subcutaneous/Photocurrent/temp5.py`
  - `2025_Subcutaneous/Photocurrent/temp6.py`
- See `2025_Subcutaneous/Photocurrent/temp_scripts_index.md` for current roles and suggested permanent names.
- Longer-term: extract shared helpers for `_save_csv`, matplotlib style setup, safe output directory creation, panel loops, signal-only SVG export, and sorted file discovery.

## Verification

Commands run:
- AST parse over 120 checked non-GUI scripts.
- `py_compile` over checked non-GUI scripts.
- `python dev_scripts/check_analysis_scripts.py`

Result:
- No syntax errors.
- All checked non-GUI scripts are guarded against accidental execution during import.
- Reusable checker reports 120 checked scripts, 0 blocking issues, and 10 temp/test named scripts to clean up later.
