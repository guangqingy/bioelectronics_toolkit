# Web GUI Run Manifest Backbone

Date: 2026-05-08

## Purpose

The web GUI now has a project-level run history layer. Output-producing actions can save a JSON manifest into the project folder so a later browser session can recover:

- which interface created the output
- which input files were used
- which parameters and file profile were active
- which output files were written
- warnings/errors from the run

## Storage Layout

For a project root such as `/path/to/project`, records are stored under:

```text
/path/to/project/.dataprocess_cache/
  file_profiles.json
  run_history.json
  runs/
    <run_id>.json
    <run_id>.md
    <run_id>.zip
```

`run_history.json` is a compact index. Each JSON file in `runs/` is the full manifest. Markdown reports and zip archives are generated on demand from the Runs page.

## API

- `POST /api/run_history/record`
- `POST /api/run_history/list`
- `POST /api/run_history/get`
- `POST /api/run_history/check`
- `POST /api/run_history/report`
- `POST /api/run_history/package`

The shared frontend helper is:

```js
recordRunHistory({
  view: 'fluorescence_gif',
  title: 'Merged GIF',
  project_root: '/path/to/project',
  input_files: [{path: '/path/to/input.tif'}],
  outputs: [{path: '/path/to/output.gif'}],
  parameters: {},
});
```

The helper is intentionally non-blocking for user workflows: if manifest saving fails, the original export or analysis result is not discarded.

New manifests include parameter/input/output SHA-256 hashes plus server context such as Python version, host, platform, and source GUI route. The check endpoint compares recorded file size and mtime fingerprints against the current files and flags missing, changed, timestamp-changed, or newly created files.

## GUI Entry Point

Open `Settings` and use the `Run History` card to refresh the current project's recent runs. Clicking a row shows the manifest JSON in the card so output files and parameters can be checked without leaving the browser.

There is also a dedicated `/runs` page in the top navigation. It supports project-folder browsing, interface filtering, run counts, output counts, input/output tables, warnings/errors, and full manifest JSON inspection.

The `/runs` page can now open a saved manifest back in its originating interface. It stores the selected manifest in browser `sessionStorage`, navigates to the target page, and the target page applies matching parameters. Several high-use views also restore page state such as selected files, queues, detected pairs/pulses/peaks, ROI lists, TIFF stack settings, and GIF queues.

Parameter comparison is supported by selecting one manifest as the compare base, then selecting another manifest and using `Compare With Base`. The same page can also run a file check, write a markdown report, or create an output archive containing `manifest.json`, `report.md`, `package_index.json`, and existing output files.

## Connected Outputs

Initial wiring covers:

- Pipelines script runs
- CSV plot/full/merge exports
- ABF single exports, queue CSV exports, and ABF batch summaries
- ABF figure-generator exports
- RHD current-channel, all-channel, and queue exports
- EMG grouped peak exports
- EChem photocurrent pair exports
- EChem photovoltage pulse exports
- EChem lineshape average CSV exports
- TIFF single and batch stack exports
- Leica LIF order CSV exports
- Leica LIF selected and batch TIFF exports
- 3D TIFF viewer HTML exports
- ROI sequence output exports
- ROI sequence GIF exports
- GIF merge exports
- GIF ROI preview exports
- GIF ROI time-analysis exports
- GIF kymograph exports
- Histology rename and QuPath-name sync metadata updates

Current rerun behavior is intentionally manual-safe: a manifest can open the originating interface, restore parameters and page state, and then the user explicitly runs the export/analysis again. Full one-click rerun should only be added per interface where overwriting and missing-input behavior are unambiguous.
