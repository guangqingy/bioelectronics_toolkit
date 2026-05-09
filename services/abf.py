from __future__ import annotations

from typing import Callable

import numpy as np


def default_baseline_indices(n_points: int) -> tuple[int, int]:
    i0, i1 = 19000, 20000
    if i1 > n_points or (i1 - i0) < 5:
        i0 = int(0.38 * n_points)
        i1 = int(0.40 * n_points)
    i0 = max(0, min(i0, n_points - 1))
    i1 = max(i0 + 1, min(i1, n_points))
    return i0, i1


def baseline_apply(
    y: np.ndarray,
    t: np.ndarray,
    pre0_ms: float | None = None,
    pre1_ms: float | None = None,
    use_default: bool = False,
) -> tuple[np.ndarray, float]:
    baseline = 0.0
    n_points = len(y)

    if pre0_ms is not None and pre1_ms is not None and len(t) > 1:
        dt = float(t[1] - t[0])
        if dt > 0:
            i0 = max(0, int(float(pre0_ms) / 1000.0 / dt))
            i1 = min(n_points, int(float(pre1_ms) / 1000.0 / dt))
            if i1 > i0:
                baseline = float(np.mean(y[i0:i1]))
                return y - baseline, baseline

    if use_default and n_points > 5:
        i0, i1 = default_baseline_indices(n_points)
        baseline = float(np.mean(y[i0:i1]))
        return y - baseline, baseline

    return y, baseline


def baseline_subtract(
    y: np.ndarray,
    t: np.ndarray,
    pre0_ms: float | None,
    pre1_ms: float | None,
) -> np.ndarray:
    if pre0_ms is None or pre1_ms is None or len(t) < 2:
        return y
    dt = float(t[1] - t[0])
    if dt <= 0:
        return y
    i0 = max(0, int(float(pre0_ms) / 1000.0 / dt))
    i1 = min(len(t), int(float(pre1_ms) / 1000.0 / dt))
    if i1 > i0:
        return y - float(np.mean(y[i0:i1]))
    return y


def estimate_resistance(i_trace: np.ndarray, v_trace: np.ndarray, dt: float) -> float | None:
    try:
        dv = np.diff(v_trace)
        thresh = np.std(dv) * 3
        edges = np.where(np.abs(dv) > thresh)[0]
        if len(edges) < 2:
            return None

        edge = int(edges[0])
        win = max(10, int(0.002 / float(dt)))
        v_pre = np.mean(v_trace[max(0, edge - win) : edge])
        v_post = np.mean(v_trace[edge + 1 : edge + 1 + win])
        i_pre = np.mean(i_trace[max(0, edge - win) : edge])
        i_post = np.mean(i_trace[edge + 1 : edge + 1 + win])
        d_v = v_post - v_pre
        d_i = i_post - i_pre
        if abs(d_i) > 1e-6:
            return float(abs(d_v / d_i))
    except Exception:
        return None
    return None


def detect_peaks(
    t_full: np.ndarray,
    y_full: np.ndarray,
    t0: float | None,
    t1: float | None,
    use_all: bool,
    polarity: str,
    distance_ms: float,
    find_peaks_func: Callable,
    height: float | None = None,
    prominence: float | None = None,
) -> tuple[list[dict], list[float]]:
    if use_all:
        t_use = t_full
        y_use = y_full
        idx_base = np.arange(len(t_full))
        t0_plot = float(t_full[0]) if len(t_full) else 0.0
        t1_plot = float(t_full[-1]) if len(t_full) else 0.0
    else:
        if t0 is None or t1 is None:
            raise ValueError("Set analysis window t0/t1 or enable use_all")
        if t1 < t0:
            t0, t1 = t1, t0
        mask = (t_full >= t0) & (t_full <= t1)
        if not np.any(mask):
            raise ValueError("Selected time window has no points")
        idx_base = np.where(mask)[0]
        t_use = t_full[mask]
        y_use = y_full[mask]
        t0_plot = float(t0)
        t1_plot = float(t1)

    if len(t_use) < 3:
        raise ValueError("Not enough points in selected window")

    dt = max(1e-9, float(t_use[1] - t_use[0]))
    kwargs = {"distance": max(1, int(float(distance_ms) / 1000.0 / dt))}
    if height is not None:
        kwargs["height"] = height
    if prominence is not None:
        kwargs["prominence"] = prominence

    pol = str(polarity or "positive").strip().lower()
    if pol in {"neg", "negative"}:
        idx_local, props = find_peaks_func(-y_use, **kwargs)
        amps = y_use[idx_local]
        pol_out = "NEG"
    elif pol == "abs_max":
        idx_local, props = find_peaks_func(np.abs(y_use), **kwargs)
        amps = np.abs(y_use[idx_local])
        pol_out = "ABS"
    else:
        idx_local, props = find_peaks_func(y_use, **kwargs)
        amps = y_use[idx_local]
        pol_out = "POS"

    peaks: list[dict] = []
    for i, local_index in enumerate(idx_local):
        gi = int(idx_base[int(local_index)])
        peaks.append(
            {
                "idx": gi,
                "global_index": gi,
                "time": float(t_full[gi]),
                "amplitude": float(amps[i]),
                "width": (
                    float(props.get("widths", [np.nan] * len(idx_local))[i])
                    if "widths" in props
                    else None
                ),
                "prominence": (
                    float(props.get("prominences", [np.nan] * len(idx_local))[i])
                    if "prominences" in props
                    else None
                ),
                "polarity": pol_out,
            }
        )
    return peaks, [t0_plot, t1_plot]
