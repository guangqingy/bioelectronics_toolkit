# ADR-002: Keep Services as Plain Modules

## Context

Analysis code is reused by WebGUI routes, desktop launchers, and tests. A
plugin system could make extension points explicit, but it would add
registration, discovery, versioning, and packaging complexity.

## Decision

Keep shared behavior in plain `services/` modules instead of introducing a
plugin system.

## Consequences

The code remains easy to inspect and test. Route modules can import concrete
service functions without runtime discovery. The tradeoff is that external
extensions need normal Python changes rather than installable plugins.
