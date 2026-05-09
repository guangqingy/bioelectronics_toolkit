# EMG_selection_GUI.py
# -*- coding: utf-8 -*-
"""
EMG Selection GUI (from CSV folders; separate load vs process; grouped-peak export)
- Left (fixed width): Main Folder -> Subfolders -> Channel (CSV in subfolder);
  Peak Properties table (Height, Duration, Group) with manual delete/restore & manual group edit.
- Right (top): Peak Recognition + Grouping controls (compact rows, above figure).
- Right (bottom): Preview plot (time_s vs value_uV), segment/axis controls.
- No PNG/SVG/general CSV exports.
- Grouping does NOT write files. Files are only written when clicking "Export grouped peaks".

Assumptions:
- Each subfolder contains the CSV(s) exported by your RHD viewer; columns: time_s, value_uV (case-insensitive tolerated).
- One recording may contain multiple channel CSVs, named like: <folder_name>_<channel>.csv

Dependencies: numpy, pandas, scipy, matplotlib, tkinter
    pip install numpy pandas scipy matplotlib
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from scipy.signal import find_peaks, peak_widths

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

# --------------------------- Defaults & layout ---------------------------
from config import DEFAULT_START_DIR  # noqa: E402  (load from config.py)
SIDEBAR_W = 340  # px
PLOT_INIT_H = 520  # px
LINE_COLOR = "tab:blue"
EXPORT_HALF_MS = 100.0  # fixed export window: ±100 ms around peak


# --------------------------- Utilities ---------------------------
def list_subfolders_with_csvs(main: Path) -> List[Path]:
    """Return subfolders that contain at least one *.csv (non-recursive)."""
    out = []
    if not main.is_dir():
        return out
    for p in sorted(main.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and any(
            child.is_file() and child.suffix.lower() == ".csv" for child in p.iterdir()
        ):
            out.append(p)
    return out


def list_channel_csvs(folder: Path) -> List[Path]:
    """
    List CSV files that look like channel files inside the subfolder (non-recursive).
    Heuristic: file name starts with "<folder.name>_" and ends with ".csv",
    and we exclude known non-channel files like "*_peaks_summary.csv".
    """
    if not folder.is_dir():
        return []
    prefix = folder.name + "_"
    out = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".csv":
            name = p.name
            if name.startswith(prefix) and not name.endswith("_peaks_summary.csv"):
                out.append(p)
    return sorted(out, key=lambda x: x.name.lower())


def find_time_value_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """Find (time_col, value_col) with tolerant case-insensitive matching."""
    lowers = {c.lower(): c for c in df.columns}
    t = lowers.get("time_s") or lowers.get("time") or None
    v = lowers.get("value_uv") or lowers.get("value") or lowers.get("amplitude_uv") or None
    # fallback: first two numeric-ish
    if t is None or v is None:
        numeric_like = []
        for c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce")
            if np.isfinite(s).sum() >= max(10, int(0.5 * len(s))):
                numeric_like.append(c)
        if t is None and numeric_like:
            t = numeric_like[0]
        if v is None and len(numeric_like) >= 2:
            v = numeric_like[1]
    return t, v


def ms_to_samples(ms: float, fs: float) -> int:
    """Convert milliseconds to integer samples at fs (Hz)."""
    return int(round(ms * 1e-3 * fs))


def robust_noise_std(x: np.ndarray) -> float:
    """Robust noise sigma via MAD."""
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return float(1.4826 * mad)


def sanitize_name(s: str) -> str:
    """Keep letters/digits/-/_ ; replace others by '_' for safe filenames."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)


# --------------------------- Peak detection & features ---------------------------
def build_kwargs(
    sig,
    fs,
    min_peak_distance_ms,
    min_width_ms,
    wlen_ms,
    min_prominence_uV,
    min_height_uV,
    use_adaptive_sigma,
    sigma_for_prom,
    sigma_for_height,
):
    """Assemble scipy.find_peaks kwargs including adaptive thresholds."""
    kwargs = {"distance": max(1, ms_to_samples(min_peak_distance_ms, fs))}
    if min_width_ms is not None:
        kwargs["width"] = ms_to_samples(min_width_ms, fs)
    if wlen_ms is not None:
        w = ms_to_samples(wlen_ms, fs)
        if 3 <= w < sig.size:
            kwargs["wlen"] = w
    prom_thr = (
        None if (min_prominence_uV is None or min_prominence_uV <= 0) else float(min_prominence_uV)
    )
    height_thr = None if (min_height_uV is None) else float(min_height_uV)
    if use_adaptive_sigma:
        sigma = robust_noise_std(sig)
        med = float(np.median(sig))
        prom_adapt = (
            (sigma_for_prom or 0) * sigma if (sigma_for_prom and sigma_for_prom > 0) else None
        )
        height_adapt = (
            med + (sigma_for_height or 0) * sigma
            if (sigma_for_height and sigma_for_height > 0)
            else None
        )
        if prom_adapt is not None:
            prom_thr = prom_adapt if prom_thr is None else max(prom_thr, prom_adapt)
        if height_adapt is not None:
            height_thr = height_adapt if height_thr is None else max(height_thr, height_adapt)
    if prom_thr is not None:
        kwargs["prominence"] = prom_thr
    if height_thr is not None:
        kwargs["height"] = height_thr
    return kwargs


