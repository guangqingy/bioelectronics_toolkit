# DataProcess Web Architecture

DataProcess Web is the Flask browser interface for the desktop-oriented
bioelectronics toolkit. It wraps CSV, ABF, RHD, EMG, electrochemistry,
fluorescence, histology, and pipeline scripts behind a consistent local UI.

The app is intended to run on a trusted local machine:

```bash
python3 web_app.py
```

Open `http://127.0.0.1:7433` in a browser after the server starts.
Set `DATAPROCESS_WEB_NO_BROWSER=1` when another launcher should control which
tool page is opened.

## Layout

```text
DataProcess/
├── web_app.py                  # Flask composition root and page routes
├── desktop_apps/               # Web launchers plus legacy Tkinter apps
├── services/                   # Shared processing logic used by Web and desktop
├── web_api/                    # Domain API modules
│   ├── response.py             # Unified API envelope
│   ├── jobs.py                 # In-memory background job manager
│   ├── preferences.py          # Global and per-view defaults
│   ├── file_profiles.py        # Per-file cached UI settings
│   ├── run_history.py          # Run manifests and package/report helpers
│   ├── fluorescence.py         # Shared fluorescence helpers and route composition
│   ├── fluorescence_*_routes.py# Focused fluorescence route groups
│   └── *_viewer.py, *_pc.py... # Other domain-specific route modules
├── web_templates/              # Jinja pages, one main template per view
├── web_static/
│   ├── style.css
│   └── js/
│       ├── dp_core.js
│       ├── dp_settings_schema.js
│       ├── dp_manifest.js
│       ├── dp_settings.js
│       ├── dp_profiles.js
│       └── dp_jobs.js
├── tests/                      # Stdlib unittest smoke and contract tests
└── WEB_README.md
```

## API Contract

Every JSON response under `/api/*` is wrapped by `web_api.response`:

```json
{
  "ok": true,
  "data": {},
  "outputs": [],
  "warnings": [],
  "error": null
}
```

The compatibility layer also copies old top-level fields such as `saved_path`
or `img` so older pages keep working while newer code reads from `data`,
`outputs`, `warnings`, and `error`.

Output records are inferred from common legacy fields:

- Single paths: `saved_path`, `output_path`, `csv_path`, `plot_path`,
  `summary_path`, `manifest_path`, `package_path`, `combined_tiff`, `output_dir`.
- Path lists: `saved_paths`, `generated_files`, `stack_files`, `segment_paths`.
- Record lists: `outputs` and script `artifacts`.

Ambiguous source metadata paths are not inferred automatically. If an export
creates a metadata sidecar, the route should return it explicitly in
`outputs`.

## Background Jobs

Heavy or side-effect-heavy operations should expose a `*_job` endpoint. The
preferred adapter for older synchronous route bodies is:

```python
return submit_flask_route_job(
    app,
    jobs,
    "/api/domain/export",
    "domain.export",
    "Human-readable job title",
    api_domain_export,
    request.json or {},
)
```

The in-memory job manager exposes:

- `POST /api/jobs/list`
- `POST /api/jobs/get`
- `POST /api/jobs/cancel`
- `POST /api/jobs/cleanup`

Job records include `status`, `progress`, `message`, `data`, `outputs`,
`warnings`, and `error`. The Settings modal uses `dp_jobs.js` to show recent
jobs and cancellation controls.

## Local State

Local state is deliberately stored outside browser-local-only storage so it can
be reused from any browser on the same machine.

- `web_gui_settings.json`: global and per-view defaults. This is local cache and
  is gitignored. `web_gui_settings.example.json` documents the shape.
- `.dataprocess_cache/file_profiles.json`: per-project, per-file UI settings.
- `.dataprocess_cache/runs/*.json`: durable run records for generated
  outputs and later packaging/reporting.

Do not commit real data, local cache files, or machine-specific paths.

## Template Contract

Every tool page should follow the base shell:

1. `block controls`: left panel controls only.
2. `block main`: right-side preview and results only.
3. `block scripts`: page-specific JavaScript only.

Common browser helpers belong in `web_static/js/*.js`, not inline in
`base.html`. Page-specific code can stay in the template until it is stable
enough to extract.

## Route Module Contract

New domain modules should expose:

```python
def register_example_routes(app, ctx):
    ...
```

Guidelines:

- Keep route registration in `web_app.py`.
- Use shared helpers from `ctx`, such as `err`, `fig_to_b64`, `float_or`,
  `int_or`, `browse_files`, and optional dependency flags.
- Return JSON dictionaries or `api_ok(...)`/`api_error(...)`; the envelope
  middleware is the fallback.
- Provide job-backed routes for long-running exports and batch operations.
- Keep download-only streaming endpoints synchronous unless there is a separate
  save-to-disk workflow.

## Validation

Run these before committing WebGUI changes:

```bash
python3 -m ruff check .
python3 -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
python3 -m unittest discover -s tests -v
```

The test suite uses Flask's test client, so it does not require launching a
separate browser or server.

## Maintenance Notes

- Keep public documentation in sync with route/module changes.
- Avoid committing temporary files, generated outputs, pyc files, or local
  settings.
- Prefer focused changes in one domain module plus related template/static JS
  updates.
- When adding an export, record durable output paths in the API response so run
  manifests and the job monitor can reason about generated files.
- For large domains, prefer focused route modules such as
  `fluorescence_gif_routes.py` and shared service modules over adding more
  routes to a single monolithic file.
- Web routes should parse requests, call `services/*`, and return the unified
  API envelope. They should not become the only copy of a data-processing
  algorithm if a desktop entry point also needs that behavior.
- Fluorescence stack/LUT, ROI metric primitives, and basic TIFF-to-GIF frame
  generation are now service-backed. GIF ROI overlay/crop analysis remains the
  next fluorescence-specific extraction target.
