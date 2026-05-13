# ADR-001: Use Flask for the WebGUI

## Context

The WebGUI is a local desktop-browser interface for lab data analysis. It needs
simple page rendering, form-driven JSON APIs, background job polling, and easy
startup from installed console commands.

## Decision

Use Flask instead of FastAPI for the WebGUI.

## Consequences

Flask keeps the app small, familiar, and easy to launch from local Python
environments. It also matches the existing Jinja template structure. The tradeoff
is that typed request models and automatic OpenAPI docs are not first-class; API
contracts are covered by tests and shared helper functions instead.
