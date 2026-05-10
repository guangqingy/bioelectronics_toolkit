# Feature Integration Prompt

Use this prompt when adding or refactoring a feature in this repository. It is
written so it can be copied into a future AI/code-review session before the
implementation starts.

## Copy-Paste Prompt

```text
You are maintaining bioelectronics_toolkit, a research-lab toolkit for ABF,
RHD/EMG, electrochemistry, fluorescence imaging, histology naming, CSV viewing,
and project-specific analysis pipelines.

Treat the WebGUI as the canonical user surface. Desktop bte-* commands should
open the matching WebGUI route by default, with legacy Tkinter behavior kept
only as a compatibility fallback when needed.

When integrating a new feature or migrating an existing workflow, follow these
rules:

1. Understand the existing shape first.
   - Read README.md, WEB_README.md, CONTRIBUTING.md, docs/README.md, and the
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
   - Public clones should render pages cleanly. If local project data/scripts
     are missing, show a clear "Local script missing" or equivalent state
     instead of throwing a traceback or exposing a private path.
   - Use full user-facing names in navigation, cards, buttons, and docs.
     Abbreviations are acceptable only when the full name is nearby.
   - Make controls keyboard-navigable with visible focus states.
   - Blocking errors should use the persistent error banner; success can use a
     short toast; page state should use setStatus(...).
   - Long operations should expose job/progress/status feedback instead of
     leaving a disabled button as the only signal.
   - Destructive file operations need a clear confirmation, a dry-run mode when
     feasible, and an operation log under .dataprocess_cache/.

4. Use the API and job contracts consistently.
   - Return api_ok/api_error-style envelopes from Web API routes.
   - New endpoints should explicitly report generated outputs/artifacts instead
     of relying on legacy response inference.
   - Background jobs should call service-task functions directly. Do not wrap a
     Flask route by manufacturing test_request_context.
   - Keep run manifests and file profiles compatible with existing cache paths.

5. Integrate pipelines through the registry.
   - Pipeline Runner metadata belongs in pipelines/registry.json.
   - Pipeline documentation belongs under docs/pipelines/.
   - Pipeline script IDs must be stable snake_case keys. Do not rename them once
     settings, run history, or manifests may reference them.
   - If a registered script lives in a local project data tree, mark that state
     clearly through availability metadata and docs.

6. Keep desktop launchers thin.
   - Update desktop_apps/web_launcher.py for any new or renamed user workflow.
   - Add console scripts in pyproject.toml only for real user-facing commands.
   - If a legacy GUI remains, place it under desktop_apps/legacy/ and avoid
     adding new root-level GUI scripts.

7. Add tests at the right level.
   - Service behavior: unit tests under tests/ that do not require a browser.
   - Web route contracts: Flask test_client smoke/API tests.
   - Repository contracts: tests that prevent hard-coded local paths, broken
     entry points, stale docs paths, and route/launcher drift.
   - For migrations, add tests that prove old duplicate maps or wrapper
     patterns are gone when that is the goal.

8. Update documentation and release notes.
   - Update README.md for user-visible workflows, examples, commands, or setup.
   - Update WEB_README.md for web architecture/API contract changes.
   - Update docs/README.md or the relevant docs/*.md file for maintenance
     decisions.
   - Update CHANGELOG.md under [Unreleased] for user-visible changes.
   - If changing package version or release metadata, keep pyproject.toml,
     CITATION.cff, CHANGELOG.md links, and version tests in sync.

9. Run the validation chain before finishing.
   - python -m ruff check .
   - python -m ruff check services tests desktop_apps/web_launcher.py desktop_apps/launchers --select E,F,W,I --ignore E402
   - python -m ruff check web_api --select F --ignore E402
   - python -m compileall -q -f $(git ls-files '*.py' | grep -v '^\.dataprocess_cache/')
   - python -m unittest discover -s tests -v
   - python dev_scripts/check_analysis_scripts.py when pipeline/script safety is touched

10. Final response should say exactly what changed, what was validated, and any
    remaining limitations. If a GitHub release or repository setting cannot be
    changed from code, say so explicitly.
```

## Maintainer Notes

This prompt is intentionally stricter than a minimal contribution guide. It is
meant to protect the current architecture from drifting back toward:

- monolithic route files and heavy templates;
- GUI-specific analysis logic that is hard to test;
- local-only paths leaking into public code;
- duplicate pipeline maps outside `pipelines/registry.json`;
- WebGUI/desktop launcher naming drift;
- silent long-running operations with no progress or manifest trail.

When a future change is small, apply the spirit of the prompt without creating
extra abstractions. When a future change touches a shared route, service,
launcher, cache contract, or pipeline registry entry, apply the full checklist.
