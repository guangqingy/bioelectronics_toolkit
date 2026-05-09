# photocurrent_multiple_figure_GUI.py
# -*- coding: utf-8 -*-
"""
Photocurrent Multiple Figure GUI (two-line folder panel + queue ordering + custom output folder)
- Main folder → scan immediate subfolders containing at least one "summary_*.csv".
- Left panel shows:
    * Upper list: ALL detected subfolders.
    * Lower list: ANALYSIS QUEUE (user-controlled order).
    * Buttons: Add ↓, Remove ↑, Move Up, Move Down.
    * Queue Label editor:
        - default = folder name
        - editable per queue item
        - "Use Folder Name" fills the input with the folder name (does NOT apply)
        - "Set Label" applies the current input to the selected queue item
- Right panel:
    * Output folder name (created under MAIN).
    * Linear and Log x-range inputs (multiple ranges like "0-1; 0-4; 0-100").
    * Metric toggles: Peak / Integral.
    * "Analyze Queue" action.
- Uses queue labels (editable) in legends.
- Y-axis auto-scales within each x-range.
- Output: <MAIN>/<OUTPUT_NAME>/*.png
- No dynamic status text that alters layout; completion via dialogs only.

Requirements: numpy, pandas, matplotlib, tkinter
"""

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import LogLocator, ScalarFormatter

# --------------------------- Global style ---------------------------
mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": [
            "Arial",
            "Helvetica",
            "DejaVu Sans",
            "Liberation Sans",
            "Nimbus Sans",
            "sans-serif",
        ],
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    }
)

from config import DEFAULT_START_DIR  # noqa: E402  (load from config.py)
DEFAULT_OUT_NAME = "plots_quick_analysis"
DPI = 600
EPS = 1e-9

# Flexible column names to interoperate with prior pipelines
POWER_COL_CANDIDATES = ["power_density", "power_mW_mm2", "power_mW_mm^2", "power_mW"]
PEAK_COLS_CANDIDATES = ["capacitance_peak", "capacitance_peak_norm"]
INT_COLS_CANDIDATES = ["integral_charge", "integral_charge_norm"]


