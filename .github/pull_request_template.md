## Summary

<!-- One short paragraph: what does this PR do and why? -->

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would break existing usage)
- [ ] Documentation only
- [ ] Refactor / chore

## Checklist

- [ ] Tests added or updated under `tests/` (if applicable)
- [ ] `python -m py_compile` passes for all touched files
- [ ] `python -m unittest discover -s tests` passes locally
- [ ] `ruff check .` passes
- [ ] Service coverage did not drop, or the drop is explained below
- [ ] `dev_scripts/check_services_ratio.py` has no baseline regression
- [ ] `dev_scripts/check_private_service_usage.py` passes
- [ ] Any baseline update is explained in the PR description
- [ ] Any new route file > 200 LOC, service file > 600 LOC, or JS file > 400 LOC includes a PR note explaining why splitting would be worse
- [ ] Any page with > 18 visible user-facing controls includes a UX review note
- [ ] New web routes use Pydantic request schemas and appear in OpenAPI docs
- [ ] docs/CHANGELOG.md updated under `[Unreleased]`
- [ ] README / docs updated if behavior changed

## Baseline / architecture notes

<!-- If services_ratio_baseline.json changed, explain why the regression is acceptable.
     If any touched file exceeds a LOC budget, explain why it should stay together.
     If a page exceeds the visible-control budget, summarize the UX grouping choice. -->

## Screenshots / sample output

<!-- For GUI changes, include a before/after screenshot. For analysis changes,
     paste a small sample CSV or plot. -->
