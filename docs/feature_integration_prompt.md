# Feature Integration Prompt

Use this prompt when adding or refactoring a feature in this repository. It is
written so it can be copied into a future AI/code-review session before the
implementation starts.

## Copy-Paste Prompt

```text
You are maintaining bioelectronics_toolkit, a research-lab toolkit for ABF,
Intan `.rhd` / EMG, electrochemistry, fluorescence imaging, histology naming, CSV viewing,
and run-history-backed local analysis workflows.

Treat the WebGUI as the canonical user surface. Desktop bte-* commands should
open the matching WebGUI route or call a shared service; do not add new
long-lived Tkinter analysis paths.

When integrating a new feature or migrating an existing workflow, follow these
rules:

1. Understand the existing shape first.
   - Read README.md, docs/webgui.md, .github/CONTRIBUTING.md, docs/README.md, and the
     relevant service/web_api/template/static files before editing.
   - Prefer existing local patterns over new framework ideas.
   - Keep changes scoped to the requested workflow and avoid unrelated cleanup.

2. Put logic in the right layer.
   - Reusable data loading, numeric analysis, parsing, detection, export table
     assembly, and file IO policy belong in services/<domain>/.
   - web_api/<domain>.py should validate request payloads, call services, and
     serialize responses. It should not become the analysis engine.
   - web_app.py should stay a composition root: app creation, shared context,
     route registration, version metadata, and top-level wiring only.
   - Templates should stay readable. Move large page-specific JavaScript to
     web_static/js/pages/ and page CSS to web_static/css/ or shared CSS.
   - Shared frontend behavior belongs in web_static/js/dp_*.js, not duplicated
     across templates.

3. Keep WebGUI behavior professional and visitor-friendly.
   - Never hard-code developer-local absolute paths in tracked code, templates,
     docs, tests, or examples.
   - Public clones should render pages cleanly. If local project data is
     missing, show a clear user-facing state instead of throwing a traceback or
     exposing a private path.
   - Use full user-facing names in navigation, cards, buttons, and docs.
     Abbreviations are acceptable only when the full name is nearby.
   - Make controls keyboard-navigable with visible focus states.
   - Blocking errors should use the persistent error banner; success can use a
     short toast; page state should use setStatus(...).
   - Long operations should expose job/progress/status feedback instead of
     leaving a disabled button as the only signal.
   - Destructive file operations need a clear confirmation, a dry-run mode when
     feasible, and a small operation log only when files are actually moved,
     renamed, or deleted.

4. Use the API and job contracts consistently.
   - Return api_ok/api_error-style envelopes from Web API routes.
   - New endpoints should explicitly report generated outputs/artifacts instead
     of relying on legacy response inference.
   - Background jobs should call service-task functions directly. Do not wrap a
     Flask route by manufacturing test_request_context.
   - Keep run manifests and file profiles compatible with existing cache paths.

5. Keep desktop launchers thin.
   - Update desktop_apps/web_launcher.py for any new or renamed user workflow.
   - Add console scripts in pyproject.toml only for real user-facing commands.
   - Native desktop helpers belong in desktop_apps/native/ and should call
     shared services.
   - Command-line compatibility wrappers belong in desktop_apps/cli/.
   - Avoid adding new root-level GUI scripts.

6. Add tests at the right level.
   - Service behavior: unit tests under tests/ that do not require a browser.
   - Web route contracts: Flask test_client smoke/API tests.
   - Repository contracts: tests that prevent hard-coded local paths, broken
     entry points, stale docs paths, and route/launcher drift.
   - For migrations, add tests that prove old duplicate maps or wrapper
     patterns are gone when that is the goal.

7. Update documentation and release notes.
   - Update README.md for user-visible workflows, examples, commands, or setup.
   - Update docs/webgui.md for web architecture/API contract changes.
   - Update docs/README.md or the relevant docs/*.md file for maintenance
     decisions.
   - Update docs/CHANGELOG.md under [Unreleased] for user-visible changes.
   - If changing package version or release metadata, keep pyproject.toml,
     CITATION.cff, docs/CHANGELOG.md links, and version tests in sync.

8. Run the validation chain before finishing.
   - python -m ruff check .
   - python -m ruff check services tests desktop_apps/web_launcher.py desktop_apps/launchers desktop_apps/native desktop_apps/cli --select E,F,W,I --ignore E402
   - python -m ruff check web_api --select F --ignore E402
   - python -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
   - python -m pytest tests --ignore=tests/e2e -v
   - coverage run --source=services -m pytest tests --ignore=tests/e2e && coverage report
   - python dev_scripts/check_services_ratio.py --warn-only
9. Final response should say exactly what changed, what was validated, and any
    remaining limitations. If a GitHub release or repository setting cannot be
    changed from code, say so explicitly.
```

## Maintainer Notes

This prompt is intentionally stricter than a minimal contribution guide. It is
meant to protect the current architecture from drifting back toward:

- monolithic route files and heavy templates;
- GUI-specific analysis logic that is hard to test;
- local-only paths leaking into public code;
- WebGUI/desktop launcher naming drift;
- silent long-running operations with no progress or manifest trail.

When a future change is small, apply the spirit of the prompt without creating
extra abstractions. When a future change touches a shared route, service,
launcher, or cache contract, apply the full checklist.

## Page Density Budget

Any single WebGUI page must not exceed **20 visible controls** in the default
state. Count parameters and buttons; do not count controls hidden inside closed
`<details>` sections or mode-specific groups that are currently hidden.

When a page approaches the limit, use this order:

1. Mode-specific groups: show only the controls relevant to the selected mode.
2. Collapsible advanced sections.
3. Split the workflow into a separate page when the budget is still exceeded by
   more than 50%.

Adding analysis to a page already at 18 or more visible controls requires a UX
review note in the PR description explaining which option was chosen and why.

## Save vs Export Naming

- **Save** means persisting program state, settings, profiles, or context.
- **Export** means writing a user artifact to disk, such as CSV, PNG, SVG, GIF,
  TIFF, JSON, or ZIP.

Never mix the two verbs for the same operation type.

## Button Hierarchy

- One primary button per page: the main Run, Detect, Generate, Analyze, or
  Preview action. Use `.btn-primary`.
- Secondary actions such as Browse, Add to Queue, and Reset use
  `.btn-secondary`.
- Destructive actions such as Clear, Remove, and Delete Profile use
  `.btn-danger` plus a confirmation dialog.
- Icon-only quick actions use `.btn-icon` with an accessible label or tooltip.

If a page has multiple run-type buttons, make them mode-specific so only the
relevant primary action is visible.

## Empty States

Every page that requires file selection, parameter entry, or a loaded dataset
must render a useful empty state before data is loaded:

1. A short prompt explaining what to do next.
2. A link or pointer to a relevant `examples/` path when examples exist.
3. Optionally, a static screenshot of the loaded state.

Do not render a blank main content area.

## Long-Running Operations

Any operation expected to run longer than three seconds must:

1. Show progress through `.status-progress`.
2. Disable the trigger button while running with `btnBusy(...)`.
3. Provide a Cancel button through the jobs API.
4. Display estimated time remaining when the operation can estimate it.
