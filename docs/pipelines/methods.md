# Methods Notes

These notes summarize the algorithms exposed by the WebGUI and services layer.
They are intended as a starting point for manuscript Methods text; always cite
the exact toolkit version, commit, and run manifest for published results.

## ABF Patch-Clamp

- Input: Axon `.abf` files loaded through `pyabf`.
- Baseline: optional pre-window mean subtraction in milliseconds, or the
  default 38-40% sweep interval for compatibility with historical examples.
- Peak detection: `scipy.signal.find_peaks` on positive, negative, or absolute
  traces. Distance is configured in milliseconds and converted to samples from
  the sweep time base.
- Units: time in seconds internally; UI windows are commonly entered in
  milliseconds; amplitudes follow the ABF channel label.

## Electrochemistry Photocurrent

- Input: delimited text/CSV files with detected time and current columns, with a
  numeric-line fallback for headerless exports.
- Pair detection: positive peaks are found with `scipy.signal.find_peaks`; each
  positive peak is paired with the most negative point inside a configured
  delay window.
- Defaults: thresholds and delay windows are user-visible parameters and should
  be reported from the run manifest for each export.
- Units: time in seconds, current in milliamps when the source column is
  `current_mA`.

## Electrochemistry Photovoltage

- Input: delimited text/CSV files with detected time and voltage columns.
- Baseline removal: rolling median by default; Savitzky-Golay detrending is
  available when selected.
- Pulse detection: positive or negative pulse candidates use
  `scipy.signal.find_peaks` plus `scipy.signal.peak_widths`; spacing and minimum
  width are configured in milliseconds and converted to samples.
- Units: time in seconds, voltage in volts when the source column is voltage.

## EMG / RHD

- Input: Intan `.rhd` recordings through the vendored Intan reference parser, or
  exported CSV traces for peak review.
- Pairing/merge behavior: adjacent split `.rhd` files can be merged by filename
  sequence when requested.
- Peak selection: positive, negative, or both polarities use
  `scipy.signal.find_peaks`; adaptive thresholds derive from signal scale when
  enabled.
- Units: time in seconds; amplitude units follow the source trace and UI label.

## Fluorescence TIFF / LIF

- Input: TIFF stacks via `tifffile`; Leica LIF metadata and series access via
  `readlif`.
- Stack operations: selected pages, normalization, 3D export, GIF preview, and
  ROI metrics are implemented in `services/fluorescence/`.
- ROI metrics: rectangular and concentric ROI measurements compute per-frame
  intensity summaries with optional background correction and reference
  normalization.
- Calibration: pixel size and z-spacing are inferred from TIFF/LIF metadata
  when available; otherwise user-supplied values should be recorded in the run
  manifest.

## Histology

- Input: exported TIFF projects and marker-specific image files.
- ROI analysis: polygon masks are rasterized with Pillow; marker intensity and
  area summaries are computed inside the selected ROI.
- Naming: case/sample/file naming helpers standardize project layout without
  changing raw input data unless the user explicitly runs a filesystem action.

## Reproducibility

- Each saved run manifest records toolkit version, git commit, Python version,
  platform, and key dependency versions.
- CSV export comments and SVG metadata include the same provenance fields where
  the export helper is used.
- Golden regression tests pin representative numerical outputs from the bundled
  ABF, electrochemistry CSV, and fluorescence TIFF examples.
