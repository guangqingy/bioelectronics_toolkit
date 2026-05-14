# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Migrate telemetry, preferences, file profiles, run history, and scripts-panel
  request handling to Pydantic schemas, with OpenAPI request bodies generated
  from registered schemas.
- Migrate CSV viewer, ABF batch, and electrochemistry request handling to
  Pydantic schemas, including job endpoints and CSV export query validation.
- Migrate figure generator and histology request handling to Pydantic schemas,
  keeping direct routes and background job routes on the same validation path.
- Migrate EMG peaks request handling to Pydantic schemas across the current
  grouped-peak workflow and legacy peak-export compatibility endpoints.

## [0.5.0] - 2026-05-14

### Changed
- Major architecture refactor inverted the project layering: `web_api/` shrank
  from roughly 16k LOC to 7.7k LOC, `services/` grew from roughly 2.6k LOC to
  12.5k LOC, and `web_api/fluorescence.py` was reduced from 2,212 lines to a
  17-line composition entry point.
- ABF and CSV trace SVG exports now use the same clean, ungrouped axis/tick/data
  output style as the RHD viewer, and saved trace image exports use short
  incrementing suffixes.
- RHD processing plots can now be exported as CSV, PNG, or SVG through a shared
  processing service, and file/page profile controls are collapsed under
  advanced sections by default.
- RHD Viewer now exposes more analysis controls for envelope, smoothing, FFT,
  STFT, fitting, and figure output styling.
- Dense analysis controls now use mode-specific parameter groups so unrelated
  settings stay hidden until the matching function is selected.
- Architecture guardrails now cover page density, button naming, long-operation
  UX, route/service LOC budgets, design tokens, and service-ratio CI reporting.
- Request validation has started moving to Pydantic schemas, `/docs` exposes a
  lightweight OpenAPI view, and traceback responses are redacted outside debug
  mode with correlation IDs.
- Background job state is persisted to `.dataprocess_cache/jobs.sqlite`, and
  trace export naming now goes through a shared service helper.
- Large WebGUI route modules have been thinned into service modules for ABF,
  CSV, EMG, histology, jobs, run history, scripts, system pickers, file
  profiles, and fluorescence route context sharing; the service-ratio CI check
  now fails regressions instead of warning only.
- Shared frontend assets are split into focused JS and CSS modules, with
  oversized Fluorescence GIF/ROI page scripts broken into smaller page modules.

### Added
- Opt-in anonymous local telemetry counters, disabled by default, for page opens
  and export clicks without paths, filenames, parameters, or data contents.
- Service coverage reporting in CI and monthly metrics-log documentation.

## [0.4.0] - 2026-05-13

### Added
- Maintenance prompt documenting repository standards for future feature
  integration, WebGUI/service layering, pipeline registration, testing, and
  release updates.
- Windows CI smoke coverage for importing the WebGUI and compiling Python
  modules.
- Separate Fluorescence Timecourse and Kymograph pages, plus a top-level README
  demo GIF, Dependabot config, security policy, maintainer onboarding guide,
  architecture decision records, and a legacy desktop retirement tracker.

### Changed
- Fluorescence GIF, ROI, RHD, and EMG-heavy pages now use clearer split routes,
  collapsible sections, and more consistent Save/Export/Undo wording.
- ABF batch export workflow is centralized on the ABF Batch page instead of the
  ABF Viewer queue.

### Fixed
- Windows Browse picker failures no longer fall back to Tkinter in Flask worker
  threads and now tell users to paste a path manually.
- Windows Browse can request foreground permission before opening native picker
  dialogs.

## [0.3.0] - 2026-05-09

### Added
- Cmd/Ctrl+K command palette for jumping to WebGUI tools, demos, pipeline
  categories, run history, and the version API.
- `/api/version` endpoint for machine-readable WebGUI version and commit
  metadata.
- `bte-rhd-viewer` console command as the primary Intan RHD viewer entry point,
  with `bte-emg-viewer` retained as a compatibility alias.
- Canonical `pipelines/` registry for WebGUI Pipeline Runner categories,
  scripts, parameters, documentation links, and local availability checks.
- WebGUI version/commit display, keyboard-shortcut help, persistent error
  banner, generic file-list filters, and status progress bars.
- ABF batch dry-run mode with custom confirmation for filesystem changes and
  operation logs under `.dataprocess_cache/operation_logs/`.

