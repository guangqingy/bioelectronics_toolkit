# Repository Structure

This repository keeps small root compatibility scripts for historical command
names, but the large Tkinter implementations now live under
`desktop_apps/legacy/`. The WebGUI is the more complete product surface and is
the default target for desktop launcher commands.

## Current Layout

```text
DataProcess/
+-- *_gui.py                  # thin compatibility launchers; use --legacy for Tkinter
+-- desktop_apps/
|   +-- web_launcher.py       # opens the canonical WebGUI page for each tool
|   +-- legacy/               # historical Tkinter applications
+-- web_app.py                # Flask composition root
+-- web_api/                  # Web route modules and shared Web services
+-- services/                 # UI-independent processing services
+-- web_templates/            # Jinja templates
+-- web_static/               # CSS and shared JavaScript
+-- tests/                    # smoke and contract tests
+-- pipeline_readmes/         # domain pipeline notes and audits
+-- docs/                     # repository-level maintenance notes
```

This shape is supported by `pyproject.toml`, where GUI console scripts point at
`desktop_apps.web_launcher:*_main`. Root modules such as
`fluorescence_roi_gui.py` remain as compatibility launchers.

## Recommended Direction

Keep root entry points thin. New shared behavior should move into import-safe
service modules before either the desktop legacy layer or Web layer calls it.

A future package layout can look like this:

```text
src/bioelectronics_toolkit/
+-- desktop/                  # thin Tkinter launchers
+-- web/
|   +-- app.py
|   +-- api/
|   +-- templates/
|   +-- static/
+-- services/                 # shared ABF, RHD, TIFF, EMG, echem, CSV logic
+-- config.py
```

Do this migration only after extracting duplicated algorithms. Moving files
first would make the code look cleaner without solving the real maintenance
problem.

## Near-Term Rules

- Use the WebGUI as the canonical reference for feature completeness, settings,
  file profiles, jobs, API envelopes, output records, and run manifests.
- Keep desktop GUI entry points import-safe and make sure each has a callable
  `main()` that opens WebGUI by default. Legacy Tkinter behavior should remain
  available through `--legacy` while users migrate.
- When desktop and Web need the same algorithm, extract the algorithm into a
  shared service module and have both surfaces import it. The fluorescence
  stack/LUT workflow now uses `services/fluorescence/stack.py`, ROI metrics use
  `services/fluorescence/roi.py`, and basic TIFF-to-GIF rendering uses
  `services/fluorescence/gif.py`.
- Split large Web route files by feature area before moving package paths. The
  fluorescence Web routes are now separated into stack, 3D, GIF, and ROI
  registration modules while the older helper layer is being migrated.
- Keep local state out of git: `web_gui_settings.json`, `config.json`, real
  data, generated outputs, `.dataprocess_cache/`, `__pycache__/`, and
  `.DS_Store`.
- Update `README.md`, `WEB_README.md`, and the relevant file in
  `pipeline_readmes/` whenever a user-facing workflow changes.

## Suggested Refactor Order

1. Add service modules for fluorescence TIFF/LUT, ROI, and GIF workflows. These
   have the largest desktop/Web drift today.
2. Reuse the same services from `web_api/fluorescence.py` and the root
   fluorescence desktop scripts.
3. Repeat for ABF peak detection and electrochemistry once fluorescence is
   stable.
4. Repeat the service-backed pattern for ABF peak detection, electrochemistry,
   EMG, and CSV workflows as those domains change.
