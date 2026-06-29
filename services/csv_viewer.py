from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services import csv_tools
from services.io_guards import assert_file_size_within_limit
from services.matplotlib_utils import close_figure, new_subplots
from services.trace_decimate import DEFAULT_MAX_POINTS, decimate_xy


class CsvViewerService:
    """Plot, merge, and export helpers for the CSV viewer routes."""

    def __init__(
        self,
        *,
        apply_axes_limits: Callable[..., None],
        fig_to_b64: Callable[[Any], str],
        clean_trace_svg: Callable[..., bytes],
        line_color: str,
    ):
        self.apply_axes_limits = apply_axes_limits
        self.fig_to_b64 = fig_to_b64
        self.clean_trace_svg = clean_trace_svg
        self.line_color = line_color

    @staticmethod
    def _num(value: Any, default: float) -> float:
        """Apply a numeric default for blank/unset typed request fields.

        Type coercion now happens at the request schema boundary
        (OptFloat/OptInt); this only fills the default when a field is None.
        """
        return default if value is None else value

    @staticmethod
    def _prepare_uplot_xy(
        x: np.ndarray, y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
        """Sort and de-duplicate x values before handing data to uPlot."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        n_in = int(x.shape[0])
        if n_in == 0:
            return x, y, {
                "n_prepared": 0,
                "x_unique_count": 0,
                "x_duplicate_count": 0,
                "x_strictly_increasing": True,
                "x_sorted": False,
                "x_duplicates_collapsed": False,
            }

        x_strictly_increasing = bool(np.all(np.diff(x) > 0)) if n_in > 1 else True
        unique_count = int(np.unique(x).shape[0])
        x_sorted = False
        x_duplicates_collapsed = False

        if n_in > 1:
            order = np.argsort(x, kind="mergesort")
            x_sorted = bool(np.any(order != np.arange(n_in)))
            if x_sorted:
                x = x[order]
                y = y[order]

            unique_x, inverse, counts = np.unique(
                x, return_inverse=True, return_counts=True
            )
            if int(unique_x.shape[0]) != int(x.shape[0]):
                sums = np.bincount(inverse, weights=y)
                y = sums / counts
                x = unique_x
                x_duplicates_collapsed = True

        return x, y, {
            "n_prepared": int(x.shape[0]),
            "x_unique_count": unique_count,
            "x_duplicate_count": n_in - unique_count,
            "x_strictly_increasing": x_strictly_increasing,
            "x_sorted": x_sorted,
            "x_duplicates_collapsed": x_duplicates_collapsed,
        }

    def plot_preview_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        path = data.get("path", "")
        x_col = data.get("x_col", "")
        y_col = data.get("y_col", "")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        downsample = self._num(data.get("dsf"), 1)

        x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max, downsample=downsample)
        fig, ax = new_subplots(figsize=(8, 4))
        ax.plot(x, y, color=self.line_color, lw=0.9)
        ax.margins(x=0)
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        ax.grid(True, alpha=0.4)
        self.apply_axes_limits(ax, None, None, y_min, y_max)
        ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
        fig.tight_layout()
        return {"img": self.fig_to_b64(fig)}

    def trace_data_payload(
        self, data: dict[str, Any], max_points: int = DEFAULT_MAX_POINTS
    ) -> dict[str, Any]:
        """Return decimated x/y arrays for client-side interactive plotting.

        No matplotlib involved: the browser draws and zooms the trace locally,
        so this avoids a server ``savefig`` on every view change. The data is
        envelope-preserving-decimated to keep the payload small without hiding
        spikes (see ``services.trace_decimate``).
        """
        path = data.get("path", "")
        x_col = data.get("x_col", "")
        y_col = data.get("y_col", "")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        downsample = self._num(data.get("dsf"), 1)

        x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max, downsample=downsample)
        n_full = int(x.shape[0])
        x, y, x_meta = self._prepare_uplot_xy(x, y)
        xd, yd = decimate_xy(x, y, max_points=max_points)
        warnings = []
        if x_meta["x_unique_count"] < 2 and n_full > 0:
            warnings.append(
                "Selected X column has only one distinct numeric value; choose a time column."
            )
        elif x_meta["x_duplicates_collapsed"]:
            warnings.append("Duplicate X values were averaged before plotting.")
        if x_meta["x_sorted"]:
            warnings.append("X values were sorted before plotting.")

        payload = {
            "x": xd.tolist(),
            "y": yd.tolist(),
            "x_label": x_col,
            "y_label": y_col,
            "title": Path(path).name,
            "y_min": y_min,
            "y_max": y_max,
            "n_full": n_full,
            "n_points": int(xd.shape[0]),
            "decimated": int(xd.shape[0]) < n_full,
            **x_meta,
        }
        if warnings:
            payload["warnings"] = warnings
        return payload

    def merge_preview_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        paths = data.get("paths", [])
        x_col = data.get("x_col", "")
        y_col = data.get("y_col", "")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        if not isinstance(paths, list) or not paths:
            raise ValueError("Merge queue is empty")
        if not x_col or not y_col:
            raise ValueError("x_col and y_col are required")

        colors = [
            "#3E6AE1",
            "#e06c00",
            "#2ca02c",
            "#d62728",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#17becf",
            "#bcbd22",
            "#7f7f7f",
        ]
        fig, ax = new_subplots(figsize=(9, 4))
        plotted = 0
        for index, path in enumerate(paths):
            try:
                x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max)
            except KeyError:
                continue
            ax.plot(x, y, color=colors[index % len(colors)], lw=0.9, label=Path(path).stem)
            plotted += 1
        if plotted == 0:
            close_figure(fig)
            raise ValueError("No mergeable rows found for selected columns/window")
        ax.margins(x=0)
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        ax.legend(fontsize=9, frameon=False)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        return {"img": self.fig_to_b64(fig)}

    def merge_export_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        paths = data.get("paths", [])
        x_col = data.get("x_col", "")
        y_col = data.get("y_col", "")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        drop_first_subsequent = bool(data.get("drop_first_subsequent", True))

        if not isinstance(paths, list) or not paths:
            raise ValueError("Merge queue is empty")
        if not x_col or not y_col:
            raise ValueError("x_col and y_col are required")

        out_df = csv_tools.merge_xy_tables(
            paths,
            x_col,
            y_col,
            x_min=x_min,
            x_max=x_max,
            drop_first_subsequent=drop_first_subsequent,
        )
        out_name = csv_tools.default_merge_name(x_min, x_max)
        out_path = Path(paths[0]).parent / out_name
        payload = out_df.to_csv(index=False).encode("utf-8")
        return {
            "payload": payload,
            "out_name": out_name,
            "out_path": out_path,
            "rows": int(len(out_df)),
        }

    def plot_export_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        path = data.get("path", "")
        fmt = str(data.get("fmt", "png") or "png").lower()
        x_col = data.get("x_col", "")
        y_col = data.get("y_col", "")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        src = Path(path)
        x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max)

        if fmt == "csv":
            buf = io.BytesIO()
            pd.DataFrame({x_col: x, y_col: y}).to_csv(buf, index=False)
            buf.seek(0)
            out_path = src.with_name(f"{src.stem}_plot.csv")
            return {
                "payload": buf.getvalue(),
                "out_path": out_path,
                "mimetype": "text/csv",
                "download_name": "export.csv",
                "output_type": "csv",
                "role": "plot_csv",
            }

        if fmt == "svg":
            return {
                "payload": self.clean_trace_svg(
                    x, y, y_min=y_min, y_max=y_max, line_color=self.line_color
                ),
                "out_path": src.with_name(f"{src.stem}_plot.svg"),
                "mimetype": "image/svg+xml",
                "download_name": f"{src.stem}_plot.svg",
                "output_type": "svg",
                "role": "plot",
            }

        fig, ax = new_subplots(figsize=(8, 4))
        ax.plot(x, y, color=self.line_color, lw=0.9)
        ax.margins(x=0)
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        self.apply_axes_limits(ax, None, None, y_min, y_max)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()

        buf = io.BytesIO()
        dpi = 300 if fmt == "png" else None
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        close_figure(fig)
        buf.seek(0)
        return {
            "payload": buf.getvalue(),
            "out_path": src.with_name(f"{src.stem}_plot.{fmt}"),
            "mimetype": "image/png" if fmt == "png" else "image/svg+xml",
            "download_name": f"export.{fmt}",
            "output_type": fmt,
            "role": "plot",
        }

    @staticmethod
    def full_csv_export_payload(data: dict[str, Any]) -> dict[str, Any]:
        path = data.get("path", "")
        src = Path(path)
        assert_file_size_within_limit(src, label="CSV")
        df = pd.read_csv(path)
        return {
            "payload": df.to_csv(index=False).encode("utf-8"),
            "out_path": src.with_name(f"{src.stem}_full.csv"),
            "download_name": f"{src.stem}.csv",
            "rows": int(len(df)),
        }