def detect_with_polarity(sig, fs, params, polarity: str):
    """Detect peaks per polarity; if 'both', merge with min distance keeping larger |amp|."""
    pos_idx = np.array([], dtype=int)
    pos_w_ms = np.array([], dtype=float)
    neg_idx = np.array([], dtype=int)
    neg_w_ms = np.array([], dtype=float)

    if polarity in ("positive", "both"):
        kw_pos = build_kwargs(sig, fs, **params)
        pos_idx, _ = find_peaks(sig, **kw_pos)
        if pos_idx.size:
            w_s, _, _, _ = peak_widths(sig, pos_idx, rel_height=0.5)
            pos_w_ms = (w_s / fs) * 1e3

    if polarity in ("negative", "both"):
        inv = -sig
        kw_neg = build_kwargs(inv, fs, **params)
        neg_idx, _ = find_peaks(inv, **kw_neg)
        if neg_idx.size:
            w_s, _, _, _ = peak_widths(inv, neg_idx, rel_height=0.5)
            neg_w_ms = (w_s / fs) * 1e3

    if polarity != "both":
        idx = pos_idx if polarity == "positive" else neg_idx
        w_ms = pos_w_ms if polarity == "positive" else neg_w_ms
        sgn = (
            np.ones_like(idx, dtype=int)
            if polarity == "positive"
            else -np.ones_like(idx, dtype=int)
        )
        return idx, w_ms, sgn

    # Merge
    all_idx = np.concatenate([pos_idx, neg_idx])
    all_sgn = np.concatenate([np.ones_like(pos_idx, dtype=int), -np.ones_like(neg_idx, dtype=int)])
    all_w = np.concatenate([pos_w_ms, neg_w_ms])

    if all_idx.size == 0:
        return all_idx, all_w, all_sgn

    order = np.argsort(all_idx)
    all_idx = all_idx[order]
    all_sgn = all_sgn[order]
    all_w = all_w[order]
    keep = np.ones(all_idx.size, dtype=bool)
    min_dist = ms_to_samples(params["min_peak_distance_ms"], fs)
    for i in range(all_idx.size):
        if not keep[i]:
            continue
        j = i + 1
        while j < all_idx.size and (all_idx[j] - all_idx[i]) < min_dist:
            ai = abs(sig[all_idx[i]])
            aj = abs(sig[all_idx[j]])
            if aj > ai:
                keep[i] = False
                break
            else:
                keep[j] = False
            j += 1
    return all_idx[keep], all_w[keep], all_sgn[keep]


def compute_features(sig, t, fs, idx, pre_ms, post_ms, noise_win_start_ms, noise_win_end_ms):
    """Compute per-peak features: height (amplitude), FWHM, etc."""
    # Baseline & noise from a pre-peak window when available
    ns = max(0, idx - ms_to_samples(noise_win_start_ms, fs))
    ne = max(0, idx - ms_to_samples(noise_win_end_ms, fs))
    if ne > ns and (ne - ns) >= ms_to_samples(5, fs):
        baseline = float(np.median(sig[ns:ne]))
        noise_sd = float(np.std(sig[ns:ne], ddof=1))
    else:
        L = ms_to_samples(500, fs)
        win = sig[max(0, idx - L) : idx] if idx > 10 else sig[: min(L, sig.size)]
        baseline = float(np.median(win)) if win.size else 0.0
        noise_sd = float(np.std(win, ddof=1)) if win.size else np.nan

    pk_value = float(sig[idx])
    amp = pk_value - baseline
    try:
        w_s, _, _, _ = peak_widths(sig if amp >= 0 else -sig, np.array([idx]), rel_height=0.5)
        fwhm_ms = float((w_s[0] / fs) * 1e3)
    except Exception:
        fwhm_ms = np.nan

    # Segment used only to compute area (features); export uses fixed ±100 ms elsewhere
    pre = ms_to_samples(pre_ms, fs)
    post = ms_to_samples(post_ms, fs)
    start = max(0, idx - pre)
    end = min(sig.size, idx + post)
    seg = sig[start:end]
    area_uVms = float(np.trapz(np.abs(seg), dx=1 / fs) * 1e3)

    return {
        "peak_idx": int(idx),
        "peak_time_s": float(t[idx]),
        "height_uV": float(amp),
        "fwhm_ms": float(fwhm_ms),
        "baseline_uV": float(baseline),
        "noise_std_uV": float(noise_sd),
        "area_uVms": float(area_uVms),
    }


