# Maintenance Docs

These notes describe repository-level maintenance decisions. They are separate
from `pipeline_readmes/`, which documents data-analysis workflows.

## Index

- [Repository structure](repository_structure.md): current layout, recommended
  package direction, and near-term rules for keeping the repo maintainable.
- [Desktop and WebGUI parity](desktop_web_parity.md): feature comparison across
  root desktop GUI scripts and WebGUI routes, with WebGUI treated as the
  canonical reference.
- [Desktop strategy](desktop_strategy.md): how root desktop GUI scripts should
  evolve into thin Web launchers or service-backed clients.

## When To Update

Update these docs when:

- A root desktop GUI gains or loses a major feature.
- A WebGUI route becomes the canonical implementation for a workflow.
- Shared analysis logic is extracted into a new service module.
- The package layout, console entry points, or local cache paths change.
