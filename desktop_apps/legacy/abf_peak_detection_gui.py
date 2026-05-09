# peak_detection_gui.py
# -*- coding: utf-8 -*-
"""
ABF Peak Detection GUI (R-normalized; macOS-safe; no scroll-zoom)
- Folder-based .abf browser (left) + peak list (middle) + plot (right).
- Load ABF (sweep 0):
    ch0: I (pA), ch1: V (mV), ch2: analog (optional)
- Normalize by resistance:
    1) Detect V step edges (preferred) → edge-centered ΔV/ΔI → R(MΩ)
    2) Fallback dual-window if edges not found
    3) Baseline subtract I (same window rule as photocurrent_preview_GUI)
    4) I_norm = (I − baseline) × R(MΩ)  (units ≈ mV)
- Peak detection:
    - Single polarity peaks only (POS or NEG)
    - Use analysis window [t0, t1] (set via "Use Current X" or Span tool)
    - SciPy find_peaks with height / prominence / distance (distance in ms)
- Exports:
    * PNG (preview; current view)
    * SVG (signal-only; current view)
    * CSV summary of selected peaks
    * Per-peak segment CSV (±window_ms around peak), saved in subfolder {stem}/

Dependencies: pyabf, numpy, scipy, matplotlib, pandas, tkinter (built-in)
    pip install pyabf numpy scipy matplotlib pandas
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
import pyabf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.widgets import SpanSelector
from scipy.signal import find_peaks

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

# Initial pixel layout (fixed at start; window resizable overall)
SIDEBAR_W = 340  # px
MIDDLE_INIT_W = 260  # px
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"


# --------------------------- Helpers: robust slicing ---------------------------


def _safe_slice(n: int, a, b) -> slice:
    """Clamp indices [a, b) into array length n; return full slice if invalid."""
    a = max(0, int(a))
    b = max(0, int(b))
    a = min(a, n - 1)
    b = min(b, n)
    if b <= a:
        return slice(0, n)
    return slice(a, b)


# --------------------------- Helpers: R estimation ---------------------------


def _find_voltage_edges(raw_v: np.ndarray) -> Tuple[Optional[int], Optional[int]]:
    """
    Roughly detect voltage pulse rising/falling edges on mV trace using
    10-sample mean & 0.4 mV threshold.
    Returns (pu_V_idx, pd_V_idx) or (None, None) if not found.
    """
    if raw_v.size < 40:
        return None, None
    UP, DOWN = 1, 0
    direction = DOWN
    pu_V, pd_V = None, None
    tmp = 0
    for n in range(0, raw_v.size - 10):
        tmpval = float(np.mean(raw_v[tmp : tmp + 10]))
        curval = float(np.mean(raw_v[n : n + 10]))
        dv = curval - tmpval
        if direction == UP and dv > 0.4:
            pu_V = n
            tmp = n
            direction = DOWN
        elif direction == DOWN and dv > 0.4:
            tmp = n
        elif direction == DOWN and dv < -0.4:
            pd_V = n
            tmp = n
            direction = UP
        elif direction == UP and dv < -0.4:
            tmp = n
        if pu_V is not None and pd_V is not None:
            break
    return pu_V, pd_V


def _compute_R_MOhm_from_edges(I_pA: np.ndarray, V_mV: np.ndarray, pd_V_idx: int) -> float:
    """
    Edge-centered ΔV/ΔI → R(MΩ).
    Windows: [pd_V−1000 : pd_V−1000+500] and [pd_V+1000 : pd_V+1000+500].
    """
    n = len(V_mV)
    start = max(0, pd_V_idx - 1000)
    end = min(n - 1, pd_V_idx + 1000)
    N = 500
    i1 = np.sum(I_pA[start : start + N])
    i2 = np.sum(I_pA[end : end + N])
    v1 = np.sum(V_mV[start : start + N])
    v2 = np.sum(V_mV[end : end + N])
    Ip = abs(i2 - i1) * 1e-12  # A
    Vp = abs(v2 - v1) * 1e-3  # V
    if Ip <= 0:
        return float("nan")
    return (Vp / Ip) / 1e6  # MΩ


def _compute_R_MOhm_fallback(I_pA: np.ndarray, V_mV: np.ndarray) -> float:
    """
    Fallback when edges not found: dual-window ΔV(mV)/ΔI(pA) × 1000 = MΩ.
    Primary windows: [9000:10000] vs [25000:26000]; fallback: 15–17 % vs 75–77 %.
    """
    n = len(I_pA)
    s1 = _safe_slice(n, 9000, 10000)
    s2 = _safe_slice(n, 25000, 26000)
    if (s1.stop - s1.start) < 5 or (s2.stop - s2.start) < 5:
        s1 = _safe_slice(n, int(0.15 * n), int(0.17 * n))
        s2 = _safe_slice(n, int(0.75 * n), int(0.77 * n))
    dv_mV = float(np.mean(V_mV[s1]) - np.mean(V_mV[s2]))
    di_pA = float(np.mean(I_pA[s1]) - np.mean(I_pA[s2]))
    if abs(di_pA) < 1e-9:
        return float("nan")
    return (dv_mV / di_pA) * 1000.0  # MΩ


# --------------------------- Helpers: ABF loading & normalization ---------------------------


def _load_abf_first_sweep(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load sweep 0 of an ABF file; return (t, I_pA, V_mV, analog)."""
    abf = pyabf.ABF(path)
    sw = abf.sweepList[0]
    abf.setSweep(sw, channel=0)
    t = abf.sweepX.copy()
    I = abf.sweepY.copy()
    abf.setSweep(sw, channel=1)
    V = abf.sweepY.copy()
    analog = np.zeros_like(I)
    try:
        abf.setSweep(sw, channel=2)
        analog = abf.sweepY.copy()
    except Exception:
        pass
    return t, I, V, analog


