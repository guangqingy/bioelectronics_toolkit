# Maintenance Docs

These notes describe repository-level maintenance decisions.

## Index

- [Repository structure](repository_structure.md): current layout, recommended
  package direction, and near-term rules for keeping the repo maintainable.
- [WebGUI architecture](webgui.md): Flask route layout, API response contract,
  background jobs, local safety assumptions, and browser assets.
- [Changelog](CHANGELOG.md): notable release and unreleased changes.
- [Release checklist](release.md): PyPI trusted publishing, lock refresh, and
  Zenodo DOI steps.
- [Feature integration prompt](feature_integration_prompt.md): copy-paste
  prompt for future WebGUI, service, and launcher integration work.
- [GitHub repository checklist](github_repository_checklist.md): web-UI settings
  that cannot be represented directly in code.

## When To Update

Update these docs when:

- A desktop launcher or service-backed desktop helper gains or loses a major
  feature.
- A WebGUI route becomes the canonical implementation for a workflow.
- Shared analysis logic is extracted into a new service module.
- The package layout, console entry points, or local cache paths change.
- GitHub repository metadata, release settings, or social preview assets change.
