# photocurrent_preview_GUI.py
# -*- coding: utf-8 -*-
"""
Photocurrent Viewer (macOS-safe; no resampling, no scroll-zoom)
- Folder-based .abf browser with left sidebar (fixed pixel width) and right preview.
- Processing: baseline subtraction + optional R-scaling via I × R(MΩ), with robust fallback.
- Exports:
    * SVG (signal-only, current preview window; no axes/ticks/text/spines; tight bbox; transparent)
    * PNG (full preview figure with axes; current preview window; configurable DPI)
    * CSV (preview window only)
    * CSV (entire processed file)
- NEW:
    * Processing Options: checkbox to enable/disable R-scaling (I × R in MΩ).
    * Batch Export Queue under the files list (Add/Remove/Move/Export).
    * Downsampling factor (×) applied to ALL exports (CSV/SVG/PNG/Batch) without affecting on-screen plots.
- UI compact rows; Y-axis controls placed on a new line right after X-axis controls.
- No mouse wheel zoom; use Axis inputs, Segment, or toolbar buttons for navigation.

Dependencies: pyabf, numpy, matplotlib, tkinter (built-in), pandas
    pip install pyabf numpy matplotlib pandas
"""

import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyabf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

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

# Initial pixel layout
SIDEBAR_W = 340  # px
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"


# --------------------------- Helpers: pulse & R estimation ---------------------------


def _safe_slice(n: int, a, b) -> slice:
    """Clamp indices [a,b) into array length n; return full slice if invalid."""
    a = max(0, int(a))
    b = max(0, int(b))
    a = min(a, n - 1)
    b = min(b, n)
    if b <= a:
        return slice(0, n)
    return slice(a, b)


def _find_voltage_edges(raw_v: np.ndarray) -> tuple[int | None, int | None]:
    """
    Roughly detect voltage pulse rising/falling edges on mV trace using 10-sample mean & 0.4 mV threshold.
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
    Replicate your batch method: windowed ΔV/ΔI around voltage falling edge, then convert to MΩ.
      - Windows: [pd_V-1000 : pd_V-1000+500] and [pd_V+1000 : pd_V+1000+500] (clamped to valid range)
      - Use sums (ratio unaffected by dividing by N)
      - Convert: pA -> A (×1e-12), mV -> V (×1e-3), finally Ω->MΩ (/1e6)
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
    R_ohm = Vp / Ip
    return R_ohm / 1e6  # MΩ


def _compute_R_MOhm_fallback(I_pA: np.ndarray, V_mV: np.ndarray) -> float:
    """
    Fallback when edges not found: use two broad windows to estimate ΔV(mV), ΔI(pA),
    then R(MΩ) ≈ (ΔV/ΔI) × 1000. Windows:
      primary:  [9000:10000] vs [25000:26000]
      fallback: [15%:17%]    vs [75%:77%]
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
    # (mV/pA) × 1000 = MΩ
    return (dv_mV / di_pA) * 1000.0


def _parse_pulse_ms_from_name(path: str):
    """Extract pulse width like '50ms' or '50_0ms' from filename; return float(ms) or None."""
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(\d+(?:_\d+)?)ms", base, flags=re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1).replace("_", "."))


# --------------------------- Load & process ---------------------------