### Changed
- Pipeline documentation moved under `docs/pipelines/`, and maintainer scripts
  moved under `dev_scripts/` to separate public workflow docs from repository
  maintenance utilities.
- Settings modal sections are separated into tabs for defaults, run history,
  jobs, and advanced JSON.
- Version labels omit commit metadata when the commit is unavailable instead of
  showing `unknown`.
- Pipeline Runner page and API now consume `pipelines/registry.json` instead of
  maintaining duplicate script maps in the route module and template.
- Dashboard now starts with demo-data entry points, recently used tools, and an
  all-tools board instead of requiring manual tool grouping.
- Top navigation is grouped by domain with dropdown menus and full tool names.
- Form affordances now include clearer units/tooltips for photocurrent
  detection controls, reset buttons for marked parameter sections, danger
  button styling, larger base text, and visible focus outlines.

### Fixed
- Removed the hard-coded developer path from the ABF viewer template.
- Web app version detection no longer imports Python 3.11-only `tomllib`, so
  the Python 3.10 CI job can import `web_app.py`.
- Restricted automatic default-path filling to actual folder/file/path inputs
  so token and parameter fields are not overwritten.

## [0.2.0] - 2026-05-09

### Added
- `services/fluorescence/` shared service layer for stack export, ROI
  metrics, and TIFF-to-GIF rendering.
- Web-first desktop launcher package under `desktop_apps/`, with legacy
  Tkinter applications preserved under `desktop_apps/legacy/`.
- Thin source-tree launcher modules grouped under `desktop_apps/launchers/` so
  the repository root stays focused on project-level entry points.
- Focused fluorescence route modules for stack, 3D, GIF, and ROI endpoints.
- Shared service modules for CSV trace handling, electrochemistry parsing and
  detection, ABF baseline/peak helpers, EMG peak helpers, and RHD channel/merge
  helpers.
- Service-level unit tests for CSV, electrochemistry, ABF, EMG, and RHD
  primitives.
- Repository-level maintenance docs for structure, desktop/Web parity, and
  desktop migration strategy.
- `.gitattributes` for stable LF line endings and binary data handling.
- Tiny synthetic example data under `examples/` for ABF, electrochemistry CSV,
  and fluorescence TIFF smoke/demo workflows.
- WebGUI screenshots and favicon assets under `web_static/img/` and
  `web_static/favicon.ico`.
- `tests/__init__.py` so unittest modules are also importable as a package.

### Changed
- Desktop GUI launchers now act as thin WebGUI launchers by default; pass
  `--legacy` to open the historical Tkinter implementation.
- CI, packaging metadata, and README now target Python 3.10 - 3.12.
- Ruff is used as a baseline correctness gate for syntax and undefined-name
  issues while legacy scripts are gradually cleaned up.
- CI now applies stricter `E/F/W/I` lint to maintained `services/`, `tests/`,
  and desktop launcher code while keeping legacy Tkinter outside that strict
  gate.
- Web API modules now run an all-module `F` lint gate for unused imports and
  undefined/unused names.
- WebGUI API responses, job records, run manifests, settings, and file-profile
  behavior are documented as the canonical interface.
- CSV, electrochemistry, ABF, EMG, and RHD Web routes now delegate more core
  numeric/data-loading behavior to shared services.
- Background job endpoints now use body-driven `submit_json_task(...)` workers
  instead of manufacturing Flask request contexts.
- `pyproject.toml` is now the single dependency source of truth; the duplicate
  hand-maintained `requirements.txt` has been removed.
- The Intan RHD reference parser moved from the repository root to
  `vendor/intan/importrhdutilities.py`.
- The `bte-web` command now exposes normal `--help`, `--host`, `--port`, and
  `--no-browser` CLI options.
- Development dependencies now list Ruff only; tests continue to run with
  stdlib `unittest`.

### Fixed
- Removed a Python 2-era `unichr` branch from `importrhdutilities.py`.

### Known Migration Notes
- `bte-fl-lut` opens the WebGUI fluorescence page by default, but the dedicated
  LUT editor still lives in `desktop_apps.legacy.fluorescence_lut_gui` until
  `services/fluorescence/lut.py` is added.

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
- MIT license, top-level README, dependency metadata, and pipeline
  documentation under `docs/pipelines/`.
- GitHub Actions CI workflow (lint + unittest + compileall).
- `sync_to_github.sh` helper script for quick chore-style syncs.

[Unreleased]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/guangqingy/bioelectronics_toolkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/guangqingy/bioelectronics_toolkit/releases/tag/v0.1.0