# --------------------------- CSV helpers ---------------------------
def _find_matching_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return the first matching column by case-sensitive then case-insensitive search."""
    for c in candidates:
        if c in df.columns:
            return c
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _read_all_summaries(folder: Path) -> Optional[pd.DataFrame]:
    """
    Read all "summary_*.csv" files directly under a subfolder (non-recursive).
    Standardize 'power_density', and keep peak/integral columns when present.
    Return concatenated DataFrame or None.
    """
    csvs = sorted(folder.glob("summary_*.csv"))
    if not csvs:
        return None

    frames = []
    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            print(f"[WARN] failed to read {csv_path}: {e}")
            continue
        if df is None or df.empty:
            continue

        pcol = _find_matching_column(df, POWER_COL_CANDIDATES)
        if pcol is None:
            print(f"[WARN] no power column in {csv_path.name}")
            continue

        df = df.rename(columns={pcol: "power_density"}).copy()

        # Keep columns if they exist
        keep_meta = ["sample_id", "spot_id", "seq_index", "file"]
        cols = ["power_density"]
        peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
        int_col = _find_matching_column(df, INT_COLS_CANDIDATES)
        if peak_col:
            cols.append(peak_col)
        if int_col:
            cols.append(int_col)
        cols.extend([c for c in keep_meta if c in df.columns and c not in cols])

        frames.append(df[cols].copy())

    if not frames:
        return None

    out = pd.concat(frames, axis=0, ignore_index=True)
    out["power_density"] = pd.to_numeric(out["power_density"], errors="coerce")
    for c in PEAK_COLS_CANDIDATES + INT_COLS_CANDIDATES:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out.replace([np.inf, -np.inf], np.nan, inplace=True)
    out.dropna(subset=["power_density"], inplace=True)
    return out if not out.empty else None


def _aggregate(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """
    Group by power_density and compute mean±SEM for value_col; sorted by power.
    SEM = sample std (ddof=1) / sqrt(n); if n<=1 -> sem=0.
    """
    tmp = df[["power_density", value_col]].dropna()
    if tmp.empty:
        return pd.DataFrame(columns=["power_density", "mean", "sem", "n"])
    g = (
        tmp.groupby("power_density", as_index=False)
        .agg(mean=(value_col, "mean"), std=(value_col, "std"), n=(value_col, "count"))
        .sort_values("power_density", kind="mergesort")
    )
    g["std"] = g["std"].fillna(0.0)
    g["n"] = g["n"].fillna(0).astype(int)
    g["sem"] = 0.0
    m = g["n"].values > 1
    g.loc[m, "sem"] = g.loc[m, "std"].values / np.sqrt(g.loc[m, "n"].values.astype(float))
    return g[["power_density", "mean", "sem", "n"]]


def _raw_max_value(df: pd.DataFrame, value_col: str) -> Optional[float]:
    """
    Return max(raw values) for value_col (peak intensity / charge magnitude) within this series.
    This is the normalization factor: strongest peak (or largest integral) becomes 1.
    """
    if df is None or df.empty or (value_col not in df.columns):
        return None
    v = pd.to_numeric(df[value_col], errors="coerce").values
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    m = float(np.max(v))
    if (not np.isfinite(m)) or m == 0:
        return None
    return m


def _scale_group_by_factor(g: pd.DataFrame, factor: float) -> Optional[pd.DataFrame]:
    """Scale aggregated group mean/sem by factor; keep columns power_density/mean/sem/n."""
    if g is None or g.empty or ("mean" not in g.columns):
        return None
    if (factor is None) or (not np.isfinite(factor)) or factor == 0:
        return None
    gg = g.copy()
    gg["mean"] = gg["mean"] / factor
    if "sem" in gg.columns:
        gg["sem"] = gg["sem"] / factor
    return gg


# --------------------------- Plot helpers ---------------------------
def _parse_ranges(s: str) -> List[Tuple[float, float]]:
    """
    Parse ranges string like '0-1; 0-4; 0.01-100'.
    Returns list of (xmin, xmax). Invalid tokens are ignored.
    """
    out: List[Tuple[float, float]] = []
    if not s.strip():
        return out
    for block in s.split(";"):
        block = block.strip()
        if not block or "-" not in block:
            continue
        a, b = block.split("-", 1)
        try:
            xmin = float(a.strip())
            xmax = float(b.strip())
        except Exception:
            continue
        if np.isfinite(xmin) and np.isfinite(xmax) and xmin != xmax:
            if xmin > xmax:
                xmin, xmax = xmax, xmin
            out.append((xmin, xmax))
    return out


def _min_positive_x(dfs: List[pd.DataFrame]) -> float:
    """Find the smallest positive power across aggregated dataframes."""
    vals = []
    for df in dfs:
        if df is None or df.empty or "power_density" not in df:
            continue
        x = np.asarray(df["power_density"])
        x = x[np.isfinite(x) & (x > 0)]
        if x.size:
            vals.append(np.min(x))
    return min(vals) if vals else EPS


def _clip_to_range(df: pd.DataFrame, xmin: float, xmax: float) -> pd.DataFrame:
    """Subset df within [xmin, xmax] on power_density."""
    m = np.isfinite(df["power_density"])
    g = df.loc[m]
    g = g[(g["power_density"] >= xmin) & (g["power_density"] <= xmax)]
    return g


def _legend_outside_right(ax: plt.Axes, fig: plt.Figure):
    """
    Place legend outside on the right, vertically centered.
    Shrink the main axes area so the legend is fully visible in the saved figure.
    """
    # Reserve ~22% of figure width for the legend (do NOT enlarge left column)
    fig.tight_layout(rect=(0.0, 0.0, 0.78, 1.0))
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, borderaxespad=0.0)


def _unique_label(existing: Dict[str, pd.DataFrame], label: str) -> str:
    """Ensure labels are unique among series to avoid overwrite."""
    if label not in existing:
        return label
    base = label
    k = 2
    while f"{base} ({k})" in existing:
        k += 1
    return f"{base} ({k})"


def _plot_linear(
    groups: Dict[str, pd.DataFrame],
    ylabel: str,
    title_txt: str,
    xmin: float,
    xmax: float,
    out_path: Path,
):
    """One figure (linear x). Y autoscale from data in range; legend outside right."""
    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        plt.close(fig)
        return
    ax.set_xlabel("Power density (mW/mm²)")
    ax.set_ylabel(ylabel)
    ax.set_xlim(xmin, xmax)
    ax.set_title(title_txt)
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    _legend_outside_right(ax, fig)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def _plot_log(
    groups: Dict[str, pd.DataFrame],
    ylabel: str,
    title_txt: str,
    xmin: float,
    xmax: float,
    out_path: Path,
):
    """One figure (log x). Y autoscale; legend outside right; clamp xmin>0."""
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True
    if not plotted:
        plt.close(fig)
        return

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)
    ax.set_xlabel("Power density (mW/mm², log scale)")
    ax.set_ylabel(ylabel)
    ax.set_title(title_txt + "  (log x)")
    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, alpha=0.5)
    _legend_outside_right(ax, fig)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


# --------------------------- SVG (plot-only + legend-only) ---------------------------
def _plot_linear_svg_plotonly(
    groups: Dict[str, pd.DataFrame], xmin: float, xmax: float, out_path: Path
):
    """
    Linear x SVG: frame + ticks + plotted content only.
    No text at all (no title/labels/tick numbers). Transparent background.
    """
    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        gsub = _clip_to_range(gdf, xmin, xmax)
        if gsub.empty:
            continue
        ax.errorbar(
            gsub["power_density"].values,
            gsub["mean"].values,
            yerr=gsub["sem"].values if "sem" in gsub.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return

    ax.set_xlim(xmin, xmax)

    ax.grid(False)

    # Remove all text but keep tick marks
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])

    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()

    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)


def _plot_log_svg_plotonly(
    groups: Dict[str, pd.DataFrame], xmin: float, xmax: float, out_path: Path
):
    """
    Log x SVG: frame + ticks + plotted content only.
    No text at all (no title/labels/tick numbers). Transparent background.
    """
    minpos = _min_positive_x(list(groups.values()))
    xmin_safe = max(xmin if xmin > 0 else minpos, EPS)

    fig = plt.figure(figsize=(6, 4.5), dpi=DPI)
    ax = plt.gca()
    plotted = False
    for label, gdf in groups.items():
        if gdf is None or gdf.empty:
            continue
        g = gdf.copy()
        g = g[(g["power_density"] > 0) & np.isfinite(g["power_density"])]
        g = _clip_to_range(g, xmin_safe, xmax)
        if g.empty:
            continue
        ax.errorbar(
            g["power_density"].values,
            g["mean"].values,
            yerr=g["sem"].values if "sem" in g.columns else None,
            fmt="o-",
            linewidth=1.2,
            markersize=3.5,
            capsize=2.5,
            label=label,
        )
        plotted = True

    if not plotted:
        plt.close(fig)
        return

    ax.set_xscale("log")
    ax.set_xlim(xmin_safe, xmax)

    ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    ax.xaxis.set_major_formatter(ScalarFormatter())

    ax.grid(False)

    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_title("")
    ax.set_xticklabels([])
    ax.set_yticklabels([])

    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()

    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)


def _legend_svg_only_no_text(groups: Dict[str, pd.DataFrame], out_path: Path):
    """
    Legend-only SVG (no text labels), transparent background.
    No dummy plot (avoid strange points).
    Handles mimic 'o-' style and default color cycle order.
    """
    labels = list(groups.keys())
    if not labels:
        return

    prop = mpl.rcParams.get("axes.prop_cycle", None)
    colors = []
    if prop is not None:
        try:
            colors = prop.by_key().get("color", [])
        except Exception:
            colors = []
    if not colors:
        colors = ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]

    handles = []
    for i, _lab in enumerate(labels):
        c = colors[i % len(colors)]
        h = Line2D([], [], color=c, marker="o", linestyle="-", linewidth=1.2, markersize=3.5)
        handles.append(h)

    fig = plt.figure(figsize=(1.1, 0.35 * max(1, len(handles))), dpi=DPI)
    ax = plt.gca()

    ax.legend(
        handles=handles,
        labels=[""] * len(handles),
        loc="center",
        frameon=False,
        handlelength=1.6,
        handletextpad=0.0,
        borderaxespad=0.0,
        labelspacing=0.35,
    )

    ax.axis("off")
    fig.patch.set_alpha(0.0)
    ax.set_facecolor("none")
    fig.tight_layout()

    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", transparent=True, facecolor="none")
    plt.close(fig)


# --------------------------- GUI ---------------------------
class AbfPhotocurrentFigureApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABF — Photocurrent Figures")
        self.geometry("1200x740")
        self.minsize(920, 620)

        # State
        self.main_folder = tk.StringVar(value=DEFAULT_START_DIR)
        self.available_subfolders: List[Path] = []  # all detected (upper list)
        self.queue_subfolders: List[Path] = []  # analysis queue (lower list, ordered)
        self.queue_labels: Dict[str, str] = {}  # folder_name -> custom label

        # Output folder name (created under MAIN)
        self.output_name = tk.StringVar(value=DEFAULT_OUT_NAME)

        # Metric switches
        self.use_peak = tk.IntVar(value=1)
        self.use_integral = tk.IntVar(value=1)

        # X-range entries
        self.linear_ranges_var = tk.StringVar(value="0-1; 0-100")
        self.log_ranges_var = tk.StringVar(value="0.01-1; 0.01-100")

        # Label editor var
        self.label_edit_var = tk.StringVar(value="")

        # Layout
        self._build_layout()
        self._build_left()
        self._build_right()

        # Initial scan
        self.scan_main()

    # ---- Layout
    def _build_layout(self):
        # Keep left column width unchanged (do NOT enlarge)
        self.grid_columnconfigure(0, minsize=340, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left = ttk.Frame(self, padding=(8, 8, 8, 8))
        self.left.grid(row=0, column=0, sticky="nsew")
        self.left.update_idletasks()
        self.left.grid_propagate(False)

        self.right = ttk.Frame(self, padding=(8, 8, 8, 8))
        self.right.grid(row=0, column=1, sticky="nsew")

    def _build_left(self):
        # Main folder chooser
        box = ttk.LabelFrame(self.left, text="Main Folder")
        box.pack(fill="x")
        r1 = ttk.Frame(box)
        r1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(r1, text="Path:").pack(side="left")
        ttk.Entry(r1, textvariable=self.main_folder, width=30).pack(
            side="left", padx=6, fill="x", expand=True
        )
        r2 = ttk.Frame(box)
        r2.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(r2, text="Browse…", command=self.choose_main).pack(side="left")
        ttk.Button(r2, text="Refresh", command=self.scan_main).pack(side="left", padx=6)

        # Upper list: Available subfolders
        avail_box = ttk.LabelFrame(self.left, text="Available Subfolders (summary_*.csv)")
        avail_box.pack(fill="both", expand=False, pady=(6, 4))
        self.list_avail = tk.Listbox(
            avail_box, height=10, exportselection=False, selectmode=tk.EXTENDED
        )
        self.list_avail.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb1 = ttk.Scrollbar(avail_box, orient="vertical", command=self.list_avail.yview)
        sb1.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.list_avail.config(yscrollcommand=sb1.set)

        # Middle controls (compact row)
        mid = ttk.Frame(self.left)
        mid.pack(fill="x", pady=(4, 4))
        ttk.Button(mid, text="Add ↓", command=self.add_to_queue).pack(side="left", padx=(0, 6))
        ttk.Button(mid, text="Remove ↑", command=self.remove_from_queue).pack(side="left", padx=6)
        ttk.Button(mid, text="Move Up", command=lambda: self.move_in_queue(-1)).pack(
            side="left", padx=6
        )
        ttk.Button(mid, text="Move Down", command=lambda: self.move_in_queue(+1)).pack(
            side="left", padx=6
        )

        # Lower list: Analysis queue (ordered)
        queue_box = ttk.LabelFrame(self.left, text="Analysis Queue (ordered)")
        queue_box.pack(fill="both", expand=False, pady=(4, 0))
        self.list_queue = tk.Listbox(
            queue_box, height=8, exportselection=False, selectmode=tk.EXTENDED
        )
        self.list_queue.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.list_queue.bind(
            "<<ListboxSelect>>", self._on_queue_select
        )  # update entry on selection
        sb2 = ttk.Scrollbar(queue_box, orient="vertical", command=self.list_queue.yview)
        sb2.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.list_queue.config(yscrollcommand=sb2.set)

        # Label editor (two-row layout to avoid widening left column)
        label_box = ttk.LabelFrame(self.left, text="Queue Label (default: folder name)")
        label_box.pack(fill="x", expand=False, pady=(6, 0))

        r_lab1 = ttk.Frame(label_box)
        r_lab1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(r_lab1, text="Label:").pack(side="left")
        ttk.Entry(r_lab1, textvariable=self.label_edit_var, width=26).pack(side="left", padx=6)

        r_lab2 = ttk.Frame(label_box)
        r_lab2.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Button(r_lab2, text="Set Label", command=self.set_label_for_selected).pack(side="left")
        ttk.Button(r_lab2, text="Use Folder Name", command=self.fill_folder_name_into_entry).pack(
            side="left", padx=6
        )

    def _build_right(self):
        # Row A: Output folder name (under MAIN)
        rowA = ttk.LabelFrame(self.right, text="Output")
        rowA.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        rowA.grid_columnconfigure(1, weight=1)
        ttk.Label(rowA, text="Folder name:").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        ttk.Entry(rowA, textvariable=self.output_name).grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )

        # Row B: Linear ranges
        rowB = ttk.LabelFrame(self.right, text="Linear X-ranges (mW/mm²)")
        rowB.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        rowB.grid_columnconfigure(0, weight=1)
        ttk.Entry(rowB, textvariable=self.linear_ranges_var).grid(
            row=0, column=0, sticky="ew", padx=6, pady=6
        )
        ttk.Label(rowB, text="Format: a-b; c-d; …").grid(
            row=1, column=0, sticky="w", padx=6, pady=(0, 6)
        )

        # Row C: Log ranges
        rowC = ttk.LabelFrame(self.right, text="Log X-ranges (mW/mm²)")
        rowC.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        rowC.grid_columnconfigure(0, weight=1)
        ttk.Entry(rowC, textvariable=self.log_ranges_var).grid(
            row=0, column=0, sticky="ew", padx=6, pady=6
        )
        ttk.Label(rowC, text="Format: a-b; c-d; …  (a>0)").grid(
            row=1, column=0, sticky="w", padx=6, pady=(0, 6)
        )

        # Row D: Metrics
        rowD = ttk.LabelFrame(self.right, text="Metrics")
        rowD.grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Checkbutton(rowD, text="Peak (capacitance)", variable=self.use_peak).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(rowD, text="Integral (charge)", variable=self.use_integral).pack(
            side="left", padx=6
        )

        # Row E: Actions
        rowE = ttk.Frame(self.right)
        rowE.grid(row=4, column=0, sticky="w")
        ttk.Button(rowE, text="Analyze Queue", command=self.run_analysis).pack(side="left")
        ttk.Button(rowE, text="Normalize", command=self.export_normalized).pack(side="left", padx=6)
        ttk.Button(rowE, text="Export SVG", command=self.export_svg).pack(side="left", padx=6)

        ttk.Frame(self.right).grid(row=5, column=0, sticky="nsew")

    # ---- Folder ops
    def choose_main(self):
        try:
            d = filedialog.askdirectory(initialdir=self.main_folder.get() or DEFAULT_START_DIR)
        except Exception as e:
            messagebox.showerror("Folder", f"Folder dialog failed: {e}")
            return
        if d:
            self.main_folder.set(d)
            self.scan_main()

    def scan_main(self):
        """List immediate subfolders that contain at least one summary_*.csv."""
        base = Path(self.main_folder.get())
        self.list_avail.delete(0, tk.END)
        self.available_subfolders = []

        if not base.is_dir():
            return

        for child in sorted(base.iterdir()):
            if child.is_dir() and any(child.glob("summary_*.csv")):
                self.available_subfolders.append(child)
                self.list_avail.insert(tk.END, child.name)

        # Preserve queue items that still exist; drop missing
        existing_names = {p.name for p in self.available_subfolders}
        kept_queue = [
            p
            for p in self.queue_subfolders
            if p.name in existing_names and (base / p.name).exists()
        ]
        self.queue_subfolders = [
            base / p.name for p in kept_queue
        ]  # normalize to new base if changed
        self._refresh_queue_listbox()

    # ---- Queue management + labels
    def _refresh_queue_listbox(self):
        self.list_queue.delete(0, tk.END)
        for p in self.queue_subfolders:
            lab = self.queue_labels.get(p.name, p.name)
            self.list_queue.insert(tk.END, f"{p.name}  \u2192  {lab}")  # folder → label

    def _on_queue_select(self, _evt=None):
        """Populate the entry with the CURRENT label (or folder name if none)."""
        sel = self.list_queue.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(self.queue_subfolders)):
            return
        p = self.queue_subfolders[idx]
        current_label = self.queue_labels.get(p.name, p.name)
        self.label_edit_var.set(current_label)

    def add_to_queue(self):
        """Add selected items from available list to the queue (skip duplicates)."""
        base = Path(self.main_folder.get())
        idxs = list(self.list_avail.curselection())
        if not idxs:
            return
        names_in_queue = {p.name for p in self.queue_subfolders}
        for i in idxs:
            p = self.available_subfolders[i]
            if p.name not in names_in_queue:
                self.queue_subfolders.append(base / p.name)
                # default label = folder name (if not set already)
                self.queue_labels.setdefault(p.name, p.name)
        self._refresh_queue_listbox()

    def remove_from_queue(self):
        """Remove selected items from the queue (labels kept in case re-added)."""
        idxs = sorted(self.list_queue.curselection(), reverse=True)
        if not idxs:
            return
        for i in idxs:
            del self.queue_subfolders[i]
        self._refresh_queue_listbox()

    def move_in_queue(self, delta: int):
        """Move selected queue items up or down by one position (preserve relative order)."""
        n = len(self.queue_subfolders)
        if n == 0:
            return
        sel = list(self.list_queue.curselection())
        if not sel:
            return

        new_order = self.queue_subfolders[:]
        indices = sel if delta < 0 else sel[::-1]
        new_selection = []
        for i in indices:
            j = i + delta
            if 0 <= j < n:
                new_order[i], new_order[j] = new_order[j], new_order[i]
                new_selection.append(j)
            else:
                new_selection.append(i)
        self.queue_subfolders = new_order
        self._refresh_queue_listbox()
        self.list_queue.selection_clear(0, tk.END)
        for j in new_selection:
            self.list_queue.selection_set(j)
        self._on_queue_select()

    def set_label_for_selected(self):
        """Apply the text in the input field to the first selected queue item."""
        sel = self.list_queue.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(self.queue_subfolders)):
            return
        p = self.queue_subfolders[idx]
        new_label = self.label_edit_var.get().strip()
        if new_label == "":
            new_label = p.name
        self.queue_labels[p.name] = new_label
        self._refresh_queue_listbox()
        # keep selection and reflect applied label in entry
        self.list_queue.selection_clear(0, tk.END)
        self.list_queue.selection_set(idx)
        self._on_queue_select()

    def fill_folder_name_into_entry(self):
        """
        Fill the input with the folder name of the first selected queue item.
        NOTE: This does NOT change the label until 'Set Label' is pressed.
        """
        sel = self.list_queue.curselection()
        if not sel:
            return
        idx = sel[0]
        if not (0 <= idx < len(self.queue_subfolders)):
            return
        p = self.queue_subfolders[idx]
        self.label_edit_var.set(p.name)

    # ---- Core analysis
    def run_analysis(self):
        if not self.queue_subfolders:
            messagebox.showinfo("Analyze", "Please add one or more subfolders to the queue.")
            return

        lin_ranges = _parse_ranges(self.linear_ranges_var.get())
        log_ranges = _parse_ranges(self.log_ranges_var.get())
        if not lin_ranges and not log_ranges:
            messagebox.showerror("Ranges", "Please enter at least one x-range.")
            return

        series_peak: Dict[str, pd.DataFrame] = {}
        series_int: Dict[str, pd.DataFrame] = {}

        for folder in self.queue_subfolders:
            df = _read_all_summaries(folder)
            if df is None:
                continue

            peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
            int_col = _find_matching_column(df, INT_COLS_CANDIDATES)

            # Use custom label (fallback to folder name)
            base_label = self.queue_labels.get(folder.name, folder.name)
            # Ensure uniqueness in each series dict
            if self.use_peak.get() and peak_col:
                label = _unique_label(series_peak, base_label)
                series_peak[label] = _aggregate(df, peak_col)
            if self.use_integral.get() and int_col:
                label = _unique_label(series_int, base_label)
                series_int[label] = _aggregate(df, int_col)

        if self.use_peak.get() and not series_peak:
            messagebox.showwarning("Data", "No usable peak data found in queued folders.")
        if self.use_integral.get() and not series_int:
            messagebox.showwarning("Data", "No usable integral data found in queued folders.")
        if (self.use_peak.get() and not series_peak) and (
            self.use_integral.get() and not series_int
        ):
            return

        out_name = self.output_name.get().strip() or DEFAULT_OUT_NAME
        base = Path(self.main_folder.get())
        out_dir = base / out_name
        out_dir.mkdir(parents=True, exist_ok=True)

        generated = 0

        # Peak figures
        if self.use_peak.get() and series_peak:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"peak_linear_{xmin}-{xmax}.png"
                _plot_linear(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax, out)
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"peak_log_{xmin}-{xmax}.png"
                _plot_log(series_peak, "Peak (normalized)", "Peak vs Power", xmin, xmax, out)
                generated += 1

        # Integral figures
        if self.use_integral.get() and series_int:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"integral_linear_{xmin}-{xmax}.png"
                _plot_linear(
                    series_int,
                    "Integrated charge (normalized)",
                    "Integrated Charge vs Power",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"integral_log_{xmin}-{xmax}.png"
                _plot_log(
                    series_int,
                    "Integrated charge (normalized)",
                    "Integrated Charge vs Power",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1

        messagebox.showinfo("Done", f"Generated {generated} figure(s) in:\n{out_dir}")

    def export_svg(self):
        """
        Export plot-only SVG + legend-only SVG into the same output folder.
        Plot-only SVG: frame + ticks + plotted content only; no text; transparent bg.
        Legend-only SVG: handles only (no text), transparent bg.
        """
        if not self.queue_subfolders:
            messagebox.showinfo("Export SVG", "Please add one or more subfolders to the queue.")
            return

        lin_ranges = _parse_ranges(self.linear_ranges_var.get())
        log_ranges = _parse_ranges(self.log_ranges_var.get())
        if not lin_ranges and not log_ranges:
            messagebox.showerror("Ranges", "Please enter at least one x-range.")
            return

        series_peak: Dict[str, pd.DataFrame] = {}
        series_int: Dict[str, pd.DataFrame] = {}

        for folder in self.queue_subfolders:
            df = _read_all_summaries(folder)
            if df is None:
                continue

            peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
            int_col = _find_matching_column(df, INT_COLS_CANDIDATES)

            base_label = self.queue_labels.get(folder.name, folder.name)
            if self.use_peak.get() and peak_col:
                label = _unique_label(series_peak, base_label)
                series_peak[label] = _aggregate(df, peak_col)
            if self.use_integral.get() and int_col:
                label = _unique_label(series_int, base_label)
                series_int[label] = _aggregate(df, int_col)

        if self.use_peak.get() and not series_peak:
            messagebox.showwarning("Data", "No usable peak data found in queued folders.")
        if self.use_integral.get() and not series_int:
            messagebox.showwarning("Data", "No usable integral data found in queued folders.")
        if (self.use_peak.get() and not series_peak) and (
            self.use_integral.get() and not series_int
        ):
            return

        out_name = self.output_name.get().strip() or DEFAULT_OUT_NAME
        base = Path(self.main_folder.get())
        out_dir = base / out_name
        out_dir.mkdir(parents=True, exist_ok=True)

        generated = 0

        # Legend-only SVGs (handles only, no text)
        if self.use_peak.get() and series_peak:
            _legend_svg_only_no_text(series_peak, out_dir / "peak_legend.svg")
            generated += 1
        if self.use_integral.get() and series_int:
            _legend_svg_only_no_text(series_int, out_dir / "integral_legend.svg")
            generated += 1

        # Peak plot-only SVGs
        if self.use_peak.get() and series_peak:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"peak_linear_{xmin}-{xmax}.svg"
                _plot_linear_svg_plotonly(series_peak, xmin, xmax, out)
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"peak_log_{xmin}-{xmax}.svg"
                _plot_log_svg_plotonly(series_peak, xmin, xmax, out)
                generated += 1

        # Integral plot-only SVGs
        if self.use_integral.get() and series_int:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"integral_linear_{xmin}-{xmax}.svg"
                _plot_linear_svg_plotonly(series_int, xmin, xmax, out)
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"integral_log_{xmin}-{xmax}.svg"
                _plot_log_svg_plotonly(series_int, xmin, xmax, out)
                generated += 1

        messagebox.showinfo("Export SVG", f"Generated {generated} SVG file(s) in:\n{out_dir}")

    def export_normalized(self):
        """
        Export normalized CSV and normalized plots.
        Normalization factor is the strongest raw value within each series (folder):
            - Peak: max(raw peak values) -> 1
            - Integral: max(raw integral values) -> 1
        Then aggregated mean/sem are divided by that factor.
        Output:
            - <MAIN>/<OUTPUT_NAME>/normalized_series.csv
            - <MAIN>/<OUTPUT_NAME>/norm_*.png (same range grid & style as normal outputs)
        """
        if not self.queue_subfolders:
            messagebox.showinfo("Normalize", "Please add one or more subfolders to the queue.")
            return

        lin_ranges = _parse_ranges(self.linear_ranges_var.get())
        log_ranges = _parse_ranges(self.log_ranges_var.get())
        if not lin_ranges and not log_ranges:
            messagebox.showerror("Ranges", "Please enter at least one x-range.")
            return

        if (not self.use_peak.get()) and (not self.use_integral.get()):
            messagebox.showinfo("Normalize", "Please select at least one metric (Peak / Integral).")
            return

        out_name = self.output_name.get().strip() or DEFAULT_OUT_NAME
        base = Path(self.main_folder.get())
        out_dir = base / out_name
        out_dir.mkdir(parents=True, exist_ok=True)

        series_peak_norm: Dict[str, pd.DataFrame] = {}
        series_int_norm: Dict[str, pd.DataFrame] = {}
        rows = []

        for folder in self.queue_subfolders:
            df = _read_all_summaries(folder)
            if df is None:
                continue

            peak_col = _find_matching_column(df, PEAK_COLS_CANDIDATES)
            int_col = _find_matching_column(df, INT_COLS_CANDIDATES)

            base_label = self.queue_labels.get(folder.name, folder.name)

            # Peak normalization: strongest raw peak -> 1
            if self.use_peak.get() and peak_col:
                g_raw = _aggregate(df, peak_col)
                if g_raw is not None and (not g_raw.empty):
                    nf = (
                        float(np.nanmax(np.abs(g_raw["mean"].values)))
                        if np.isfinite(np.nanmax(np.abs(g_raw["mean"].values)))
                        else None
                    )
                else:
                    nf = None

                if nf is not None and nf != 0:
                    g_norm = _scale_group_by_factor(g_raw, nf)
                    if g_norm is not None and (not g_norm.empty):
                        label = _unique_label(series_peak_norm, base_label)
                        series_peak_norm[label] = g_norm

                        tmp = g_norm.copy()
                        tmp["folder"] = folder.name
                        tmp["series_label"] = base_label
                        tmp["metric"] = "peak"
                        tmp["norm_factor"] = nf
                        tmp["mean_norm"] = tmp["mean"]
                        tmp["sem_norm"] = tmp["sem"] if "sem" in tmp.columns else np.nan
                        tmp["mean_raw"] = (
                            g_raw["mean"].values
                            if (g_raw is not None and len(g_raw) == len(tmp))
                            else np.nan
                        )
                        tmp["sem_raw"] = (
                            g_raw["sem"].values
                            if (
                                g_raw is not None
                                and "sem" in g_raw.columns
                                and len(g_raw) == len(tmp)
                            )
                            else np.nan
                        )
                        rows.append(
                            tmp[
                                [
                                    "folder",
                                    "series_label",
                                    "metric",
                                    "power_density",
                                    "mean_raw",
                                    "sem_raw",
                                    "mean_norm",
                                    "sem_norm",
                                    "norm_factor",
                                ]
                            ]
                        )

            # Integral normalization: strongest raw integral -> 1
            if self.use_integral.get() and int_col:
                nf = _raw_max_value(df, int_col)
                if nf is not None and nf != 0:
                    g_raw = _aggregate(df, int_col)
                    g_norm = _scale_group_by_factor(g_raw, nf)
                    if g_norm is not None and (not g_norm.empty):
                        label = _unique_label(series_int_norm, base_label)
                        series_int_norm[label] = g_norm

                        tmp = g_norm.copy()
                        tmp["folder"] = folder.name
                        tmp["series_label"] = base_label
                        tmp["metric"] = "integral"
                        tmp["norm_factor"] = nf
                        tmp["mean_norm"] = tmp["mean"]
                        tmp["sem_norm"] = tmp["sem"] if "sem" in tmp.columns else np.nan
                        tmp["mean_raw"] = (
                            g_raw["mean"].values
                            if (g_raw is not None and len(g_raw) == len(tmp))
                            else np.nan
                        )
                        tmp["sem_raw"] = (
                            g_raw["sem"].values
                            if (
                                g_raw is not None
                                and "sem" in g_raw.columns
                                and len(g_raw) == len(tmp)
                            )
                            else np.nan
                        )
                        rows.append(
                            tmp[
                                [
                                    "folder",
                                    "series_label",
                                    "metric",
                                    "power_density",
                                    "mean_raw",
                                    "sem_raw",
                                    "mean_norm",
                                    "sem_norm",
                                    "norm_factor",
                                ]
                            ]
                        )

        if (self.use_peak.get() and not series_peak_norm) and (
            self.use_integral.get() and not series_int_norm
        ):
            messagebox.showwarning("Normalize", "No usable data found to normalize/export.")
            return

        # CSV
        if rows:
            out_df = pd.concat(rows, axis=0, ignore_index=True)
            out_df["power_density"] = pd.to_numeric(out_df["power_density"], errors="coerce")
            out_df.sort_values(
                ["folder", "metric", "power_density"], kind="mergesort", inplace=True
            )
            out_path = out_dir / "normalized_series.csv"
            try:
                out_df.to_csv(out_path, index=False)
            except Exception as e:
                messagebox.showerror("Normalize", f"Failed to write CSV:\n{out_path}\n\n{e}")
                return

        generated = 0

        # Normalized Peak figures
        if self.use_peak.get() and series_peak_norm:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"norm_peak_linear_{xmin}-{xmax}.png"
                _plot_linear(
                    series_peak_norm,
                    "Peak (max raw peak = 1)",
                    "Peak vs Power (normalized)",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"norm_peak_log_{xmin}-{xmax}.png"
                _plot_log(
                    series_peak_norm,
                    "Peak (max raw peak = 1)",
                    "Peak vs Power (normalized)",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1

        # Normalized Integral figures
        if self.use_integral.get() and series_int_norm:
            for xmin, xmax in lin_ranges:
                out = out_dir / f"norm_integral_linear_{xmin}-{xmax}.png"
                _plot_linear(
                    series_int_norm,
                    "Integrated charge (max raw integral = 1)",
                    "Integrated Charge vs Power (normalized)",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1
            for xmin, xmax in log_ranges:
                out = out_dir / f"norm_integral_log_{xmin}-{xmax}.png"
                _plot_log(
                    series_int_norm,
                    "Integrated charge (max raw integral = 1)",
                    "Integrated Charge vs Power (normalized)",
                    xmin,
                    xmax,
                    out,
                )
                generated += 1

        messagebox.showinfo(
            "Normalize", f"Generated {generated} normalized figure(s) in:\n{out_dir}"
        )


# --------------------------- Main ---------------------------
def main() -> None:
    app = AbfPhotocurrentFigureApp()
    app.mainloop()


if __name__ == "__main__":
    main()
