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

The commands below are stable user entry points after `pip install -e .`. GUI
commands open the corresponding WebGUI page by default; the older Tkinter
windows remain available with `--legacy` and live under `desktop_apps/legacy/`.
The source launcher modules are grouped under `desktop_apps/launchers/`.

### Patch-clamp / `.abf` analysis

| Command | What it does |
| --- | --- |
| `bte-abf-batch` | Batch-process folders of `.abf` files, parse `{main}_{treat}_sample_..._.abf` filenames, optional reorganization and segment CSV export. |
| `bte-abf-peaks` | Interactive peak detection on ABF sweeps. |
| `bte-abf-sweep` | Quick visualization of individual ABF sweeps. |
| `bte-abf-pc-viewer` | Photocurrent-specific viewer with stim alignment. |
| `bte-abf-pc-figure` | Generates publication-quality photocurrent figures. |

### Electrochemistry

| Command | What it does |
| --- | --- |
| `bte-echem-pc` | Photocurrent waveform analysis and plotting. |
| `bte-echem-pv` | Photovoltage waveform analysis and plotting. |

### EMG / Intan

| Command | What it does |
| --- | --- |
| `bte-emg-viewer` | Viewer for Intan `.rhd` recordings. |
| `bte-emg-peaks` | Manual / semi-automatic peak selection on EMG traces. |
| `importrhdutilities.py` | Helper module for parsing `.rhd` files (used by the viewer). |

### Fluorescence imaging

| Command | What it does |
| --- | --- |
| `bte-fl-roi` | ROI-based intensity analysis on TIFF stacks. |
| `bte-fl-lut` | Lookup-table editor / preview. |
| `bte-fl-gif` | Convert multi-page TIFFs to GIFs for sharing. |

### Misc utilities

| Command | What it does |
| --- | --- |
| `bte-csv-viewer` | Browse and overlay CSV traces in a folder. |
| `bte-histology` | Standardize histology file naming. |

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

The WebGUI is the default desktop surface. Use the installed `bte-*` commands
for day-to-day work:

```bash
bte-abf-batch
bte-echem-pc
bte-fl-roi
# ...etc
```

To run the old Tkinter window for one-off legacy work, add `--legacy`:

```bash
bte-fl-roi --legacy
```

For source-tree execution without installing entry points, run launcher modules
with `python3 -m`, for example:

```bash
python3 -m desktop_apps.launchers.fluorescence_roi_gui
python3 -m desktop_apps.launchers.fluorescence_roi_gui --legacy
```

If a legacy tool complains about missing dependencies (e.g. `pyabf`,
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
├── desktop_apps/
│   ├── launchers/                  # thin WebGUI/CLI entry modules
│   ├── web_launcher.py             # maps desktop commands to WebGUI pages
│   └── legacy/                     # historical Tkinter applications
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
  metrics, basic TIFF-to-GIF rendering, CSV trace merging, electrochemistry
  parsing/detection, ABF peak/baseline helpers, EMG peak helpers, and RHD
  channel/merge helpers now use this layer. Thin user-facing launchers live in
  `desktop_apps/launchers/`; large historical Tkinter apps live under
  `desktop_apps/legacy/`.
- Pipeline-level documentation (per analysis flow) lives under
  [`pipeline_readmes/`](./pipeline_readmes/).
- Before committing Web GUI changes, run:

  ```bash
  python3 -m ruff check .
  python3 -m ruff check services tests desktop_apps/web_launcher.py desktop_apps/launchers --select E,F,W,I --ignore E402
  python3 -m ruff check web_api --select F --ignore E402
  python3 -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
  python3 -m unittest discover -s tests
  ```

- CI intentionally applies two lint levels: a whole-repository baseline for
  fatal syntax/undefined-name issues, and a stricter gate for maintained
  `services/`, `tests/`, and desktop launcher code. Web API modules also run a
  stricter unused-name/import gate. Historical Tkinter files under
  `desktop_apps/legacy/` are kept out of the strict style gate until they are
  ported or retired.

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
