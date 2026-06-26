from __future__ import annotations

from typing import Any, Callable

import numpy as np

from services.echem_lineshape_common import (
    DEFAULT_CROP_T0,
    DEFAULT_CROP_T1,
    _figure_class,
    _float_or,
    _int_or,
    normalize_kind,
)
from services.echem_lineshape_sources import y_limits_for_samples
from services.trace_decimate import DEFAULT_MAX_POINTS, decimate_xy


def resample_to_grid(t_rel: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    if len(t_rel) < 2:
        return np.full_like(grid, np.nan, dtype=float)
    return np.interp(grid, t_rel, y, left=np.nan, right=np.nan)


def selected_indexes(selected: object, total: int) -> list[int]:
    if not isinstance(selected, list):
        return []
    out: list[int] = []
    for item in selected:
        idx = _int_or(item, -1)
        if 0 <= idx < total:
            out.append(idx)
    seen: set[int] = set()
    return [idx for idx in out if not (idx in seen or seen.add(idx))]


def compute_average(
    samples: list[dict[str, Any]],
    selected: list[int],
    *,
    x_min: float = DEFAULT_CROP_T0,
    x_max: float = DEFAULT_CROP_T1,
    x_offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    if not selected:
        raise ValueError("No samples selected")
    first = samples[selected[0]]
    t_first = np.asarray(first.get("t") or [], dtype=float)
    dt_est = float(np.median(np.diff(t_first))) if len(t_first) > 2 else 2e-4
    dt = min(max(dt_est, 5e-5), 5e-4)
    grid = np.arange(x_min, x_max + dt / 2, dt, dtype=float)
    rows: list[np.ndarray] = []
    for idx in selected:
        sample = samples[idx]
        t = np.asarray(sample.get("t") or [], dtype=float) + x_offset
        y = np.asarray(sample.get("y") or [], dtype=float)
        if len(t) < 2 or len(y) < 2:
            continue
        rows.append(resample_to_grid(t, y, grid))
    if not rows:
        raise ValueError("No valid selected samples")
    matrix = np.vstack(rows)
    valid = np.any(np.isfinite(matrix), axis=0)
    if not np.any(valid):
        raise ValueError("Selected samples do not overlap the current x range")
    return grid[valid], np.nanmean(matrix[:, valid], axis=0)


def y_label(kind: str) -> str:
    return "Photovoltage |V| (V)" if normalize_kind(kind) == "photovoltage" else "Photocurrent (mA)"


def _apply_axes(
    ax,
    *,
    x_min: float,
    x_max: float,
    y_min: float | None,
    y_max: float | None,
    kind: str,
) -> None:
    ax.set_xlim(x_min, x_max)
    if y_min is not None and y_max is not None and y_max > y_min:
        ax.set_ylim(y_min, y_max)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(y_label(kind))
    ax.grid(True, alpha=0.3, linewidth=0.5)


def average_plot_b64(
    samples: list[dict[str, Any]],
    selected: list[int],
    fig_to_b64: Callable[..., str],
    *,
    kind: str,
    x_min: float,
    x_max: float,
    x_offset: float,
    y_min: float | None,
    y_max: float | None,
) -> tuple[str, dict[str, Any]]:
    Figure = _figure_class()
    grid, avg = compute_average(samples, selected, x_min=x_min, x_max=x_max, x_offset=x_offset)
    fig = Figure(figsize=(4.2, 3.0), dpi=130)
    ax = fig.add_subplot(111)
    ax.plot(grid, avg, lw=1.8, color="black")
    ax.set_title(f"Average (n={len(selected)})", fontsize=10)
    _apply_axes(ax, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, kind=kind)
    fig.tight_layout()
    avg_data = {
        "time_s": grid.tolist(),
        "t_ms": (grid * 1000.0).tolist(),
        "y": avg.tolist(),
        "y_column": (
            "photovoltage_abs_V"
            if normalize_kind(kind) == "photovoltage"
            else "photocurrent_mA"
        ),
    }
    return fig_to_b64(fig), avg_data


def plot_payload(data: dict[str, Any], fig_to_b64: Callable[..., str]) -> dict[str, Any]:
    samples = data.get("samples") if isinstance(data.get("samples"), list) else []
    if not samples:
        raise ValueError("No samples provided")
    kind = normalize_kind(data.get("kind"))
    x_min = _float_or(data.get("crop_t0"), DEFAULT_CROP_T0)
    x_max = _float_or(data.get("crop_t1"), DEFAULT_CROP_T1)
    if x_min is None or x_max is None or x_max <= x_min:
        raise ValueError("X max must be greater than X min")
    selected = selected_indexes(data.get("selected"), len(samples))
    x_offset = _float_or(data.get("x_offset"), 0.0) or 0.0
    y_min = _float_or(data.get("y_min"), None)
    y_max = _float_or(data.get("y_max"), None)
    avg_img, avg_data = average_plot_b64(
        samples,
        selected,
        fig_to_b64,
        kind=kind,
        x_min=x_min,
        x_max=x_max,
        x_offset=x_offset,
        y_min=y_min,
        y_max=y_max,
    )
    return {
        "avg_img": avg_img,
        "avg_data": avg_data,
        "n_selected": len(selected),
        "n_total": len(samples),
        "x_limits": [x_min, x_max],
        "y_limits": (
            [y_min, y_max]
            if y_min is not None and y_max is not None
            else y_limits_for_samples(samples)
        ),
    }


def trace_data_payload(data: dict[str, Any], *, max_points: int = DEFAULT_MAX_POINTS) -> dict[str, Any]:
    samples = data.get("samples") if isinstance(data.get("samples"), list) else []
    if not samples:
        raise ValueError("No samples provided")
    kind = normalize_kind(data.get("kind"))
    x_min = _float_or(data.get("crop_t0"), DEFAULT_CROP_T0)
    x_max = _float_or(data.get("crop_t1"), DEFAULT_CROP_T1)
    if x_min is None or x_max is None or x_max <= x_min:
        raise ValueError("X max must be greater than X min")
    selected = selected_indexes(data.get("selected"), len(samples))
    x_offset = _float_or(data.get("x_offset"), 0.0) or 0.0
    y_min = _float_or(data.get("y_min"), None)
    y_max = _float_or(data.get("y_max"), None)
    grid, avg = compute_average(samples, selected, x_min=x_min, x_max=x_max, x_offset=x_offset)
    dx, dy = decimate_xy(grid, avg, max_points=max_points)
    finite = avg[np.isfinite(avg)]
    if y_min is None or y_max is None or y_max <= y_min:
        if finite.size:
            ymin = float(np.min(finite))
            ymax = float(np.max(finite))
            span = max(1e-12, ymax - ymin)
            y_min = ymin - 0.05 * span
            y_max = ymax + 0.05 * span
        else:
            y_min, y_max = 0.0, 1.0
    avg_data = {
        "time_s": grid.tolist(),
        "t_ms": (grid * 1000.0).tolist(),
        "y": avg.tolist(),
        "y_column": (
            "photovoltage_abs_V"
            if normalize_kind(kind) == "photovoltage"
            else "photocurrent_mA"
        ),
    }
    return {
        "x": dx.tolist(),
        "y": dy.tolist(),
        "x_label": "Time (s)",
        "y_label": y_label(kind),
        "title": f"Average (n={len(selected)})",
        "y_min": y_min,
        "y_max": y_max,
        "n_full": int(len(grid)),
        "n_points": int(len(dx)),
        "decimated": int(len(dx)) < int(len(grid)),
        "avg_data": avg_data,
        "n_selected": len(selected),
        "n_total": len(samples),
        "x_limits": [x_min, x_max],
        "y_limits": [y_min, y_max],
    }


__all__ = [
    "average_plot_b64",
    "compute_average",
    "plot_payload",
    "resample_to_grid",
    "selected_indexes",
    "trace_data_payload",
    "y_label",
]
