# Repository Structure

This repository keeps desktop launchers under `desktop_apps/launchers/`, native
helper windows under `desktop_apps/native/`, command-line compatibility wrappers
under `desktop_apps/cli/`, and shared processing logic under `services/`. The
WebGUI is the canonical product surface and the default target for installed
desktop commands. Native desktop helpers should call shared services rather
than duplicating analysis logic.

## Current Layout

```text
DataProcess/
+-- START_HERE.md             # non-developer entry guide
+-- easy_start/               # double-click setup/start scripts
+-- examples/                 # tiny synthetic demo data
+-- web_app.py                # Flask composition root and local app entry
+-- config.example.json       # optional config template
+-- web_gui_settings.example.json
+-- README.md
+-- LICENSE
+-- desktop_apps/
|   +-- launchers/            # thin WebGUI/CLI launchers
|   +-- native/               # service-backed Tk helper windows
|   +-- cli/                  # small CLI compatibility wrappers
|   +-- web_launcher.py       # opens the canonical WebGUI page for each tool
+-- web_api/                  # Page/API routes, context, jobs, and local system routes
+-- services/                 # UI-independent processing services
+-- web_templates/            # Jinja templates
+-- web_static/               # CSS and shared JavaScript
+-- vendor/                   # vendored reference parsers kept out of root
+-- dev_scripts/              # maintainer-only repository scripts
+-- tests/                    # smoke and contract tests
+-- docs/                     # architecture, WebGUI, changelog, and release notes
+-- .github/                  # CI, contribution, security, and issue templates
```

This shape is supported by `pyproject.toml`, where GUI console scripts point at
`desktop_apps.web_launcher:*_main`, while direct source-tree launchers live
under `desktop_apps/launchers/`, native helper windows under
`desktop_apps/native/`, and pure CLI wrappers under `desktop_apps/cli/`.

## Recommended Direction

Keep root entry points thin. New shared behavior should move into import-safe
service modules before either desktop helpers or Web routes call it.

A future package layout can look like this:

```text
src/bioelectronics_toolkit/
+-- desktop/                  # thin desktop launchers and native helpers
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
  `main()` that opens WebGUI or calls a shared service.
- When desktop and Web need the same algorithm, extract the algorithm into a
  shared service module and have both surfaces import it. The fluorescence
  stack workflow now uses `services/fluorescence/stack.py`, ROI metrics use
  `services/fluorescence/roi.py`, and basic TIFF-to-GIF rendering uses
  `services/fluorescence/gif.py`. CSV, echem, ABF, EMG, and RHD helpers now
  also have service modules, so Web routes should not reimplement those core
  algorithms inline.
- Keep vendored reference parsers under `vendor/` when they are not maintained
  as first-party services. The Intan RHD parser lives under
  `vendor/intan/importrhdutilities.py`; `services/rhd.py` wraps the parts the
  app uses.
- Keep strict lint focused on maintained code. CI runs a whole-repository
  baseline for fatal issues, and the `E/F/W/I` style gate covers `services/`,
  `tests/`, and desktop launcher code while Web API modules run a stricter
  unused-name/import gate.
- Split large Web route files by feature area before moving package paths. The
  fluorescence Web routes are now separated into stack, 3D, GIF, and ROI
  registration modules while the older helper layer is being migrated.
- Keep `web_app.py` as a composition root. Local file-picker/shutdown behavior
  belongs in `web_api/system.py`, page route registration belongs in
  `web_api/pages.py`, and shared route registration dependencies belong in
  `web_api/context.py`.
- Keep stable page CSS/JS out of large templates. Use `web_static/css/` and
  `web_static/js/pages/` for page-specific assets once they no longer need
  Jinja interpolation. Shared Jinja fragments belong in
  `web_templates/partials/` rather than growing `base.html`.
- Prefer service-task jobs through `submit_json_task(...)`; route modules should
  not manufacture Flask request contexts for background work.
- Keep local state out of git: `.dataprocess_cache/web_gui_settings.json`, `config.json`, real
  data, generated outputs, `.dataprocess_cache/`, `__pycache__/`, and
  `.DS_Store`.
- Keep root friendly for non-developer users: setup guides, app entry points,
  examples, and essential packaging/config files can stay there; maintainer
  docs belong in `docs/` or `.github/`.
- Update `README.md` and `docs/webgui.md` whenever a user-facing workflow
  changes.

## Suggested Refactor Order

1. Add service modules for fluorescence TIFF/LUT, ROI, and GIF workflows. These
   have the largest desktop/Web drift today.
2. Reuse the same services from `web_api/fluorescence.py` and desktop helpers.
3. Keep thinning ABF, electrochemistry, EMG, RHD, and CSV Web routes as their
   service modules grow.
4. Expand service-level tests before tightening route-level lint on additional
   Web modules.
