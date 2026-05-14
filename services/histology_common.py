from __future__ import annotations

import re
from typing import Any


def sanitize_name(value: str, fallback: str = "Untitled") -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw:
        raw = fallback
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    raw = raw.strip("._-")
    return raw or fallback


def parse_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def normalize_rotate_deg(v: Any) -> int:
    deg = parse_int(v, 0)
    if deg not in {0, 90, 180, 270}:
        return 0
    return deg


_bool = parse_bool
_int = parse_int
_normalize_rotate_deg = normalize_rotate_deg
