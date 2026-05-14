# Contributing to bioelectronics_toolkit

Thanks for your interest in contributing. This is a research-lab toolkit, so
the bar is "useful and consistent" rather than "production-grade." Small,
focused PRs are easier to review than large omnibus ones.

## Getting set up

```bash
git clone git@github.com:guangqingy/bioelectronics_toolkit.git
cd bioelectronics_toolkit

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"            # installs runtime dependencies + ruff
pre-commit install                 # auto-format on each commit
cp config.example.json config.json # optional: customize default paths
```

## Branch & commit conventions

- Work in a topic branch off `main`: `git switch -c feat/lif-zoom-fix`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org):
  - `feat(emg): add bandpass filter to RHD viewer`
  - `fix(echem): correct lineshape baseline window`
  - `docs(readme): clarify config.json fallback order`
  - `chore: bump ruff to 0.4.10`
  - `refactor(web_api): extract shared response envelope`
  - `test(fluorescence): smoke test for ROI export`
- One logical change per commit. If you find yourself writing "and" in the
  message, that's two commits.

## Before opening a PR

Run locally:

```bash
ruff check .
ruff check services tests desktop_apps/web_launcher.py desktop_apps/launchers --select E,F,W,I --ignore E402
ruff check web_api --select F --ignore E402
python3 -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
python3 -m unittest discover -s tests -v
coverage run --source=services -m unittest discover -s tests && coverage report
python3 dev_scripts/check_services_ratio.py
python3 dev_scripts/check_services_ratio.py --check-loc-budget --warn-only
python3 dev_scripts/check_private_service_usage.py
```

CI runs tests across Python 3.10 - 3.12. Lint has two levels: `ruff check .`
keeps a whole-repository correctness baseline, while the stricter command is
the maintained-code gate for `services/`, `tests/`, and desktop launcher code.
Web API modules also run an all-module unused-name/import gate. Historical
Tkinter files under `desktop_apps/legacy/` are intentionally outside that strict
style gate until a workflow is migrated or retired. Green CI is required to
merge.

Update [`CHANGELOG.md`](./CHANGELOG.md) under `[Unreleased]` describing
user-visible changes.

If `dev_scripts/services_ratio_baseline.json` changes, the PR description must
explain why the baseline update is acceptable. Normal feature work should make
the service:route ratio stay the same or improve; use
`python3 dev_scripts/check_services_ratio.py --update-baseline` only when the
regression is intentional and justified.

## Adding or changing a GUI workflow

The WebGUI is the canonical user surface. Launcher modules under
`desktop_apps/launchers/` should stay thin compatibility entry points that open
the matching WebGUI route by default.

For larger feature work, start by copying the maintenance prompt in
[`docs/feature_integration_prompt.md`](./docs/feature_integration_prompt.md)
into the planning or coding session.

1. Put reusable analysis logic in `services/<domain>/`.
2. Update the Web route/template first.
3. If a Tkinter fallback is still needed, keep it in `desktop_apps/legacy/` and
   make it call the same service logic where practical.
4. Add or update the route mapping in `desktop_apps/web_launcher.py`.
5. Add or update any thin source launcher in `desktop_apps/launchers/`.
6. Register or adjust console scripts in `pyproject.toml`.
7. Add tests under `tests/` for the service, launcher, and API contract.
8. Update `README.md`, `WEB_README.md`, and the relevant `docs/` note.
9. Use full user-facing names in navigation and cards; keep abbreviations only
   when the full name is also visible.
10. Make the workflow keyboard-navigable: focus states visible, dropdowns usable
   with focus, and common actions reachable without mouse-only gestures.
11. Send blocking errors through the persistent error banner, use toast messages
   for short-lived success feedback, and prefer `setStatus(...)` for inline
   page state.
12. For Pipeline Runner entries, use stable `snake_case` script IDs and do not
   rename them once saved settings or run manifests may exist.

## Adding a new web blueprint

Follow the contract documented in [`WEB_README.md`](./WEB_README.md):

- Expose `register_xxx_routes(app, ctx)` from `web_api/xxx.py`.
- Use shared helpers from `ctx` (`err`, `fig_to_b64`, `float_or`, …).
- Templates follow the `block controls / block main / block scripts` pattern
  in `web_templates/base.html`.
- Register the blueprint once from `web_app.py`.

## Repository style

- Python source targets Python 3.10+.
- Use LF line endings and UTF-8 text; `.gitattributes` and `.editorconfig`
  enforce the baseline.
- Keep generated outputs, local settings, data folders, caches, and notebooks
  out of git unless they are tiny documented examples.
- New service modules should be import-safe and testable without launching a
  GUI or browser.
- New or changed Web routes should keep reusable data loading, numeric
  transforms, detection logic, and export table assembly in `services/`; the
  route should mostly validate payloads, call the service, and serialize the
  response.
- Prefer small route modules over growing `web_app.py` or a single monolithic
  domain file.
- Single-file LOC budgets after refactor:
  - `services/<domain>.py` or `services/<domain>/<area>.py`: target <= 600 LOC
  - `web_api/<domain>_routes.py`: target <= 200 LOC
  - JavaScript module: target <= 400 LOC
- Files exceeding these budgets need a top-of-file comment explaining why
  splitting would be worse than keeping the behavior together.
- `dev_scripts/check_services_ratio.py` is a ratchet: existing modules may not
  move reusable logic back from `services/` into `web_api/`, and new Web API
  modules should start with service LOC >= route LOC.

## Reporting bugs

Use the bug-report issue template. Always include:

- Which tool / script
- Python version and OS
- Toolkit commit (`git rev-parse --short HEAD`)
- A minimal repro and the full traceback if applicable

## License

By contributing, you agree that your contributions are released under the
[MIT License](./LICENSE).
