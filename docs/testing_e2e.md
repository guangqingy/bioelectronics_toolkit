# Playwright E2E Testing

The e2e suite starts the Flask WebGUI on `127.0.0.1:7433` and drives it with
Playwright Chromium.

## Install

```bash
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

On Linux CI or a fresh Linux workstation, install browser system dependencies:

```bash
python -m playwright install --with-deps chromium
```

## Run

```bash
python -m pytest tests/e2e
```

To point the tests at an already running server:

```bash
DP_E2E_BASE_URL=http://127.0.0.1:7433 python -m pytest tests/e2e
```

Traces and screenshots are written under `test-results/e2e/`.

## Next Test To Add

The next high-value e2e test should cover the command palette more deeply:
open Cmd/Ctrl+K, search across multiple domains, keyboard through results, and
verify Escape closes the palette without navigation.
