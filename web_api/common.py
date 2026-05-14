"""
Shared utilities for web_api modules.

Centralises small helpers that were previously copy-pasted into every
register_*_routes() closure.
"""

# ── String → boolean helpers ─────────────────────────────────────────────────
_SAVE_MODES = frozenset({"save", "server", "path", "local", "source"})
_TRUE_STRS = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_STRS = frozenset({"0", "false", "no", "n", "off"})


def mode_is_save(mode) -> bool:
    """Return True when *mode* indicates the result should be written to disk."""
    return str(mode or "").strip().lower() in _SAVE_MODES


def as_bool(v, default: bool = False) -> bool:
    """Coerce a JSON value to bool, with a sensible *default* for None/missing."""
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in _TRUE_STRS:
        return True
    if s in _FALSE_STRS:
        return False
    return default