def load_and_process_abf(file_path: str, apply_scaling: bool = True):
    """
    Load ABF and produce:
        time_s:       (N,) seconds
        current_pa*:  (N,) baseline-corrected current; if apply_scaling, *= R(MΩ)  (units ≈ mV)
        info: dict with meta fields (including R_MOhm and edge indices)
    """
    abf = pyabf.ABF(file_path)
    sweep = abf.sweepList[0]

    # ch0: current (pA)
    abf.setSweep(sweep, channel=0)
    time_s = abf.sweepX.copy()
    raw_i = abf.sweepY.copy()

    # ch1: voltage (mV)
    abf.setSweep(sweep, channel=1)
    raw_v = abf.sweepY.copy()

    n = len(time_s)
    if n < 10:
        raise RuntimeError("Not enough data points to plot.")

    # --- Estimate R in MΩ using voltage edges (primary) or window fallback ---
    pu_V, pd_V = _find_voltage_edges(raw_v)
    if pu_V is not None:
        R_MOhm = _compute_R_MOhm_from_edges(raw_i, raw_v, pu_V)
        R_method = "edge-centered rising (±1000, N=500)"
    else:
        R_MOhm = _compute_R_MOhm_fallback(raw_i, raw_v)
        R_method = "dual-window fallback"

    # --- Baseline window: prefer [19000:20000], else 38–40% ---
    bsl = _safe_slice(n, 19000, 20000)
    if (bsl.stop - bsl.start) < 5:
        bsl = _safe_slice(n, int(0.38 * n), int(0.40 * n))
    baseline = float(np.mean(raw_i[bsl]))

    # --- Apply scaling: I_corr = (I - baseline) × R(MΩ)  ---
    if apply_scaling and np.isfinite(R_MOhm):
        scale = R_MOhm
        current_pa = (raw_i - baseline) * scale  # pA·MΩ ≈ mV
    else:
        scale = 1.0
        current_pa = raw_i - baseline

    info = {
        "pulse_ms": _parse_pulse_ms_from_name(file_path),
        "apply_scaling": bool(apply_scaling),
        "R_MOhm": float(R_MOhm) if np.isfinite(R_MOhm) else float("nan"),
        "R_method": R_method,
        "scale": scale,  # equals R_MOhm when scaling is ON; else 1.0
        "baseline_raw_i": baseline,  # pA
        "file_base": os.path.splitext(os.path.basename(file_path))[0],
        "dir": os.path.dirname(file_path),
        "path": file_path,
        "n_points": n,
        "pu_V_idx": None if pu_V is None else int(pu_V),
        "pd_V_idx": None if pd_V is None else int(pd_V),
    }
    return time_s, current_pa, info


