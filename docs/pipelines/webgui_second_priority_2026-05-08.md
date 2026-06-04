# Web GUI Second-Priority Architecture Pass

Date: 2026-05-08

## What Changed

### Shared Frontend Core

Common browser helpers were moved out of `web_templates/base.html` into:

```text
web_static/js/dp_core.js
web_static/js/dp_settings_schema.js
web_static/js/dp_manifest.js
web_static/js/dp_settings.js
web_static/js/dp_profiles.js
web_static/js/dp_jobs.js
```

`dp_core.js` now owns:

- `toast`
- `setStatus`
- `api`
- API-envelope normalization
- basic job helpers: `dpJobGet`, `dpJobList`, `dpJobCancel`, `dpPollJob`
- job endpoint helper: `dpRunJobEndpoint`

`dp_manifest.js` now owns shared path, manifest, run-history, and manifest-restore helpers such as:

- `dpPathDir`
- `dpAsPathRecords`
- `recordRunHistory`
- `dpOpenManifestInView`
- `dpConsumePendingRunManifest`
- `dpApplyRunManifestFallback`

`dp_settings_schema.js` owns interface labels, route URLs, LUT options, and editable-default schemas.

`dp_settings.js` owns the Settings modal, global/default-folder preferences, page defaults, and native file/folder picker helpers.

`dp_profiles.js` owns generic file-profile load/save/delete behavior.

`dp_jobs.js` owns the lightweight Background Jobs monitor shown in the Settings panel.

`base.html` now only keeps the page shell, modal markup, Jinja-injected constants, and a small startup script.

### Unified API Envelope

All JSON responses under `/api/*` now pass through a central envelope layer:

```json
{
  "ok": true,
  "data": {},
  "outputs": [],
  "warnings": [],
  "error": null
}
```

The compatibility layer also keeps legacy fields at the top level. For example, an old response like:

```json
{"saved_path": "/tmp/out.csv"}
```

now becomes:

```json
{
  "ok": true,
  "data": {"saved_path": "/tmp/out.csv"},
  "outputs": [{"path": "/tmp/out.csv", "type": "csv"}],
  "warnings": [],
  "error": null,
  "saved_path": "/tmp/out.csv"
}
```

This lets existing pages keep working while new code can rely on the standard envelope.

The envelope layer now also infers output records from older response fields such as `saved_path`, `output_path`, `saved_paths`, `generated_files`, `stack_files`, `manifest_path`, `package_path`, and script `artifacts`. Background jobs store this inferred list on the job record as `job.outputs`, while preserving any legacy `data.outputs` list used by older batch-export pages. Ambiguous source metadata paths are not inferred automatically; true sidecar outputs, such as LIF metadata JSON files, are returned explicitly by their export route.

### Job System

A central in-memory job manager was added:

```text
web_api/jobs.py
```

Available job endpoints:

- `POST /api/jobs/list`
- `POST /api/jobs/get`
- `POST /api/jobs/cancel`
- `POST /api/jobs/cleanup`

Pipelines script execution now uses this job manager internally while preserving the previous `/api/scripts/run` and `/api/scripts/status` response shape.

Earlier migration notes referred to a Flask route job bridge for wrapping older synchronous API routes. Current route modules use body-driven job tasks instead, so background work no longer depends on manufacturing Flask request contexts.

The following heavier endpoints now have job-backed entry points:

- `POST /api/fluorescence/merge_gif_job`
- `POST /api/fluorescence/gif_roi/analyze_job`
- `POST /api/fluorescence/gif_roi/kymograph_job`
- `POST /api/fluorescence/lif/export_tiff_job`
- `POST /api/fluorescence/lif/export_tiff_batch_job`
- `POST /api/figure/run_job`
- `POST /api/abf_batch/process_job`
- `POST /api/rhd/export_all_job`
- `POST /api/rhd/export_queue_job`
- `POST /api/emg/export_job`
- `POST /api/emg/export_peaks_job`
- `POST /api/run_history/package_job`
- `POST /api/fluorescence/stack_export_job`
- `POST /api/fluorescence/stack_export_batch_job`
- `POST /api/fluorescence/normalize_job`
- `POST /api/fluorescence/make_gif_job`
- `POST /api/fluorescence/3d/export_volume_job`
- `POST /api/fluorescence/gif_roi/export_preview_job`
- `POST /api/fluorescence/gif_roi/export_job`
- `POST /api/fluorescence/gif_roi/kymograph_export_job`
- `POST /api/fluorescence/roi/export_sequence_job`
- `POST /api/fluorescence/roi/export_sequence_gif_job`
- `POST /api/fluorescence/lif/export_volume3d_job`
- `POST /api/csv/merge_job`
- `POST /api/csv/export_merge_job`
- `POST /api/csv/export_job`
- `POST /api/csv/export_csv_job`
- `POST /api/echem/export_job`
- `POST /api/echem_pv/export_job`
- `POST /api/echem/lineshape/export_avg_job`
- `POST /api/abf/export_job`
- `POST /api/abf/export_peaks_job`
- `POST /api/histology/rename_job`

The original synchronous endpoints are still present for compatibility, but the Web GUI now calls job-backed endpoints for the heavier or side-effect-heavy actions: GIF generation/analysis/export, fluorescence stack/3D/ROI exports, LIF TIFF exports, ABF figure/batch/single exports, RHD exports, EMG exports, CSV merge/export, echem exports, histology rename, and run-history packaging.

The Settings modal now includes a `Background Jobs` card, which lists recent in-memory jobs and can request cancellation for pending/running jobs.
Completed jobs also show an inferred output count and first output path when available.

## Repository Hygiene Follow-Up

- Added stdlib `unittest` coverage in `tests/test_webgui_contracts.py` for the
  API envelope, job output inference, page rendering, and CSV export job
  contract.
- Moved local Web GUI preferences out of tracked source state:
  `.dataprocess_cache/web_gui_settings.json` is ignored and `web_gui_settings.example.json`
  documents the expected shape.
- Removed tracked local editor/agent configuration from the repository and
  ignored `.claude/` and `.vscode/`.
- Updated `README.md`, `docs/webgui.md`, and `docs/pipelines/README.md` with
  current validation and cache behavior.

## Migration Direction

Recommended next steps:

- Move the remaining generic helpers from `base.html` into focused files such as `dp_settings.js`, `dp_profiles.js`, and `dp_manifest.js`.
- Keep download-only streaming endpoints synchronous unless a separate save-to-disk workflow is added. They are intentionally not routed through JSON jobs.
- Gradually update API route bodies to return `api_ok(...)` directly, leaving the envelope middleware as a safety net.
- Once templates no longer depend on legacy top-level fields, remove the compatibility copying layer.
