"""
Shared utilities for web_api modules.

Centralises small helpers that were previously copy-pasted into every
register_*_routes() closure, and provides the unified echem file loader
used by both the PC and PV modules.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

# ── Regex for picking numbers out of plain-text data files ──────────────────
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")

# ── String → boolean helpers ─────────────────────────────────────────────────
_SAVE_MODES = frozenset({"save", "server", "path", "local", "source"})
_TRUE_STRS  = frozenset({"1", "true", "yes", "y", "on"})
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


# ── Unified electrochemistry file loader ─────────────────────────────────────

def load_echem_file(path, value_col_hints: list):
    """
    Load a two-column time/value file from an electrochemistry instrument.

    Supports tab-, comma-, and semicolon-separated .txt/.csv files.
    Falls back to a line-by-line numeric extraction when header-based
    parsing fails.

    Args:
        path:             Path-like pointing to the data file.
        value_col_hints:  List of lowercase substrings used to identify the
                          value (non-time) column from the file header.
                          e.g. ["<i>", "current", "i/m", "i/µ", "i/a"]  (PC)
                               ["voltage", "potential", "ewe", "v/"]     (PV)

    Returns:
        (t, v, t_col_name, v_col_name)
        where *t* and *v* are monotonically increasing float64 numpy arrays.
    """

    def _sort_by_time(t_arr, v_arr):
        if np.any(np.diff(t_arr) <= 0):
            order = np.argsort(t_arr)
            return t_arr[order], v_arr[order]
        return t_arr, v_arr

    def _parse_numeric_lines(p):
        """Last-resort: extract the first two numbers from each non-comment line."""
        t_list, v_list = [], []
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                nums = FLOAT_RE.findall(line.replace(",", " "))
                if len(nums) < 2:
                    continue
                try:
                    t_list.append(float(nums[0]))
                    v_list.append(float(nums[1]))
                except ValueError:
                    continue
        if not t_list:
            raise ValueError(f"No numeric data detected in: {Path(p).name}")
        t_arr = np.asarray(t_list, dtype=float)
        v_arr = np.asarray(v_list, dtype=float)
        return _sort_by_time(t_arr, v_arr)

    for sep in ("\t", ",", ";"):
        try:
            df = pd.read_csv(
                path,
                sep=sep,
                decimal=",",
                engine="python",
                header=0,
                encoding="latin-1",
            )
            t_col = next(
                (c for c in df.columns if any(k in c.lower() for k in ("time", "t/"))),
                None,
            )
            v_col = next(
                (c for c in df.columns if any(k in c.lower() for k in value_col_hints)),
                None,
            )
            if t_col and v_col:
                t_raw = pd.to_numeric(df[t_col], errors="coerce")
                v_raw = pd.to_numeric(df[v_col], errors="coerce")
                valid = (~t_raw.isna()) & (~v_raw.isna())
                t = t_raw[valid].to_numpy(dtype=float)
                v = v_raw[valid].to_numpy(dtype=float)
                if len(t) == 0:
                    continue
                t, v = _sort_by_time(t, v)
                return t, v, t_col, v_col
        except Exception:
            pass

    t, v = _parse_numeric_lines(path)
    return t, v, "time_s", "value"