# --------------------------- GUI ---------------------------
class EmgPeakSelectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EMG — Peak Selector")
        self.geometry("1280x820")
        self.minsize(980, 640)

        # State
        self.main_folder = tk.StringVar(value=DEFAULT_START_DIR)
        self.subfolders: List[Path] = []
        self.cur_subfolder: Optional[Path] = None

        # Channel selection state
        self.channel_csvs: List[Path] = []
        self.cur_csv: Optional[Path] = None
        self.var_channel = tk.StringVar(value="")

        # Data
        self.time_s: Optional[np.ndarray] = None
        self.value_uV: Optional[np.ndarray] = None
        self.fs: Optional[float] = None  # inferred sampling rate (Hz)

        # Peaks
        self.df_peaks = pd.DataFrame(
            columns=[
                "peak_idx",
                "peak_time_s",
                "height_uV",
                "fwhm_ms",
                "baseline_uV",
                "noise_std_uV",
                "area_uVms",
            ]
        )
        # track manual deletions by peak_idx
        self.removed_peaks: set[int] = set()

        # Detection controls
        self.var_polarity = tk.StringVar(value="both")
        self.var_dist = tk.DoubleVar(value=400.0)  # ms
        self.var_prom = tk.DoubleVar(value=100.0)  # uV
        self.var_height = tk.DoubleVar(value=200.0)  # uV
        self.var_minw = tk.DoubleVar(value=0.1)  # ms
        self.var_wlen = tk.StringVar(value="")  # ms or blank
        self.var_adapt = tk.BooleanVar(value=True)
        self.var_kprom = tk.DoubleVar(value=1.0)
        self.var_kheight = tk.DoubleVar(value=1.0)
        # feature half-window (for metrics only; export uses fixed ±100 ms)
        self.feature_half_ms = tk.DoubleVar(value=300.0)

        # Grouping controls
        self.var_period = tk.DoubleVar(value=2.0)  # Hz
        self.var_gapfac = tk.DoubleVar(value=1.5)
        # Repurpose "Peaks/group" field as START group ID (offset)
        self.var_gstart = tk.IntVar(value=0)

        # Manual group edit (under Peak Properties)
        self.var_group_set = tk.IntVar(value=1)

        # Segment & axis controls (for viewing)
        self.seg_start_var = tk.StringVar(value="")
        self.seg_end_var = tk.StringVar(value="")
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")

        # Build UI
        self._build_layout()
        self._build_left()
        self._build_top_controls()
        self._build_plot()

        # Initial scan
        self.scan_main()
        if self.subfolders:
            self.list_subfolders.selection_set(0)
            self.on_pick_subfolder(None)

    # ---------- Layout ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, minsize=PLOT_INIT_H, weight=1)

        # Left column (fixed initial width)
        self.left = ttk.Frame(self, padding=(8, 8, 8, 8))
        self.left.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.left.update_idletasks()
        self.left.grid_propagate(False)  # keep width stable

        # Right: top controls and plot
        self.top_controls = ttk.Frame(self, padding=(8, 8, 8, 0))
        self.top_controls.grid(row=0, column=1, sticky="ew")
        self.top_controls.grid_columnconfigure(0, weight=1)

        self.plot_area = ttk.Frame(self, padding=(8, 6, 8, 8))
        self.plot_area.grid(row=1, column=1, sticky="nsew")
        self.plot_area.grid_columnconfigure(0, weight=1)
        self.plot_area.grid_rowconfigure(1, weight=1)

    def _build_left(self):
        # Main Folder
        box = ttk.LabelFrame(self.left, text="Main Folder")
        box.pack(fill="x", side="top")
        r1 = ttk.Frame(box)
        r1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(r1, text="Path:").pack(side="left")
        ttk.Entry(r1, textvariable=self.main_folder, width=28).pack(
            side="left", padx=6, fill="x", expand=True
        )
        r2 = ttk.Frame(box)
        r2.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(r2, text="Browse…", command=self.choose_main).pack(side="left")
        ttk.Button(r2, text="Refresh", command=self.scan_main).pack(side="left", padx=6)

        # Subfolders (upper list)
        sub_box = ttk.LabelFrame(self.left, text="Subfolders (per recording)")
        sub_box.pack(fill="both", expand=True, pady=(6, 4))
        self.list_subfolders = tk.Listbox(sub_box, height=10, exportselection=False)
        self.list_subfolders.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.list_subfolders.bind("<<ListboxSelect>>", self.on_pick_subfolder)
        sb1 = ttk.Scrollbar(sub_box, orient="vertical", command=self.list_subfolders.yview)
        sb1.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.list_subfolders.config(yscrollcommand=sb1.set)

        # Channel selector directly under subfolders
        chan_box = ttk.LabelFrame(self.left, text="Channel (CSV in subfolder)")
        chan_box.pack(fill="x", expand=False, pady=(4, 4))

        # row 1: label + combobox
        row1 = ttk.Frame(chan_box)
        row1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(row1, text="Channel:").pack(side="left")
        self.combo_channels = ttk.Combobox(
            row1, state="readonly", textvariable=self.var_channel, width=32
        )
        self.combo_channels.pack(side="left", padx=6, fill="x", expand=True)
        self.combo_channels.bind("<<ComboboxSelected>>", self.on_pick_channel)

        # row 2: reload (moved to next line to save horizontal space)
        row2 = ttk.Frame(chan_box)
        row2.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Button(row2, text="Reload", command=self._reload_selected_channel).pack(side="left")

        # Peak Properties table (lower, fixed width via column widths)
        prop_box = ttk.LabelFrame(self.left, text="Peak Properties (Height, Duration, Group)")
        prop_box.pack(fill="both", expand=True, pady=(4, 0))
        cols = ("#", "time_s", "height_uV", "fwhm_ms", "group_id")
        self.tree = ttk.Treeview(
            prop_box, columns=cols, show="headings", selectmode="extended", height=12
        )
        # keep # column for simple ordinal display
        self.tree.heading("#", text="#")
        self.tree.column("#", width=40, anchor=tk.CENTER, stretch=False)
        self.tree.heading("time_s", text="time_s")
        self.tree.column("time_s", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.heading("height_uV", text="height (µV)")
        self.tree.column("height_uV", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.heading("fwhm_ms", text="duration (ms)")
        self.tree.column("fwhm_ms", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.heading("group_id", text="group")
        self.tree.column("group_id", width=70, anchor=tk.CENTER, stretch=False)
        prop_box.grid_rowconfigure(0, weight=1)
        prop_box.grid_columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(prop_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb2.set)
        sb2.grid(row=0, column=1, sticky="ns")

        # Tag style for removed peaks
        self.tree.tag_configure("removed", background="#ffe4cc")

        # Row of delete/restore
        ctr = ttk.Frame(prop_box)
        ctr.grid(row=1, column=0, sticky="ew", padx=0, pady=(4, 2))
        ttk.Button(ctr, text="Delete Selected", command=self._delete_selected_from_list).pack(
            side="left"
        )
        ttk.Button(ctr, text="Restore Selected", command=self._restore_selected_in_list).pack(
            side="left", padx=6
        )
        ttk.Button(ctr, text="Reset Removals", command=self._reset_all_removals).pack(
            side="left", padx=6
        )

        # Manual group edit row
        gctr = ttk.Frame(prop_box)
        gctr.grid(row=2, column=0, sticky="ew", padx=0, pady=(2, 6))
        ttk.Label(gctr, text="Group ID").pack(side="left")
        tk.Spinbox(
            gctr, from_=1, to=9999, increment=1, width=6, textvariable=self.var_group_set
        ).pack(side="left", padx=(6, 8))
        ttk.Button(gctr, text="Set for Selected", command=self._set_group_for_selected).pack(
            side="left"
        )
        ttk.Button(gctr, text="Clear group", command=self._clear_group_for_selected).pack(
            side="left", padx=6
        )

        # Double-click toggle delete/restore
        self.tree.bind("<Double-1>", self._on_tree_double_click)

    # ---------- Top controls (Recognition + Grouping; compact rows) ----------
    def _build_top_controls(self):
        # Row A: Recognition (line 1)
        A = ttk.LabelFrame(self.top_controls, text="Peak Recognition (current view)")
        A.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for c in range(8):
            A.grid_columnconfigure(c, weight=0)
        ttk.Label(A, text="Polarity").grid(row=0, column=0, sticky="w", padx=4)
        ttk.Combobox(
            A, values=["positive", "negative", "both"], textvariable=self.var_polarity, width=10
        ).grid(row=0, column=1, padx=4)
        ttk.Label(A, text="Min dist (ms)").grid(row=0, column=2, sticky="w")
        ttk.Entry(A, textvariable=self.var_dist, width=8).grid(row=0, column=3, padx=4)
        ttk.Label(A, text="Prom (µV)").grid(row=0, column=4, sticky="w")
        ttk.Entry(A, textvariable=self.var_prom, width=8).grid(row=0, column=5, padx=4)
        ttk.Label(A, text="Height (µV)").grid(row=0, column=6, sticky="w")
        ttk.Entry(A, textvariable=self.var_height, width=8).grid(row=0, column=7, padx=4)

        # Row A2: Recognition (line 2)
        A2 = ttk.Frame(self.top_controls)
        A2.grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(A2, text="Min width (ms)").pack(side="left")
        ttk.Entry(A2, textvariable=self.var_minw, width=8).pack(side="left", padx=4)
        ttk.Label(A2, text="wlen (ms, blank=auto)").pack(side="left", padx=(12, 0))
        ttk.Entry(A2, textvariable=self.var_wlen, width=10).pack(side="left", padx=4)
        ttk.Checkbutton(A2, text="Adaptive σ", variable=self.var_adapt).pack(
            side="left", padx=(12, 6)
        )
        ttk.Label(A2, text="k_prom").pack(side="left")
        ttk.Entry(A2, textvariable=self.var_kprom, width=6).pack(side="left", padx=4)
        ttk.Label(A2, text="k_height").pack(side="left")
        ttk.Entry(A2, textvariable=self.var_kheight, width=6).pack(side="left", padx=4)

        # Row A3: feature half-window (metrics only)
        A3 = ttk.Frame(self.top_controls)
        A3.grid(row=2, column=0, sticky="w", pady=(0, 6))
        ttk.Label(A3, text="Segment ± (ms) for feature calc").pack(side="left")
        tk.Spinbox(
            A3, from_=10, to=2000, increment=10, width=6, textvariable=self.feature_half_ms
        ).pack(side="left", padx=6)
        ttk.Button(A3, text="Detect (append)", command=lambda: self.detect_peaks(False)).pack(
            side="left", padx=(12, 4)
        )
        ttk.Button(A3, text="Detect (replace)", command=lambda: self.detect_peaks(True)).pack(
            side="left", padx=4
        )

        # Row B: Grouping
        B = ttk.LabelFrame(self.top_controls, text="Grouping")
        B.grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Label(B, text="Period (Hz)").pack(side="left", padx=(4, 2))
        ttk.Entry(B, textvariable=self.var_period, width=8).pack(side="left")
        ttk.Label(B, text="Gap × Period").pack(side="left", padx=(12, 2))
        ttk.Entry(B, textvariable=self.var_gapfac, width=8).pack(side="left")

        # IMPORTANT: keep original name but repurpose meaning → start group ID
        ttk.Label(B, text="Group Start").pack(side="left", padx=(12, 2))
        tk.Spinbox(
            B, from_=-9999, to=9999, increment=1, width=8, textvariable=self.var_gstart
        ).pack(side="left")

        ttk.Button(B, text="Group now", command=self.group_by_time).pack(side="left", padx=(12, 4))
        ttk.Button(B, text="Export grouped peaks", command=self.export_grouped_peak_segments).pack(
            side="left", padx=6
        )

    # ---------- Plot area ----------
    def _build_plot(self):
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.plot_area, textvariable=self.status_var, foreground="#444").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.fig, self.ax = plt.subplots(figsize=(9, 4.8))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_area)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.plot_area, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        # Segment row
        D = ttk.Frame(self.top_controls)
        D.grid(row=4, column=0, sticky="w", pady=(2, 2))
        ttk.Label(D, text="Segment Start (s)").pack(side="left")
        ttk.Entry(D, width=12, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
        ttk.Label(D, text="End (s)").pack(side="left")
        ttk.Entry(D, width=12, textvariable=self.seg_end_var).pack(side="left", padx=(4, 8))
        ttk.Button(D, text="Apply", command=self.apply_segment_window).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(D, text="Full View", command=self.reset_axes).pack(side="left")
        # Axis rows
        E1 = ttk.Frame(self.top_controls)
        E1.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ttk.Label(E1, text="X min (s)").pack(side="left")
        ttk.Entry(E1, width=12, textvariable=self.xmin_var).pack(side="left", padx=(4, 8))
        ttk.Label(E1, text="X max (s)").pack(side="left")
        ttk.Entry(E1, width=12, textvariable=self.xmax_var).pack(side="left", padx=(4, 12))
        E2 = ttk.Frame(self.top_controls)
        E2.grid(row=6, column=0, sticky="w", pady=(2, 0))
        ttk.Label(E2, text="Y min (µV)").pack(side="left")
        ttk.Entry(E2, width=12, textvariable=self.ymin_var).pack(side="left", padx=(4, 8))
        ttk.Label(E2, text="Y max (µV)").pack(side="left")
        ttk.Entry(E2, width=12, textvariable=self.ymax_var).pack(side="left", padx=(4, 12))
        ttk.Button(E2, text="Apply", command=self.apply_axes_from_inputs).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(E2, text="Grab", command=self.update_axes_inputs_from_view).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(E2, text="Reset", command=self.reset_axes).pack(side="left")

    # ---------- Left ops ----------
    def choose_main(self):
        try:
            d = filedialog.askdirectory(initialdir=self.main_folder.get() or DEFAULT_START_DIR)
        except Exception as e:
            messagebox.showerror("Folder", f"Dialog failed: {e}")
            return
        if d:
            self.main_folder.set(d)
            self.scan_main()

    def scan_main(self):
        base = Path(self.main_folder.get())
        self.subfolders = list_subfolders_with_csvs(base)
        self.list_subfolders.delete(0, tk.END)
        for p in self.subfolders:
            self.list_subfolders.insert(tk.END, p.name)
        # clear state
        self.cur_subfolder = None
        self.channel_csvs = []
        self.cur_csv = None
        self.var_channel.set("")
        self.time_s = None
        self.value_uV = None
        self.fs = None
        self.df_peaks = self.df_peaks.iloc[0:0]
        self.removed_peaks.clear()
        self._refresh_peak_table()
        self._clear_plot()
        self.status_var.set(f"Scanned: {base}")

    def on_pick_subfolder(self, _evt):
        sel = self.list_subfolders.curselection()
        if not sel:
            return
        self.cur_subfolder = self.subfolders[int(sel[0])]
        # Populate channels for this subfolder
        self._populate_channel_combo()
        if self.channel_csvs:
            # Auto-load the first channel
            self.cur_csv = self.channel_csvs[0]
            self.var_channel.set(self._channel_display_name(self.channel_csvs[0]))
            self.load_channel_csv(self.cur_csv)
            self.removed_peaks.clear()
            extra = (
                f" (multiple CSVs found; loaded first: {self.cur_csv.name})"
                if len(self.channel_csvs) > 1
                else ""
            )
            self.status_var.set(f"Loaded: {self.cur_subfolder.name}/{self.cur_csv.name}{extra}")
        else:
            self.status_var.set(f"Subfolder: {self.cur_subfolder.name} (no channel CSV found)")
            self.time_s = None
            self.value_uV = None
            self.fs = None
            self._clear_plot()

    def _populate_channel_combo(self):
        """Fill channel combobox with CSVs in current subfolder."""
        self.channel_csvs = list_channel_csvs(self.cur_subfolder) if self.cur_subfolder else []
        names = [self._channel_display_name(p) for p in self.channel_csvs]
        self.combo_channels["values"] = names
        if names:
            self.combo_channels.current(0)
        else:
            self.var_channel.set("")
            self.combo_channels.set("")

    def _channel_display_name(self, p: Path) -> str:
        """Return just the channel suffix after '<folder>_' (fallback to stem)."""
        if (
            self.cur_subfolder
            and p.name.startswith(self.cur_subfolder.name + "_")
            and p.suffix.lower() == ".csv"
        ):
            return p.name[len(self.cur_subfolder.name) + 1 : -4]
        return p.stem

    def _current_channel_label(self) -> str:
        """Current channel label used in filenames."""
        if self.cur_csv is None:
            return ""
        # Use display name to match UI; sanitize for filesystem
        return sanitize_name(self._channel_display_name(self.cur_csv))

    def on_pick_channel(self, _evt):
        """Load the channel picked from combobox."""
        name = self.var_channel.get().strip()
        if not name or not self.channel_csvs:
            return
        # match by display name
        for p in self.channel_csvs:
            if self._channel_display_name(p) == name:
                self.cur_csv = p
                self.load_channel_csv(self.cur_csv)
                self.removed_peaks.clear()
                self.status_var.set(f"Loaded: {self.cur_subfolder.name}/{self.cur_csv.name}")
                return

    def _reload_selected_channel(self):
        """Reload currently selected channel CSV (button under combobox)."""
        if self.cur_csv is None:
            return
        self.load_channel_csv(self.cur_csv)
        self.removed_peaks.clear()
        self.status_var.set(f"Reloaded: {self.cur_subfolder.name}/{self.cur_csv.name}")

    # ---------- Data load & plot ----------
    def load_channel_csv(self, path: Path):
        try:
            df = pd.read_csv(path)
        except Exception as e:
            messagebox.showerror("Load CSV", f"{path.name}\n{e}")
            return
        t_col, v_col = find_time_value_columns(df)
        if t_col is None or v_col is None:
            messagebox.showerror("Columns", f"Cannot find time/value columns in {path.name}")
            return
        t = pd.to_numeric(df[t_col], errors="coerce").to_numpy()
        y = pd.to_numeric(df[v_col], errors="coerce").to_numpy()
        m = np.isfinite(t) & np.isfinite(y)
        if m.sum() < 3:
            messagebox.showerror("Data", f"Not enough numeric rows in {path.name}")
            return
        self.time_s = t[m]
        self.value_uV = y[m]
        dt = np.diff(self.time_s)
        dt = dt[np.isfinite(dt) & (dt > 0)]
        self.fs = float(1.0 / np.median(dt)) if dt.size else np.nan
        # reset peaks for new channel
        self.df_peaks = self.df_peaks.iloc[0:0]
        self.removed_peaks.clear()
        self._replot_full()

    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _replot_full(self):
        if self.time_s is None or self.value_uV is None:
            self._clear_plot()
            return
        self.ax.clear()
        self.ax.plot(self.time_s, self.value_uV, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Amplitude (µV)")
        self.ax.relim()
        self.ax.autoscale()
        self.canvas.draw_idle()
        for v in (
            self.xmin_var,
            self.xmax_var,
            self.ymin_var,
            self.ymax_var,
            self.seg_start_var,
            self.seg_end_var,
        ):
            v.set("")
        self._draw_peak_marks()

    def _draw_peak_marks(self):
        if self.time_s is None or self.df_peaks.empty:
            return
        x0, x1 = self.ax.get_xlim()
        view = (self.df_peaks["peak_time_s"] >= x0) & (self.df_peaks["peak_time_s"] <= x1)
        # skip removed peaks
        for _, r in self.df_peaks.loc[view].iterrows():
            if int(r["peak_idx"]) in self.removed_peaks:
                continue
            self.ax.plot(
                [r["peak_time_s"]],
                [r["baseline_uV"] + r["height_uV"]],
                marker="x",
                color="gray",
                ms=6,
            )
        self.canvas.draw_idle()

    # ---------- Segment & axes (view only) ----------
    def apply_segment_window(self):
        if self.time_s is None:
            return
        try:
            s0 = float(self.seg_start_var.get().strip())
            s1 = float(self.seg_end_var.get().strip())
        except Exception:
            messagebox.showerror("Segment", "Start/End must be numeric (seconds).")
            return
        if s0 == s1:
            messagebox.showerror("Segment", "Start and End must differ.")
            return
        if s0 > s1:
            s0, s1 = s1, s0
        s0 = max(self.time_s[0], min(s0, self.time_s[-1]))
        s1 = max(self.time_s[0], min(s1, self.time_s[-1]))
        if s1 - s0 <= 0:
            messagebox.showerror("Segment", "Window must be positive within data range.")
            return
        self.ax.set_xlim(s0, s1)
        self.canvas.draw_idle()
        self._draw_peak_marks()

    def apply_axes_from_inputs(self):
        if self.time_s is None:
            return
        cur_xlim = list(self.ax.get_xlim())
        cur_ylim = list(self.ax.get_ylim())

        def _get(var, cur):
            txt = var.get().strip()
            return float(txt) if txt != "" else cur

        try:
            xmin = _get(self.xmin_var, cur_xlim[0])
            xmax = _get(self.xmax_var, cur_xlim[1])
            ymin = _get(self.ymin_var, cur_ylim[0])
            ymax = _get(self.ymax_var, cur_ylim[1])
        except Exception:
            messagebox.showerror("Axis", "Axis limits must be numeric.")
            return
        if xmin == xmax or ymin == ymax:
            messagebox.showerror("Axis", "Min and Max must differ.")
            return
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        self.ax.set_xlim(xmin, xmax)
        self.ax.set_ylim(ymin, ymax)
        self.canvas.draw_idle()
        self._draw_peak_marks()

    def update_axes_inputs_from_view(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.xmin_var.set(f"{x0:.6g}")
        self.xmax_var.set(f"{x1:.6g}")
        self.ymin_var.set(f"{y0:.6g}")
        self.ymax_var.set(f"{y1:.6g}")

    def update_axis_inputs_from_view(self):
        # alias to keep older button wiring working
        return self.update_axes_inputs_from_view()

    def reset_axes(self):
        self._replot_full()

    # ---------- Detection & Grouping ----------
    def detect_peaks(self, replace: bool):
        if self.time_s is None or self.value_uV is None or not np.isfinite(self.fs):
            messagebox.showinfo("Detect", "Load a recording first.")
            return

        # Use current view as detection window
        x0, x1 = self.ax.get_xlim()
        mwin = (self.time_s >= x0) & (self.time_s <= x1)
        if mwin.sum() < 3:
            messagebox.showinfo("Detect", "Current window too small.")
            return
        xs = self.value_uV[mwin]
        ts = self.time_s[mwin]
        base_idx = np.searchsorted(self.time_s, ts[0])

        params = dict(
            min_peak_distance_ms=float(self.var_dist.get()),
            min_width_ms=float(self.var_minw.get()),
            wlen_ms=None if self.var_wlen.get().strip() == "" else float(self.var_wlen.get()),
            min_prominence_uV=float(self.var_prom.get()),
            min_height_uV=float(self.var_height.get()),
            use_adaptive_sigma=bool(self.var_adapt.get()),
            sigma_for_prom=float(self.var_kprom.get()),
            sigma_for_height=float(self.var_kheight.get()),
        )
        idxs, _, _ = detect_with_polarity(xs, float(self.fs), params, self.var_polarity.get())
        if replace:
            keep = ~((self.df_peaks["peak_time_s"] >= x0) & (self.df_peaks["peak_time_s"] <= x1))
            self.df_peaks = self.df_peaks.loc[keep].reset_index(drop=True)
            # also clear removed flags for peaks that got dropped
            self.removed_peaks.intersection_update(
                set(self.df_peaks["peak_idx"].astype(int).tolist())
            )

        # Build features
        half_for_features = float(self.feature_half_ms.get())
        new_rows = []
        for i in idxs:
            gidx = base_idx + int(i)
            feat = compute_features(
                self.value_uV,
                self.time_s,
                float(self.fs),
                gidx,
                pre_ms=half_for_features,
                post_ms=half_for_features,
                noise_win_start_ms=40.0,
                noise_win_end_ms=10.0,
            )
            # replace duplicate index
            if gidx in self.df_peaks["peak_idx"].values:
                self.df_peaks = self.df_peaks.drop(
                    self.df_peaks.index[self.df_peaks["peak_idx"] == gidx]
                )
                if gidx in self.removed_peaks:
                    self.removed_peaks.discard(gidx)
            new_rows.append(feat)

        if new_rows:
            self.df_peaks = pd.concat(
                [self.df_peaks, pd.DataFrame(new_rows)], axis=0, ignore_index=True
            )
            self.df_peaks.sort_values(by="peak_time_s", inplace=True, kind="mergesort")
            self.df_peaks.reset_index(drop=True, inplace=True)

        self._replot_full()
        self._refresh_peak_table()
        messagebox.showinfo("Detect", f"Detected {len(idxs)} peak(s).")

    def _ensure_group_column(self):
        if "group_id" not in self.df_peaks.columns:
            self.df_peaks["group_id"] = np.nan

    def group_by_time(self):
        """Assign group_id to detected (non-removed) peaks based on time gaps.
        Note: This DOES NOT export files; use 'Export grouped peaks' for writing CSVs.
        """
        if self.df_peaks.empty:
            messagebox.showinfo("Group", "No peaks to group.")
            return
        # Work on filtered (not-removed) peaks
        df_work = self.df_peaks[~self.df_peaks["peak_idx"].isin(self.removed_peaks)].copy()
        if df_work.empty:
            messagebox.showwarning("Group", "All peaks are removed.")
            return

        per_hz = float(self.var_period.get())
        gap_fac = float(self.var_gapfac.get())
        start_id = int(self.var_gstart.get())

        # sort by time for consistent grouping
        df = df_work.sort_values("peak_time_s").reset_index(drop=True)
        times = df["peak_time_s"].to_numpy()

        if per_hz > 0:
            period_s = 1.0 / per_hz
            thr = gap_fac * period_s
            gid = start_id
            G = np.empty(len(df), dtype=int)
            G[0] = gid
            for i in range(1, len(df)):
                # start a new group if the inter-peak interval exceeds threshold
                if (times[i] - times[i - 1]) > thr:
                    gid += 1
                G[i] = gid
        else:
            # single group if invalid period; assign start_id to all
            G = np.full(len(df), start_id, dtype=int)

        df["group_id"] = G

        # Merge back into self.df_peaks without losing peaks outside df_work
        self._ensure_group_column()
        # clear group_id for removed peaks to keep UI consistent
        self.df_peaks.loc[self.df_peaks["peak_idx"].isin(self.removed_peaks), "group_id"] = np.nan
        # update/assign for kept peaks (by peak_idx)
        for pk, gid in zip(
            df["peak_idx"].astype(int).tolist(), df["group_id"].astype(int).tolist()
        ):
            self.df_peaks.loc[self.df_peaks["peak_idx"] == pk, "group_id"] = gid

        self._replot_full()
        self._refresh_peak_table()

        n_groups = int(df["group_id"].nunique())
        messagebox.showinfo(
            "Group",
            f"Created {n_groups} group(s). No files exported.\n"
            f"Use 'Export grouped peaks' to write CSVs.",
        )

    # ---------- Peak Properties table ----------
    def _refresh_peak_table(self):
        # clear current rows
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._ensure_group_column()

        # rebuild table; iid = peak_idx to allow stable selection and toggling
        for i, r in self.df_peaks.iterrows():
            pk = int(r["peak_idx"])
            tags = ("removed",) if pk in self.removed_peaks else ()
            gid = (
                "" if (("group_id" not in r or pd.isna(r["group_id"]))) else f"{int(r['group_id'])}"
            )
            self.tree.insert(
                "",
                "end",
                iid=str(pk),
                values=(
                    i + 1,
                    f"{r['peak_time_s']:.3f}",
                    f"{r['height_uV']:.1f}",
                    ("" if np.isnan(r["fwhm_ms"]) else f"{r['fwhm_ms']:.2f}"),
                    gid,
                ),
                tags=tags,
            )

    # --- manual delete/restore from left list ---
    def _on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        pk = int(item)
        if pk in self.removed_peaks:
            self.removed_peaks.remove(pk)
        else:
            self.removed_peaks.add(pk)
        self._refresh_peak_table()
        self._replot_full()

    def _delete_selected_from_list(self):
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            pk = int(item)
            self.removed_peaks.add(pk)
        self._refresh_peak_table()
        self._replot_full()

    def _restore_selected_in_list(self):
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            pk = int(item)
            if pk in self.removed_peaks:
                self.removed_peaks.remove(pk)
        self._refresh_peak_table()
        self._replot_full()

    def _reset_all_removals(self):
        if not self.removed_peaks:
            return
        self.removed_peaks.clear()
        self._refresh_peak_table()
        self._replot_full()

    # --- manual group edit handlers ---
    def _set_group_for_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Group", "Select peak(s) in the table first.")
            return
        gid = int(self.var_group_set.get())
        self._ensure_group_column()
        for item in sel:
            pk = int(item)
            self.df_peaks.loc[self.df_peaks["peak_idx"] == pk, "group_id"] = gid
        self._refresh_peak_table()

    def _clear_group_for_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._ensure_group_column()
        for item in sel:
            pk = int(item)
            self.df_peaks.loc[self.df_peaks["peak_idx"] == pk, "group_id"] = np.nan
        self._refresh_peak_table()

    # ---------- Export grouped peak segments (fixed ±100 ms) ----------
    def export_grouped_peak_segments(self):
        """
        After grouping (or manual group edits), write per-peak CSVs under folders named by "<group_number>_<channel>"
        in the same directory as the channel CSV:
            <subfolder>/<group_number>_<channel>/peak_<channel>_<k:04d}_t<time>s.csv
        Each CSV: columns -> t_rel_ms, value_uV
        Also (re)write a master peaks summary CSV:
            <subfolder>/<subfolder_name>_<channel>_peaks_summary.csv
        """
        if self.cur_subfolder is None or self.time_s is None or self.value_uV is None:
            return
        if self.df_peaks.empty:
            messagebox.showwarning(
                "Export", "No peaks to export. Group peaks first with 'Group now'."
            )
            return
        if "group_id" not in self.df_peaks.columns or self.df_peaks["group_id"].isna().all():
            messagebox.showwarning("Export", "No group assignments found. Group peaks first.")
            return

        chan = self._current_channel_label() or "channel"

        pre = ms_to_samples(EXPORT_HALF_MS, float(self.fs))
        post = ms_to_samples(EXPORT_HALF_MS, float(self.fs))

        # Save master peaks summary at subfolder root (include channel in filename)
        try:
            summary_path = (
                self.cur_subfolder / f"{self.cur_subfolder.name}_{chan}_peaks_summary.csv"
            )
            self.df_peaks.to_csv(summary_path, index=False)
        except Exception as e:
            messagebox.showwarning("Export", f"Failed to save peaks summary: {e}")

        # Export per group (skip removed peaks, skip NaN groups)
        df = self.df_peaks.dropna(subset=["group_id"]).copy()
        df = df[~df["peak_idx"].isin(self.removed_peaks)]
        if df.empty:
            messagebox.showwarning("Export", "No peaks to export after removals / NaN groups.")
            return

        df["group_id"] = df["group_id"].astype(int)
        for gid in sorted(df["group_id"].unique()):
            sub = df.loc[df["group_id"] == gid].sort_values("peak_time_s").reset_index(drop=True)
            group_dir = self.cur_subfolder / f"{gid}_{chan}"  # include channel in directory name
            try:
                group_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showwarning("Export", f"Cannot create folder '{group_dir}': {e}")
                continue

            for k, r in sub.iterrows():
                idx = int(r["peak_idx"])
                start = max(0, idx - pre)
                end = min(self.value_uV.size, idx + post)
                t_rel_ms = (self.time_s[start:end] - self.time_s[idx]) * 1e3
                y_rel = self.value_uV[start:end]
                fname = group_dir / f"peak_{chan}_{k:04d}_t{r['peak_time_s']:.6f}s.csv"
                try:
                    pd.DataFrame({"t_rel_ms": t_rel_ms, "value_uV": y_rel}).to_csv(
                        fname, index=False
                    )
                except Exception as e:
                    messagebox.showwarning("Export", f"Failed to save {fname.name}: {e}")


# --------------------------- Main ---------------------------
def main() -> None:
    app = EmgPeakSelectorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
