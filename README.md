# bioelectronics_toolkit

[![CI](https://github.com/guangqingy/bioelectronics_toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/guangqingy/bioelectronics_toolkit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://www.conventionalcommits.org)

![30 second WebGUI demo](web_static/img/demo_webgui.gif)

![DataProcess WebGUI screenshot](web_static/img/screenshot_home.png)

## Who This Is For

`bioelectronics_toolkit` is for researchers who need a local, inspectable way to
turn common bioelectronics lab files into reproducible figures, CSV exports,
and run manifests. It is intentionally desktop-first: the WebGUI is the
canonical surface for day-to-day analysis, while shared `services/` modules keep
the scientific logic testable and reusable from launchers, scripts, and future
automation.

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

```mermaid
flowchart LR
    user[Researcher] --> web[Local WebGUI]
    user --> launcher[bte-* launchers]
    web --> api[web_api routes]
    launcher --> api
    api --> services[services algorithms]
    services --> outputs[CSV / PNG / SVG / manifests]
    desktop[thin desktop launchers] --> launcher
```

## Features

The commands below are stable user entry points after `pip install -e .`. Most
GUI commands open the corresponding WebGUI page; desktop helpers that remain as
native windows call shared `services/` modules instead of carrying their own
analysis engines. Source launcher modules are grouped under
`desktop_apps/launchers/`, native Tk helper windows under
`desktop_apps/native/`, and command-line compatibility wrappers under
`desktop_apps/cli/`.

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
| `bte-echem-photocurrent` | Photocurrent waveform analysis and plotting. |
| `bte-echem-photovoltage` | Photovoltage waveform analysis and plotting. |

### EMG / Intan

| Command | What it does |
| --- | --- |
| `bte-emg-analysis` | Browse, preview, process, rename, and export Intan `.rhd` EMG recordings. |
| `bte-emg-peak-selection` | Manual / semi-automatic peak selection on EMG traces. |

Intan `.rhd` parsing is provided by the vendored reference parser under
`vendor/intan/`.

### Fluorescence imaging

| Command | What it does |
| --- | --- |
| `bte-fl-roi` | ROI-based intensity analysis on TIFF stacks. |
| `bte-fl-lut` | Lookup-table editor / preview. |
| `bte-fl-gif` | Convert multi-page TIFFs to GIFs for sharing. |
| `bte-fl-preview-export` | Service-backed fluorescence TIFF preview/export helper. |
| `bte-fl-manual-roi` | English manual polygon ROI desktop tool using shared ROI services. |
| `bte-fl-marker-roi` | CLI analysis for manual fluorescence ROIs with DAPI/SMA/macrophage outputs. |

### Misc utilities

| Command | What it does |
| --- | --- |
| `bte-csv-viewer` | Browse and overlay CSV traces in a folder. |
| `bte-histology` | Standardize histology naming and run direct ETS ROI marker analysis. |
| `bte-histology-analysis` | Open the Web histology ROI analysis and tuning page. |
| `bte-histology-line-measure` | English service-backed line measurement tool for histology images. |

### Web app

`web_app.py` is a Flask app that wraps most of the analysis routines above
behind a unified browser UI. See [`docs/webgui.md`](./docs/webgui.md) for the
internal architecture (route modules, templates, shared helpers, API response
contracts, background jobs, and local cache behavior).

The WebGUI is designed for desktop browsers (Chrome, Edge, Firefox, or Safari).
Mobile and tablet layouts are not currently supported.

```bash
python3 web_app.py
```

Then open `http://127.0.0.1:7433`.

## Installation

For non-developer users, start with [`START_HERE.md`](./START_HERE.md). It
points to double-click scripts under `easy_start/` that create the local Python
environment and launch the WebGUI.

Requires Python 3.10-3.12 (3.12 recommended).

```bash
# 1. clone
git clone git@github.com:guangqingy/bioelectronics_toolkit.git
cd bioelectronics_toolkit

# 2. create a virtual environment (optional but recommended)
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate

# 3. install dependencies
pip install -e .
```

For a reproducible Python 3.12 setup matching the checked lock file, use:

```bash
pip install -c requirements-lock.txt -e ".[dev]"
```

CI tests normal dependency resolution on Python 3.10, 3.11, and 3.12. The
`requirements-lock.txt` file records a known-good Python 3.12 dependency set for
release and reproduction checks.

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
| `.dataprocess_cache/web_gui_settings.json` | Global and per-view default parameters for the local Web GUI. |
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
bte-echem-photocurrent
bte-fl-roi
# ...etc
```

For source-tree execution without installing entry points, run launcher modules
with `python3 -m`, for example:

```bash
python3 -m desktop_apps.launchers.fluorescence_roi_gui
```

Installed commands include normal CLI help:

```bash
bte-abf-batch --help      # WebGUI launcher options
bte-web --help            # Flask/WebGUI entry point
```

## Try It In 30 Seconds

The repository includes tiny synthetic examples under [`examples/`](./examples/)
so a fresh clone can exercise the UI without lab data:

```bash
pip install -e .
bte-web --self-check
python3 web_app.py
```

Then open `http://127.0.0.1:7433` and try:

| File | Suggested page |
| --- | --- |
| `examples/sample_patch_clamp.abf` | ABF viewer / peak detection |
| `examples/sample_echem_photocurrent.csv` | Photocurrent or CSV viewer |
| `examples/sample_fluorescence_stack.tif` | TIFF / ROI / GIF pages |

Additional screenshots:

![ABF viewer screenshot](web_static/img/screenshot_abf_viewer.png)

![Fluorescence ROI screenshot](web_static/img/screenshot_fluorescence_roi.png)

## Project layout

```text
bioelectronics_toolkit/
├── START_HERE.md                   # non-developer setup guide
├── easy_start/                     # double-click install/start scripts
├── examples/                       # tiny synthetic demo data
├── web_app.py                      # local WebGUI entry point
├── config.example.json             # optional config template
├── web_gui_settings.example.json    # optional WebGUI settings template
│
├── services/                       # reusable scientific/data-processing logic
├── web_api/                        # Flask JSON/page routes
├── web_templates/                  # Jinja pages and partials
├── web_static/                     # CSS, JS, icons, vendored browser assets
├── desktop_apps/                   # installed bte-* launchers and service-backed helpers
├── tests/                          # pytest and Playwright tests
├── docs/                           # architecture, release, changelog, WebGUI docs
├── dev_scripts/                    # maintainer-only checks and helpers
├── vendor/                         # vendored reference parsers
├── .github/                        # CI, issue templates, contribution/security docs
```

## Development notes

- The web app is layered into thin `register_*_routes(app, ctx)` modules under
  `web_api/`. New tools should follow the contract documented in
  [`docs/webgui.md`](./docs/webgui.md).
- Repository organization and maintainer notes are indexed in
  [`docs/README.md`](./docs/README.md).
- Shared algorithms should live under `services/` before they are reused by
  both WebGUI routes and desktop entry points. Fluorescence stack export, ROI
  metrics, basic TIFF-to-GIF rendering, CSV trace merging, electrochemistry
  parsing/detection, ABF peak/baseline helpers, EMG peak helpers, and Intan
  `.rhd` channel/merge helpers now use this layer. Thin user-facing launchers live in
  `desktop_apps/launchers/`; native helper windows live in
  `desktop_apps/native/`; command-line compatibility wrappers live in
  `desktop_apps/cli/`. Temporary desktop tools must call shared services rather
  than carrying processing logic in Tk callbacks.
- Before committing Web GUI changes, run:

  ```bash
  python3 -m ruff check .
  python3 -m ruff check services tests desktop_apps/web_launcher.py desktop_apps/launchers desktop_apps/native desktop_apps/cli --select E,F,W,I --ignore E402
  python3 -m ruff check web_api --select F --ignore E402
  python3 -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
  bte-web --self-check
  python3 -m pytest tests --ignore=tests/e2e
  python3 dev_scripts/check_no_pyplot.py
  ```

- CI intentionally applies two lint levels: a whole-repository baseline for
  fatal syntax/undefined-name issues, and a stricter gate for maintained
  `services/`, `tests/`, and desktop launcher code. Web API modules also run a
  stricter unused-name/import gate.

## Contributing

Issues and pull requests are welcome. See
[`CODE_OF_CONDUCT.md`](./.github/CODE_OF_CONDUCT.md) and
[`CONTRIBUTING.md`](./.github/CONTRIBUTING.md) for the workflow (branch naming,
Conventional Commits, pre-commit hooks, CI expectations).

## Changelog

Notable changes to each release are tracked in
[`docs/CHANGELOG.md`](./docs/CHANGELOG.md).

## Citing this work

If you use `bioelectronics_toolkit` in academic work, please cite it via the
metadata in [`CITATION.cff`](./CITATION.cff). GitHub renders a "Cite this
repository" button on the right sidebar that copies a ready-to-paste BibTeX
or APA entry. Zenodo metadata is prepared in [`.zenodo.json`](./.zenodo.json);
the version DOI should be added here and to `CITATION.cff` after the repository
owner enables Zenodo for a tagged release.

## License

Released under the [MIT License](./LICENSE).
