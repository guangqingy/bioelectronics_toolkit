# Release Checklist

Use this checklist when cutting a public release.

## Version

1. Update `pyproject.toml`, `CITATION.cff`, and `docs/CHANGELOG.md`.
2. Run the full local validation commands from `README.md`.
3. Confirm `bte-web --self-check` passes in a fresh environment.
4. Refresh `requirements-lock.txt` from a clean Python 3.12 virtualenv when
   dependency versions intentionally change.

## PyPI

The `Release` GitHub Actions workflow builds an sdist and wheel for tags named
`vX.Y.Z` and publishes with PyPI trusted publishing.

Before the first release, a project owner must configure PyPI:

1. Create or claim the `bioelectronics-toolkit` project on PyPI.
2. Add a trusted publisher for this GitHub repository.
3. Use workflow name `Release`, environment `pypi`, and project name
   `bioelectronics-toolkit`.

## Zenodo DOI

Zenodo DOI minting is an external repository setting, not a source-only change.
Before the next public release:

1. Enable the GitHub repository in Zenodo.
2. Confirm `.zenodo.json` renders the expected software metadata.
3. Create the GitHub release from the same `vX.Y.Z` tag.
4. Add the minted version DOI to `CITATION.cff` and the README citation badge.
