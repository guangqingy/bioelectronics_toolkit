# ADR-004: Pipeline Registry as Catalog Pointer

## Context

Many project workflows depend on large local datasets and analysis scripts that
are not suitable for the public repository. The WebGUI still needs a consistent
way to list and describe those workflows.

## Decision

Use `pipelines/registry.json` as a catalog of pointers to project-specific
scripts rather than requiring every script and dataset to live in this repo.

## Consequences

External users can inspect workflow categories, parameters, and availability
status without receiving private or oversized lab data. Local users can run the
same catalog when the referenced project trees exist. The tradeoff is that fresh
public clones will show some entries as `Local script missing`.
