"""
Centralized configuration loader for the bioelectronics_toolkit GUIs.

Reads `config.json` from the same directory as this file (the project root).
If `config.json` is missing, falls back to `config.example.json`, then to
built-in defaults that work out-of-the-box right after cloning.

To customize for your machine:
    1. Copy ``config.example.json`` to ``config.json``
    2. Edit values to taste

``config.json`` is gitignored, so your local settings stay local.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_USER_CFG = _HERE / "config.json"
_EXAMPLE_CFG = _HERE / "config.example.json"


def _load() -> dict:
    """Load the first available config file; return {} if none parseable."""
    for path in (_USER_CFG, _EXAMPLE_CFG):
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
    return {}


_cfg = _load()


def _expand_path(value: Any, fallback: str) -> str:
    """Return an expanded path string; fallback when value is missing/empty."""
    if not value or not isinstance(value, str):
        return fallback
    return os.path.expanduser(value)


# ---- Public configuration values ----

#: Default starting directory for folder/file pickers across all GUIs.
#: Falls back to the project root (the directory containing this module),
#: so the toolkit works immediately after cloning without any config edits.
DEFAULT_START_DIR: str = _expand_path(
    _cfg.get("default_start_dir"),
    fallback=str(_HERE),
)


def get(key: str, default: Any = None) -> Any:
    """Generic accessor for any extra key stored in ``config.json``."""
    return _cfg.get(key, default)
