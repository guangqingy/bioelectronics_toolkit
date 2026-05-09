# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `services/fluorescence/` shared service layer for stack/LUT export, ROI
  metrics, and TIFF-to-GIF rendering.
- Web-first desktop launcher package under `desktop_apps/`, with legacy
  Tkinter applications preserved under `desktop_apps/legacy/`.
- Focused fluorescence route modules for stack, 3D, GIF, and ROI endpoints.
- Shared service modules for CSV trace handling, electrochemistry parsing and
  detection, ABF baseline/peak helpers, EMG peak helpers, and RHD channel/merge
  helpers.
- Service-level unit tests for CSV, electrochemistry, ABF, EMG, and RHD
  primitives.
- Repository-level maintenance docs for structure, desktop/Web parity, and
  desktop migration strategy.
- `.gitattributes` for stable LF line endings and binary data handling.

### Changed
- Root `*_gui.py` files now act as thin WebGUI launchers by default; pass
  `--legacy` to open the historical Tkinter implementation.
- CI, packaging metadata, and README now target Python 3.10 - 3.12.
- Ruff is used as a baseline correctness gate for syntax and undefined-name
  issues while legacy scripts are gradually cleaned up.
- CI now applies stricter `E/F/W/I` lint to maintained `services/`, `tests/`,
  and Web launcher code while keeping legacy Tkinter outside that strict gate.
- Web API modules now run an all-module `F` lint gate for unused imports and
  undefined/unused names.
- WebGUI API responses, job records, run manifests, settings, and file-profile
  behavior are documented as the canonical interface.
- CSV, electrochemistry, ABF, EMG, and RHD Web routes now delegate more core
  numeric/data-loading behavior to shared services.

### Fixed
- Removed a Python 2-era `unichr` branch from `importrhdutilities.py`.

## [0.1.0] - 2026-05-09

### Added
- First public release of `bioelectronics_toolkit`.
- 14 desktop Tkinter + matplotlib GUIs covering ABF patch-clamp analysis,
  electrochemistry (photocurrent / photovoltage), EMG and Intan RHD viewing,
  fluorescence imaging (ROI / LUT / TIFF→GIF), histology naming, and CSV
  folder browsing.
- Flask web app (`web_app.py`) exposing most analysis routines through a
  unified browser UI, served at `http://127.0.0.1:7433`.
- Per-domain blueprint modules under `web_api/` (`abf_batch`, `abf_viewer`,
  `csv_viewer`, `echem_lineshape`, `echem_pc`, `echem_pv`, `emg_peaks`,
  `figure_generator`, `file_profiles`, `fluorescence`, `histology`, `jobs`,
  `lif_viewer`, `preferences`, `response`, `rhd_viewer`, `run_history`,
  `scripts_panel`).
- Centralized configuration loader (`config.py`) replacing per-script
  hard-coded paths; reads `config.json` (gitignored) with fallback to
  `config.example.json` and finally to the project root.
- Project metadata via `pyproject.toml` (PEP 621), including `[project.scripts]`
  console-script entry points (`bte-abf-batch`, `bte-echem-pc`, `bte-web`, …).
- MIT license, top-level README, dependency manifest (`requirements.txt`),
  and pipeline documentation under `pipeline_readmes/`.
- GitHub Actions CI workflow (lint + unittest + compileall).
- `sync_to_github.sh` helper script for quick chore-style syncs.

[Unreleased]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/guangqingy/bioelectronics_toolkit/releases/tag/v0.1.0
