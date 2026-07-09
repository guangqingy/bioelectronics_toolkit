"""Summary CSV parsing, aggregation, and range helpers for figure generation."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .constants import EPS, INT_COLS_CANDIDATES, PEAK_COLS_CANDIDATES, POWER_COL_CANDIDATES

_RANGE_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_RANGE_RE = re.compile(rf"({_RANGE_NUMBER})\s*(?:-|\u2013|\u2014|to|\.\.)\s*({_RANGE_NUMBER})")


def _find_matching_column(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _parse_ranges(raw):
    out = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                _append_range(out, item[0], item[1])
            else:
                _parse_range_text(str(item or ""), out)
        return out

    _parse_range_text(str(raw or ""), out)
    return out


def _parse_range_text(text, out):
    for match in _RANGE_RE.finditer(text.strip()):
        _append_range(out, match.group(1), match.group(2))


def _append_range(out, raw_min, raw_max):
    try:
        xmin = float(str(raw_min).strip())
        xmax = float(str(raw_max).strip())
    except Exception:
        return
    if np.isfinite(xmin) and np.isfinite(xmax) and xmin != xmax:
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        out.append((xmin, xmax))


def _fmt_range_value(v):
    return f"{float(v):g}"


def _read_all_summaries(folder):
    csvs = sorted(folder.glob("summary_*.csv"))
    if not csvs:
        return None

    frames = []
    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            continue
        if df is None or df.empty:
            continue

        pcol = _find_matching_column(df, POWER_COL_CANDIDATES)
        if pcol is None:
            continue

        df = df.rename(columns={pcol: "power_density"}).copy()
        cols = ["power_density"]

        peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
        int_col = _find_matching_column(df, INT_COLS_CANDIDATES)
        if peak_col:
            cols.append(peak_col)
        if int_col:
            cols.append(int_col)

        frames.append(df[cols].copy())

    if not frames:
        return None

    out = pd.concat(frames, axis=0, ignore_index=True)
    out["power_density"] = pd.to_numeric(out["power_density"], errors="coerce")
    for c in PEAK_COLS_CANDIDATES + INT_COLS_CANDIDATES:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out.replace([np.inf, -np.inf], np.nan, inplace=True)
    out.dropna(subset=["power_density"], inplace=True)
    return out if not out.empty else None


def _aggregate(df, value_col):
    tmp = df[["power_density", value_col]].dropna()
    if tmp.empty:
        return pd.DataFrame(columns=["power_density", "mean", "sem", "n"])
    g = (
        tmp.groupby("power_density", as_index=False)
        .agg(mean=(value_col, "mean"), std=(value_col, "std"), n=(value_col, "count"))
        .sort_values("power_density", kind="mergesort")
    )
    g["std"] = g["std"].fillna(0.0)
    g["n"] = g["n"].fillna(0).astype(int)
    g["sem"] = 0.0
    m = g["n"].values > 1
    g.loc[m, "sem"] = g.loc[m, "std"].values / np.sqrt(g.loc[m, "n"].values.astype(float))
    return g[["power_density", "mean", "sem", "n"]]


def _raw_max_value(df, value_col):
    if df is None or df.empty or value_col not in df.columns:
        return None
    v = pd.to_numeric(df[value_col], errors="coerce").values
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    m = float(np.max(v))
    if (not np.isfinite(m)) or m == 0:
        return None
    return m


def _scale_group_by_factor(g, factor):
    if g is None or g.empty or "mean" not in g.columns:
        return None
    if (factor is None) or (not np.isfinite(factor)) or factor == 0:
        return None
    gg = g.copy()
    gg["mean"] = gg["mean"] / factor
    if "sem" in gg.columns:
        gg["sem"] = gg["sem"] / factor
    return gg


def _clip_to_range(df, xmin, xmax):
    m = np.isfinite(df["power_density"])
    g = df.loc[m]
    g = g[(g["power_density"] >= xmin) & (g["power_density"] <= xmax)]
    return g


def _min_positive_x(dfs):
    vals = []
    for df in dfs:
        if df is None or df.empty or "power_density" not in df:
            continue
        x = np.asarray(df["power_density"])
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            vals.append(np.min(x))
    return min(vals) if vals else EPS


def _unique_label(existing, label):
    if label not in existing:
        return label
    base = label
    k = 2
    while f"{base} ({k})" in existing:
        k += 1
    return f"{base} ({k})"


def _metric_flags(d):
    use_peak = bool(d.get("use_peak", True))
    use_integral = bool(d.get("use_integral", True))

    metrics = d.get("metrics")
    if isinstance(metrics, dict):
        use_peak = bool(metrics.get("peak", use_peak))
        use_integral = bool(metrics.get("integral", use_integral))
    elif isinstance(metrics, list):
        ms = {str(x).strip().lower() for x in metrics}
        use_peak = "peak" in ms
        use_integral = "integral" in ms

    metric_single = str(d.get("metric", "")).strip().lower()
    if metric_single in {"peak", "capacitance_peak", "capacitance_peak_norm"}:
        use_peak, use_integral = True, False
    elif metric_single in {"integral", "integral_charge", "integral_charge_norm"}:
        use_peak, use_integral = False, True

    return use_peak, use_integral


def _build_series(queue, use_peak, use_integral):
    series_peak = {}
    series_int = {}

    for item in queue:
        folder = Path(item.get("path", ""))
        if not folder.is_dir():
            continue
        base_label = str(item.get("label") or folder.name)

        df = _read_all_summaries(folder)
        if df is None:
            continue

        peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
        int_col = _find_matching_column(df, INT_COLS_CANDIDATES)

        if use_peak and peak_col:
            g = _aggregate(df, peak_col)
            if g is not None and not g.empty:
                label = _unique_label(series_peak, base_label)
                series_peak[label] = g
        if use_integral and int_col:
            g = _aggregate(df, int_col)
            if g is not None and not g.empty:
                label = _unique_label(series_int, base_label)
                series_int[label] = g

    return series_peak, series_int


def _default_linear_range(groups):
    xmax = 0.0
    for g in groups.values():
        if g is None or g.empty:
            continue
        x = pd.to_numeric(g["power_density"], errors="coerce").to_numpy()
        x = x[np.isfinite(x)]
        if x.size:
            xmax = max(xmax, float(np.max(x)))
    if xmax <= 0:
        xmax = 1.0
    return [(0.0, xmax)]


def _default_log_range(groups):
    all_dfs = [g for g in groups.values() if g is not None and not g.empty]
    minpos = _min_positive_x(all_dfs)
    xmax = minpos
    for g in all_dfs:
        x = pd.to_numeric(g["power_density"], errors="coerce").to_numpy()
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            xmax = max(xmax, float(np.max(x)))
    if xmax <= minpos:
        xmax = minpos * 10.0
    return [(float(minpos), float(xmax))]


def _resolve_output_root(main_folder, queue):
    p = Path(str(main_folder or "").strip()) if str(main_folder or "").strip() else None
    if p is not None and p.is_dir():
        return p
    if queue:
        q0 = Path(queue[0].get("path", ""))
        if q0.exists():
            return q0.parent if q0.is_dir() else q0.parent
    return None
