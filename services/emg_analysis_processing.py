"""Signal filtering, processing, and export helpers for EMG analysis traces."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.colors import is_color_like
from matplotlib.figure import Figure
from scipy import signal

from services.matplotlib_utils import new_subplots


@dataclass(slots=True)
class ProcessingResult:
    """Computed EMG analysis processing output ready for preview or export."""

    figure: Figure
    metadata: dict[str, Any]
    table: pd.DataFrame
    kind: str


def _float_or(value: object, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or(value: object, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def downsample_factor(value: object, n_points: int) -> int:
    raw = str(value if value is not None else "auto").strip().lower()
    if raw in {"", "auto", "adaptive"}:
        return max(1, int((max(0, int(n_points)) + 49999) // 50000))
    return max(1, min(5000, _int_or(value, 1)))


def _float_range(params: dict, key: str, default: float, low: float, high: float) -> float:
    value = _float_or(params.get(key), default)
    if value is None or not np.isfinite(value):
        value = default
    return max(low, min(high, float(value)))


def _int_range(params: dict, key: str, default: int, low: int, high: int) -> int:
    return max(low, min(high, _int_or(params.get(key), default)))


def _color(value: object, default: str) -> str:
    color = str(value or default).strip() or default
    return color if is_color_like(color) else default


def figure_params(
    params: dict,
    *,
    default_line_color: str,
    default_show_title: bool = True,
) -> dict[str, Any]:
    return {
        "width_in": _float_range(params, "fig_width_in", 10.0, 3.0, 20.0),
        "height_in": _float_range(params, "fig_height_in", 3.5, 1.8, 12.0),
        "dpi": _int_range(params, "fig_dpi", 300, 72, 1200),
        "line_width": _float_range(params, "trace_line_width", 0.6, 0.1, 5.0),
        "line_color": _color(params.get("trace_color"), default_line_color),
        "show_grid": _as_bool(params.get("show_grid"), True),
        "show_title": _as_bool(params.get("show_title"), default_show_title),
    }


def rhd_window(name: str, n: int) -> np.ndarray:
    if n <= 1:
        return np.ones(max(1, n), dtype=float)
    kind = str(name or "hann").strip().lower()
    if kind in {"none", "boxcar", "rect", "rectangular"}:
        return np.ones(n, dtype=float)
    if kind in {"hamming", "hamm"}:
        return np.hamming(n)
    if kind in {"blackman", "black"}:
        return np.blackman(n)
    return np.hanning(n)


def smooth_signal(
    y: np.ndarray,
    fs: float,
    win_ms: float,
    method: str = "moving",
    sg_poly: int = 2,
) -> np.ndarray:
    win_ms = max(0.0, float(win_ms or 0.0))
    if win_ms <= 0 or len(y) < 3:
        return y
    win = max(1, int(round(fs * win_ms / 1000.0))) if fs > 0 else 1
    if win <= 1:
        return y
    if str(method or "moving").strip().lower() in {"savgol", "sg", "savitzky-golay"}:
        if win % 2 == 0:
            win += 1
        win = min(win, len(y) if len(y) % 2 == 1 else len(y) - 1)
        if win < 3:
            return y
        poly = max(1, min(int(sg_poly or 2), win - 1))
        return signal.savgol_filter(y, window_length=win, polyorder=poly, mode="interp")
    kernel = np.ones(win, dtype=float) / win
    return np.convolve(y, kernel, mode="same")


def apply_time_window(
    t: np.ndarray,
    y: np.ndarray,
    x_min: float | None,
    x_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    if x_min is not None:
        mask = t >= x_min
        t, y = t[mask], y[mask]
    if x_max is not None:
        mask = t <= x_max
        t, y = t[mask], y[mask]
    return t, y


def filter_params(params: dict) -> dict[str, Any]:
    return {
        "type": str(params.get("filter_type", params.get("filter", "none")) or "none")
        .strip()
        .lower(),
        "low_hz": _float_or(params.get("filter_low_hz"), None),
        "high_hz": _float_or(params.get("filter_high_hz"), None),
        "notch_hz": _float_or(params.get("filter_notch_hz"), 60.0),
        "notch_q": max(1.0, _float_or(params.get("filter_notch_q"), 30.0) or 30.0),
        "order": max(1, min(12, _int_or(params.get("filter_order"), 4))),
    }


def apply_filter(y: np.ndarray, fs: float, params: dict) -> np.ndarray:
    kind = str(params.get("type") or "none").lower()
    y = np.asarray(y, dtype=float)
    if kind in {"", "none", "off"} or y.size < 8 or fs <= 0:
        return y

    nyq = fs / 2.0
    try:
        if kind == "notch":
            f0 = float(params.get("notch_hz") or 0.0)
            if not (0 < f0 < nyq):
                return y
            b, a = signal.iirnotch(f0, float(params.get("notch_q") or 30.0), fs=fs)
            return signal.filtfilt(b, a, y)

        order = int(params.get("order") or 4)
        low = params.get("low_hz")
        high = params.get("high_hz")
        if kind == "highpass":
            if low is None or not (0 < low < nyq):
                return y
            sos = signal.butter(order, low, btype="highpass", fs=fs, output="sos")
        elif kind == "lowpass":
            if high is None or not (0 < high < nyq):
                return y
            sos = signal.butter(order, high, btype="lowpass", fs=fs, output="sos")
        elif kind in {"bandpass", "iir", "iir_bandpass"}:
            if low is None or high is None or not (0 < low < high < nyq):
                return y
            sos = signal.butter(order, [low, high], btype="bandpass", fs=fs, output="sos")
        else:
            return y
        return signal.sosfiltfilt(sos, y)
    except ValueError:
        return y


def y_inversion_enabled(params: dict) -> bool:
    return _as_bool(params.get("invert_y"), False)


def apply_y_polarity(y: np.ndarray, params: dict) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    return -y if y_inversion_enabled(params) else y


def finish_axis(
    ax, t: np.ndarray, y_min: float | None, y_max: float | None, *, grid: bool = True
) -> None:
    if len(t):
        x0 = float(t[0])
        x1 = float(t[-1])
        if x1 == x0:
            x1 = x0 + 1e-9
        ax.set_xlim(x0, x1)
    ax.margins(x=0)
    if y_min is not None or y_max is not None:
        ymin, ymax = ax.get_ylim()
        ax.set_ylim(
            ymin if y_min is None else float(y_min), ymax if y_max is None else float(y_max)
        )
    if grid:
        ax.grid(True, alpha=0.4)
    else:
        ax.grid(False)


def process_trace(
    t,
    y,
    fs: float,
    params: dict,
    *,
    default_line_color: str,
) -> ProcessingResult:
    mode = str(params.get("process_type") or "envelope").strip().lower()
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    fig_params = figure_params(
        params, default_line_color=default_line_color, default_show_title=False
    )
    fig, ax = new_subplots(
        figsize=(fig_params["width_in"], fig_params["height_in"]),
        dpi=fig_params["dpi"],
    )
    meta: dict[str, Any] = {"process_type": mode}
    table = pd.DataFrame()
    if t.size == 0 or y.size == 0:
        ax.text(0.5, 0.5, "No data in current window", ha="center", va="center")
        ax.axis("off")
        fig.tight_layout()
        return ProcessingResult(fig, meta, table, mode)

    if mode == "envelope":
        processed = np.abs(signal.hilbert(y - np.nanmean(y)))
        envelope_smooth_ms = _float_or(params.get("envelope_smooth_ms"), 0.0) or 0.0
        processed = smooth_signal(processed, fs, envelope_smooth_ms)
        dsf = downsample_factor(params.get("downsample", "auto"), len(t))
        ax.plot(
            t[::dsf], processed[::dsf], color=fig_params["line_color"], lw=fig_params["line_width"]
        )
        ax.set_ylabel("Envelope (uV)")
        finish_axis(ax, t, None, None, grid=fig_params["show_grid"])
        table = pd.DataFrame({"time_s": t, "envelope_uV": processed})
        meta.update(
            {"envelope_smooth_ms": envelope_smooth_ms, "points": int(len(processed[::dsf]))}
        )
    elif mode == "smooth":
        win_ms = max(0.01, _float_or(params.get("smooth_ms"), 5.0) or 5.0)
        method = str(params.get("smooth_method") or "moving").strip().lower()
        sg_poly = max(1, min(5, _int_or(params.get("sg_poly"), 2)))
        processed = smooth_signal(y, fs, win_ms, method, sg_poly)
        dsf = downsample_factor(params.get("downsample", "auto"), len(t))
        ax.plot(
            t[::dsf], processed[::dsf], color=fig_params["line_color"], lw=fig_params["line_width"]
        )
        ax.set_ylabel("Smoothed (uV)")
        finish_axis(ax, t, None, None, grid=fig_params["show_grid"])
        table = pd.DataFrame({"time_s": t, "smoothed_uV": processed})
        meta.update(
            {
                "smooth_ms": win_ms,
                "smooth_method": method,
                "sg_poly": sg_poly,
                "points": int(len(processed[::dsf])),
            }
        )
    elif mode == "fitting":
        degree = max(1, min(5, _int_or(params.get("fit_degree"), 1)))
        t0 = t - float(t[0])
        coeff = np.polyfit(t0, y, degree)
        fitted = np.polyval(coeff, t0)
        dsf = downsample_factor(params.get("downsample", "auto"), len(t))
        if _as_bool(params.get("fit_show_raw"), True):
            ax.plot(
                t[::dsf], y[::dsf], color="#A5AFBF", lw=max(0.2, fig_params["line_width"] * 0.7)
            )
        ax.plot(
            t[::dsf],
            fitted[::dsf],
            color=fig_params["line_color"],
            lw=max(0.2, fig_params["line_width"] * 1.4),
        )
        ax.set_ylabel("Fit (uV)")
        finish_axis(ax, t, None, None, grid=fig_params["show_grid"])
        table = pd.DataFrame({"time_s": t, "raw_uV": y, "fitted_uV": fitted})
        meta.update({"fit_degree": degree, "coefficients": [float(c) for c in coeff]})
    elif mode == "fft":
        dt = 1.0 / fs if fs > 0 else float(np.median(np.diff(t))) if t.size > 1 else 1.0
        centered = y - np.nanmean(y)
        window_name = str(params.get("fft_window") or "hann").strip().lower()
        window = rhd_window(window_name, centered.size)
        freq = np.fft.rfftfreq(centered.size, d=dt)
        amp = np.abs(np.fft.rfft(centered * window))
        scale = np.sum(window) if np.sum(window) else centered.size
        amp = 2.0 * amp / scale
        fft_max_hz = _float_or(params.get("fft_max_hz"), None)
        if fft_max_hz is not None and fft_max_hz > 0:
            keep = freq <= fft_max_hz
            freq = freq[keep]
            amp = amp[keep]
        if _as_bool(params.get("fft_log"), False):
            amp = np.maximum(amp, np.finfo(float).tiny)
            ax.set_yscale("log")
        ax.plot(freq, amp, color=fig_params["line_color"], lw=fig_params["line_width"])
        ax.set_xlabel("Frequency (Hz)")
        ax.set_ylabel("Amplitude (uV)")
        ax.margins(x=0)
        if fig_params["show_grid"]:
            ax.grid(True, alpha=0.4)
        else:
            ax.grid(False)
        table = pd.DataFrame({"frequency_hz": freq, "amplitude_uV": amp})
        meta.update(
            {
                "points": int(freq.size),
                "fft_window": window_name,
                "fft_max_hz": fft_max_hz,
                "frequency_max": float(freq[-1]) if freq.size else 0.0,
            }
        )
    elif mode == "stft":
        win_ms = max(1.0, _float_or(params.get("stft_ms"), 100.0) or 100.0)
        nperseg = max(16, int(round(fs * win_ms / 1000.0))) if fs > 0 else 256
        nperseg = min(nperseg, y.size)
        overlap_pct = _float_range(params, "stft_overlap_pct", 50.0, 0.0, 95.0)
        noverlap = max(0, min(nperseg - 1, int(round(nperseg * overlap_pct / 100.0))))
        window_name = (
            str(params.get("stft_window") or params.get("fft_window") or "hann").strip().lower()
        )
        freq, tt, zxx = signal.stft(
            y - np.nanmean(y),
            fs=fs if fs > 0 else 1.0,
            window=rhd_window(window_name, nperseg),
            nperseg=nperseg,
            noverlap=noverlap,
        )
        mag = np.abs(zxx)
        stft_max_hz = _float_or(params.get("stft_max_hz"), None)
        if stft_max_hz is not None and stft_max_hz > 0:
            keep = freq <= stft_max_hz
            freq = freq[keep]
            mag = mag[keep, :]
        label = "Magnitude"
        if _as_bool(params.get("stft_log"), False):
            mag = np.log10(np.maximum(mag, np.finfo(float).tiny))
            label = "Log magnitude"
        cmap = str(params.get("stft_cmap") or "viridis").strip() or "viridis"
        im = ax.pcolormesh((tt + t[0]), freq, mag, shading="auto", cmap=cmap)
        fig.colorbar(im, ax=ax, label=label)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Frequency (Hz)")
        if tt.size:
            ax.set_xlim(float(t[0]), float(t[0] + tt[-1]))
        ax.grid(False)
        table_data: dict[str, Any] = {"frequency_hz": freq}
        for idx, time_s in enumerate(tt + t[0]):
            table_data[f"t_{float(time_s):.6g}_s"] = mag[:, idx]
        table = pd.DataFrame(table_data)
        meta.update(
            {
                "stft_ms": win_ms,
                "stft_overlap_pct": overlap_pct,
                "stft_window": window_name,
                "stft_max_hz": stft_max_hz,
                "frequency_bins": int(freq.size),
                "time_bins": int(tt.size),
            }
        )
    else:
        dsf = downsample_factor(params.get("downsample", "auto"), len(t))
        ax.plot(t[::dsf], y[::dsf], color=fig_params["line_color"], lw=fig_params["line_width"])
        ax.set_ylabel("Amplitude (uV)")
        finish_axis(ax, t, None, None, grid=fig_params["show_grid"])
        table = pd.DataFrame({"time_s": t, "value_uV": y})
        meta["process_type"] = "trace"
        mode = "trace"

    if mode not in {"fft", "stft"}:
        ax.set_xlabel("Time (s)")
    if fig_params["show_title"] and mode != "stft":
        ax.set_title(str(mode).upper(), fontsize=10, color="#5C5E62")
    fig.tight_layout()
    return ProcessingResult(fig, meta, table, mode)


def dataframe_csv_bytes(frame: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    frame.to_csv(buf, index=False)
    buf.seek(0)
    return buf.getvalue()


def figure_bytes(figure: Figure, fmt: str, *, dpi: int | None = None) -> bytes:
    buf = io.BytesIO()
    figure.savefig(buf, format=fmt, dpi=dpi if fmt == "png" else None, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()
