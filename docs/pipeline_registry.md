# Pipeline Registry

The WebGUI Pipeline Runner is catalog-driven. The canonical catalog is
`pipelines/registry.json`; `pipelines/registry.py` loads it, validates IDs, and
annotates each script with local availability.

This matters because many current pipelines are a catalog over project-specific
wrappers around local analysis folders such as `2025_Subcutaneous/`, which are
intentionally not tracked in the public repository. Fresh clones can still
inspect the workflows, but unavailable scripts are labeled `Local script
missing` in the UI and return a clear API response instead of failing with a
developer-only path.

## Contracts

- Script IDs are stable and used by saved settings and run manifests.
- Categories are rendered from the registry in `/scripts/<category>`.
- `/api/pipelines/catalog` exposes the same registry with availability fields.
- `/api/scripts/run` looks up scripts through `pipelines.registry`, not a local
  route-level map.

## Maintenance

When adding a pipeline:

1. Add or update the entry in `pipelines/registry.json`.
2. Update the matching file under `docs/pipelines/`.
3. Keep script paths relative to the repository root unless there is a strong
   reason to use an absolute path.
4. Run `python -m unittest discover -s tests` and `ruff check .`.
