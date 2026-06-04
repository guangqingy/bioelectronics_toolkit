# Onboarding for Maintainers

This project is a local-lab data processing toolkit with two surfaces:
WebGUI-first workflows and legacy Tkinter desktop tools. New behavior should
prefer the WebGUI and shared service modules.

## Key Files

- `web_app.py`: Flask composition root and local development entry point.
- `config.py`: local configuration loader for machine-specific defaults.
- `web_api/`: route modules for WebGUI pages and JSON APIs.
- `web_templates/`: Jinja templates for WebGUI views.
- `web_static/`: shared CSS and JavaScript for the browser UI.
- `services/`: reusable numerical and file-processing logic.
- `pipelines/`: catalog of project-specific data workflows.
- `desktop_apps/launchers/`: thin command launchers that open WebGUI pages.
- `desktop_apps/legacy/`: historical Tkinter applications kept for fallback.
- `tests/`: service and WebGUI contract tests.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 web_app.py
```

Then open `http://127.0.0.1:7433`. For quick confidence before a commit, run:

```bash
python -m pytest -q
python -m py_compile $(git ls-files '*.py')
```

## Current Technical Debt

- `fluorescence_lut` still opens the legacy editor; add
  `services/fluorescence/lut.py` before retiring it.
- `desktop_apps/legacy/` is intentionally retained, but most new work should
  land in `services/` plus `web_api/`.
- Pipeline registry entries point to local project trees; public clones can see
  the catalog but cannot run every workflow without the matching local data.
- Some WebGUI templates still contain page-local JavaScript. Prefer extracting
  larger scripts into `web_static/js/pages/` when touching those pages.

## Release Rhythm

1. Land focused commits with tests.
2. Update `docs/CHANGELOG.md` under `[Unreleased]`.
3. Tag a minor release when user-visible WebGUI or cross-platform changes
   accumulate.
4. Keep README screenshots or demo GIF current when the main UI changes.

## Contacts

- Primary maintainer: Guangqing
- Lab/security contact: guangqing@uchicago.edu