# --------------------------- GUI ---------------------------
class AbfPhotocurrentViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABF — Photocurrent Viewer")
        self.geometry("1280x820")
        self.minsize(980, 640)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files = []  # list[str] of full paths
        self.queue_files: list[str] = []  # batch export queue (full paths)

        self.time_s = None
        self.current_pa = None
        self.info = {}
        self.png_dpi_var = tk.IntVar(value=300)

        # Processing option: enable/disable R-scaling (I × R in MΩ)
        self.apply_scaling_var = tk.IntVar(value=1)  # 1 = ON (default), 0 = OFF

        # Export downsampling factor (×)
        self.ds_factor_var = tk.StringVar(value="1")

        # Axis/segment vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")
        self.seg_start_var = tk.StringVar(value="")
        self.seg_end_var = tk.StringVar(value="")

        # Build layout
        self._build_layout()
        self._build_left()
        self._build_top_controls()
        self._build_plot()

        # Initial scan & preload
        self.scan_folder()
        if self.files:
            self.file_listbox.selection_set(0)
            self.load_selected_file()

    # ---------- Layout ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, minsize=PLOT_INIT_H, weight=1)

        self.left = ttk.Frame(self, padding=(8, 8, 8, 8))
        self.left.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.left.update_idletasks()
        self.left.grid_propagate(False)

        self.top_controls = ttk.Frame(self, padding=(8, 8, 8, 0))
        self.top_controls.grid(row=0, column=1, sticky="ew")
        self.top_controls.grid_columnconfigure(0, weight=1)

        self.plot_area = ttk.Frame(self, padding=(8, 6, 8, 8))
        self.plot_area.grid(row=1, column=1, sticky="nsew")
        self.plot_area.grid_columnconfigure(0, weight=1)
        self.plot_area.grid_rowconfigure(1, weight=1)

    # ---------- Left ----------
    def _build_left(self):
        folder_box = ttk.LabelFrame(self.left, text="Folder")
        folder_box.pack(fill="x", side="top")

        row1 = ttk.Frame(folder_box)
        row1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(row1, text="Path:").pack(side="left")
        self.folder_entry = ttk.Entry(row1, textvariable=self.folder_path, width=30)
        self.folder_entry.pack(side="left", padx=6, fill="x", expand=True)

        row2 = ttk.Frame(folder_box)
        row2.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(row2, text="Browse…", command=self.choose_folder).pack(side="left")
        ttk.Button(row2, text="Refresh", command=self.scan_folder).pack(side="left", padx=6)

        files_box = ttk.LabelFrame(self.left, text=".abf files")
        files_box.pack(fill="both", expand=True, pady=(6, 4))

        self.file_listbox = tk.Listbox(files_box, height=10, exportselection=False)
        self.file_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.file_listbox.bind("<<ListboxSelect>>", lambda e: self.load_selected_file())
        sb = ttk.Scrollbar(files_box, orient="vertical", command=self.file_listbox.yview)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.file_listbox.config(yscrollcommand=sb.set)

        # Batch Export Queue
        queue_box = ttk.LabelFrame(self.left, text="Batch Export Queue")
        queue_box.pack(fill="both", expand=True, pady=(4, 0))

        self.queue_listbox = tk.Listbox(queue_box, height=8, exportselection=False)
        self.queue_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(6, 2))
        sbq = ttk.Scrollbar(queue_box, orient="vertical", command=self.queue_listbox.yview)
        sbq.pack(side="right", fill="y", padx=(0, 6), pady=(6, 2))
        self.queue_listbox.config(yscrollcommand=sbq.set)

        qctrl1 = ttk.Frame(queue_box)
        qctrl1.pack(fill="x", padx=6, pady=(2, 2))
        ttk.Button(qctrl1, text="Add (↓)", command=self.queue_add_current_file).pack(side="left")
        ttk.Button(qctrl1, text="Remove (↑)", command=self.queue_remove_selected).pack(
            side="left", padx=6
        )

        qctrl2 = ttk.Frame(queue_box)
        qctrl2.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(qctrl2, text="Move Up", command=lambda: self.queue_move(-1)).pack(side="left")
        ttk.Button(qctrl2, text="Move Down", command=lambda: self.queue_move(+1)).pack(
            side="left", padx=6
        )

        qact = ttk.Frame(queue_box)
        qact.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(qact, text="Export Queue → CSV (full)", command=self.queue_export_all).pack(
            side="left"
        )

    # ---------- Top controls ----------
    def _build_top_controls(self):
        # Row A: Graphics export
        rowA = ttk.Frame(self.top_controls)
        rowA.grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowA, text="Export SVG (signal only)", command=self.export_svg_signal_only).pack(
            side="left"
        )
        ttk.Button(rowA, text="Export PNG (preview)", command=self.export_png_preview).pack(
            side="left", padx=8
        )
        ttk.Label(rowA, text="PNG DPI:").pack(side="left", padx=(12, 4))
        tk.Spinbox(
            rowA, from_=72, to=600, increment=10, width=6, textvariable=self.png_dpi_var
        ).pack(side="left")

        # Row A2: Processing Options (R-scaling on/off)
        rowA2 = ttk.Frame(self.top_controls)
        rowA2.grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Checkbutton(
            rowA2,
            text="R-scaling (I × R, MΩ)",
            variable=self.apply_scaling_var,
            command=self.on_toggle_scaling,
        ).pack(side="left")

        # Row B: Data export + Downsampling (to the right)
        rowB = ttk.Frame(self.top_controls)
        rowB.grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowB, text="Export CSV (preview)", command=self.export_csv_preview).pack(
            side="left"
        )
        ttk.Button(rowB, text="Export CSV (full file)", command=self.export_csv_full).pack(
            side="left", padx=8
        )

        ttk.Label(rowB, text="Downsample ×").pack(side="left", padx=(16, 4))
        ds_combo = ttk.Combobox(
            rowB,
            width=6,
            textvariable=self.ds_factor_var,
            values=["1", "2", "3", "5", "10"],
            state="normal",
        )
        ds_combo.pack(side="left")

        # Row C: Segment (quick view window)
        rowC = ttk.Frame(self.top_controls)
        rowC.grid(row=3, column=0, sticky="w", pady=(2, 2))
        ttk.Label(rowC, text="Segment Start (s):").pack(side="left")
        ttk.Entry(rowC, width=10, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowC, text="End (s):").pack(side="left")
        ttk.Entry(rowC, width=10, textvariable=self.seg_end_var).pack(side="left", padx=(4, 8))
        ttk.Button(rowC, text="Apply", command=self.apply_segment_window).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(rowC, text="Full View", command=self.reset_axes).pack(side="left")

        # Row D1: X-axis
        rowD1 = ttk.Frame(self.top_controls)
        rowD1.grid(row=4, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowD1, text="X min (s):").pack(side="left")
        ttk.Entry(rowD1, width=10, textvariable=self.xmin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowD1, text="X max (s):").pack(side="left")
        ttk.Entry(rowD1, width=10, textvariable=self.xmax_var).pack(side="left", padx=(4, 12))

        # Row D2: Y-axis + buttons
        rowD2 = ttk.Frame(self.top_controls)
        rowD2.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowD2, text="Y min:").pack(side="left")
        ttk.Entry(rowD2, width=10, textvariable=self.ymin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowD2, text="Y max:").pack(side="left")
        ttk.Entry(rowD2, width=10, textvariable=self.ymax_var).pack(side="left", padx=(4, 12))
        ttk.Button(rowD2, text="Apply", command=self.apply_axes_from_inputs).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowD2, text="Grab", command=self.update_axis_inputs_from_view).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowD2, text="Reset", command=self.reset_axes).pack(side="left")

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

    # ---------- Folder scan / file load ----------
    def choose_folder(self):
        try:
            d = filedialog.askdirectory(initialdir=self.folder_path.get() or DEFAULT_START_DIR)
        except Exception as e:
            messagebox.showerror("Folder", f"Folder dialog failed: {e}")
            return
        if d:
            self.folder_path.set(d)
            self.scan_folder()

    def scan_folder(self):
        path = self.folder_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showwarning("Folder", "Please choose a valid folder.")
            return
        abfs = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(".abf")]
        abfs.sort()
        self.files = abfs
        self.file_listbox.delete(0, tk.END)
        for p in self.files:
            self.file_listbox.insert(tk.END, os.path.basename(p))
        self.status_var.set(f"Scanned: {path}")
        if not self.files:
            self._clear_plot()

    def load_selected_file(self):
        """Load and process the currently selected ABF using the R-scaling toggle."""
        if not self.files:
            return
        try:
            idxs = self.file_listbox.curselection()
            if not idxs:
                return
            path = self.files[int(idxs[0])]
            time_s, current_pa, info = load_and_process_abf(
                path, apply_scaling=bool(self.apply_scaling_var.get())
            )
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return
        self.time_s = time_s
        self.current_pa = current_pa
        self.info = info
        self._replot_full()
        scale_note = "ON" if info.get("apply_scaling", True) else "OFF"
        Rtxt = "nan" if not np.isfinite(info.get("R_MOhm", np.nan)) else f"{info['R_MOhm']:.3g}"
        self.status_var.set(
            f"Loaded: {os.path.basename(path)} | N={info['n_points']} | "
            f"R-scaling={scale_note} | R(MΩ)={Rtxt} | method={info.get('R_method','?')} | "
            f"baseline(rawI)={info['baseline_raw_i']:.3g}"
        )

    def on_toggle_scaling(self):
        """Reprocess currently selected file when the R-scaling checkbox is toggled."""
        if self.files and self.file_listbox.curselection():
            self.load_selected_file()

    # ---------- Plot helpers ----------
    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _replot_full(self):
        if self.time_s is None:
            self._clear_plot()
            return
        self.ax.clear()
        self.ax.plot(self.time_s, self.current_pa, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel("Time (s)")
        # dynamic y label based on scaling mode
        if self.info.get("apply_scaling", False) and np.isfinite(self.info.get("R_MOhm", np.nan)):
            self.ax.set_ylabel("Current (pA·MΩ)")  # ≈ mV
        else:
            self.ax.set_ylabel("Current (pA)")
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

    def apply_segment_window(self):
        if self.time_s is None:
            return
        try:
            s0 = float(self.seg_start_var.get().strip())
            s1 = float(self.seg_end_var.get().strip())
        except Exception:
            messagebox.showerror("Segment", "Start and End must be numbers (seconds).")
            return
        if s0 == s1:
            messagebox.showerror("Segment", "Start and End must be different.")
            return
        if s0 > s1:
            s0, s1 = s1, s0
        t0, t1 = float(self.time_s[0]), float(self.time_s[-1])
        s0 = max(t0, min(s0, t1))
        s1 = max(t0, min(s1, t1))
        if s1 - s0 <= 0:
            messagebox.showerror(
                "Segment", "Segment length must be positive and within time range."
            )
            return
        self.ax.set_xlim(s0, s1)
        self.canvas.draw_idle()

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

    # ---------- Downsampling helpers (exports only) ----------
    def _get_ds_factor(self) -> int:
        """Parse and clamp downsampling factor (integer ≥1)."""
        try:
            k = int(str(self.ds_factor_var.get()).strip())
        except Exception:
            k = 1
        return max(1, k)

    @staticmethod
    def _downsample_xy(x: np.ndarray, y: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Simple decimation by factor k (no filtering)."""
        if k <= 1:
            return x, y
        return x[::k], y[::k]

    # ---------- Utility ----------
    def _current_view_limits(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return x0, x1, y0, y1

    @staticmethod
    def _tag(val: float) -> str:
        return f"{val:.6f}".replace(".", "p")

    # ---------- Exports ----------
    def export_svg_signal_only(self):
        if self.time_s is None or not self.info:
            messagebox.showinfo("Export", "Load an ABF file first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        mask = (self.time_s >= x0) & (self.time_s <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return
        t = self.time_s[mask]
        i = self.current_pa[mask]
        k = self._get_ds_factor()
        t, i = self._downsample_xy(t, i, k)
        if len(t) < 2:
            messagebox.showwarning("Export", "Downsampling produced too few points to draw.")
            return
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "preview")
        out_path = os.path.join(out_dir, f"{base}_preview_signal.svg")
        try:
            from matplotlib.backends.backend_svg import FigureCanvasSVG
            from matplotlib.figure import Figure

            fig = Figure(figsize=(8, 3), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(t, i, color=LINE_COLOR, lw=1.0)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_title("")
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_xticks([])
            ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_visible(False)
            ax.set_frame_on(False)
            ax.axis("off")
            ax.set_position([0, 0, 1, 1])
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
        self.status_var.set(f"Exported SVG (signal-only): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_png_preview(self):
        if self.time_s is None or not self.info:
            messagebox.showinfo("Export", "Load an ABF file first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        k = self._get_ds_factor()
        t_ds, i_ds = self._downsample_xy(self.time_s, self.current_pa, k)
        if len(t_ds) < 2:
            messagebox.showwarning("Export", "Downsampling produced too few points to draw.")
            return
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "preview")
        out_path = os.path.join(out_dir, f"{base}_preview.png")
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(t_ds, i_ds, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            if self.info.get("apply_scaling", False) and np.isfinite(
                self.info.get("R_MOhm", np.nan)
            ):
                ax.set_ylabel("Current (pA·MΩ)")
            else:
                ax.set_ylabel("Current (pA)")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
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
        self.status_var.set(f"Exported PNG (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_preview(self):
        if self.time_s is None or not self.info:
            messagebox.showinfo("Export", "Load an ABF file first.")
            return
        x0, x1, _, _ = self._current_view_limits()
        mask = (self.time_s >= x0) & (self.time_s <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return
        t = self.time_s[mask]
        i = self.current_pa[mask]
        k = self._get_ds_factor()
        t, i = self._downsample_xy(t, i, k)
        df = pd.DataFrame({"time_s": t, "current_pA": i})
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "preview")
        out_path = os.path.join(out_dir, f"{base}_preview_{self._tag(x0)}-{self._tag(x1)}s.csv")
        try:
            df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return
        self.status_var.set(f"Exported CSV (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_full(self):
        if self.time_s is None or not self.info:
            messagebox.showinfo("Export", "Load an ABF file first.")
            return
        k = self._get_ds_factor()
        t, i = self._downsample_xy(self.time_s, self.current_pa, k)
        df = pd.DataFrame({"time_s": t, "current_pA": i})
        out_dir = self.info.get("dir", os.getcwd())
        base = self.info.get("file_base", "data")
        out_path = os.path.join(out_dir, f"{base}.csv")
        try:
            df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return
        self.status_var.set(f"Exported CSV (full file): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    # ---------- Queue ----------
    def _refresh_queue_listbox(self):
        self.queue_listbox.delete(0, tk.END)
        for p in self.queue_files:
            self.queue_listbox.insert(tk.END, os.path.basename(p))

    def queue_add_current_file(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        p = self.files[int(sel[0])]
        if p not in self.queue_files:
            self.queue_files.append(p)
            self._refresh_queue_listbox()

    def queue_remove_selected(self):
        sel = list(self.queue_listbox.curselection())
        if not sel:
            return
        for i in sorted(sel, reverse=True):
            del self.queue_files[i]
        self._refresh_queue_listbox()

    def queue_move(self, delta: int):
        n = len(self.queue_files)
        if n == 0:
            return
        sel = list(self.queue_listbox.curselection())
        if not sel:
            return
        idxs = sel if delta < 0 else sel[::-1]
        new_sel = []
        for i in idxs:
            j = i + delta
            if 0 <= j < n:
                self.queue_files[i], self.queue_files[j] = self.queue_files[j], self.queue_files[i]
                new_sel.append(j)
            else:
                new_sel.append(i)
        self._refresh_queue_listbox()
        self.queue_listbox.selection_clear(0, tk.END)
        for j in new_sel:
            self.queue_listbox.selection_set(j)

    def queue_export_all(self):
        if not self.queue_files:
            messagebox.showinfo("Export Queue", "Queue is empty.")
            return
        total = 0
        ok = 0
        k = self._get_ds_factor()
        for f in self.queue_files:
            total += 1
            try:
                time_s, current_pa, info = load_and_process_abf(
                    f, apply_scaling=bool(self.apply_scaling_var.get())
                )
                t_ds, i_ds = self._downsample_xy(time_s, current_pa, k)
                out_dir = info.get("dir", os.path.dirname(f))
                base = info.get("file_base", Path(f).stem)
                out_path = os.path.join(out_dir, f"{base}.csv")
                pd.DataFrame({"time_s": t_ds, "current_pA": i_ds}).to_csv(out_path, index=False)
                ok += 1
            except Exception as e:
                print(f"[WARN] failed to export {os.path.basename(f)}: {e}")
        messagebox.showinfo("Export Queue", f"Exported {ok}/{total} file(s) to CSV.")


# --------------------------- Main ---------------------------
def main() -> None:
    app = AbfPhotocurrentViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
