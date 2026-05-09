# Pipeline Registry

`pipelines/registry.json` is the single source of truth for the WebGUI
Pipeline Runner. It describes the visible categories, script IDs, display
names, parameter fields, documentation links, and the local script path used
when a pipeline is runnable in a developer checkout.

The actual Subcutaneous analysis scripts are project-specific and are not
tracked in the public repository. Fresh clones still show the registered
pipelines, but the WebGUI marks unavailable scripts as `Local script missing`
instead of pretending they can run.

## Files

- `registry.json` - editable catalog for humans and reviewers.
- `registry.py` - loader, validation, availability checks, and script lookup.
- `pipeline_readmes/` - domain notes that explain the scientific workflow.

## Adding A Pipeline

1. Add the script entry to `registry.json`.
2. Give it a stable `id`; run history and saved settings key off this value.
3. Add parameter definitions with `key`, `label`, `type`, optional `default`,
   `desc`, and `options` where needed.
4. Document the workflow in `pipeline_readmes/`.
5. Run `python -m unittest discover -s tests` and `ruff check .`.
