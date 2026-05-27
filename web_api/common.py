"""
Shared utilities for web_api modules.

Centralises small helpers that were previously copy-pasted into every
register_*_routes() closure.
"""

import base64
import io
from pathlib import Path

from services.matplotlib_utils import close_figure

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


def fig_to_b64(fig, dpi=96, fmt="png", *, tight: bool = False):
    buf = io.BytesIO()
    save_kwargs = {"format": fmt, "dpi": dpi}
    if tight:
        save_kwargs["bbox_inches"] = "tight"
    fig.savefig(buf, **save_kwargs)
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    close_figure(fig)
    return data


def browse_files(folder, exts):
    p = Path(folder)
    if not p.is_dir():
        return []
    result = []
    for f in sorted(p.iterdir()):
        if f.suffix.lower() in exts:
            result.append({"name": f.name, "path": str(f)})
    return result


def browse_files_recursive(folder, exts, max_files=300):
    p = Path(folder)
    if not p.is_dir():
        return []
    result = []
    for f in sorted(p.rglob("*")):
        if f.suffix.lower() in exts:
            result.append({"name": f.name, "path": str(f), "rel": str(f.relative_to(p))})
            if len(result) >= max_files:
                break
    return result


def float_or(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def int_or(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def request_data():
    from flask import request

    if request.method == "GET":
        return request.args
    return request.json or {}


def apply_axes_limits(ax, xmin, xmax, ymin, ymax):
    if xmin is not None or xmax is not None:
        cur = ax.get_xlim()
        ax.set_xlim(
            xmin if xmin is not None else cur[0],
            xmax if xmax is not None else cur[1],
        )
    if ymin is not None or ymax is not None:
        cur = ax.get_ylim()
        ax.set_ylim(
            ymin if ymin is not None else cur[0],
            ymax if ymax is not None else cur[1],
        )
