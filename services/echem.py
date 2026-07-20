from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.ndimage import median_filter

from services.trace_decimate import DEFAULT_MAX_POINTS, decimate_xy

FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")

PC_VALUE_COL_HINTS = ["<i>", "current", "i/m", "i/\u00b5", "i/a", "ewe"]
PV_VALUE_COL_HINTS = ["voltage", "potential", "ewe", "v/"]
SAVE_MODES = frozenset({"save", "server", "path", "local", "source"})

_CURRENT_TO_NA = {
    "a": 1e9,
    "ma": 1e6,
    "ua": 1e3,
    "µa": 1e3,
    "na": 1.0,
}
_POTENTIAL_TO_MV = {"v": 1e3, "mv": 1.0, "uv": 1e-3, "µv": 1e-3}


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
    source = Path(str(path or "").strip()).expanduser()
    if not source.is_file():
        raise ValueError(f"EChem file not found: {source}")
    for sep in ("\t", ",", ";"):
        try:
            df = pd.read_csv(
                source,
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

    t, v = _parse_numeric_lines(source)
    return t, v, "time_s", "value"


def load_photocurrent(path: str | Path):
    return load_echem_file(path, PC_VALUE_COL_HINTS)


def load_photovoltage(path: str | Path):
    return load_echem_file(path, PV_VALUE_COL_HINTS)


def _column_unit(column: object, units: dict[str, float]) -> str | None:
    """Return the physical unit embedded in an EChem column header."""
    text = str(column or "").strip().lower().replace("μ", "µ")
    # Prefer longer names so ``mA``/``mV`` cannot be mistaken for ``A``/``V``.
    for unit in sorted(units, key=len, reverse=True):
        if re.search(rf"(?:^|[/_(\s]){re.escape(unit)}(?:$|[)\]\s])", text):
            return unit
        if text.endswith(f"_{unit}"):
            return unit
    return None


def load_photocurrent_nA(path: str | Path):
    """Load a CA-style current trace and normalize its values to nA.

    The legacy loader intentionally preserves source units for the interactive
    detector.  Quantification needs a stable contract, so this companion
    loader converts A/mA/uA/nA columns explicitly and rejects ambiguous value
    columns instead of silently labelling them nA.
    """
    t, current, t_col, current_col = load_photocurrent(path)
    unit = _column_unit(current_col, _CURRENT_TO_NA)
    if unit is None:
        raise ValueError(f"Current unit is missing from column: {current_col}")
    return t, current * _CURRENT_TO_NA[unit], t_col, "current_nA"


def load_photovoltage_mV(path: str | Path):
    """Load a CP-style potential trace and normalize its values to mV."""
    t, potential, t_col, potential_col = load_photovoltage(path)
    unit = _column_unit(potential_col, _POTENTIAL_TO_MV)
    if unit is None:
        raise ValueError(f"Potential unit is missing from column: {potential_col}")
    return t, potential * _POTENTIAL_TO_MV[unit], t_col, "potential_mV"


def load_cv(path: str | Path) -> tuple[np.ndarray, np.ndarray, str, str]:
    """Load cyclic-voltammetry potential (V) and current (uA)."""
    source = Path(str(path or "").strip()).expanduser()
    if not source.is_file():
        raise ValueError(f"EChem file not found: {source}")
    frame = pd.read_csv(source)
    potential_col = next(
        (c for c in frame.columns if "potential" in c.lower() or c.lower() in {"ewe", "e/v"}),
        None,
    )
    current_col = next((c for c in frame.columns if "current" in c.lower()), None)
    if potential_col is None or current_col is None:
        raise ValueError(f"CV columns not found in: {source.name}")

    potential = pd.to_numeric(frame[potential_col], errors="coerce")
    current = pd.to_numeric(frame[current_col], errors="coerce")
    valid = potential.notna() & current.notna()
    potential_v = potential[valid].to_numpy(dtype=float)
    current_raw = current[valid].to_numpy(dtype=float)
    potential_unit = _column_unit(potential_col, _POTENTIAL_TO_MV)
    current_unit = _column_unit(current_col, _CURRENT_TO_NA)
    if potential_unit is None or current_unit is None:
        raise ValueError(f"CV units are missing from columns: {potential_col}, {current_col}")
    return (
        potential_v * _POTENTIAL_TO_MV[potential_unit] / 1e3,
        current_raw * _CURRENT_TO_NA[current_unit] / 1e3,
        "potential_V",
        "current_uA",
    )


def load_corrtest(
    path: str | Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float | None]:
    """Load standardized or raw CorrTest data as seconds, nA, V and OCP V.

    Raw CorrTest ASCII files contain a long numeric metadata header.  Parsing
    them with the generic numeric-line fallback corrupts the time series, so
    the data block is located strictly after ``End Comments``.
    """
    source = Path(str(path or "").strip()).expanduser()
    if not source.is_file():
        raise ValueError(f"EChem file not found: {source}")

    if source.suffix.lower() == ".csv":
        frame = pd.read_csv(source)
        required = {"time_s", "current_nA"}
        if not required.issubset(frame.columns):
            raise ValueError(f"CorrTest columns not found in: {source.name}")
        time_s = pd.to_numeric(frame["time_s"], errors="coerce")
        current_nA = pd.to_numeric(frame["current_nA"], errors="coerce")
        potential = (
            pd.to_numeric(frame["potential_V"], errors="coerce")
            if "potential_V" in frame
            else pd.Series(np.zeros(len(frame)), index=frame.index)
        )
        valid = time_s.notna() & current_nA.notna() & potential.notna()
        ocp = None
        if "ocp_V" in frame:
            ocp_values = pd.to_numeric(frame["ocp_V"], errors="coerce").dropna()
            if not ocp_values.empty:
                ocp = float(ocp_values.iloc[0])
        return (
            time_s[valid].to_numpy(dtype=float),
            current_nA[valid].to_numpy(dtype=float),
            potential[valid].to_numpy(dtype=float),
            ocp,
        )

    raw = source.read_bytes()
    marker = b"End Comments"
    offset = raw.find(marker)
    if offset < 0:
        raise ValueError(f"CorrTest data marker not found in: {source.name}")
    data_start = raw.find(b"\n", offset)
    if data_start < 0:
        raise ValueError(f"CorrTest data block is empty in: {source.name}")

    rows: list[tuple[float, float, float]] = []
    for line in raw[data_start + 1 :].decode("latin-1", errors="ignore").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 3:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    if not rows:
        raise ValueError(f"No CorrTest samples found in: {source.name}")
    data = np.asarray(rows, dtype=float)
    time_s = data[:, 2]
    if len(time_s) > 1:
        sample_frequency = re.search(rb"SampleFrq=([\d.]+)", raw[: data_start + 1])
        expected_dt = 1.0 / float(sample_frequency.group(1)) if sample_frequency else None
        positive_deltas = np.diff(time_s)
        positive_deltas = positive_deltas[positive_deltas > 0]
        measured_dt = float(np.median(positive_deltas)) if len(positive_deltas) else float("nan")
        if expected_dt and np.isfinite(measured_dt):
            ratio = measured_dt / expected_dt
            if 500 <= ratio <= 1500:
                time_s = time_s * 1e-3
            elif not 0.5 <= ratio <= 2.0:
                raise ValueError(
                    f"CorrTest sample timing disagrees with SampleFrq in: {source.name}"
                )
    ocp_match = re.search(rb"OcpValue=([-+\d.eE]+)", raw[: data_start + 1])
    if not ocp_match:
        ocp_match = re.search(
            rb"Open Circuit Potential \(V\):\s*([-+\d.eE]+)", raw[: data_start + 1]
        )
    ocp = float(ocp_match.group(1)) if ocp_match else None
    return time_s, data[:, 1] * 1e9, data[:, 0], ocp


def _clip_xy(
    t: np.ndarray,
    values: np.ndarray,
    x_min: float | None,
    x_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    if x_min is not None:
        mask = t >= x_min
        t, values = t[mask], values[mask]
    if x_max is not None:
        mask = t <= x_max
        t, values = t[mask], values[mask]
    return t, values


def trace_data_payload(
    data: dict[str, Any],
    *,
    loader: Callable[[str | Path], tuple[np.ndarray, np.ndarray, str, str]],
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    """Return a decimated numeric echem trace for browser-side plotting."""
    path = data.get("path", "")
    x_min = _float_or(data.get("x_min", data.get("t0")), None)
    x_max = _float_or(data.get("x_max", data.get("t1")), None)
    y_min = _float_or(data.get("y_min"), None)
    y_max = _float_or(data.get("y_max"), None)

    t, values, t_col, value_col = loader(path)
    t, values = _clip_xy(t, values, x_min, x_max)
    n_full = int(t.shape[0])
    td, yd = decimate_xy(t, values, max_points=max_points)
    duration = round(float(t[-1] - t[0]), 3) if len(t) else 0
    return {
        "x": td.tolist(),
        "y": yd.tolist(),
        "x_label": t_col,
        "y_label": value_col,
        "title": Path(path).name,
        "t_range": [float(t[0]), float(t[-1])] if len(t) else [0.0, 0.0],
        "duration": duration,
        "y_min": y_min,
        "y_max": y_max,
        "n_full": n_full,
        "n_points": int(td.shape[0]),
        "decimated": int(td.shape[0]) < n_full,
    }


def photocurrent_trace_data_payload(
    data: dict[str, Any],
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    return trace_data_payload(data, loader=load_photocurrent, max_points=max_points)


def photovoltage_trace_data_payload(
    data: dict[str, Any],
    max_points: int = DEFAULT_MAX_POINTS,
) -> dict[str, Any]:
    return trace_data_payload(data, loader=load_photovoltage, max_points=max_points)


def _float_or(value: Any, default: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mode_requests_save(mode: Any) -> bool:
    return str(mode or "").strip().lower() in SAVE_MODES


def photovoltage_outputs(
    output_folder: Path, summary_path: Path, saved_paths: list[str]
) -> list[dict]:
    outputs = [
        {"path": str(output_folder), "type": "directory", "role": "photovoltage_pulse_folder"},
        {"path": str(summary_path), "type": "csv", "role": "photovoltage_pulse_summary"},
    ]
    outputs.extend(
        {"path": path, "type": "csv", "role": "photovoltage_pulse_window"}
        for path in saved_paths
        if path != str(summary_path)
    )
    return outputs


def photovoltage_export_payload(
    d: dict,
    *,
    savgol_filter_func: Callable | None = None,
) -> dict:
    pulses = d.get("pulses", [])
    path = d.get("path", "")
    mode = d.get("mode", "download")

    if not pulses:
        raise ValueError("No pulses to export")

    stem = Path(path).stem if path else "pulses"
    if mode_requests_save(mode):
        if not path:
            raise ValueError("Missing source file path")

        src = Path(path)
        t, v, _t_col, _v_col = load_photovoltage(path)
        if len(t) == 0:
            raise ValueError("No data points found in file")

        params = d.get("params", {}) if isinstance(d.get("params", {}), dict) else {}
        baseline_method = normalize_baseline_method(
            d.get(
                "baseline_method", params.get("baseline_method", d.get("detrend_method", "median"))
            )
        )
        baseline_win_ms = _float_or(
            d.get("baseline_win_ms", params.get("baseline_win_ms", d.get("bl_win_ms", 50.0))),
            50.0,
        )
        sg_window_ms = _float_or(d.get("sg_window_ms", params.get("sg_window_ms", 51.0)), 51.0)
        sg_poly = _int_or(d.get("sg_poly", params.get("sg_poly", 3)), 3)

        e_det = detrend_signal(
            t,
            v,
            method=baseline_method,
            window_ms=float(baseline_win_ms),
            sg_window_ms=float(sg_window_ms),
            sg_poly=int(sg_poly),
            savgol_filter_func=savgol_filter_func,
        )

        window = d.get("window", [])
        if isinstance(window, list) and len(window) >= 2:
            win_t0 = _float_or(window[0], np.nan)
            win_t1 = _float_or(window[1], np.nan)
        else:
            win_t0 = _float_or(d.get("t0"), np.nan)
            win_t1 = _float_or(d.get("t1"), np.nan)

        peak_min_v = _float_or(
            d.get("peak_min_v", d.get("peak_min_V", params.get("peak_min_V"))), np.nan
        )
        min_width_ms = _float_or(d.get("min_width_ms", params.get("min_width_ms")), np.nan)
        min_spacing_ms = _float_or(d.get("min_spacing_ms", params.get("min_spacing_ms")), np.nan)

        output_folder = src.with_name(src.stem)
        output_folder.mkdir(parents=True, exist_ok=True)

        summary_path = output_folder / f"{src.stem}_pulses_summary.csv"
        rows = []
        pulse_indices = []
        saved_paths = [str(summary_path)]
        export_idx = 1
        for pulse in pulses:
            gi = int(pulse.get("idx", -1)) if pulse.get("idx", None) is not None else -1
            if gi < 0 or gi >= len(t):
                tp = _float_or(pulse.get("t", pulse.get("time")), None)
                if tp is None:
                    continue
                gi = int(np.argmin(np.abs(t - tp)))

            rows.append(
                [
                    export_idx,
                    int(pulse.get("original_index", export_idx)),
                    float(t[gi]),
                    float(v[gi]),
                    float(e_det[gi]),
                    _float_or(pulse.get("width_ms", pulse.get("duration")), np.nan),
                    win_t0,
                    win_t1,
                    peak_min_v,
                    min_width_ms,
                    min_spacing_ms,
                    baseline_method,
                    float(baseline_win_ms),
                    float(sg_window_ms),
                    int(sg_poly),
                ]
            )
            pulse_indices.append((export_idx, gi))
            export_idx += 1

        header = (
            "export_index,original_index,peak_t_s,peak_V_raw,peak_V_detrended,halfwidth_ms,"
            "window_start_s,window_end_s,peak_min_V,min_width_ms,min_spacing_ms,"
            "baseline_method,baseline_win_ms,sg_window_ms,sg_poly"
        )
        with summary_path.open("w", encoding="utf-8") as f:
            f.write(header + "\n")
            for row in rows:
                f.write(
                    ",".join(
                        f"{value:.9g}" if isinstance(value, (float, int)) else str(value)
                        for value in row
                    )
                    + "\n"
                )

        pulse_window_ms = _float_or(d.get("pulse_window_ms"), 50.0)
        if pulse_window_ms is None or pulse_window_ms <= 0:
            pulse_window_ms = 50.0

        saved_count = 0
        for export_idx, gi in pulse_indices:
            tp = float(t[gi])
            t_start = tp - (pulse_window_ms / 1000.0)
            t_end = tp + (pulse_window_ms / 1000.0)
            mask = (t >= t_start) & (t <= t_end)
            if not np.any(mask):
                continue

            pulse_path = output_folder / f"{src.stem}_pulse_{export_idx:03d}.csv"
            with pulse_path.open("w", encoding="utf-8") as f:
                f.write("time_s,voltage_V\n")
                for t_val, v_val in zip(t[mask], e_det[mask], strict=True):
                    f.write(f"{float(t_val):.9g},{float(v_val):.9g}\n")
            saved_count += 1
            saved_paths.append(str(pulse_path))

        return {
            "kind": "save",
            "data": {
                "ok": True,
                "saved_path": str(output_folder),
                "summary_path": str(summary_path),
                "saved_count": saved_count,
                "saved_paths": saved_paths,
                "outputs": photovoltage_outputs(output_folder, summary_path, saved_paths),
            },
        }

    df = pd.DataFrame(pulses)
    return {
        "kind": "download",
        "payload": df.to_csv(index=False).encode("utf-8"),
        "mimetype": "text/csv",
        "download_name": f"{stem}_pulses.csv",
    }


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
        if pos_val >= pos_min_mA and neg_val <= -abs(neg_min_abs_mA):
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
    return median_filter(np.asarray(x), size=win_pts, mode="nearest")


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
    for loc, width in zip(locs, widths, strict=True):
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
