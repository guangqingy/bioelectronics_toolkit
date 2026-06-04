"""Envelope-preserving trace decimation for client-side interactive plots.

The WebGUI historically rendered every trace server-side with matplotlib and
shipped a base64 PNG to the browser. That makes every zoom/pan/parameter
change a full round trip plus a matplotlib ``savefig`` (hundreds of ms),
regardless of backend language.

The interactive plotting path instead sends *numbers* to the browser and lets
the client (uPlot) draw and zoom locally. To keep payloads small without
hiding spikes, we decimate with min/max bucketing rather than naive striding:
for each horizontal bucket we keep the local minimum and maximum sample. This
preserves the visual envelope (peaks and troughs) that electrophysiology and
electrochemistry traces depend on, which simple ``x[::n]`` striding aliases
away.

The function is intentionally dependency-light (numpy only) and pure so it can
be reused by any trace tool (CSV, ABF, echem, EMG) and unit-tested in
isolation.
"""

from __future__ import annotations

import numpy as np

# Roughly two points per horizontal pixel on a typical preview canvas; high
# enough to look identical to the full trace, low enough to stay snappy.
DEFAULT_MAX_POINTS = 4000


def decimate_xy(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = DEFAULT_MAX_POINTS,
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a trace to ``<= max_points`` while preserving its envelope.

    Splits the samples into ``max_points // 2`` buckets (by sample index, which
    is robust for non-monotonic x) and keeps the local min-y and max-y sample
    in each bucket, emitted in original sample order so the polyline stays
    continuous.

    Parameters
    ----------
    x, y:
        Equal-length 1-D arrays. Coerced to float.
    max_points:
        Target upper bound on returned points. Values below 4 (or ``None``)
        disable decimation.

    Returns
    -------
    (x, y):
        Float arrays with ``len <= max_points``. If the input already fits, it
        is returned unchanged (as float arrays).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = int(x.shape[0])

    if max_points is None or max_points < 4 or n <= max_points:
        return x, y

    n_buckets = max(1, max_points // 2)
    edges = np.linspace(0, n, n_buckets + 1).astype(int)

    out_x: list[float] = []
    out_y: list[float] = []
    for i in range(n_buckets):
        lo, hi = int(edges[i]), int(edges[i + 1])
        if hi <= lo:
            continue
        xs = x[lo:hi]
        ys = y[lo:hi]
        i_min = int(np.argmin(ys))
        i_max = int(np.argmax(ys))
        if i_min <= i_max:
            out_x.extend((xs[i_min], xs[i_max]))
            out_y.extend((ys[i_min], ys[i_max]))
        else:
            out_x.extend((xs[i_max], xs[i_min]))
            out_y.extend((ys[i_max], ys[i_min]))

    return np.asarray(out_x, dtype=float), np.asarray(out_y, dtype=float)
