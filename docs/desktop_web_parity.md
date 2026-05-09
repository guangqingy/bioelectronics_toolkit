# Desktop And WebGUI Parity

The WebGUI is the canonical behavior reference for this repository. It has the
most complete settings layer, file-profile cache, background jobs, API response
envelopes, output discovery, and run manifests. Root desktop launcher scripts
open WebGUI by default; use `--legacy` to run the historical Tkinter
implementation when it is still needed for a one-off workflow.

## Parity Matrix

| Domain | Desktop entry point | WebGUI reference | Current status |
| --- | --- | --- | --- |
| CSV traces | `csv_folder_viewer_gui.py` | `/csv`, `web_api/csv_viewer.py` | Broadly aligned for browse, overlay, export, and merge. Web adds job/profile/manifest behavior. |
| ABF batch | `abf_batch_processor_gui.py` | `/abf/batch`, `web_api/abf_batch.py` | Core workflow is aligned, but Web output contracts and run records are richer. |
| ABF viewer/sweeps | `abf_sweep_viewer_gui.py`, `abf_photocurrent_viewer_gui.py` | `/abf/viewer`, `web_api/abf_viewer.py` | Similar viewing intent, split differently across desktop scripts. Web is the reference for browser behavior. |
| ABF peaks | `abf_peak_detection_gui.py` | `/abf/peaks`, `web_api/abf_viewer.py` | Algorithmic overlap exists, but desktop and Web should be reconciled through a shared service before claiming exact parity. |
| ABF figures | `abf_photocurrent_figure_gui.py` | `/abf/figure`, `web_api/figure_generator.py` | Similar output intent. Web is job-backed and manifest-aware. |
| Electrochemistry | `echem_photocurrent_gui.py`, `echem_photovoltage_gui.py` | `/echem/photocurrent`, `/echem/photovoltage` | Duplicated domain logic exists. Web is the reference for stable outputs and defaults. |
| RHD viewer | `emg_rhd_viewer_gui.py` | `/emg/rhd`, `web_api/rhd_viewer.py` | Broadly aligned for loading and export. Web adds recursive browse, jobs, profiles, and manifests. |
| EMG peaks | `emg_peak_selector_gui.py` | `/emg/peaks`, `web_api/emg_peaks.py` | Similar analysis goal. Web has the stronger integration with grouped export and run records. |
| TIFF/LUT | `fluorescence_lut_gui.py` | `/fluorescence`, `web_api/fluorescence.py` | Close feature overlap for LUT/range/background/denoise/export. Web adds profiles, jobs, batch manifests, and should be canonical. |
| Fluorescence ROI | `fluorescence_roi_gui.py` | `/fluorescence/roi`, `web_api/fluorescence.py` | Not full parity. Web adds per-file profiles, job-backed exports, radial/concentric ROI outputs, and DeltaF/F0 paths. Desktop keeps some older advanced plot options. |
| TIFF to GIF | `fluorescence_tiff_to_gif.py` | `/fluorescence/gif`, `web_api/fluorescence.py` | Core TIFF reading, slice selection, LUT rendering, scale bar, timestamp, GIF, and preview output are service-backed. Web remains richer for queue/merge workflows, ROI overlays, crop/ROI previews, analysis CSVs, kymographs, profiles, and jobs. |
| LIF viewer | none | `/fluorescence/lif`, `web_api/lif_viewer.py` | Web-only. This is acceptable if documented as a WebGUI-only workflow. |
| Histology naming | `histology_naming_gui.py` | `/histology`, `web_api/histology.py` | Root entry opens WebGUI. Legacy Tkinter implementation remains available under `desktop_apps/legacy/`. |

## Maintenance Recommendation

The best long-term fix is not to manually keep two independent GUIs identical.
Instead, extract shared processing into import-safe service modules and let both
the desktop and Web layers call those services.

Good first targets:

- Fluorescence TIFF/LUT export and sidecar handling. The initial shared service
  now lives in `services/fluorescence/stack.py`.
- Fluorescence ROI metric calculation. The initial shared metric service now
  lives in `services/fluorescence/roi.py`; Web ROI routes and the desktop ROI
  GUI have started using it.
- GIF frame selection, LUT rendering, scale-bar/timestamp overlay, and basic
  GIF/preview output now live in `services/fluorescence/gif.py`.
- Fluorescence ROI CSV/image output writers.
- GIF ROI overlay, crop, kymograph, and analysis exporters.
- ABF peak detection and resistance normalization.
- Electrochemistry waveform parsing and peak/export logic.

## User-Facing Guidance

- Prefer the WebGUI for batch work, cached defaults, per-file settings, and
  reproducible outputs.
- Use `python3 <tool>_gui.py --legacy` only for quick one-off inspection when
  the older Tkinter controls are still needed.
- When a desktop result differs from WebGUI output, treat the WebGUI as the
  expected behavior and either port the desktop script to the shared service or
  document it as a legacy-only feature.