def normalize_current_by_R(path: str) -> Tuple[np.ndarray, np.ndarray, Dict]:
    """
    Load ABF and return (t_s, I_norm, info).
    I_norm = (I_raw − baseline) × R(MΩ)  if R is finite, else (I_raw − baseline).
    """
    t, I, V, _ = _load_abf_first_sweep(path)
    n = len(t)
    if n < 10:
        raise RuntimeError("Not enough data points.")

    pu_V, pd_V = _find_voltage_edges(V)
    if pd_V is not None:
        R = _compute_R_MOhm_from_edges(I, V, pd_V)
        R_method = "edge-centered (±1000, N=500)"
    else:
        R = _compute_R_MOhm_fallback(I, V)
        R_method = "dual-window fallback"

    # Baseline window consistent with photocurrent_preview_GUI
    bsl = _safe_slice(n, 19000, 20000)
    if (bsl.stop - bsl.start) < 5:
        bsl = _safe_slice(n, int(0.38 * n), int(0.40 * n))
    baseline = float(np.mean(I[bsl]))

    if np.isfinite(R):
        I_norm = (I - baseline) * float(R)
        scale = float(R)
    else:
        I_norm = I - baseline
        scale = 1.0

    info = {
        "path": path,
        "dir": os.path.dirname(path),
        "file_base": os.path.splitext(os.path.basename(path))[0],
        "n_points": n,
        "baseline_raw_i": baseline,
        "R_MOhm": float(R) if np.isfinite(R) else float("nan"),
        "R_method": R_method,
        "scale": scale,
        "pu_V_idx": None if pu_V is None else int(pu_V),
        "pd_V_idx": None if pd_V is None else int(pd_V),
    }
    return t, I_norm, info


# --------------------------- Helpers: peak detection ---------------------------


def _estimate_fs(t: np.ndarray) -> float:
    """Estimate sampling frequency from time vector."""
    if t.size < 3:
        return 1000.0
    dt = float(np.median(np.diff(t)))
    return 1.0 / dt if dt > 0 else 1000.0


