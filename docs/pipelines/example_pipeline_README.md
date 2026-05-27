# Runnable Example Pipeline

This category exists so a public clone has at least one Pipeline Runner entry
that is runnable without private project data.

## Bundled Example Summary

`pipelines/examples/example_summary.py` reads:

- `examples/sample_patch_clamp.abf`
- `examples/sample_echem_photocurrent.csv`
- `examples/sample_fluorescence_stack.tif`

It writes three artifacts to `.dataprocess_cache/exports/pipeline_examples/`
by default:

- `example_pipeline_summary.csv`
- `example_pipeline_summary.json`
- `example_pipeline_summary.png`

The script is intentionally small and deterministic. It is a smoke pipeline for
installation checks, Pipeline Runner availability, and artifact collection; it
is not a replacement for the domain-specific analysis pages.
