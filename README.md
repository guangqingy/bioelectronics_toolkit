# bioelectronics_toolkit

[![CI](https://github.com/guangqingy/bioelectronics_toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/guangqingy/bioelectronics_toolkit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://www.conventionalcommits.org)

A collection of desktop GUIs and a lightweight Flask web app for processing
data from common bioelectronics and electrophysiology instruments. Tools cover
electrochemistry (photocurrent / photovoltage), EMG and patch-clamp recordings
(Axon `.abf`, Intan `.rhd`), fluorescence imaging (TIFF, LIF), histology
naming, and CSV trace review.

The desktop GUIs are built on Tkinter + matplotlib and run on macOS, Linux and
Windows. The web app exposes the most complete browser-based versions of these
workflows. For new feature work, the WebGUI is the canonical reference for
output contracts, cached settings, file profiles, background jobs, and run
manifests. The desktop GUIs remain supported standalone tools, but some older
desktop workflows are intentionally thinner than their WebGUI equivalents.

## Features

The script names below are stable user entry points. For GUI workflows they
open the corresponding WebGUI page by default; the older Tkinter windows remain
available with `--legacy` and live under `desktop_apps/legacy/`.

### Patch-clamp / `.abf` analysis

| Script | What it does |
| --- | --- |
| `abf_batch_processor_gui.py` | Batch-process folders of `.abf` files, parse `{main}_{treat}_sample_..._.abf` filenames, optional reorganization and segment CSV export. |
| `abf_peak_detection_gui.py` | Interactive peak detection on ABF sweeps. |
| `abf_sweep_viewer_gui.py` | Quick visualization of individual ABF sweeps. |
| `abf_photocurrent_viewer_gui.py` | Photocurrent-specific viewer with stim alignment. |
| `abf_photocurrent_figure_gui.py` | Generates publication-quality photocurrent figures. |

### Electrochemistry

| Script | What it does |
| --- | --- |
| `echem_photocurrent_gui.py` | Photocurrent waveform analysis and plotting. |
| `echem_photovoltage_gui.py` | Photovoltage waveform analysis and plotting. |

### EMG / Intan

| Script | What it does |
| --- | --- |
| `emg_rhd_viewer_gui.py` | Viewer for Intan `.rhd` recordings. |
| `emg_peak_selector_gui.py` | Manual / semi-automatic peak selection on EMG traces. |
| `importrhdutilities.py` | Helper module for parsing `.rhd` files (used by the viewer). |

### Fluorescence imaging

| Script | What it does |
| --- | --- |
| `fluorescence_roi_gui.py` | ROI-based intensity analysis on TIFF stacks. |
| `fluorescence_lut_gui.py` | Lookup-table editor / preview. |
| `fluorescence_tiff_to_gif.py` | Convert multi-page TIFFs to GIFs for sharing. |

### Misc utilities

| Script | What it does |
| --- | --- |
| `csv_folder_viewer_gui.py` | Browse and overlay CSV traces in a folder. |
| `histology_naming_gui.py` | Standardize histology file naming. |

### Web app

`web_app.py` is a Flask app that wraps most of the analysis routines above
behind a unified browser UI. See [`WEB_README.md`](./WEB_README.md) for the
internal architecture (route modules, templates, shared helpers, API response
contracts, background jobs, and local cache behavior).

```bash
python3 web_app.py
```

Then open `http://127.0.0.1:7433`.

## Installation

Requires Python 3.10+ (3.11 recommended).

```bash
# 1. clone
git clone git@github.com:guangqingy/bioelectronics_toolkit.git
cd bioelectronics_toolkit

# 2. create a virtual environment (optional but recommended)
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate

# 3. install dependencies
pip install -r requirements.txt
```

Tkinter is part of the Python standard library on macOS and Windows. On Linux
you may need to install it explicitly (e.g. `sudo apt install python3-tk`).

## Configuration

The toolkit ships with a tiny config layer (`config.py`) so paths and other
machine-specific settings stay out of the source.

```bash
cp config.example.json config.json   # then edit config.json
```

Currently supported keys:

| Key | Meaning | Default if unset |
| --- | --- | --- |
| `default_start_dir` | Initial folder shown by every GUI's file/folder picker | The repo root |

`config.json` is gitignored, so your local edits never leak into commits.

The Web GUI also creates local cache files while you work:

| Path | Meaning |
| --- | --- |
| `web_gui_settings.json` | Global and per-view default parameters for the local Web GUI. |
| `.dataprocess_cache/file_profiles.json` | Per-project, per-file cached UI settings. |
| `.dataprocess_cache/runs/` | Saved run manifests used by the Runs page and package/report tools. |

These files are gitignored. Use
[`web_gui_settings.example.json`](./web_gui_settings.example.json) as the
documented settings shape.

## Running desktop tools

The WebGUI is the default desktop surface. Historical script names still work,
but they now open the matching local WebGUI page:

```bash
python3 abf_batch_processor_gui.py
python3 echem_photocurrent_gui.py
python3 fluorescence_roi_gui.py
# ...etc
```

To run the old Tkinter window for one-off legacy work, add `--legacy`:

```bash
python3 fluorescence_roi_gui.py --legacy
```

After `pip install -e .`, the `bte-*` commands also open WebGUI pages by
default. If a legacy tool complains about missing dependencies (e.g. `pyabf`,
`pyserial`), make sure your virtualenv is active and `pip install -r
requirements.txt` ran without errors.

## Project layout

```text
bioelectronics_toolkit/
├── README.md                       # this file
├── LICENSE                         # MIT
├── requirements.txt
├── config.py                       # config loader
├── config.example.json             # config template (copy to config.json)
├── web_gui_settings.example.json    # Web GUI local settings template
├── .gitignore
│
├── *_gui.py                        # thin WebGUI launchers; --legacy opens Tkinter
├── desktop_apps/
│   ├── web_launcher.py             # maps desktop commands to WebGUI pages
│   └── legacy/                     # historical Tkinter applications
├── fluorescence_tiff_to_gif.py      # service-backed TIFF-to-GIF CLI
├── importrhdutilities.py            # Intan `.rhd` parsing helper
│
├── web_app.py                      # Flask app entry point
├── web_api/                        # route modules per domain
├── services/                       # shared processing logic used by UI surfaces
├── web_templates/                  # Jinja templates
├── web_static/                     # CSS / JS assets
├── WEB_README.md                   # web-app architecture notes
├── docs/                           # repository structure and parity notes
├── tests/                          # stdlib unittest contract/smoke tests
│
└── pipeline_readmes/               # data-processing pipeline docs
```

## Development notes

- The web app is layered into thin `register_*_routes(app, ctx)` modules under
  `web_api/`. New tools should follow the contract documented in
  [`WEB_README.md`](./WEB_README.md).
- Repository organization and desktop/Web parity notes are indexed in
  [`docs/README.md`](./docs/README.md).
- Shared algorithms should live under `services/` before they are reused by
  both WebGUI routes and desktop entry points. Fluorescence stack/LUT, ROI
  metrics, and basic TIFF-to-GIF rendering now use this layer. Large historical
  Tkinter apps live under `desktop_apps/legacy/`; root `*_gui.py` files should
  stay as thin Web launchers.
- Pipeline-level documentation (per analysis flow) lives under
  [`pipeline_readmes/`](./pipeline_readmes/).
- Before committing Web GUI changes, run:

  ```bash
  python3 -m ruff check .
  python3 -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
  python3 -m unittest discover -s tests
  ```

- Style: PEP 8, `# noqa: E402` is used in GUI scripts where the
  `from config import DEFAULT_START_DIR` import sits between the stdlib block
  and module-level matplotlib config.

## Contributing

Issues and pull requests are welcome. See [`CONTRIBUTING.md`](./CONTRIBUTING.md)
for the workflow (branch naming, Conventional Commits, pre-commit hooks, CI
expectations).

## Changelog

Notable changes to each release are tracked in [`CHANGELOG.md`](./CHANGELOG.md).

## Citing this work

If you use `bioelectronics_toolkit` in academic work, please cite it via the
metadata in [`CITATION.cff`](./CITATION.cff). GitHub renders a "Cite this
repository" button on the right sidebar that copies a ready-to-paste BibTeX
or APA entry.

## License

Released under the [MIT License](./LICENSE).
