# ADR-003: Retain Legacy Desktop Apps Temporarily

## Context

The project began as Tkinter desktop tools. The WebGUI is now the canonical
surface, but several historical workflows are still useful as fallback tools
while parity work continues.

## Decision

Keep `desktop_apps/legacy/` in the repository and expose it through
`--legacy` launcher flags where appropriate.

## Consequences

Users can still reach older workflows during migration, and regressions in the
WebGUI have a fallback path. The tradeoff is maintenance overhead: legacy files
must not become the place for new product behavior, and retirement should be
tracked in `docs/desktop_strategy.md`.
