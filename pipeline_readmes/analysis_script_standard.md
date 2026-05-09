# Analysis Script Standard

Use this standard for non-GUI analysis scripts.

## Required Structure

1. Header docstring:
   - Purpose.
   - Expected input files and required columns.
   - Output folder and generated file types.
   - Key assumptions.

2. Imports:
   - Standard library first.
   - Third-party libraries second.
   - Local imports last.

3. Configuration block:
   - `BASE_DIR = Path(__file__).resolve().parent / "..."`
   - `OUT_DIR = BASE_DIR / "..."`
   - Constants and panel definitions.
   - No output directory creation at import time.

4. Helpers:
   - Column detection.
   - File discovery.
   - CSV read/write.
   - Signal/image/statistics processing.
   - Plot/export functions.

5. Execution:
   - `def main() -> None:`
   - Validate input directories.
   - Create output directories.
   - Loop over panels/groups with per-panel `try/except` only when skipping one bad panel is acceptable.
   - Print clear `[INFO]`, `[WARN]`, `[OK]`, `[ERROR]`, `[DONE]` messages.

6. Guard:

```python
if __name__ == "__main__":
    main()
```

## Output Rules

- Save plot data as CSV alongside figures whenever possible.
- Prefer deterministic ordering with `sorted(...)`.
- Use `Path` objects instead of string path concatenation.
- Keep generated PNG/SVG/PDF names stable.
- Do not call `plt.show()` in batch analysis scripts.
- Close figures with `plt.close(fig)` after saving.
- Avoid hidden side effects during import, especially:
  - Running analysis loops.
  - Creating output folders.
  - Reading input data.
  - Writing output files.

## Naming Rules

- Use `group_<n>_<short_description>.py` for configured paper/figure analysis scripts.
- Use `model_<description>.py` only for reusable model-style scripts or wrappers.
- Use helper names like `<domain>_utils.py` only for import-only utilities.
- Avoid `temp*.py`, `test.py`, and copy-number suffixes in production analysis folders.

## Maintenance Check

Run:

```bash
python pipeline_readmes/check_analysis_scripts.py
```

Expected healthy result:
- `Blocking issues: 0`
- Temp/test named scripts may remain only while legacy scratch scripts are being retired.

