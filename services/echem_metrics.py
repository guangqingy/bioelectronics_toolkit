"""Quantitative electrochemistry metrics shared by the WebGUI and scripts.

This module carries the analysis layer that previously lived (duplicated) inside
each EChem figure script: light-pulse detection on chronoamperometry, per-pulse
amplitude / peak-to-peak / charge quantification, chronopotentiometry cycle
amplitudes, and spike / plateau metrics for square-wave modulated recordings.

Every entry point takes plain arrays rather than file paths, so it composes with
whichever loader the caller already has (``services.echem.load_photocurrent``,
the standardized CSV exports, or a raw instrument reader).

Conventions
-----------
* Time is seconds, CA current is nA (or nA cm^-2 if the caller pre-normalizes by
  electrode area), CP potential is mV, charge is nC.
* Detection operates in real time throughout. Sample-index arithmetic is avoided
  because these recordings contain acquisition dropouts, so an index-based
  period would silently drift.
* Short dropouts (<= 2 ms) are interpolated; longer gaps stay NaN and the
  affected cycle is rejected rather than bridged with invented data.
* ``summarize_values`` reports the median and the pulse-to-pulse SD, not the
  standard error, so the spread describes the physical variability.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:  # scipy is a hard dependency of the toolkit; degrade gracefully anyway.
    from scipy import signal as _signal
except ImportError:  # pragma: no cover - exercised only without scipy
    _signal = None

__all__ = [
    "DEFAULT_DETECTION",
    "DEFAULT_MEASUREMENT",
    "DETECTION_PRESETS",
    "adc_rails",
    "average_pulse",
    "cycle_amplitudes",
    "cv_anodic_peak_summary",
    "detect_pulses",
    "detect_steps",
    "estimate_period",
    "final_complete_cycle",
    "find_period_s",
    "level_baseline",
    "pulse_metrics",
    "pulse_metrics_summary",
    "robust_dt",
    "rolling_median_detrend",
    "square_wave_metrics",
    "summarize_values",
    "suppress_mains",
]

# Detection defaults tuned on the 10 kHz BioLogic photocurrent recordings.
DEFAULT_DETECTION: dict[str, Any] = {
    "edge_exclusion_s": 0.3,
    "threshold_mad": 6.0,
    "minimum_gap_s": 0.3,
    "period_tolerance_fraction": 0.10,
    "max_period_multiple": 4,
    "detrend_window_s": 1.5,
    "polarity": "positive",
}

DEFAULT_MEASUREMENT: dict[str, Any] = {
    "baseline_ms": 20.0,
    "post_fraction": 0.55,
    "post_cap_ms": 200.0,
}

# Named presets the GUI can offer directly instead of exposing seven raw knobs.
DETECTION_PRESETS: dict[str, dict[str, Any]] = {
    "default": dict(DEFAULT_DETECTION),
    "legacy_day": {
        **DEFAULT_DETECTION,
        "edge_exclusion_s": 0.0,
        "minimum_gap_s": 0.05,
        "period_tolerance_fraction": 0.2,
    },
    "waveform": {
        **DEFAULT_DETECTION,
        "edge_exclusion_s": 0.25,
        "minimum_gap_s": 0.2,
        "period_tolerance_fraction": 0.2,
    },
    "auto_polarity": {**DEFAULT_DETECTION, "polarity": "auto"},
}

# CorrTest LED driver periods; the hardware runs ~1.45% fast against nominal.
_HARDWARE_PERIOD_RATIO = 0.9855
# Nominal LED controller settings. ``_refine_period_s`` applies the measured
# hardware correction once; using corrected periods here applied it twice.
_FIXED_PERIOD_CANDIDATES_S = (0.25, 0.5, 1.0, 2.0, 5.0)
_PRE_EDGE_FRACTION = 0.10


# --------------------------------------------------------------------------
# sampling helpers
# --------------------------------------------------------------------------
def robust_dt(time_s: np.ndarray) -> float:
    """Median positive sampling interval; robust to recorded data gaps."""
    gaps = np.diff(np.asarray(time_s, dtype=float))
    gaps = gaps[np.isfinite(gaps) & (gaps > 0)]
    return float(np.median(gaps)) if gaps.size else float("nan")


def rolling_median_detrend(values: np.ndarray, window: int) -> np.ndarray:
    """Remove slow drift with a centered rolling median."""
    baseline = pd.Series(values).rolling(int(window), center=True, min_periods=1).median()
    return np.asarray(values, dtype=float) - baseline.to_numpy()


def detrend_window_samples(detection: dict, time_s: np.ndarray) -> int:
    """Convert the detrend window from seconds to an odd sample count."""
    if "detrend_window_samples" in detection:
        return int(detection["detrend_window_samples"]) | 1
    dt = float(np.median(np.diff(time_s[: min(len(time_s), 2000)])))
    return max(3, int(round(float(detection.get("detrend_window_s", 1.5)) / dt)) | 1)


def suppress_mains(
    values: np.ndarray,
    dt: float,
    freqs: tuple[float, ...] = (50.0, 60.0),
    harmonics: int = 3,
    quality: float = 30.0,
) -> np.ndarray:
    """Zero-phase notch out mains pickup and its harmonics.

    Intended for display and for waveform composites; per-pulse amplitudes are
    measured on unfiltered data so the notch cannot bias the reported numbers.
    """
    if _signal is None or not np.isfinite(dt) or dt <= 0:
        return np.asarray(values, dtype=float)
    fs = 1.0 / dt
    out = np.asarray(values, dtype=float)
    for base in freqs:
        for harmonic in range(1, int(harmonics) + 1):
            f0 = base * harmonic
            if f0 >= 0.45 * fs:
                break
            b, a = _signal.iirnotch(f0, quality, fs=fs)
            out = _signal.filtfilt(b, a, out)
    return out


def adc_rails(values: np.ndarray) -> tuple[float, float] | None:
    """Return (low, high) clipping levels if the ADC pins, else ``None``.

    A pinned converter repeats its extreme code many times; 100 repeats is well
    above what genuine signal produces at these sampling rates.
    """
    values = np.asarray(values, dtype=float)
    low, high = float(np.min(values)), float(np.max(values))
    span = high - low
    n_low = int(np.sum(values == low))
    n_high = int(np.sum(values == high))
    if n_low < 100 and n_high < 100:
        return None
    return (
        low + 0.002 * span if n_low >= 100 else -np.inf,
        high - 0.002 * span if n_high >= 100 else np.inf,
    )


def _interpolate_short_gaps(
    time_s: np.ndarray,
    values: np.ndarray,
    target_s: np.ndarray,
    max_gap_s: float = 0.002,
) -> np.ndarray:
    """Linear interpolation that never bridges an acquisition dropout."""
    t = np.asarray(time_s, dtype=float)
    y = np.asarray(values, dtype=float)
    q = np.asarray(target_s, dtype=float)
    finite = np.isfinite(t) & np.isfinite(y)
    t, y = t[finite], y[finite]
    out = np.full(q.shape, np.nan, dtype=float)
    if t.size < 2:
        return out

    right = np.searchsorted(t, q, side="left")
    exact = right < t.size
    exact[exact] &= np.isclose(
        t[right[exact]], q[exact], rtol=0.0, atol=max(robust_dt(t) * 1e-5, 1e-12)
    )
    out[exact] = y[right[exact]]

    use = (~exact) & (right > 0) & (right < t.size)
    if np.any(use):
        r = right[use]
        left = r - 1
        gap = t[r] - t[left]
        good = gap <= max_gap_s + 1e-12
        use_at = np.flatnonzero(use)[good]
        r, left, gap = r[good], left[good], gap[good]
        fraction = (q[use_at] - t[left]) / gap
        out[use_at] = y[left] + fraction * (y[r] - y[left])
    return out


def _event_grid(bounds_s: tuple[float, float], dt: float) -> np.ndarray:
    start, stop = map(float, bounds_s)
    count = max(2, int(np.floor((stop - start) / dt)))
    return start + np.arange(count) * dt


def _resampled_events(
    time_s: np.ndarray,
    values: np.ndarray,
    event_times_s: np.ndarray,
    relative_s: np.ndarray,
    minimum_coverage: float = 0.90,
) -> np.ndarray:
    rows = []
    for event_time in np.asarray(event_times_s, dtype=float):
        row = _interpolate_short_gaps(time_s, values, event_time + relative_s)
        if np.mean(np.isfinite(row)) >= minimum_coverage:
            rows.append(row)
    return np.asarray(rows) if rows else np.empty((0, len(relative_s)))


def _integrate_finite(x: np.ndarray, y: np.ndarray) -> float:
    """Integrate finite contiguous runs without bridging long NaN gaps."""
    finite = np.isfinite(x) & np.isfinite(y)
    indices = np.flatnonzero(finite)
    if indices.size < 2:
        return 0.0
    trapezoid = getattr(np, "trapezoid", None)
    if trapezoid is None:  # NumPy < 2.0
        trapezoid = np.trapz
    total = 0.0
    for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
        if run.size >= 2:
            total += float(trapezoid(y[run], x[run]))
    return total


# --------------------------------------------------------------------------
# pulse detection
# --------------------------------------------------------------------------
def estimate_period(event_times_s: np.ndarray, settings: dict) -> tuple[float, np.ndarray]:
    """Infer the light-pulse period from onset times, tolerating missed pulses.

    Each observed gap may span several periods, so candidate periods are tested
    as gap/multiple and scored first by how many gaps they explain directly.
    Returns the period and a boolean mask of gaps consistent with it.
    """
    event_times_s = np.asarray(event_times_s, dtype=float)
    if event_times_s.size < 3:
        return float("nan"), np.array([], dtype=bool)

    gaps = np.diff(event_times_s)
    minimum_gap = float(settings["minimum_gap_s"])
    tolerance = float(settings["period_tolerance_fraction"])
    max_multiple = int(settings["max_period_multiple"])

    candidates = [
        gap / multiple
        for gap in gaps
        if np.isfinite(gap) and gap >= minimum_gap
        for multiple in range(1, max_multiple + 1)
        if gap / multiple >= minimum_gap
    ]

    best_period, best_valid = float("nan"), np.zeros(gaps.size, dtype=bool)
    best_score = (-1, -1, -np.inf)
    for candidate in candidates:
        multiples = np.rint(gaps / candidate).astype(int)
        allowed = (multiples >= 1) & (multiples <= max_multiple)
        normalized = np.full(gaps.shape, np.nan)
        normalized[allowed] = gaps[allowed] / multiples[allowed]
        valid = allowed & (np.abs(normalized - candidate) <= tolerance * candidate)
        direct = int(np.sum(valid & (multiples == 1)))
        total = int(np.sum(valid))
        error = (
            float(np.median(np.abs(normalized[valid] - candidate) / candidate)) if total else np.inf
        )
        score = (direct, total, -error)
        if score > best_score:
            best_score, best_period, best_valid = score, float(candidate), valid

    if best_score[0] < 2:
        return float("nan"), np.zeros(gaps.size, dtype=bool)

    multiples = np.rint(gaps[best_valid] / best_period).astype(int)
    period = float(np.median(gaps[best_valid] / multiples))
    multiples = np.rint(gaps / period).astype(int)
    allowed = (multiples >= 1) & (multiples <= max_multiple)
    normalized = np.full(gaps.shape, np.nan)
    normalized[allowed] = gaps[allowed] / multiples[allowed]
    links = allowed & (np.abs(normalized - period) <= tolerance * period)
    return period, links


def detect_pulses(
    time_s: np.ndarray, signal: np.ndarray, settings: dict
) -> tuple[np.ndarray, float, float]:
    """MAD-threshold onset detection followed by periodic-train filtering.

    ``signal`` must already be detrended and sign-adjusted so pulses go UP.
    Isolated threshold crossings that do not belong to the periodic train are
    discarded, which is what keeps electrical spikes out of the statistics.

    Returns ``(onset_indices, dt, period_s)``.
    """
    time_s = np.asarray(time_s, dtype=float)
    signal = np.asarray(signal, dtype=float)
    empty = (np.array([], dtype=int), float("nan"), float("nan"))

    finite = np.isfinite(time_s) & np.isfinite(signal)
    selected = np.flatnonzero(finite)
    if selected.size < 3 or np.any(np.diff(time_s[selected]) <= 0):
        return empty

    edge = float(settings["edge_exclusion_s"])
    selected = selected[
        (time_s[selected] >= time_s[selected[0]] + edge)
        & (time_s[selected] <= time_s[selected[-1]] - edge)
    ]
    if selected.size < 3:
        return empty

    dt = float(np.median(np.diff(time_s[selected])))
    work = signal[selected]
    center = float(np.median(work))
    sigma = 1.4826 * float(np.median(np.abs(work - center)))
    hot = work > center + float(settings["threshold_mad"]) * sigma
    crossings = np.flatnonzero(hot[1:] & ~hot[:-1]) + 1

    candidates: list[int] = []
    for crossing in crossings:
        index = int(selected[crossing])
        if not candidates or time_s[index] - time_s[candidates[-1]] >= settings["minimum_gap_s"]:
            candidates.append(index)
    onsets = np.asarray(candidates, dtype=int)

    period, links = estimate_period(time_s[onsets], settings)
    if not np.isfinite(period):
        return np.array([], dtype=int), dt, float("nan")

    periodic = np.zeros(onsets.size, dtype=bool)
    periodic[:-1] |= links
    periodic[1:] |= links
    return onsets[periodic], dt, period


def _refine_rising_edges(
    time_s: np.ndarray,
    work: np.ndarray,
    onsets: np.ndarray,
    search_before_s: float = 0.003,
    search_after_s: float = 0.006,
) -> np.ndarray:
    """Refine threshold crossings to the steepest real (non-gap) slope."""
    t = np.asarray(time_s, dtype=float)
    y = np.asarray(work, dtype=float)
    dt = robust_dt(t)
    refined: list[float] = []
    for onset in np.asarray(onsets, dtype=int):
        guess = t[onset]
        lo = max(0, int(np.searchsorted(t, guess - search_before_s)))
        hi = min(len(t) - 1, int(np.searchsorted(t, guess + search_after_s)))
        if hi <= lo:
            continue
        local_dt = np.diff(t[lo : hi + 1])
        slope = np.diff(y[lo : hi + 1]) / local_dt
        slope[local_dt > max(5.0 * dt, 0.001)] = -np.inf
        refined.append(t[lo + int(np.nanargmax(slope)) + 1] if np.isfinite(slope).any() else guess)
    return np.asarray(refined, dtype=float)


def _resolve_polarity(detrended: np.ndarray, detection: dict, signed: bool) -> bool:
    """True when the photoresponse is cathodic (downward)."""
    if signed or str(detection.get("polarity", "positive")).lower() == "auto":
        median = float(np.median(detrended))
        return (median - np.percentile(detrended, 1)) > (np.percentile(detrended, 99) - median)
    return str(detection.get("polarity", "positive")).lower() == "negative"


# --------------------------------------------------------------------------
# chronoamperometry: per-pulse quantification
# --------------------------------------------------------------------------
def pulse_metrics(
    time_s: np.ndarray,
    current_nA: np.ndarray,
    detection: dict | None = None,
    measurement: dict | None = None,
    signed: bool = False,
) -> dict[str, Any]:
    """Per-pulse photocurrent metrics with dropout- and clipping-aware QC.

    Returns per-pulse arrays plus the QC counters needed to judge them:

    ``amplitudes_nA``
        Peak minus pre-pulse baseline for each accepted pulse.
    ``p2p_nA``
        Robust peak-to-peak (99th - 1st percentile) inside the ON window.
    ``charge_nC``
        Time integral of the positive excursion above baseline.
    ``n_clipped`` / ``n_rejected_gaps``
        Pulses dropped because the ADC railed, or because a dropout left the
        window under 95% covered. Non-zero counts mean the median is computed
        from fewer pulses than were detected.

    With ``signed=True`` the polarity is inferred per recording and cathodic
    amplitudes are returned negative, which is what the bias-series analysis
    needs; otherwise amplitudes are magnitudes.
    """
    detection = {**DEFAULT_DETECTION, **(detection or {})}
    measurement = {**DEFAULT_MEASUREMENT, **(measurement or {})}
    time_s = np.asarray(time_s, dtype=float)
    current_nA = np.asarray(current_nA, dtype=float)

    detrended = rolling_median_detrend(current_nA, detrend_window_samples(detection, time_s))
    rails = adc_rails(current_nA) if signed else None
    cathodic = _resolve_polarity(detrended, detection, signed)
    work = -detrended if cathodic else detrended

    onsets, dt, period = detect_pulses(time_s, work, detection)
    result: dict[str, Any] = {
        "amplitudes_nA": np.array([]),
        "p2p_nA": np.array([]),
        "charge_nC": np.array([]),
        "onsets": onsets,
        "dt": dt,
        "period_s": period,
        "n_clipped": 0,
        "n_rejected_gaps": 0,
        "polarity": "cathodic" if cathodic else "anodic",
        "detrended": detrended,
        "work": work,
    }
    if onsets.size < 3 or not np.isfinite(period):
        return result

    baseline_s = float(measurement["baseline_ms"]) / 1000.0
    post_s = min(
        float(measurement["post_fraction"]) * period,
        float(measurement["post_cap_ms"]) / 1000.0,
    )
    relative = _event_grid((-baseline_s, post_s), dt)
    event_times = _refine_rising_edges(time_s, work, onsets)

    amplitudes: list[float] = []
    p2p: list[float] = []
    charge: list[float] = []
    clipped = rejected = 0

    for event_time in event_times:
        start = int(np.searchsorted(time_s, event_time - baseline_s))
        stop = int(np.searchsorted(time_s, event_time + post_s))
        if start <= 0 or stop >= len(time_s):
            continue
        if rails is not None and (
            np.any(current_nA[start:stop] <= rails[0]) or np.any(current_nA[start:stop] >= rails[1])
        ):
            clipped += 1
            continue

        segment = _interpolate_short_gaps(time_s, work, event_time + relative)
        if np.mean(np.isfinite(segment)) < 0.95:
            rejected += 1
            continue

        pre = relative < 0
        post = relative >= 0
        if np.count_nonzero(np.isfinite(segment[pre])) < 3:
            rejected += 1
            continue

        base = float(np.nanmedian(segment[pre]))
        peak = float(np.nanmax(segment[post])) - base
        amplitudes.append(-peak if (signed and cathodic) else peak)
        p2p.append(float(np.nanpercentile(segment[post], 99) - np.nanpercentile(segment[post], 1)))
        positive = np.clip(segment[post] - base, 0.0, None)
        charge.append(_integrate_finite(relative[post], positive))

    result.update(
        amplitudes_nA=np.asarray(amplitudes),
        p2p_nA=np.asarray(p2p),
        charge_nC=np.asarray(charge),
        n_clipped=int(clipped),
        n_rejected_gaps=int(rejected),
    )
    return result


def summarize_values(values: np.ndarray) -> tuple[float, float, float]:
    """Return (median, pulse-to-pulse SD, IQR) of per-pulse values."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return float("nan"), float("nan"), float("nan")
    sd = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return (
        float(np.median(values)),
        sd,
        float(np.subtract(*np.percentile(values, [75, 25]))),
    )