def detect_peaks_single_polarity(
    t: np.ndarray,
    y: np.ndarray,
    t0: float,
    t1: float,
    polarity: str,
    height: Optional[float],
    prominence: Optional[float],
    distance_ms: float,
) -> List[int]:
    """
    Return list of global peak indices in t/y restricted to [t0, t1].
    polarity: "POS" (find_peaks on y) or "NEG" (find_peaks on −y).
    """
    if t1 <= t0:
        return []
    s = max(0, int(np.searchsorted(t, t0, side="left")))
    e = min(len(t), int(np.searchsorted(t, t1, side="right")))
    if e - s < 3:
        return []

    tt = t[s:e]
    yy = y[s:e]
    fs = _estimate_fs(tt)
    distance = max(1, int((distance_ms / 1000.0) * fs))
    sig = -yy if polarity.upper() == "NEG" else yy
    h = None if height is None else float(height)
    p = None if prominence is None else float(prominence)
    loc, _ = find_peaks(sig, height=h, prominence=p, distance=distance)
    return [s + int(i) for i in loc]


# --------------------------- GUI ---------------------------


class AbfPeakDetectionApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABF — Peak Detection")
        self.geometry("1280x820")
        self.minsize(1000, 660)

        # Data state
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files: List[Path] = []
        self.cur_file: Optional[Path] = None
        self.t: Optional[np.ndarray] = None
        self.y: Optional[np.ndarray] = None
        self.info: Dict = {}

        # Analysis window
        self.win_t0: Optional[float] = None
        self.win_t1: Optional[float] = None
        self.span_selector: Optional[SpanSelector] = None

        # Axis vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")

        # Detection params
        self.polarity_var = tk.StringVar(value="POS")  # POS / NEG
        self.height_var = tk.StringVar(value="")  # blank = None
        self.prom_var = tk.StringVar(value="")  # blank = None
        self.dist_ms_var = tk.DoubleVar(value=2.0)
        self.export_win_ms_var = tk.DoubleVar(value=50.0)  # ±window for segment export
        self.png_dpi_var = tk.IntVar(value=300)

        # Peak list: [{"idx": int, "selected": BooleanVar}, ...]
        self.peaks: List[Dict] = []

        # Build layout
        self._build_layout()
        self._build_left()
        self._build_middle()
        self._build_top_controls()
        self._build_plot()

        self.scan_folder()
        if self.files:
            self.file_listbox.selection_set(0)
            self.load_selected_file()

    # ---------- Layout (3-column grid: left | middle | top_controls + plot_area) ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self.grid_columnconfigure(1, minsize=MIDDLE_INIT_W, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, minsize=PLOT_INIT_H, weight=1)

        self.top_controls = ttk.Frame(self, padding=(8, 8, 8, 0))
        self.top_controls.grid(row=0, column=2, sticky="ew")
        self.top_controls.grid_columnconfigure(0, weight=1)

        self.plot_area = ttk.Frame(self, padding=(8, 6, 8, 8))
        self.plot_area.grid(row=1, column=2, sticky="nsew")
        self.plot_area.grid_columnconfigure(0, weight=1)
        self.plot_area.grid_rowconfigure(1, weight=1)

    # ---------- Left sidebar (folder + file list + detection params + window + export) ----------
    def _build_left(self):
        left = ttk.Frame(self, padding=(8, 8, 8, 8))
        left.grid(row=0, column=0, rowspan=2, sticky="nsew")
        left.grid_rowconfigure(1, weight=1)

        # Folder
        folder_box = ttk.LabelFrame(left, text="Folder", padding=5)
        folder_box.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        folder_box.grid_columnconfigure(0, weight=1)

        row1 = ttk.Frame(folder_box)
        row1.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        row1.grid_columnconfigure(0, weight=1)
        ttk.Label(row1, text="Path:").grid(row=0, column=0, sticky="w")
        ttk.Entry(row1, textvariable=self.folder_path).grid(row=1, column=0, sticky="ew")

        row2 = ttk.Frame(folder_box)
        row2.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(row2, text="Browse…", command=self.browse_folder).pack(side="left")
        ttk.Button(row2, text="Refresh", command=self.scan_folder).pack(side="left", padx=6)

        # File list
        files_box = ttk.LabelFrame(left, text=".abf files", padding=5)
        files_box.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        files_box.grid_rowconfigure(0, weight=1)
        files_box.grid_columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(files_box, height=10, exportselection=False)
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        self.file_listbox.bind("<<ListboxSelect>>", lambda e: self.load_selected_file())
        sb = ttk.Scrollbar(files_box, orient="vertical", command=self.file_listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=sb.set)

        # Detection parameters
        det_box = ttk.LabelFrame(left, text="Detection Parameters", padding=5)
        det_box.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        det_box.grid_columnconfigure(1, weight=1)

        ttk.Label(det_box, text="Polarity:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(
            det_box,
            values=["POS", "NEG"],
            state="readonly",
            textvariable=self.polarity_var,
            width=8,
        ).grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(det_box, text="Min height:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(det_box, textvariable=self.height_var, width=12).grid(
            row=1, column=1, sticky="ew", pady=2
        )

        ttk.Label(det_box, text="Prominence:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(det_box, textvariable=self.prom_var, width=12).grid(
            row=2, column=1, sticky="ew", pady=2
        )

        ttk.Label(det_box, text="Min distance (ms):").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(det_box, textvariable=self.dist_ms_var, width=12).grid(
            row=3, column=1, sticky="ew", pady=2
        )

        ttk.Separator(det_box, orient="horizontal").grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=6
        )

        ttk.Label(det_box, text="Export ±window (ms):").grid(row=5, column=0, sticky="w", pady=2)
        ttk.Entry(det_box, textvariable=self.export_win_ms_var, width=12).grid(
            row=5, column=1, sticky="ew", pady=2
        )

        ttk.Button(det_box, text="Run Detection", command=self.run_detection).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        # Analysis window
        win_box = ttk.LabelFrame(left, text="Analysis Window", padding=5)
        win_box.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        win_box.grid_columnconfigure(0, weight=1)

        self.win_label = tk.StringVar(value="Not set")
        ttk.Label(win_box, textvariable=self.win_label, anchor="w").grid(
            row=0, column=0, sticky="ew", pady=2
        )

        win_btns = ttk.Frame(win_box)
        win_btns.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(win_btns, text="Use Current X", command=self.use_current_x).pack(side="left")
        ttk.Button(win_btns, text="Enable Span Tool", command=self.enable_span_tool).pack(
            side="left", padx=6
        )
        ttk.Button(win_btns, text="Clear", command=self.clear_window).pack(side="left")

        # Export
        exp_box = ttk.LabelFrame(left, text="Export", padding=5)
        exp_box.grid(row=4, column=0, sticky="ew")
        exp_box.grid_columnconfigure(0, weight=1)

        exp_row1 = ttk.Frame(exp_box)
        exp_row1.grid(row=0, column=0, sticky="w", pady=(0, 2))
        ttk.Button(exp_row1, text="Export PNG (preview)", command=self.export_png_preview).pack(
            side="left"
        )
        ttk.Button(
            exp_row1, text="Export SVG (signal only)", command=self.export_svg_signal_only
        ).pack(side="left", padx=6)

        exp_row2 = ttk.Frame(exp_box)
        exp_row2.grid(row=1, column=0, sticky="w", pady=(0, 2))
        ttk.Label(exp_row2, text="PNG DPI:").pack(side="left")
        tk.Spinbox(
            exp_row2, from_=72, to=600, increment=10, width=6, textvariable=self.png_dpi_var
        ).pack(side="left", padx=(6, 0))

        exp_row3 = ttk.Frame(exp_box)
        exp_row3.grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(exp_row3, text="Export Peaks CSV", command=self.export_peaks_csv).pack(
            side="left"
        )

    # ---------- Middle (peak list) ----------
    def _build_middle(self):
        middle = ttk.Frame(self, padding=(8, 8, 8, 8))
        middle.grid(row=0, column=1, rowspan=2, sticky="nsew")
        middle.grid_rowconfigure(1, weight=1)
        middle.grid_columnconfigure(0, weight=1)

        # Header with count + Select/Deselect buttons
        header_fr = ttk.LabelFrame(middle, text="Detected Peaks", padding=5)
        header_fr.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.peaks_count_var = tk.StringVar(value="No peaks detected")
        ttk.Label(header_fr, textvariable=self.peaks_count_var).pack(side="left", padx=5)

        btn_fr = ttk.Frame(header_fr)
        btn_fr.pack(side="right", padx=5)
        ttk.Button(btn_fr, text="Select All", width=10, command=self.select_all_peaks).pack(
            side="left", padx=2
        )
        ttk.Button(btn_fr, text="Deselect All", width=10, command=self.deselect_all_peaks).pack(
            side="left", padx=2
        )

        # Scrollable peak list
        list_fr = ttk.LabelFrame(middle, text="Peak List (select to export)", padding=5)
        list_fr.grid(row=1, column=0, sticky="nsew")
        list_fr.grid_rowconfigure(0, weight=1)
        list_fr.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(list_fr, borderwidth=0, highlightthickness=0)
        sb_list = ttk.Scrollbar(list_fr, orient="vertical", command=canvas.yview)
        self.peaks_frame = ttk.Frame(canvas)

        self.peaks_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.peaks_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb_list.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        sb_list.grid(row=0, column=1, sticky="ns")

    # ---------- Top controls (right column, row 0) ----------
    def _build_top_controls(self):
        # Row A: X-axis limits
        rowA = ttk.Frame(self.top_controls)
        rowA.grid(row=0, column=0, sticky="w", pady=(0, 3))
        ttk.Label(rowA, text="X min (s):").pack(side="left")
        ttk.Entry(rowA, textvariable=self.xmin_var, width=10).pack(side="left", padx=(4, 8))
        ttk.Label(rowA, text="X max (s):").pack(side="left")
        ttk.Entry(rowA, textvariable=self.xmax_var, width=10).pack(side="left", padx=(4, 0))

        # Row B: Y-axis limits + unified Apply / Grab / Reset
        rowB = ttk.Frame(self.top_controls)
        rowB.grid(row=1, column=0, sticky="w", pady=(0, 3))
        ttk.Label(rowB, text="Y min:").pack(side="left")
        ttk.Entry(rowB, textvariable=self.ymin_var, width=10).pack(side="left", padx=(4, 8))
        ttk.Label(rowB, text="Y max:").pack(side="left")
        ttk.Entry(rowB, textvariable=self.ymax_var, width=10).pack(side="left", padx=(4, 12))
        ttk.Button(rowB, text="Apply", command=self.apply_axes_from_inputs).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(rowB, text="Grab", command=self.update_axis_inputs_from_view).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(rowB, text="Reset", command=self.reset_axes).pack(side="left")

    # ---------- Plot area (right column, row 1) ----------
    def _build_plot(self):
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.plot_area, textvariable=self.status_var, foreground="#444").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )

        self.fig, self.ax = plt.subplots(figsize=(9, 4.8))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_area)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        self.toolbar = NavigationToolbar2Tk(self.canvas, self.plot_area, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # ---------- Folder & file ----------
    def browse_folder(self):
        try:
            d = filedialog.askdirectory(initialdir=self.folder_path.get() or DEFAULT_START_DIR)
        except Exception as e:
            messagebox.showerror("Folder", f"Folder dialog failed: {e}")
            return
        if d:
            self.folder_path.set(d)
            self.scan_folder()

    def scan_folder(self):
        p = Path(self.folder_path.get().strip())
        if not p.is_dir():
            messagebox.showwarning("Folder", "Please choose a valid folder.")
            return
        self.files = sorted(
            [x for x in p.iterdir() if x.is_file() and x.suffix.lower() == ".abf"],
            key=lambda z: z.name.lower(),
        )
        self.file_listbox.delete(0, tk.END)
        for f in self.files:
            self.file_listbox.insert(tk.END, f.name)
        self.status_var.set(f"Scanned: {p}  |  {len(self.files)} .abf files found")
        if not self.files:
            self._clear_all()

    def load_selected_file(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        self.cur_file = self.files[int(sel[0])]
        try:
            self.t, self.y, self.info = normalize_current_by_R(str(self.cur_file))
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            self._clear_all()
            return

        self.peaks.clear()
        self._update_peaks_list()
        self.win_t0 = None
        self.win_t1 = None
        self._update_window_label()
        self._replot_full()

        Rtxt = (
            "nan"
            if not np.isfinite(self.info.get("R_MOhm", np.nan))
            else f"{self.info['R_MOhm']:.3g}"
        )
        self.status_var.set(
            f"Loaded: {self.cur_file.name} | N={self.info.get('n_points')} | "
            f"R(MΩ)={Rtxt} | method={self.info.get('R_method', '?')} | "
            f"baseline(rawI)={self.info.get('baseline_raw_i', 0):.3g}"
        )

    def _clear_all(self):
        self.t = None
        self.y = None
        self.info = {}
        self.cur_file = None
        self.peaks.clear()
        self._update_peaks_list()
        self._clear_plot()
        self.status_var.set("Ready")

    # ---------- Analysis window ----------
    def _update_window_label(self):
        if self.win_t0 is None or self.win_t1 is None:
            self.win_label.set("Not set")
        else:
            self.win_label.set(f"[{self.win_t0:.6g}, {self.win_t1:.6g}] s")

    def use_current_x(self):
        if self.t is None:
            return
        x0, x1 = self.ax.get_xlim()
        self.win_t0 = float(min(x0, x1))
        self.win_t1 = float(max(x0, x1))
        self._update_window_label()
        self._replot_full()

    def enable_span_tool(self):
        if self.t is None:
            messagebox.showinfo("Span Tool", "Load a file first.")
            return

        def _on_span(xmin, xmax):
            self.win_t0 = float(min(xmin, xmax))
            self.win_t1 = float(max(xmin, xmax))
            self._update_window_label()
            if self.span_selector is not None:
                self.span_selector.set_active(False)
            self._replot_full()

        self.span_selector = SpanSelector(
            self.ax,
            _on_span,
            "horizontal",
            useblit=True,
            interactive=True,
            drag_from_anywhere=True,
            props=dict(alpha=0.25, facecolor="green"),
        )
        self.span_selector.set_active(True)
        messagebox.showinfo("Span Tool", "Drag on the plot to select the analysis window.")

    def clear_window(self):
        self.win_t0 = None
        self.win_t1 = None
        self._update_window_label()
        self._replot_full()

    # ---------- Detection ----------
    def _parse_optional_float(self, s: str) -> Optional[float]:
        s = (s or "").strip()
        return None if s == "" else float(s)

    def run_detection(self):
        if self.t is None or self.y is None:
            messagebox.showinfo("Info", "Load a file first.")
            return
        if self.win_t0 is None or self.win_t1 is None:
            messagebox.showinfo("Info", "Set an analysis window first.")
            return
        try:
            height = self._parse_optional_float(self.height_var.get())
            prom = self._parse_optional_float(self.prom_var.get())
            dist_ms = float(self.dist_ms_var.get())
        except Exception:
            messagebox.showerror("Params", "Invalid detection parameters.")
            return

        idxs = detect_peaks_single_polarity(
            self.t,
            self.y,
            float(self.win_t0),
            float(self.win_t1),
            self.polarity_var.get(),
            height=height,
            prominence=prom,
            distance_ms=dist_ms,
        )
        self.peaks = [{"idx": int(i), "selected": tk.BooleanVar(value=True)} for i in idxs]
        self._update_peaks_list()
        self.status_var.set(
            f"Detected {len(self.peaks)} peak(s) | polarity={self.polarity_var.get()}"
        )
        self._replot_full()

    def _update_peaks_list(self):
        for w in self.peaks_frame.winfo_children():
            w.destroy()

        if not self.peaks or self.t is None or self.y is None:
            self.peaks_count_var.set("No peaks detected")
            ttk.Label(
                self.peaks_frame, text="Run detection to see peaks here", foreground="gray"
            ).pack(pady=20)
            return

        self.peaks_count_var.set(f"Total: {len(self.peaks)} peaks")

        # Column header
        header = ttk.Frame(self.peaks_frame)
        header.pack(fill="x", padx=5, pady=(5, 2))
        for text, w in (("✓", 3), ("#", 4), ("t (s)", 14), ("y (norm)", 14)):
            ttk.Label(
                header,
                text=text,
                width=w,
                anchor="center" if w <= 4 else "w",
                font=("Arial", 9, "bold"),
            ).pack(side="left")

        ttk.Separator(self.peaks_frame, orient="horizontal").pack(fill="x", padx=5, pady=2)

        for k, d in enumerate(self.peaks, start=1):
            i = d["idx"]
            row = ttk.Frame(self.peaks_frame)
            row.pack(fill="x", padx=5, pady=1)
            ttk.Checkbutton(row, variable=d["selected"], command=self._replot_full).pack(
                side="left", padx=(0, 5)
            )
            ttk.Label(row, text=str(k), width=4, anchor="center").pack(side="left")
            ttk.Label(row, text=f"{float(self.t[i]):.9g}", width=14, anchor="w").pack(side="left")
            ttk.Label(row, text=f"{float(self.y[i]):.9g}", width=14, anchor="w").pack(side="left")

    def select_all_peaks(self):
        for d in self.peaks:
            d["selected"].set(True)
        self._replot_full()

    def deselect_all_peaks(self):
        for d in self.peaks:
            d["selected"].set(False)
        self._replot_full()

    # ---------- Axis controls ----------
    def apply_axes_from_inputs(self):
        if self.t is None:
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
            messagebox.showerror("Axis", "Axis min and max must be different.")
            return
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        self.ax.set_xlim(xmin, xmax)
        self.ax.set_ylim(ymin, ymax)
        self.canvas.draw_idle()

    def update_axis_inputs_from_view(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.xmin_var.set(f"{x0:.6g}")
        self.xmax_var.set(f"{x1:.6g}")
        self.ymin_var.set(f"{y0:.6g}")
        self.ymax_var.set(f"{y1:.6g}")

    def reset_axes(self):
        self._replot_full()

    # ---------- Plot ----------
    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _replot_full(self):
        self.ax.clear()
        if self.t is None or self.y is None:
            self.canvas.draw_idle()
            return

        self.ax.plot(self.t, self.y, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("I_norm (pA·MΩ)")

        # Analysis window shading
        if self.win_t0 is not None and self.win_t1 is not None:
            self.ax.axvspan(self.win_t0, self.win_t1, alpha=0.12, color="gray")
            self.ax.axvline(self.win_t0, ls="--", lw=0.8, color="gray")
            self.ax.axvline(self.win_t1, ls="--", lw=0.8, color="gray")

        # Selected peak markers
        if self.peaks:
            sel = [d for d in self.peaks if d["selected"].get()]
            if sel:
                ts = [float(self.t[d["idx"]]) for d in sel]
                ys = [float(self.y[d["idx"]]) for d in sel]
                self.ax.scatter(ts, ys, s=50, marker="^", color="red", zorder=5)
                for j, d in enumerate(self.peaks, start=1):
                    if d["selected"].get():
                        ii = d["idx"]
                        self.ax.annotate(
                            str(j),
                            xy=(self.t[ii], self.y[ii]),
                            xytext=(3, 3),
                            textcoords="offset points",
                            fontsize=9,
                            color="red",
                        )

        self.ax.relim()
        self.ax.autoscale()
        self.canvas.draw_idle()
        for v in (self.xmin_var, self.xmax_var, self.ymin_var, self.ymax_var):
            v.set("")

    # ---------- Exports ----------
    def _current_view_limits(self) -> Tuple[float, float, float, float]:
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return float(x0), float(x1), float(y0), float(y1)

    def export_png_preview(self):
        if self.t is None or self.y is None or not self.info:
            messagebox.showinfo("Export", "Load a file first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "preview")
        out_path = os.path.join(out_dir, f"{base}_preview.png")
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(self.t, self.y, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("I_norm (pA·MΩ)")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            sel = [d for d in self.peaks if d["selected"].get()]
            if sel:
                ts = [float(self.t[d["idx"]]) for d in sel if x0 <= self.t[d["idx"]] <= x1]
                ys = [float(self.y[d["idx"]]) for d in sel if x0 <= self.t[d["idx"]] <= x1]
                if ts:
                    ax.scatter(ts, ys, s=50, marker="^", color="red", zorder=5)
            FigureCanvasAgg(fig)
            fig.savefig(
                out_path,
                dpi=int(self.png_dpi_var.get()),
                bbox_inches="tight",
                pad_inches=0.05,
                facecolor="white",
            )
            del fig
        except Exception as e:
            messagebox.showerror("Export PNG", str(e))
            return
        self.status_var.set(f"Exported PNG: {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_svg_signal_only(self):
        if self.t is None or self.y is None or not self.info:
            messagebox.showinfo("Export", "Load a file first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        mask = (self.t >= x0) & (self.t <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in current view.")
            return
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "preview")
        out_path = os.path.join(out_dir, f"{base}_preview_signal.svg")
        try:
            from matplotlib.backends.backend_svg import FigureCanvasSVG
            from matplotlib.figure import Figure

            fig = Figure(figsize=(8, 3), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(self.t[mask], self.y[mask], color=LINE_COLOR, lw=1.0)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_position([0, 0, 1, 1])
            ax.set_title("")
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_xticks([])
            ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_visible(False)
            ax.set_frame_on(False)
            ax.axis("off")
            FigureCanvasSVG(fig)
            fig.savefig(
                out_path,
                format="svg",
                bbox_inches="tight",
                pad_inches=0,
                transparent=True,
                facecolor="none",
            )
            del fig
        except Exception as e:
            messagebox.showerror("Export SVG", str(e))
            return
        self.status_var.set(f"Exported SVG: {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_peaks_csv(self):
        if self.cur_file is None or self.t is None or self.y is None or not self.peaks:
            messagebox.showinfo("Export", "No peaks to export.")
            return
        sel = [d for d in self.peaks if d["selected"].get()]
        if not sel:
            messagebox.showinfo("Export", "No peaks selected.")
            return

        out_folder = self.cur_file.parent / self.cur_file.stem
        try:
            out_folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Export", f"Cannot create folder:\n{out_folder}\n{e}")
            return

        # Summary CSV
        R = self.info.get("R_MOhm", np.nan)
        R_method = self.info.get("R_method", "")
        baseline = self.info.get("baseline_raw_i", np.nan)
        rows = []
        for export_idx, d in enumerate(sel, start=1):
            i = int(d["idx"])
            rows.append(
                {
                    "export_index": export_idx,
                    "global_index": i,
                    "t_s": float(self.t[i]),
                    "y_norm": float(self.y[i]),
                    "polarity": self.polarity_var.get(),
                    "R_MOhm": float(R) if np.isfinite(R) else np.nan,
                    "R_method": R_method,
                    "baseline_raw_i_pA": float(baseline),
                    "window_start_s": float(self.win_t0) if self.win_t0 is not None else np.nan,
                    "window_end_s": float(self.win_t1) if self.win_t1 is not None else np.nan,
                }
            )
        summary_path = out_folder / f"{self.cur_file.stem}_peaks_summary.csv"
        try:
            pd.DataFrame(rows).to_csv(summary_path, index=False, encoding="utf-8-sig")
        except Exception as e:
            messagebox.showerror("Export", f"Write summary failed:\n{e}")
            return

        # Per-peak segment CSVs
        try:
            half = float(self.export_win_ms_var.get()) / 1000.0
        except Exception:
            messagebox.showerror("Export", "Invalid export window (ms).")
            return

        saved = 0
        for export_idx, d in enumerate(sel, start=1):
            i0 = int(d["idx"])
            tp = float(self.t[i0])
            mask = (self.t >= tp - half) & (self.t <= tp + half)
            if not np.any(mask):
                continue
            seg_path = out_folder / f"{self.cur_file.stem}_peak_{export_idx:03d}.csv"
            try:
                pd.DataFrame({"time_s": self.t[mask], "I_norm": self.y[mask]}).to_csv(
                    seg_path, index=False
                )
                saved += 1
            except Exception:
                pass

        self.status_var.set(f"Exported {saved}/{len(sel)} peak segments → {out_folder.name}")
        messagebox.showinfo("Export", f"Saved summary + {saved} segments to:\n{out_folder}")


# --------------------------- Main ---------------------------
def main() -> None:
    app = AbfPeakDetectionApp()
    app.mainloop()


if __name__ == "__main__":
    main()
