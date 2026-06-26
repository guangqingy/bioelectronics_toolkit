from __future__ import annotations

from typing import Any

import numpy as np

def _read_sweep(abf: Any, *, i_ch: int, v_ch: int, analog_ch: int) -> tuple[np.ndarray, ...]:
    sweep = list(getattr(abf, "sweepList", [0]))[0]
    abf.setSweep(sweep, channel=i_ch)
    time_s = abf.sweepX.copy()
    current = abf.sweepY.copy()
    abf.setSweep(sweep, channel=v_ch)
    voltage = abf.sweepY.copy()
    try:
        abf.setSweep(sweep, channel=analog_ch)
        analog = abf.sweepY.copy()
    except Exception:
        analog = np.zeros_like(current)
    return time_s, current, voltage, analog


def _find_all_pulses(analog: np.ndarray, voltage: np.ndarray) -> tuple[int, int, float, int, int]:
    pu_a: list[int] = []
    pd_a: list[int] = []
    analog_levels: list[float] = []

    tmp = 0
    direction_up = True
    for idx, _value in enumerate(analog):
        if direction_up and analog[idx] - analog[tmp] > 0.1:
            pu_a.append(idx)
            analog_levels.append(float(analog[idx]))
            tmp = idx
            direction_up = False
        elif not direction_up and analog[idx] - analog[tmp] > 0.1:
            tmp = idx
            if analog_levels:
                analog_levels.pop()
            analog_levels.append(float(analog[idx]))
        elif not direction_up and analog[idx] - analog[tmp] < -0.1:
            pd_a.append(idx)
            tmp = idx
            direction_up = True
        elif direction_up and analog[idx] - analog[tmp] < -0.1:
            tmp = idx

    pu_v: list[int] = []
    pd_v: list[int] = []
    tmp = 0
    direction_up = False
    for idx, _value in enumerate(voltage[:-10]):
        previous = float(np.mean(voltage[tmp : tmp + 10]))
        current = float(np.mean(voltage[idx : idx + 10]))
        if direction_up and current - previous > 0.4:
            pu_v.append(idx)
            tmp = idx
            direction_up = False
        elif not direction_up and current - previous > 0.4:
            tmp = idx
        elif not direction_up and current - previous < -0.4:
            pd_v.append(idx)
            tmp = idx
            direction_up = True
        elif direction_up and current - previous < -0.4:
            tmp = idx

    if len(pu_v) < 1:
        raise RuntimeError("Voltage pulse not found; check V trace.")
    if len(pu_a) < 1:
        return -1, pu_v[0] + 1, -1.0, pu_v[0], pd_v[0] if pd_v else pu_v[0] + 1

    return (
        pu_a[0],
        pd_a[0] if pd_a else pu_a[0] + 1,
        analog_levels[0] if analog_levels else float("nan"),
        pu_v[0],
        pd_v[0] if pd_v else pu_v[0] + 1,
    )


def _resistance_mohm(current: np.ndarray, voltage: np.ndarray, pulse_index: int) -> float:
    start = max(0, pulse_index - 1000)
    end = min(len(voltage) - 1, pulse_index + 1000)
    n_points = 500

    i1 = np.sum(current[start : start + n_points])
    i2 = np.sum(current[end : end + n_points])
    v1 = np.sum(voltage[start : start + n_points])
    v2 = np.sum(voltage[end : end + n_points])

    i_delta = abs(i2 - i1) * 1e-12
    v_delta = abs(v2 - v1) * 1e-3
    if i_delta <= 0:
        return float("nan")
    return float((v_delta / i_delta) / 1e6)


def _current_metrics(current: np.ndarray, pu_a: int, pd_a: int) -> tuple[float, float, float]:
    if pu_a == -1:
        return 0.0, 0.0, 0.0

    samples_per_10_ms = int(10 / 0.01)
    lo = max(0, pu_a - 2 * samples_per_10_ms)
    hi = max(0, pu_a - samples_per_10_ms)
    avg_init = float(np.mean(current[lo:hi]))

    segment = current[pu_a:pd_a]
    max_i = float(np.max(segment))
    min_i = float(np.min(segment))
    pos_peak = max_i - avg_init
    neg_peak = min_i - avg_init
    capacitive = pos_peak if abs(pos_peak) > abs(neg_peak) else neg_peak

    far_raw = float(np.mean(current[pu_a + int(8 / 0.01) : pu_a + int(9 / 0.01)]))
    faradaic = far_raw - avg_init
    integral_pc = float(np.sum(segment - avg_init) * 0.01 / 1000.0)
    return capacitive, faradaic, integral_pc


def _normalized_metrics(
    current: np.ndarray,
    voltage: np.ndarray,
    pulses: tuple[int, int, float, int, int],
) -> tuple[float, float, float, float]:
    pu_a, pd_a, _level, pu_v, _pd_v = pulses
    resistance = _resistance_mohm(current, voltage, pu_v)
    cap, far, integral = _current_metrics(current, pu_a, pd_a)
    if not np.isfinite(resistance):
        return float("nan"), float("nan"), float("nan"), resistance
    return cap * resistance, far * resistance, integral * resistance, resistance


def _segment_bounds(
    time_s: np.ndarray,
    current: np.ndarray,
    voltage: np.ndarray,
    analog: np.ndarray,
    *,
    mode: str,
    manual_t0: float,
    manual_t1: float,
    pulses: tuple[int, int, float, int, int] | None,
) -> tuple[float, float, tuple[int, int, float, int, int] | None]:
    if mode == "manual":
        return (
            max(float(time_s[0]), manual_t0),
            min(float(time_s[-1]), manual_t1),
            pulses,
        )

    if pulses is None:
        pulses = _find_all_pulses(analog, voltage)
    pu_a, pd_a, _level, pu_v, pd_v = pulses
    start_idx = pu_a if pu_a != -1 else pu_v
    end_idx = pd_a if pd_a != -1 else (pd_v if pd_v is not None else start_idx + 1)
    start_idx = max(0, min(start_idx, len(current) - 1))
    end_idx = max(start_idx + 1, min(end_idx, len(current)))

    segment = current[start_idx:end_idx]
    peak_idx = int(np.argmax(np.abs(current))) if segment.size == 0 else start_idx + int(np.argmax(np.abs(segment)))
    peak_time = float(time_s[peak_idx])
    return max(float(time_s[0]), peak_time - 0.1), min(float(time_s[-1]), peak_time + 0.1), pulses

__all__ = [
    "_current_metrics",
    "_find_all_pulses",
    "_normalized_metrics",
    "_read_sweep",
    "_resistance_mohm",
    "_segment_bounds",
]
