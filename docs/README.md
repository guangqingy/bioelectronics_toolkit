# Maintenance Docs

These notes describe repository-level maintenance decisions. They are separate
from `pipeline_readmes/`, which documents data-analysis workflows.

## Index

- [Repository structure](repository_structure.md): current layout, recommended
  package direction, and near-term rules for keeping the repo maintainable.
- [Desktop and WebGUI parity](desktop_web_parity.md): feature comparison across
  desktop launcher commands and WebGUI routes, with WebGUI treated as the
  canonical reference.
- [Desktop strategy](desktop_strategy.md): how desktop launcher modules should
  stay thin Web launchers or service-backed clients.
- [Pipeline registry](pipeline_registry.md): how the WebGUI Pipeline Runner
  discovers categories, scripts, parameters, and local availability.
- [GitHub repository checklist](github_repository_checklist.md): web-UI settings
  that cannot be represented directly in code.

## When To Update

Update these docs when:

- A desktop launcher or legacy GUI gains or loses a major feature.
- A WebGUI route becomes the canonical implementation for a workflow.
- Shared analysis logic is extracted into a new service module.
- The package layout, console entry points, or local cache paths change.
- GitHub repository metadata, release settings, or social preview assets change.