def pulse_metrics_summary(
    time_s: np.ndarray,
    current_nA: np.ndarray,
    detection: dict | None = None,
    measurement: dict | None = None,
    signed: bool = False,
) -> dict[str, Any]:
    """Flat, JSON-safe summary of :func:`pulse_metrics` for the GUI and CSVs.

    Keys are scalars only, so the result drops straight into a results table.
    """
    metrics = pulse_metrics(time_s, current_nA, detection, measurement, signed=signed)
    amplitude, amplitude_sd, amplitude_iqr = summarize_values(metrics["amplitudes_nA"])
    p2p, p2p_sd, _ = summarize_values(metrics["p2p_nA"])
    charge, charge_sd, _ = summarize_values(metrics["charge_nC"])
    period = metrics["period_s"]
    return {
        "n_pulses": int(np.asarray(metrics["amplitudes_nA"]).size),
        "n_detected": int(np.asarray(metrics["onsets"]).size),
        "n_clipped": metrics["n_clipped"],
        "n_rejected_gaps": metrics["n_rejected_gaps"],
        "polarity": metrics["polarity"],
        "period_s": float(period) if np.isfinite(period) else None,
        "frequency_Hz": float(1.0 / period) if np.isfinite(period) and period > 0 else None,
        "amplitude_nA": amplitude,
        "amplitude_sd_nA": amplitude_sd,
        "amplitude_iqr_nA": amplitude_iqr,
        "p2p_nA": p2p,
        "p2p_sd_nA": p2p_sd,
        "charge_nC": charge,
        "charge_sd_nC": charge_sd,
    }


