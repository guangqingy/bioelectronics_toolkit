from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from services.io_guards import assert_file_size_within_limit


def read_columns(path: str | Path, nrows: int = 2) -> list[str]:
    assert_file_size_within_limit(path, label="CSV")
    return list(pd.read_csv(path, nrows=nrows).columns)


def _numeric_pair(df: pd.DataFrame, x_col: str, y_col: str) -> tuple[np.ndarray, np.ndarray]:
    if x_col not in df.columns or y_col not in df.columns:
        raise KeyError(f"Columns {x_col!r} or {y_col!r} not found")
    x = pd.to_numeric(df[x_col], errors="coerce").to_numpy()
    y = pd.to_numeric(df[y_col], errors="coerce").to_numpy()
    valid = np.isfinite(x) & np.isfinite(y)
    return x[valid], y[valid]


def window_xy(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float | None = None,
    x_max: float | None = None,
    downsample: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    dsf = max(1, int(downsample or 1))
    xx = np.asarray(x)[::dsf]
    yy = np.asarray(y)[::dsf]
    if x_min is not None:
        mask = xx >= float(x_min)
        xx, yy = xx[mask], yy[mask]
    if x_max is not None:
        mask = xx <= float(x_max)
        xx, yy = xx[mask], yy[mask]
    return xx, yy


def load_xy(
    path: str | Path,
    x_col: str,
    y_col: str,
    x_min: float | None = None,
    x_max: float | None = None,
    downsample: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    assert_file_size_within_limit(path, label="CSV")
    df = pd.read_csv(path, usecols=[x_col, y_col])
    x, y = _numeric_pair(df, x_col, y_col)
    return window_xy(x, y, x_min=x_min, x_max=x_max, downsample=downsample)


def tag_float(value: float | None) -> str:
    if value is None:
        return "auto"
    return f"{float(value):.6f}".replace(".", "p")


def merge_xy_tables(
    paths: Iterable[str | Path],
    x_col: str,
    y_col: str,
    x_min: float | None = None,
    x_max: float | None = None,
    drop_first_subsequent: bool = True,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for index, path in enumerate(paths):
        x, y = load_xy(path, x_col, y_col, x_min=x_min, x_max=x_max)
        if x.size == 0:
            continue

        sub = pd.DataFrame({x_col: x, y_col: y}).reset_index(drop=True)
        if drop_first_subsequent and index > 0 and len(sub) > 0:
            sub = sub.iloc[1:].reset_index(drop=True)
        if not sub.empty:
            rows.append(sub)

    if not rows:
        raise ValueError("No rows available in selected X window")
    return pd.concat(rows, axis=0, ignore_index=True)


def default_merge_name(x_min: float | None = None, x_max: float | None = None) -> str:
    return f"merged_preview_{tag_float(x_min)}-{tag_float(x_max)}.csv"
