from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")

PC_VALUE_COL_HINTS = ["<i>", "current", "i/m", "i/\u00b5", "i/a", "ewe"]
PV_VALUE_COL_HINTS = ["voltage", "potential", "ewe", "v/"]


def _sort_by_time(t_arr: np.ndarray, v_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if np.any(np.diff(t_arr) <= 0):
        order = np.argsort(t_arr)
        return t_arr[order], v_arr[order]
    return t_arr, v_arr


def _parse_numeric_lines(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    t_list: list[float] = []
    v_list: list[float] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
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
        raise ValueError(f"No numeric data detected in: {Path(path).name}")
    return _sort_by_time(np.asarray(t_list, dtype=float), np.asarray(v_list, dtype=float))


def load_echem_file(path: str | Path, value_col_hints: list[str]):
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
            if not t_col or not v_col:
                continue

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


def load_photocurrent(path: str | Path):
    return load_echem_file(path, PC_VALUE_COL_HINTS)


def load_photovoltage(path: str | Path):
    return load_echem_file(path, PV_VALUE_COL_HINTS)


def detect_photocurrent_pairs(
    t: np.ndarray,
    signal: np.ndarray,
    t0: float,
    t1: float,
    pos_min_mA: float,
    neg_min_abs_mA: float,
    min_delay_ms: float,
    max_delay_ms: float,
    min_pos_distance_ms: float,
    find_peaks_func: Callable,
) -> list[tuple[int, int]]:
    if t1 <= t0:
        return []

    start = max(0, int(np.searchsorted(t, t0, side="left")))
    end = min(len(t), int(np.searchsorted(t, t1, side="right")))
    if end - start < 3:
        return []

    tt = t[start:end]
    yy = signal[start:end]
    if len(tt) > 1:
        dt = float(np.median(np.diff(tt)))
    elif len(t) > 1:
        dt = float(np.median(np.diff(t)))
    else:
        dt = 1e-3
    if dt <= 0:
        dt = 1e-3

    fs = 1.0 / dt
    distance = max(1, int((float(min_pos_distance_ms) / 1000.0) * fs))
    pos_loc, _props = find_peaks_func(yy, height=float(pos_min_mA), distance=distance)

    pairs: list[tuple[int, int]] = []
    for ip in pos_loc:
        j0 = int(ip + max(1, int((float(min_delay_ms) / 1000.0) * fs)))
        j1 = int(min(len(tt), ip + int((float(max_delay_ms) / 1000.0) * fs)))
        if j1 - j0 < 2:
            continue

        neg_local = j0 + int(np.argmin(yy[j0:j1]))
        pos_val = float(yy[ip])
        neg_val = float(yy[neg_local])
        if pos_val >= pos_min_mA and abs(neg_val) >= neg_min_abs_mA:
            pairs.append((start + int(ip), start + int(neg_local)))
    return pairs


def normalize_baseline_method(method: object) -> str:
    text = str(method or "median").strip().lower()
    if text in {"median", "rolling_median"}:
        return "median"
    if text == "savgol":
        return "savgol"
    return "median"


def rolling_median(x: np.ndarray, win_pts: int) -> np.ndarray:
    if win_pts <= 1:
        return np.zeros_like(x)
    win_pts = int(win_pts | 1)
    pad = win_pts // 2
    xp = np.pad(x, pad, mode="edge")
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(xp[i : i + win_pts])
    return out


def detrend_signal(
    t: np.ndarray,
    values: np.ndarray,
    method: str = "median",
    window_ms: float = 50.0,
    sg_window_ms: float = 51.0,
    sg_poly: int = 3,
    savgol_filter_func: Callable | None = None,
) -> np.ndarray:
    if len(t) < 3:
        return values - np.median(values)

    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1e-3
    if dt <= 0:
        dt = 1e-3

    win_pts = max(1, int(round((float(window_ms) / 1000.0) / dt)))
    win_pts = win_pts if win_pts % 2 == 1 else win_pts + 1
    method = normalize_baseline_method(method)

    if method == "savgol":
        if savgol_filter_func is None:
            raise RuntimeError("Savitzky-Golay filter unavailable; install scipy")

        sg_pts = max(win_pts, int(round((float(sg_window_ms) / 1000.0) / dt)))
        sg_pts = sg_pts if sg_pts % 2 == 1 else sg_pts + 1
        if sg_pts >= len(values):
            sg_pts = len(values) - 1 if len(values) % 2 == 0 else len(values)
        if sg_pts >= 5:
            poly = max(1, min(int(sg_poly), sg_pts - 1))
            return values - savgol_filter_func(values, window_length=sg_pts, polyorder=poly)

    return values - rolling_median(values, win_pts)


def detect_positive_pulses(
    t: np.ndarray,
    detrended: np.ndarray,
    t0: float,
    t1: float,
    peak_min_v: float,
    min_width_ms: float,
    min_spacing_ms: float,
    find_peaks_func: Callable,
    peak_widths_func: Callable,
) -> list[dict]:
    if t1 <= t0:
        return []

    start = max(0, int(np.searchsorted(t, t0, side="left")))
    end = min(len(t), int(np.searchsorted(t, t1, side="right")))
    if end - start < 3:
        return []

    tt = t[start:end]
    yy = detrended[start:end]
    if len(tt) > 1:
        dt = float(np.median(np.diff(tt)))
    elif len(t) > 1:
        dt = float(np.median(np.diff(t)))
    else:
        dt = 1e-3
    if dt <= 0:
        dt = 1e-3

    fs = 1.0 / dt
    distance = max(1, int((float(min_spacing_ms) / 1000.0) * fs))
    locs, _props = find_peaks_func(yy, height=float(peak_min_v), distance=distance)
    if len(locs) == 0:
        return []

    widths, _, _, _ = peak_widths_func(yy, locs, rel_height=0.5)
    min_width_pts = max(1, int((float(min_width_ms) / 1000.0) * fs))
    out: list[dict] = []
    for loc, width in zip(locs, widths):
        if width >= min_width_pts:
            gi = start + int(loc)
            out.append(
                {
                    "idx": gi,
                    "t": float(t[gi]),
                    "amp_det_v": float(detrended[gi]),
                    "width_ms": float(1000.0 * width / fs),
                }
            )
    return out


def detect_negative_pulses(
    t: np.ndarray,
    detrended: np.ndarray,
    t0: float,
    t1: float,
    peak_min_v: float,
    min_width_ms: float,
    min_spacing_ms: float,
    find_peaks_func: Callable,
    peak_widths_func: Callable,
) -> list[dict]:
    pos_like = detect_positive_pulses(
        t,
        -detrended,
        t0,
        t1,
        peak_min_v,
        min_width_ms,
        min_spacing_ms,
        find_peaks_func,
        peak_widths_func,
    )
    for row in pos_like:
        row["amp_det_v"] = float(detrended[row["idx"]])
    return pos_like