def average_pulse(
    time_s: np.ndarray,
    current_nA: np.ndarray,
    detection: dict | None = None,
    window_ms: tuple[float, float] = (-20.0, 200.0),
    baseline_ms: float = 20.0,
    notch_hz: tuple[float, ...] | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None, int]:
    """Dropout-aware median composite aligned on each measured light edge.

    Returns ``(relative_ms, composite_nA, n_cycles)``; ``(None, None, 0)`` when
    no periodic pulse train could be established.
    """
    detection = {**DEFAULT_DETECTION, **(detection or {})}
    time_s = np.asarray(time_s, dtype=float)
    current_nA = np.asarray(current_nA, dtype=float)

    detrended = rolling_median_detrend(current_nA, detrend_window_samples(detection, time_s))
    if notch_hz:
        detrended = suppress_mains(detrended, robust_dt(time_s), freqs=tuple(notch_hz))

    cathodic = _resolve_polarity(detrended, detection, signed=False)
    work = -detrended if cathodic else detrended
    onsets, dt, period = detect_pulses(time_s, work, detection)
    if onsets.size < 3 or not np.isfinite(period):
        return None, None, 0

    relative_s = _event_grid(tuple(np.asarray(window_ms, dtype=float) / 1000.0), dt)
    event_times = _refine_rising_edges(time_s, work, onsets)
    cycles = _resampled_events(time_s, detrended, event_times, relative_s)
    if cycles.size == 0:
        return None, None, 0

    baseline = (relative_s >= -baseline_ms / 1000.0) & (relative_s < 0)
    cycles = cycles[np.sum(np.isfinite(cycles[:, baseline]), axis=1) >= 3]
    if cycles.size == 0:
        return None, None, 0

    cycles -= np.nanmedian(cycles[:, baseline], axis=1, keepdims=True)
    return relative_s * 1e3, np.nanmedian(cycles, axis=0), len(cycles)


