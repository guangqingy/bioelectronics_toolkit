# Desktop GUI Strategy

The WebGUI is the canonical product surface. Launcher modules under
`desktop_apps/launchers/` should not grow into separate products with their own
divergent analysis logic.

## Preferred Future

Desktop entry points should be one of these:

- A thin launcher that starts the local WebGUI and opens the relevant page.
- A lightweight desktop client that imports shared `services/*` modules for the
  same processing behavior as WebGUI.
- A legacy script kept only because it supports a workflow that has not yet been
  ported to WebGUI.

Current launcher modules follow the first pattern: they open the canonical
WebGUI page by default and accept `--legacy` when the historical Tkinter window
is still needed. Thin launchers live in `desktop_apps/launchers/`; the large
Tkinter files live in `desktop_apps/legacy/`.

## Why This Matters

Maintaining two independent GUIs for the same analysis makes defaults, output
files, cache behavior, and numerical results drift over time. It is better to
have one canonical interaction model in WebGUI and one shared processing layer
below it.

## Migration Pattern

1. Extract the algorithm into `services/<domain>/`.
2. Update Web routes so they call the service.
3. Update the desktop script so it calls the same service or launches the Web
   page.
4. Keep a thin launcher module until users have moved to console commands or
   packaged entry points.

## Current Direction

- `services/fluorescence/stack.py` now owns TIFF stack defaults, preprocessing,
  LUT/Fiji macro settings, selected-stack export, and generated-TIFF detection.
- `services/fluorescence/roi.py` now owns ROI pairing, rectangular/concentric
  ROI metrics, background correction primitives, radial rows, ratios, and
  reference normalization.
- `services/fluorescence/gif.py` now owns TIFF frame counting, one-based slice
  selection, GIF LUT rendering, scale-bar/timestamp overlays, and GIF/preview
  writing. The desktop TIFF-to-GIF script and Web GIF routes share these
  primitives.
- LUT editing is the one remaining fluorescence workflow with a dedicated
  legacy Tkinter implementation (`desktop_apps.legacy.fluorescence_lut_gui`).
  `bte-fl-lut` opens the WebGUI fluorescence page by default, but track the
  legacy editor explicitly until a `services/fluorescence/lut.py` migration
  lands.
- `services/csv_tools.py`, `services/echem.py`, `services/abf.py`,
  `services/emg.py`, and `services/rhd.py` now own the first shared primitives
  for CSV trace merging, electrochemistry parsing/detection, ABF baseline/peak
  helpers, EMG peak helpers, and RHD channel/paired-file handling.
- Once the service layer is stable, remaining desktop entry points can stay as
  thin wrappers around the WebGUI or shared services. The compatibility
  launcher structure is already in place.
