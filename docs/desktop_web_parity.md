# Desktop And WebGUI Parity

The WebGUI is the canonical behavior reference for this repository. It has the
most complete settings layer, file-profile cache, background jobs, API response
envelopes, output discovery, and run manifests. Desktop launchers open WebGUI
by default; use `--legacy` to run the historical Tkinter implementation when it
is still needed for a one-off workflow.

## Parity Matrix

| Domain | Desktop entry point | WebGUI reference | Current status |
| --- | --- | --- | --- |
| CSV traces | `bte-csv-viewer` | `/csv`, `web_api/csv_viewer.py` | Broadly aligned for browse, overlay, export, and merge. Core CSV loading/merging is service-backed; Web adds job/profile/manifest behavior. |
| ABF batch | `bte-abf-batch` | `/abf/batch`, `web_api/abf_batch.py` | Core workflow is aligned, but Web output contracts and run records are richer. |
| ABF viewer/sweeps | `bte-abf-sweep`, `bte-abf-pc-viewer` | `/abf/viewer`, `web_api/abf_viewer.py` | Similar viewing intent, split differently across desktop commands. Web is the reference for browser behavior. |
| ABF peaks | `bte-abf-peaks` | `/abf/peaks`, `web_api/abf_viewer.py` | ABF baseline, resistance, and peak helpers are now in `services/abf.py`; remaining parity work is mostly route/UI behavior. |
| ABF figures | `bte-abf-pc-figure` | `/abf/figure`, `web_api/figure_generator.py` | Similar output intent. Web is job-backed and manifest-aware. |
| Electrochemistry | `bte-echem-pc`, `bte-echem-pv` | `/echem/photocurrent`, `/echem/photovoltage` | File parsing, baseline/detrend helpers, and PC/PV detection primitives are now service-backed. Web remains the reference for stable outputs and defaults. |
| RHD viewer | `bte-rhd-viewer` (`bte-emg-viewer` alias) | `/emg/rhd`, `web_api/rhd_viewer.py` | Channel naming, channel resolution, paired split-file merge, wide export table helpers, and dry-run recording renames are service-backed. Web adds recursive browse, jobs, profiles, and manifests. |
| EMG peaks | `bte-emg-peaks` | `/emg/peaks`, `web_api/emg_peaks.py` | EMG column selection, numeric cleaning, sampling-rate inference, adaptive peak kwargs, and polarity-aware detection helpers are service-backed. Web has the stronger integration with grouped export and run records. |
| TIFF/LUT | `bte-fl-lut` | `/fluorescence`, `web_api/fluorescence.py` | Default launcher opens WebGUI, while the dedicated LUT editor remains the legacy Tkinter fallback. TIFF stack export is service-backed; `services/fluorescence/lut.py` remains pending. |
| Fluorescence ROI | `bte-fl-roi` | `/fluorescence/roi`, `web_api/fluorescence.py` | Not full parity. Web adds per-file profiles, job-backed exports, radial/concentric ROI outputs, and DeltaF/F0 paths. Desktop keeps some older advanced plot options. |
| TIFF to GIF | `bte-fl-gif` | `/fluorescence/gif`, `web_api/fluorescence.py` | Core TIFF reading, slice selection, LUT rendering, scale bar, timestamp, GIF, and preview output are service-backed. Web remains richer for queue/merge workflows, ROI overlays, crop/ROI previews, analysis CSVs, kymographs, profiles, and jobs. |
| LIF viewer | none | `/fluorescence/lif`, `web_api/lif_viewer.py` | Web-only. This is acceptable if documented as a WebGUI-only workflow. |
| Histology | `bte-histology` | `/histology/naming`, `/histology/analysis`, `web_api/histology.py` | Desktop entry opens WebGUI naming. Web analysis now scans histology case folders for `.ets` images and stores DataProcess ROI/marker-analysis sidecars under `.dataprocess_histology/` while retaining the existing naming workflow. Legacy Tkinter implementation remains available under `desktop_apps/legacy/`. |

## Maintenance Recommendation

The best long-term fix is not to manually keep two independent GUIs identical.
Instead, extract shared processing into import-safe service modules and let both
the desktop and Web layers call those services.

Good first targets:

- Fluorescence TIFF stack export and sidecar handling. The initial shared
  service now lives in `services/fluorescence/stack.py`; LUT editing remains a
  documented migration gap.
- Fluorescence ROI metric calculation. The initial shared metric service now
  lives in `services/fluorescence/roi.py`; Web ROI routes and the desktop ROI
  GUI have started using it.
- GIF frame selection, LUT rendering, scale-bar/timestamp overlay, and basic
  GIF/preview output now live in `services/fluorescence/gif.py`.
- CSV trace loading/merging now lives in `services/csv_tools.py`.
- Electrochemistry parsing and PC/PV detection helpers now live in
  `services/echem.py`.
- ABF baseline, resistance, and peak helpers now live in `services/abf.py`.
- EMG numeric cleaning and polarity-aware peak helpers now live in
  `services/emg.py`.
- RHD channel and split-file merge helpers now live in `services/rhd.py`.
- Fluorescence ROI CSV/image output writers.
- GIF ROI overlay, crop, kymograph, and analysis exporters.

## Legacy Audit 2026-06-09

All 13 modules under `desktop_apps/legacy/` have a thin launcher and a WebGUI
route, with `--legacy` fallback still available. The only extra desktop launcher,
`desktop_apps/launchers/fluorescence_tiff_to_gif.py`, is a service-backed CLI
wrapper rather than a historical Tk GUI.

Changes made during this audit:

- ABF batch now restores the legacy `Pure CSV Conversion` path in WebGUI. It
  writes `_segment.csv` files only, skips power parsing and summary metrics, and
  uses the legacy manual `t0/t1` segment window for pure conversion.
- ABF viewer launchers now preserve the conflicting legacy defaults: the
  photocurrent viewer opens `/abf/viewer?rnorm=1`, while the sweep viewer opens
  `/abf/viewer` with R normalization off.
- EChem photocurrent and photovoltage WebGUI pages now restore the legacy
  preview figure exports: `<stem>_preview.png` and
  `<stem>_preview_signal.svg`, separate from pairs/pulses CSV export.

Confirmed parity points:

- ABF peaks, ABF figure generation, CSV viewer, RHD viewer, EMG peak selector,
  Fluorescence LUT stack export, and Histology naming/project creation all expose
  the legacy core actions and defaults through WebGUI controls or service-backed
  routes.
- EMG grouped export keeps the legacy fixed +/-100 ms per-peak segment window.
- Fluorescence ROI keeps the legacy core defaults for prefix, GIF duration,
  scale bar, label scale, background defaults, and BG-normalized plot mode, while
  WebGUI adds file profiles, radial/concentric ROI outputs, and run manifests.

Known intentional divergence:

- Fluorescence ROI WebGUI does not try to clone every historical notebook tab
  layout from the Tk app. The Web page is the maintained workflow for sequence
  CSV/plot/GIF/reference/radial outputs; the older advanced summary plots remain
  available through `bte-fl-roi --legacy` if a one-off reproduction is needed.

## User-Facing Guidance

- Prefer the WebGUI for batch work, cached defaults, per-file settings, and
  reproducible outputs.
- Use `bte-... --legacy` only for quick one-off inspection when the older
  Tkinter controls are still needed.
- When a desktop result differs from WebGUI output, treat the WebGUI as the
  expected behavior and either port the desktop script to the shared service or
  document it as a legacy-only feature.