# --------------------------------------------------------------------------
# chronopotentiometry: photovoltage cycles
# --------------------------------------------------------------------------
def detect_steps(
    time_s: np.ndarray, potential_mV: np.ndarray, settings: dict | None = None
) -> tuple[np.ndarray, float, float]:
    """Detect CP light edges in real time, never by array-index modulo.

    When ``expected_period_s`` is supplied the edge phase is recovered by
    folding the derivative onto that period, which stays locked even where the
    edges are too soft for a per-edge threshold to fire reliably.
    """
    settings = {**DEFAULT_DETECTION, **(settings or {})}
    time_s = np.asarray(time_s, dtype=float)
    potential_mV = np.asarray(potential_mV, dtype=float)
    empty = (np.array([], dtype=int), float("nan"), float("nan"))

    finite = np.isfinite(time_s) & np.isfinite(potential_mV)
    selected = np.flatnonzero(finite)
    if selected.size < 3 or np.any(np.diff(time_s[selected]) <= 0):
        return empty

    edge = float(settings["edge_exclusion_s"])
    selected = selected[
        (time_s[selected] >= time_s[selected[0]] + edge)
        & (time_s[selected] <= time_s[selected[-1]] - edge)
    ]
    if selected.size < 3:
        return empty

    dt = robust_dt(time_s[selected])
    expected = settings.get("expected_period_s")

    if expected is not None:
        period = float(expected)
        local_dt = np.diff(time_s[selected])
        derivative = np.abs(np.diff(potential_mV[selected]) / local_dt)
        valid = local_dt <= max(5.0 * dt, 0.001)
        phase = ((time_s[selected[1:]] - time_s[selected[0]]) % period) / period
        bins = max(200, min(10000, int(round(period / dt))))
        where = np.minimum((phase * bins).astype(int), bins - 1)
        sums = np.bincount(where[valid], weights=derivative[valid], minlength=bins)
        counts = np.bincount(where[valid], minlength=bins)
        score = sums / np.maximum(counts, 1)
        width = max(3, bins // 1000) | 1
        padded = np.r_[score[-width:], score, score[:width]]
        score = np.convolve(padded, np.ones(width) / width, mode="same")[width:-width]
        phase_s = (int(np.argmax(score)) + 0.5) * period / bins
        first = time_s[selected[0]] + phase_s
        first += np.ceil((time_s[selected[0]] - first) / period) * period
        event_times = np.arange(first, time_s[selected[-1]] + 1e-12, period)
        onsets = np.searchsorted(time_s, event_times)
        onsets = onsets[(onsets > 0) & (onsets < len(time_s))]
        return np.unique(onsets), dt, period

    local_dt = np.diff(time_s[selected])
    step = np.zeros(selected.size)
    good = local_dt <= max(5.0 * dt, 0.001)
    step[1:][good] = np.abs(np.diff(potential_mV[selected])[good] / local_dt[good])
    center = float(np.median(step))
    sigma = 1.4826 * float(np.median(np.abs(step - center)))
    hot = step > center + float(settings["threshold_mad"]) * sigma
    crossings = np.flatnonzero(hot[1:] & ~hot[:-1]) + 1

    candidates: list[int] = []
    for crossing in crossings:
        index = int(selected[crossing])
        if not candidates or time_s[index] - time_s[candidates[-1]] >= settings["minimum_gap_s"]:
            candidates.append(index)
    onsets = np.asarray(candidates, dtype=int)
    period, _ = estimate_period(time_s[onsets], settings)
    return onsets, dt, period


def level_baseline(
    potential_mV: np.ndarray,
    dt: float,
    period: float,
    time_s: np.ndarray | None = None,
) -> np.ndarray:
    """Remove open-circuit-potential drift with a period-wide running median."""
    values = np.asarray(potential_mV, dtype=float)
    if not np.isfinite(period):
        return values - np.nanmedian(values)
    if time_s is None:
        time_s = np.arange(values.size) * dt
    time_s = np.asarray(time_s, dtype=float)

    coarse_dt = max(20.0 * dt, 0.002)
    grid = np.arange(time_s[0], time_s[-1] + 0.5 * coarse_dt, coarse_dt)
    coarse = np.interp(grid, time_s, values)
    window = max(3, int(round(period / coarse_dt)) | 1)
    baseline = pd.Series(coarse).rolling(window, center=True, min_periods=1).median().to_numpy()
    return values - np.interp(time_s, grid, baseline)


def cycle_amplitudes(
    time_s: np.ndarray,
    potential_mV: np.ndarray,
    settings: dict | None = None,
    window_s: tuple[float, float] | None = None,
) -> tuple[np.ndarray, float]:
    """Per-cycle robust photovoltage span after real-time resampling.

    Returns ``(amplitudes_mV, period_ms)``. The span is the 99th minus the 1st
    percentile within a cycle, which resists the single-sample spikes that a
    plain max-minus-min would latch onto.

    Pass ``settings["expected_period_s"]``. Without it the period is inferred
    from the interval between detected *edges*, and since both the ON and the
    OFF transition are edges, a light cycle is then reported as its half. The
    per-cycle window is wrong by the same factor and the amplitudes are not
    usable. Every figure script in the EChem dataset supplies the expected
    period for this reason; :func:`cycle_amplitudes_summary` reports which of
    the two paths was taken.
    """
    time_s = np.asarray(time_s, dtype=float)
    potential_mV = np.asarray(potential_mV, dtype=float)
    onsets, dt, period = detect_steps(time_s, potential_mV, settings)
    if onsets.size < 3 or not np.isfinite(period):
        return np.array([]), float("nan")

    if window_s is None:
        window_s = (-np.inf, np.inf)
    levelled = level_baseline(potential_mV, dt, period, time_s)
    relative = _event_grid((0.0, period), dt)

    amplitudes: list[float] = []
    for event_time in time_s[onsets]:
        if event_time < window_s[0] or event_time > window_s[1]:
            continue
        segment = _interpolate_short_gaps(time_s, levelled, event_time + relative)
        if np.mean(np.isfinite(segment)) >= 0.90:
            amplitudes.append(float(np.nanpercentile(segment, 99) - np.nanpercentile(segment, 1)))
    return np.asarray(amplitudes), period * 1000.0


def cycle_amplitudes_summary(
    time_s: np.ndarray,
    potential_mV: np.ndarray,
    settings: dict | None = None,
    window_s: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Flat, JSON-safe photovoltage summary for the GUI and CSVs.

    ``period_source`` is ``"expected"`` when the caller supplied the light
    period and ``"inferred"`` when it was derived from edge spacing. An
    inferred period is frequently half the true light cycle (see
    :func:`cycle_amplitudes`), so the GUI should show the amplitudes as
    provisional and prompt for the period rather than presenting them as final.
    """
    settings = {**DEFAULT_DETECTION, **(settings or {})}
    amplitudes, period_ms = cycle_amplitudes(time_s, potential_mV, settings, window_s)
    amplitude, amplitude_sd, amplitude_iqr = summarize_values(amplitudes)
    expected = settings.get("expected_period_s")
    return {
        "n_cycles": int(np.asarray(amplitudes).size),
        "period_ms": float(period_ms) if np.isfinite(period_ms) else None,
        "period_source": (
            str(settings.get("period_source"))
            if settings.get("period_source")
            else ("expected" if expected is not None else "inferred")
        ),
        "amplitude_mV": amplitude,
        "amplitude_sd_mV": amplitude_sd,
        "amplitude_iqr_mV": amplitude_iqr,
    }


# --------------------------------------------------------------------------
# cyclic voltammetry: branch-aware final-cycle anodic peak
# --------------------------------------------------------------------------
def final_complete_cycle(
    potential_v: np.ndarray, current_uA: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Select the final complete CV cycle via positive terminal crossings."""
    potential_v = np.asarray(potential_v, dtype=float)
    current_uA = np.asarray(current_uA, dtype=float)
    if len(potential_v) < 8 or len(potential_v) != len(current_uA):
        raise ValueError("CV arrays must have equal length and >= 8 points")
    terminal_v = float(potential_v[-1])
    step_v = np.diff(potential_v)
    crossings = (
        np.flatnonzero(
            (potential_v[:-1] < terminal_v) & (potential_v[1:] >= terminal_v) & (step_v > 0)
        )
        + 1
    )
    if len(crossings) < 2:
        raise ValueError("Could not delimit a complete final cycle")
    start = int(crossings[-2])
    return potential_v[start:], current_uA[start:]


def _split_cycle_branches(
    potential_v: np.ndarray, current_uA: np.ndarray
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray]]:
    cycle_v, cycle_uA = final_complete_cycle(potential_v, current_uA)
    upper_index = int(np.argmax(cycle_v))
    lower_index = upper_index + int(np.argmin(cycle_v[upper_index:]))
    return cycle_v, cycle_uA, (cycle_v[lower_index:], cycle_uA[lower_index:])


def cv_anodic_peak_summary(
    potential_v: np.ndarray,
    current_uA: np.ndarray,
    window_v: tuple[float, float] = (-0.25, -0.12),
    edge_guard_v: float = 0.02,
) -> dict[str, Any]:
    """Return the branch-aware anodic peak from the final complete CV cycle."""
    cycle_v, _cycle_uA, (anodic_v, anodic_uA) = _split_cycle_branches(potential_v, current_uA)
    low_v, high_v = map(float, window_v)
    if high_v <= low_v:
        raise ValueError("CV peak-window maximum must exceed its minimum")
    in_window = (anodic_v >= low_v) & (anodic_v <= high_v)
    base: dict[str, Any] = {
        "cycle_points": int(len(cycle_v)),
        "anodic_window_low_V": low_v,
        "anodic_window_high_V": high_v,
        "edge_guard_V": float(edge_guard_v),
    }
    if int(in_window.sum()) < 3:
        return {
            **base,
            "Ipa_uA": float("nan"),
            "Epa_V": float("nan"),
            "anodic_valid": False,
            "anodic_status": "insufficient-window-data",
            "peak_edge_distance_V": float("nan"),
        }

    window_potential = anodic_v[in_window]
    window_current = anodic_uA[in_window]
    peak_index = int(np.argmax(window_current))
    peak_v = float(window_potential[peak_index])
    peak_uA = float(window_current[peak_index])
    nonzero_steps = np.abs(np.diff(anodic_v))
    nonzero_steps = nonzero_steps[nonzero_steps > 0]
    median_step_v = float(np.median(nonzero_steps)) if len(nonzero_steps) else float("nan")
    effective_guard = (
        max(float(edge_guard_v), 2.0 * median_step_v)
        if np.isfinite(median_step_v)
        else float(edge_guard_v)
    )
    edge_distance = float(min(peak_v - low_v, high_v - peak_v))
    valid = bool(edge_distance > effective_guard)
    return {
        **base,
        "Ipa_uA": peak_uA if valid else float("nan"),
        "Epa_V": peak_v if valid else float("nan"),
        "anodic_valid": valid,
        "anodic_status": "resolved" if valid else "rejected-near-window-edge",
        "edge_guard_V": effective_guard,
        "peak_edge_distance_V": edge_distance,
    }


# --------------------------------------------------------------------------
# square-wave (CorrTest) recordings: spike and plateau
# --------------------------------------------------------------------------
def _fold_score(x: np.ndarray, period_samples: int) -> float:
    cycles = len(x) // max(1, int(period_samples))
    if cycles < 2:
        return -1.0
    folded = x[: cycles * period_samples].reshape(cycles, period_samples)
    template = np.nanmedian(folded - np.nanmedian(folded, axis=1, keepdims=True), axis=0)
    return float(np.nanvar(template) / (np.nanvar(x) + 1e-30))


def _refine_period_s(x: np.ndarray, dt: float, hint_s: float) -> float:
    """Autocorrelation refinement within +/-1% of a physical timing prior."""
    if not np.isfinite(hint_s) or hint_s <= 0:
        return float("nan")
    hardware_period = _HARDWARE_PERIOD_RATIO * hint_s
    lo = max(2, int(round(0.99 * hardware_period / dt)))
    hi = min(len(x) // 2, int(round(1.01 * hardware_period / dt)))
    if hi <= lo:
        return hardware_period

    centered = np.asarray(x, dtype=float) - np.nanmean(x)
    nfft = 1 << (2 * len(centered) - 1).bit_length()
    spectrum = np.fft.rfft(centered, nfft)
    ac = np.fft.irfft(spectrum * np.conj(spectrum), nfft)[: len(centered)]
    ac = ac / np.maximum(np.arange(len(centered), 0, -1), 1)
    region = ac[lo : hi + 1]
    if not np.isfinite(region).any():
        return hardware_period

    measured = (lo + int(np.nanargmax(region))) * dt
    return (
        measured if abs(measured - hardware_period) <= 0.005 * hardware_period else hardware_period
    )


def find_period_s(x: np.ndarray, dt: float, low_s: float = 0.2, high_s: float = 6.0) -> float:
    """Fallback period search for recordings without timing metadata."""
    scored = []
    for hint in _FIXED_PERIOD_CANDIDATES_S:
        if low_s <= hint <= high_s and hint / dt < len(x) // 2:
            period = _refine_period_s(x, dt, hint)
            scored.append((_fold_score(x, int(round(period / dt))), period))
    return max(scored)[1] if scored and max(scored)[0] >= 0.01 else float("nan")


def _coarse_running_median(time_s: np.ndarray, values: np.ndarray, window_s: float) -> np.ndarray:
    """Time-aware drift baseline, cheap even for multi-second cycles."""
    dt = robust_dt(time_s)
    block = max(1, int(round(0.002 / dt)))
    count = len(values) // block
    if count < 3:
        return np.full(len(values), np.nanmedian(values))
    t_coarse = np.asarray(time_s[: count * block]).reshape(count, block).mean(1)
    y_coarse = np.nanmedian(np.asarray(values[: count * block]).reshape(count, block), axis=1)
    window = max(3, int(round(window_s / (block * dt))) | 1)
    baseline = pd.Series(y_coarse).rolling(window, center=True, min_periods=1).median().to_numpy()
    return np.interp(time_s, t_coarse, baseline)


def _initial_edge_phase(template: np.ndarray, dt: float, align: str) -> int:
    n = len(template)
    smooth_n = max(3, int(round(0.002 / dt))) | 1
    smooth = pd.Series(template).rolling(smooth_n, center=True, min_periods=1).mean().to_numpy()
    if align == "gate":
        return int(np.nanargmax(np.abs(np.gradient(smooth, dt))))

    dev = smooth - np.nanmedian(smooth)
    peak = int(np.nanargmax(np.abs(dev)))
    sign = np.sign(dev[peak]) or 1.0
    amplitude = abs(dev[peak])
    onset = peak
    while onset > max(0, peak - int(round(0.12 * n))):
        if sign * dev[onset] <= 0.15 * amplitude:
            break
        onset -= 1
    return onset


def align_square_wave(
    time_s: np.ndarray,
    current_nA: np.ndarray,
    period_hint_s: float | None = None,
    notch_hz: tuple[float, ...] | None = None,
    align: str = "spike",
) -> dict[str, Any] | None:
    """Edge-align a square-wave modulated recording into per-cycle rows.

    Each cycle is located from the measured signal rather than assumed from the
    nominal LED timing, because the driver runs slightly fast and the offset
    accumulates over a long recording. Returns ``None`` when no modulation can
    be established.
    """
    time_s = np.asarray(time_s, dtype=float)
    current_nA = np.asarray(current_nA, dtype=float)
    dt = robust_dt(time_s)
    if not np.isfinite(dt) or dt <= 0 or time_s.size < 16:
        return None

    hint = float(period_hint_s) if period_hint_s else 0.4925
    baseline = _coarse_running_median(time_s, current_nA, max(1.5, 2.5 * hint))
    x = current_nA - baseline
    x = x - np.nanmedian(x)
    if notch_hz:
        x = suppress_mains(x, dt, freqs=tuple(notch_hz))

    period = _refine_period_s(x, dt, hint) if period_hint_s else find_period_s(x, dt)
    if not np.isfinite(period):
        return None

    initial_rel = _event_grid((0.0, period), dt)
    anchors = np.arange(time_s[0], time_s[-1] - period, period)
    rough = _resampled_events(time_s, x, anchors, initial_rel, 0.98)
    if len(rough) < 2:
        return None
    rough -= np.nanmedian(rough, axis=1, keepdims=True)

    phase_index = _initial_edge_phase(np.nanmedian(rough, axis=0), dt, align)
    predicted = anchors + initial_rel[phase_index]

    smooth_n = max(3, int(round(0.0015 / dt))) | 1
    smooth = pd.Series(x).rolling(smooth_n, center=True, min_periods=1).mean().to_numpy()
    derivative = np.gradient(smooth, time_s)
    edge_sign = np.sign(
        np.nanmedian(derivative[np.clip(np.searchsorted(time_s, predicted), 0, len(time_s) - 1)])
    )
    if not np.isfinite(edge_sign) or edge_sign == 0:
        edge_sign = 1.0

    refined: list[float] = []
    search_s = min(0.04, 0.08 * period)
    for guess in predicted:
        lo = int(np.searchsorted(time_s, guess - search_s))
        hi = int(np.searchsorted(time_s, guess + search_s))
        if hi - lo < 3:
            continue
        local = derivative[lo:hi]
        score = np.abs(local) if align == "gate" else edge_sign * local
        if np.isfinite(score).any():
            refined.append(time_s[lo + int(np.nanargmax(score))])
    refined_arr = np.asarray(refined)

    if refined_arr.size >= 3:
        spacings = np.diff(refined_arr)
        good = np.abs(spacings - period) <= 0.08 * period
        if np.any(good):
            measured = float(np.median(spacings[good]))
            if abs(measured - period) <= 0.005 * period:
                period = measured

    pre_s = _PRE_EDGE_FRACTION * period
    relative = _event_grid((-pre_s, period - pre_s), dt)
    cycles = _resampled_events(time_s, x, refined_arr, relative, 0.98)
    if len(cycles) < 2:
        return None

    pre_mask = relative < -max(3 * dt, 0.001)
    cycles -= np.nanmedian(cycles[:, pre_mask], axis=1, keepdims=True)
    return {
        "relative_s": relative,
        "cycles": cycles,
        "average": np.nanmedian(cycles, axis=0),
        "period_s": float(period),
        "n_cycles": int(len(cycles)),
    }


def square_wave_metrics(
    time_s: np.ndarray,
    current_nA: np.ndarray,
    period_hint_s: float | None = None,
    notch_hz: tuple[float, ...] | None = None,
) -> dict[str, Any]:
    """Spike and plateau metrics from individually edge-aligned valid cycles.

    ``spike_nA`` is the fast transient at light onset (per-cycle 99th percentile
    of the early ON window minus the pre-edge baseline), while ``plateau_nA`` is
    the sustained ON-minus-OFF level taken from the robust composite. The two
    separate the capacitive and faradaic parts of the response.

    ``flag`` reports why a recording yielded no numbers: ``no_modulation`` when
    no periodic light response was found, ``overrange`` when the current exceeds
    the usable range, ``ok`` otherwise.

    Caveat -- ``plateau_nA`` is duty-cycle dependent and is only comparable
    between recordings that share a light timing. It contrasts the upper and
    lower 30% of the composite, which assumes ON and OFF occupy comparable
    fractions of a cycle; the drift baseline subtracted beforehand is also least
    stable at 50% duty, where it inflates the reported swing. Measured across
    this dataset the plateau-to-spike ratio runs 0.04 at 10% duty, 0.23 at 50%
    and 1.25 at 25%, so a plateau trend built from mixed timings would be
    reading the protocol rather than the sample. ``spike_nA``, measured against
    a per-cycle pre-edge baseline, does not have this dependence.
    """
    time_s = np.asarray(time_s, dtype=float)
    current_nA = np.asarray(current_nA, dtype=float)

    result: dict[str, Any] = {
        "duration_s": round(float(time_s[-1]), 1) if time_s.size else 0.0,
        "i_median_nA": float(np.nanmedian(current_nA)) if current_nA.size else float("nan"),
    }
    if current_nA.size and abs(np.nanmedian(current_nA)) > 1e6:
        return {**result, "flag": "overrange(|I|>1mA)"}

    aligned = align_square_wave(time_s, current_nA, period_hint_s, notch_hz, align="spike")
    if aligned is None:
        return {**result, "flag": "no_modulation"}

    relative = aligned["relative_s"]
    cycles = aligned["cycles"]
    average = aligned["average"]
    period = aligned["period_s"]

    on = relative >= 0
    pre = (relative >= -min(0.02, 0.08 * period)) & (relative < 0)
    peak = on & (relative <= min(0.20, 0.45 * period))

    baselines = np.nanmedian(cycles[:, pre], axis=1)
    spikes = np.nanpercentile(cycles[:, peak], 99, axis=1) - baselines

    upper = np.nanquantile(average, 0.70)
    lower = np.nanquantile(average, 0.30)
    plateau = float(
        np.nanmedian(average[average >= upper]) - np.nanmedian(average[average <= lower])
    )

    early = (relative >= 0) & (relative <= min(0.12 * period, 0.08))
    on_sign = np.sign(average[early][np.nanargmax(np.abs(average[early]))])
    off_search = (relative >= max(0.08 * period, 0.02)) & (relative <= 0.92 * period)
    if np.any(off_search):
        off_s = float(relative[off_search][np.nanargmin(on_sign * average[off_search])])
    else:
        off_s = period / 2.0

    result.update(
        on_s_measured=round(off_s, 4),
        period_ms=round(period * 1e3, 2),
        n_cycles=aligned["n_cycles"],
        spike_nA=float(np.nanmedian(spikes)),
        spike_sd_nA=float(np.nanstd(spikes, ddof=1)) if len(spikes) > 1 else 0.0,
        plateau_nA=plateau,
        flag="ok",
    )
    return result
