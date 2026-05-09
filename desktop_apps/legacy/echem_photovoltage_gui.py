# EChem_photovoltage_GUI.py
# -*- coding: utf-8 -*-
"""
EChem Photovoltage Square-Pulse Detection GUI (single-file workflow)
- Single-file workflow for time/s and Ewe/V data (.txt/.csv)
- Detrend options: rolling median or Savitzky-Golay filter
- Detect positive/negative pulses in a chosen analysis window
- Middle panel for pulse review and manual export selection
- Export individual pulses with ±50ms windows to a folder named after the input file
- Analysis window selection via "Use Current X" or interactive Span tool
- Exports:
    * SVG (signal-only in current preview window; no axes/ticks/text/spines; tight bbox; transparent)
    * PNG (full preview with axes in current view; configurable DPI)
    * CSV (detected pulses with metadata; summary + individual pulses in subfolder)
- Layout: left sidebar, middle pulse list, top controls, right plot area

Dependencies: numpy, scipy, matplotlib, tkinter
    pip install numpy scipy matplotlib
"""

import math
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.widgets import SpanSelector
from scipy.signal import find_peaks, peak_widths, savgol_filter

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
MIDDLE_INIT_W = 260  # px (middle panel for pulse selection)
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"

# Robust float pattern (handles scientific notation)
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


# --------------------------- Parsing helpers ---------------------------
def parse_time_Ewe_V(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    Parse two-column ASCII with header like: 'time/s    Ewe/V'
    - Ignores blank lines and comment-like lines
    - Accepts whitespace or tabs, scientific notation
    Returns (t_s, e_V) as float arrays, sorted by time if needed.
    """
    t_list, e_list = [], []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            nums = FLOAT_RE.findall(line.replace(",", " "))
            if len(nums) < 2:
                continue
            try:
                t_val = float(nums[0])
                e_val = float(nums[1])
            except ValueError:
                continue
            t_list.append(t_val)
            e_list.append(e_val)
    if not t_list:
        raise ValueError(f"No numeric time/Ewe data found in: {path.name}")
    t = np.asarray(t_list, dtype=float)
    e = np.asarray(e_list, dtype=float)
    if np.any(np.diff(t) <= 0):
        order = np.argsort(t)
        t, e = t[order], e[order]
    return t, e


def list_data_files(folder: Path) -> List[Path]:
    """Return sorted list of .txt and .csv files in folder (non-recursive)."""
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".csv")]
    return sorted(files, key=lambda p: p.name.lower())


# --------------------------- Detrend utilities ---------------------------
def rolling_median(x: np.ndarray, win_pts: int) -> np.ndarray:
    """Compute rolling median baseline with given window size (forced odd)."""
    if win_pts <= 1:
        return np.zeros_like(x)
    win_pts = int(win_pts | 1)  # force odd
    pad = win_pts // 2
    xp = np.pad(x, pad, mode="edge")
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(xp[i : i + win_pts])
    return out


def detrend_signal(
    t: np.ndarray,
    e: np.ndarray,
    method: str = "median",
    window_ms: float = 50.0,
    sg_window_ms: float = 51.0,
    sg_poly: int = 3,
) -> np.ndarray:
    """
    Return e_detrended = e - baseline.
    Methods: 'median' (rolling median) or 'savgol' (Savitzky-Golay).
    """
    if len(t) < 3:
        return e - np.median(e)
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 1e-3
    win_pts = max(1, int(round((window_ms / 1000.0) / dt)))
    win_pts = win_pts if win_pts % 2 == 1 else win_pts + 1
    if method == "savgol":
        sg_pts = max(win_pts, int(round((sg_window_ms / 1000.0) / dt)))
        sg_pts = sg_pts if sg_pts % 2 == 1 else sg_pts + 1
        baseline = savgol_filter(e, window_length=max(5, sg_pts), polyorder=max(1, sg_poly))
    else:
        baseline = rolling_median(e, win_pts)
    return e - baseline


# --------------------------- Pulse detection ---------------------------
def detect_positive_pulses_in_window(
    t: np.ndarray,
    e_det: np.ndarray,
    t0: float,
    t1: float,
    peak_min_V: float,
    min_width_ms: float,
    min_spacing_ms: float,
) -> List[Dict[str, float]]:
    """
    Find positive pulses (indices and half-height widths) inside [t0, t1] on detrended signal.
    Returns list of dicts with {'idx': int, 't': float, 'amp_det_V': float, 'width_ms': float}
    """
    if t1 <= t0:
        return []
    s = int(np.searchsorted(t, t0, side="left"))
    e = int(np.searchsorted(t, t1, side="right"))
    s = max(0, s)
    e = min(len(t), e)
    if e - s < 3:
        return []

    tt = t[s:e]
    yy = e_det[s:e]
    dt = (
        float(np.median(np.diff(tt)))
        if len(tt) > 1
        else (float(np.median(np.diff(t))) if len(t) > 1 else 1e-3)
    )
    if dt <= 0:
        dt = 1e-3
    fs = 1.0 / dt
    distance = max(1, int((min_spacing_ms / 1000.0) * fs))

    locs, props = find_peaks(yy, height=peak_min_V, distance=distance)
    if len(locs) == 0:
        return []

    widths, _, _, _ = peak_widths(yy, locs, rel_height=0.5)
    min_width_pts = max(1, int((min_width_ms / 1000.0) * fs))

    out = []
    for i_loc, w in zip(locs, widths):
        if w >= min_width_pts:
            gi = s + int(i_loc)
            out.append(
                {
                    "idx": gi,
                    "t": float(t[gi]),
                    "amp_det_V": float(e_det[gi]),
                    "width_ms": float(1000.0 * w / fs),
                }
            )
    return out


def detect_negative_pulses_in_window(
    t: np.ndarray,
    e_det: np.ndarray,
    t0: float,
    t1: float,
    peak_min_V: float,
    min_width_ms: float,
    min_spacing_ms: float,
) -> List[Dict[str, float]]:
    """
    Detect NEGATIVE pulses by running positive-peak logic on (-e_det) within [t0, t1].
    Returns dicts with true (negative) amplitudes from e_det.
    """
    pos_like = detect_positive_pulses_in_window(
        t, -e_det, t0, t1, peak_min_V, min_width_ms, min_spacing_ms
    )
    # Replace amplitude with the original (negative) value from e_det
    for d in pos_like:
        d["amp_det_V"] = float(e_det[d["idx"]])
    return pos_like


# --------------------------- GUI ---------------------------
class EchemPhotovoltageApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EChem — Photovoltage Analysis")
        self.geometry("1280x820")
        self.minsize(1000, 660)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files: List[Path] = []
        self.cur_file: Optional[Path] = None
        self.t: Optional[np.ndarray] = None  # seconds
        self.e: Optional[np.ndarray] = None  # volts
        self.e_det: Optional[np.ndarray] = None
        self.png_dpi_var = tk.IntVar(value=300)

        # Axis vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")

        # Analysis window
        self.win_t0: Optional[float] = None
        self.win_t1: Optional[float] = None
        self.win_label_var = tk.StringVar(value="[not set]")

        # Detrend params
        self.bl_method_var = tk.StringVar(value="median")
        self.bl_win_ms_var = tk.DoubleVar(value=50.0)
        self.sg_win_ms_var = tk.DoubleVar(value=51.0)
        self.sg_poly_var = tk.IntVar(value=3)

        # Detection params
        self.polarity_var = tk.StringVar(value="positive")
        self.peak_min_var = tk.DoubleVar(value=0.01)  # V
        self.min_width_ms_var = tk.DoubleVar(value=5.0)
        self.min_spacing_ms_var = tk.DoubleVar(value=10.0)

        # Display options
        self.show_detrended_var = tk.BooleanVar(value=False)
        self.no_ds_var = tk.BooleanVar(value=False)
        self.max_points_var = tk.IntVar(value=300000)

        # Detected pulses (list of dicts with "idx", "t", "amp_det_V", "width_ms", "selected")
        self.pulses: List[Dict] = []

        # SpanSelector
        self.span_selector: Optional[SpanSelector] = None

        # Build layout
        self._build_layout()
        self._build_left()
        self._build_middle()
        self._build_top_controls()
        self._build_plot()

        # Initial scan & preload
        self.scan_folder()
        if self.files:
            self.file_listbox.selection_set(0)
            self.load_selected_file()

    # ---------- Layout (grid with fixed initial sidebar width) ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self.grid_columnconfigure(1, minsize=MIDDLE_INIT_W, weight=0)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

    def _build_left(self):
        """Left sidebar: folder, file list, detrend params, detection params, window controls."""
        left = ttk.Frame(self, padding=5)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew")
        left.grid_rowconfigure(1, weight=1)

        # ---------- Folder ----------
        folder_fr = ttk.LabelFrame(left, text="Folder", padding=5)
        folder_fr.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        folder_fr.grid_columnconfigure(0, weight=1)

        entry = ttk.Entry(folder_fr, textvariable=self.folder_path, width=30)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        btn = ttk.Button(folder_fr, text="Browse…", command=self.browse_folder, width=7)
        btn.grid(row=0, column=1)

        # ---------- File list ----------
        files_fr = ttk.LabelFrame(left, text="Files", padding=5)
        files_fr.grid(row=1, column=0, sticky="nsew", pady=(0, 5))
        files_fr.grid_rowconfigure(0, weight=1)
        files_fr.grid_columnconfigure(0, weight=1)

        scroll_y = ttk.Scrollbar(files_fr, orient=tk.VERTICAL)
        scroll_y.grid(row=0, column=1, sticky="ns")

        self.file_listbox = tk.Listbox(
            files_fr, yscrollcommand=scroll_y.set, selectmode=tk.SINGLE, height=10
        )
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        scroll_y.config(command=self.file_listbox.yview)
        self.file_listbox.bind("<<ListboxSelect>>", lambda e: self.load_selected_file())

        # ---------- Detrend parameters ----------
        det_fr = ttk.LabelFrame(left, text="Detrend Parameters", padding=5)
        det_fr.grid(row=2, column=0, sticky="ew", pady=(0, 5))

        ttk.Label(det_fr, text="Method:").grid(row=0, column=0, sticky="w", pady=1)
        method_cb = ttk.Combobox(
            det_fr,
            textvariable=self.bl_method_var,
            values=["median", "savgol"],
            state="readonly",
            width=10,
        )
        method_cb.grid(row=0, column=1, sticky="ew", pady=1)

        ttk.Label(det_fr, text="Window (ms):").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.bl_win_ms_var, width=10).grid(
            row=1, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="SG window (ms):").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.sg_win_ms_var, width=10).grid(
            row=2, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="SG poly:").grid(row=3, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.sg_poly_var, width=10).grid(
            row=3, column=1, sticky="ew", pady=1
        )

        ttk.Button(det_fr, text="Apply Detrend", command=self.apply_detrend).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=3
        )

        det_fr.grid_columnconfigure(1, weight=1)

        # ---------- Detection parameters ----------
        pulse_fr = ttk.LabelFrame(left, text="Detection Parameters", padding=5)
        pulse_fr.grid(row=3, column=0, sticky="ew", pady=(0, 5))

        ttk.Label(pulse_fr, text="Polarity:").grid(row=0, column=0, sticky="w", pady=1)
        pol_cb = ttk.Combobox(
            pulse_fr,
            textvariable=self.polarity_var,
            values=["positive", "negative"],
            state="readonly",
            width=10,
        )
        pol_cb.grid(row=0, column=1, sticky="ew", pady=1)

        ttk.Label(pulse_fr, text="Peak min (V):").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Entry(pulse_fr, textvariable=self.peak_min_var, width=10).grid(
            row=1, column=1, sticky="ew", pady=1
        )

        ttk.Label(pulse_fr, text="Min width (ms):").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Entry(pulse_fr, textvariable=self.min_width_ms_var, width=10).grid(
            row=2, column=1, sticky="ew", pady=1
        )

        ttk.Label(pulse_fr, text="Min spacing (ms):").grid(row=3, column=0, sticky="w", pady=1)
        ttk.Entry(pulse_fr, textvariable=self.min_spacing_ms_var, width=10).grid(
            row=3, column=1, sticky="ew", pady=1
        )

        ttk.Button(pulse_fr, text="Run Detection", command=self.run_detection).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=3
        )

        pulse_fr.grid_columnconfigure(1, weight=1)

        # ---------- Window controls ----------
        win_fr = ttk.LabelFrame(left, text="Analysis Window", padding=5)
        win_fr.grid(row=4, column=0, sticky="ew", pady=(0, 5))

        ttk.Label(win_fr, textvariable=self.win_label_var, anchor="w").grid(
            row=0, column=0, sticky="ew", pady=1
        )

        ttk.Button(win_fr, text="Use Current X", command=self.use_current_x).grid(
            row=1, column=0, sticky="ew", pady=1
        )
        ttk.Button(win_fr, text="Enable Span Tool", command=self.enable_span_tool).grid(
            row=2, column=0, sticky="ew", pady=1
        )
        ttk.Button(win_fr, text="Clear", command=self.clear_window).grid(
            row=3, column=0, sticky="ew", pady=1
        )

        win_fr.grid_columnconfigure(0, weight=1)

        # ---------- Display options ----------
        disp_fr = ttk.LabelFrame(left, text="Display Options", padding=5)
        disp_fr.grid(row=5, column=0, sticky="ew", pady=(0, 5))

        ttk.Checkbutton(
            disp_fr, text="Show detrended", variable=self.show_detrended_var, command=self.redraw
        ).grid(row=0, column=0, sticky="w", pady=1)
        ttk.Checkbutton(
            disp_fr, text="No downsampling (slow)", variable=self.no_ds_var, command=self.redraw
        ).grid(row=1, column=0, sticky="w", pady=1)

        row2 = ttk.Frame(disp_fr)
        row2.grid(row=2, column=0, sticky="ew", pady=1)
        ttk.Label(row2, text="Max points:").pack(side=tk.LEFT, padx=(0, 3))
        ttk.Entry(row2, textvariable=self.max_points_var, width=10).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

        disp_fr.grid_columnconfigure(0, weight=1)

        # ---------- Export controls ----------
        export_fr = ttk.LabelFrame(left, text="Export", padding=5)
        export_fr.grid(row=6, column=0, sticky="ew")

        ttk.Button(export_fr, text="Export SVG (signal only)", command=self.save_svg_signal_only).grid(
            row=0, column=0, sticky="ew", pady=1
        )
        ttk.Button(export_fr, text="Export PNG (preview)", command=self.save_png).grid(
            row=1, column=0, sticky="ew", pady=1
        )

        png_row = ttk.Frame(export_fr)
        png_row.grid(row=2, column=0, sticky="ew", pady=1)
        ttk.Label(png_row, text="PNG DPI:").pack(side=tk.LEFT, padx=(0, 3))
        ttk.Entry(png_row, textvariable=self.png_dpi_var, width=6).pack(side=tk.LEFT)

        ttk.Button(export_fr, text="Export Pulses CSV", command=self.export_pulses_csv).grid(
            row=3, column=0, sticky="ew", pady=1
        )

        export_fr.grid_columnconfigure(0, weight=1)

    def _build_middle(self):
        """Middle panel: detected pulses list with selection checkboxes."""
        middle = ttk.Frame(self, padding=5)
        middle.grid(row=0, column=1, rowspan=2, sticky="nsew")
        middle.grid_rowconfigure(1, weight=1)

        # ---------- Header ----------
        header_fr = ttk.LabelFrame(middle, text="Detected Pulses", padding=5)
        header_fr.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self.pulses_count_var = tk.StringVar(value="No pulses detected")
        ttk.Label(header_fr, textvariable=self.pulses_count_var).pack(side=tk.LEFT, padx=5)

        # ---------- Selection controls ----------
        btn_fr = ttk.Frame(header_fr)
        btn_fr.pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_fr, text="Select All", command=self.select_all_pulses, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_fr, text="Deselect All", command=self.deselect_all_pulses, width=10).pack(
            side=tk.LEFT, padx=2
        )

        # ---------- Scrollable pulses list ----------
        list_fr = ttk.LabelFrame(middle, text="Pulse List (select to export)", padding=5)
        list_fr.grid(row=1, column=0, sticky="nsew")
        list_fr.grid_rowconfigure(0, weight=1)
        list_fr.grid_columnconfigure(0, weight=1)

        # Canvas with scrollbar
        canvas = tk.Canvas(list_fr, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_fr, orient="vertical", command=canvas.yview)
        self.pulses_frame = ttk.Frame(canvas)

        self.pulses_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.pulses_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Bind mousewheel to scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _build_top_controls(self):
        """Top control bar: axis inputs."""
        top = ttk.Frame(self, padding=3)
        top.grid(row=0, column=2, sticky="ew")

        # Row 1: X-axis inputs
        row1 = ttk.Frame(top)
        row1.pack(side=tk.TOP, fill=tk.X, pady=1)

        ttk.Label(row1, text="X min:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(row1, textvariable=self.xmin_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Label(row1, text="X max:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(row1, textvariable=self.xmax_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Apply X", command=self.apply_x_limits).pack(side=tk.LEFT, padx=2)

        # Row 2: Y-axis inputs
        row2 = ttk.Frame(top)
        row2.pack(side=tk.TOP, fill=tk.X, pady=1)

        ttk.Label(row2, text="Y min:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(row2, textvariable=self.ymin_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Label(row2, text="Y max:").pack(side=tk.LEFT, padx=2)
        ttk.Entry(row2, textvariable=self.ymax_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Apply Y", command=self.apply_y_limits).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Reset", command=self.reset_axes).pack(side=tk.LEFT, padx=5)

    def _build_plot(self):
        """Right plot area."""
        plot_fr = ttk.Frame(self, padding=(8, 6, 8, 8))
        plot_fr.grid(row=1, column=2, sticky="nsew")
        plot_fr.grid_rowconfigure(1, weight=1)
        plot_fr.grid_columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(plot_fr, textvariable=self.status_var, foreground="#444").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )

        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_fr)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        # Toolbar
        toolbar_fr = ttk.Frame(plot_fr)
        toolbar_fr.grid(row=2, column=0, sticky="ew")
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_fr)
        self.toolbar.update()

        # Disable scroll zoom
        self.canvas.mpl_connect("scroll_event", lambda event: None)

    # ---------- Folder & file handling ----------
    def browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_path.get() or DEFAULT_START_DIR)
        if d:
            self.folder_path.set(d)
            self.scan_folder()

    def scan_folder(self):
        p = Path(self.folder_path.get())
        self.files = list_data_files(p)
        self.file_listbox.delete(0, tk.END)
        for f in self.files:
            self.file_listbox.insert(tk.END, f.name)

    def load_selected_file(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        self.cur_file = self.files[idx]
        try:
            self.t, self.e = parse_time_Ewe_V(self.cur_file)
            self.status_var.set(f"Loaded: {self.cur_file.name} ({len(self.t)} points)")
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            self.t, self.e = None, None
            self.status_var.set("Load failed")
            return

        self.e_det = None
        self.pulses.clear()
        self._update_pulses_list()
        self.win_t0 = None
        self.win_t1 = None
        self.win_label_var.set("[not set]")
        self.redraw()

    # ---------- Window handling ----------
    def use_current_x(self):
        if self.t is None:
            return
        x0, x1 = self.ax.get_xlim()
        self.win_t0, self.win_t1 = float(x0), float(x1)
        self.win_label_var.set(f"[{self.win_t0:.3f}, {self.win_t1:.3f}] s")
        self.redraw()

    def enable_span_tool(self):
        if self.t is None:
            messagebox.showinfo("Info", "Load a file first.")
            return

        def on_span(xmin, xmax):
            self.win_t0, self.win_t1 = float(xmin), float(xmax)
            self.win_label_var.set(f"[{self.win_t0:.3f}, {self.win_t1:.3f}] s")
            self.span_selector.set_active(False)
            self.redraw()

        self.span_selector = SpanSelector(
            self.ax,
            on_span,
            "horizontal",
            useblit=True,
            props=dict(alpha=0.3, facecolor="green"),
            interactive=True,
            drag_from_anywhere=True,
        )
        self.span_selector.set_active(True)
        messagebox.showinfo("Span Tool", "Drag on the plot to select the analysis window.")

    def clear_window(self):
        self.win_t0 = None
        self.win_t1 = None
        self.win_label_var.set("[not set]")
        self.redraw()

    # ---------- Detrend ----------
    def apply_detrend(self):
        if self.t is None or self.e is None:
            messagebox.showinfo("Info", "Load a file first.")
            return
        try:
            self.e_det = detrend_signal(
                self.t,
                self.e,
                method=self.bl_method_var.get(),
                window_ms=float(self.bl_win_ms_var.get()),
                sg_window_ms=float(self.sg_win_ms_var.get()),
                sg_poly=int(self.sg_poly_var.get()),
            )
            self.status_var.set("Detrend applied")
            self.redraw()
        except Exception as e:
            messagebox.showerror("Detrend error", str(e))

    # ---------- Detection ----------
    def run_detection(self):
        if self.t is None or self.e is None:
            messagebox.showinfo("Info", "Load a file first.")
            return
        if self.e_det is None:
            messagebox.showinfo("Info", "Apply detrend first.")
            return
        if self.win_t0 is None or self.win_t1 is None:
            messagebox.showinfo("Info", "Set an analysis window first.")
            return

        try:
            if self.polarity_var.get() == "positive":
                pulses_raw = detect_positive_pulses_in_window(
                    self.t,
                    self.e_det,
                    float(self.win_t0),
                    float(self.win_t1),
                    float(self.peak_min_var.get()),
                    float(self.min_width_ms_var.get()),
                    float(self.min_spacing_ms_var.get()),
                )
            else:
                pulses_raw = detect_negative_pulses_in_window(
                    self.t,
                    self.e_det,
                    float(self.win_t0),
                    float(self.win_t1),
                    float(self.peak_min_var.get()),
                    float(self.min_width_ms_var.get()),
                    float(self.min_spacing_ms_var.get()),
                )
            # Add selected state to each pulse
            self.pulses = []
            for p in pulses_raw:
                pulse_dict = dict(p)  # Copy the dict
                pulse_dict["selected"] = tk.BooleanVar(value=True)
                self.pulses.append(pulse_dict)

            self._update_pulses_list()
            self.status_var.set(f"Detected {len(self.pulses)} pulse(s)")
            self.redraw()
        except Exception as e:
            messagebox.showerror("Detection error", str(e))

    def _update_pulses_list(self):
        """Update the middle panel pulses list."""
        # Clear existing widgets
        for widget in self.pulses_frame.winfo_children():
            widget.destroy()

        if not self.pulses:
            self.pulses_count_var.set("No pulses detected")
            ttk.Label(
                self.pulses_frame, text="Run detection to see pulses here", foreground="gray"
            ).pack(pady=20)
            return

        self.pulses_count_var.set(f"Total: {len(self.pulses)} pulses")

        # Create header
        header = ttk.Frame(self.pulses_frame)
        header.pack(fill=tk.X, padx=5, pady=(5, 2))
        ttk.Label(header, text="✓", width=3, anchor="center", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="#", width=4, anchor="center", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="Time (s)", width=12, anchor="w", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="Amp (V)", width=12, anchor="w", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="Width (ms)", width=12, anchor="w", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )

        ttk.Separator(self.pulses_frame, orient="horizontal").pack(fill=tk.X, padx=5, pady=2)

        # Create rows for each pulse
        for idx, pulse in enumerate(self.pulses, start=1):
            selected_var = pulse["selected"]

            row = ttk.Frame(self.pulses_frame)
            row.pack(fill=tk.X, padx=5, pady=1)

            # Checkbox
            cb = ttk.Checkbutton(row, variable=selected_var, command=self.redraw)
            cb.pack(side=tk.LEFT, padx=(0, 5))

            # Pulse number
            ttk.Label(row, text=f"{idx}", width=4, anchor="center").pack(side=tk.LEFT)

            # Time
            t_val = float(pulse["t"])
            ttk.Label(row, text=f"{t_val:.6f}", width=12, anchor="w").pack(side=tk.LEFT)

            # Amplitude
            amp_val = float(pulse["amp_det_V"])
            ttk.Label(row, text=f"{amp_val:.6f}", width=12, anchor="w").pack(side=tk.LEFT)

            # Width
            width_val = float(pulse["width_ms"])
            ttk.Label(row, text=f"{width_val:.3f}", width=12, anchor="w").pack(side=tk.LEFT)

    def select_all_pulses(self):
        """Select all pulses for export."""
        for pulse in self.pulses:
            pulse["selected"].set(True)
        self.redraw()

    def deselect_all_pulses(self):
        """Deselect all pulses."""
        for pulse in self.pulses:
            pulse["selected"].set(False)
        self.redraw()

    # ---------- Axis controls ----------
    def apply_x_limits(self):
        try:
            xmin = float(self.xmin_var.get())
            xmax = float(self.xmax_var.get())
        except ValueError:
            return
        if xmin == xmax:
            return
        if xmin > xmax:
            xmin, xmax = xmax, xmin
        self.ax.set_xlim(xmin, xmax)
        self.canvas.draw_idle()

    def apply_y_limits(self):
        try:
            ymin = float(self.ymin_var.get())
            ymax = float(self.ymax_var.get())
        except ValueError:
            return
        if ymin == ymax:
            return
        if ymin > ymax:
            ymin, ymax = ymax, ymin
        self.ax.set_ylim(ymin, ymax)
        self.canvas.draw_idle()

    def reset_axes(self):
        self.redraw()

    # ---------- Plotting ----------
    def redraw(self):
        self.ax.clear()
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Voltage (V)")
        if self.t is None or self.e is None:
            self.canvas.draw_idle()
            return

        # Current X window
        try:
            x0 = float(self.xmin_var.get())
            x1 = float(self.xmax_var.get())
            if x1 <= x0:
                raise ValueError
        except Exception:
            x0, x1 = float(self.t[0]), float(self.t[-1])

        i0 = int(np.searchsorted(self.t, x0, side="left"))
        i1 = int(np.searchsorted(self.t, x1, side="right"))
        i0 = max(0, i0)
        i1 = min(len(self.t), i1)
        y = self.e_det if (self.show_detrended_var.get() and self.e_det is not None) else self.e
        tt, yy = self.t[i0:i1], y[i0:i1]

        # Downsampling
        MAX_POINTS = max(1, int(self.max_points_var.get()))
        if (not self.no_ds_var.get()) and len(tt) > MAX_POINTS:
            step = int(np.ceil(len(tt) / MAX_POINTS))
            tt = tt[::step]
            yy = yy[::step]

        # Draw
        self.ax.plot(tt, yy, color=LINE_COLOR, lw=1.0)

        # Window shading
        if self.win_t0 is not None and self.win_t1 is not None:
            self.ax.axvspan(self.win_t0, self.win_t1, alpha=0.12, color="gray")
            self.ax.axvline(self.win_t0, ls="--", lw=0.8, color="gray")
            self.ax.axvline(self.win_t1, ls="--", lw=0.8, color="gray")

        # Markers (only for selected pulses)
        if self.pulses:
            selected_pulses = [d for d in self.pulses if d["selected"].get()]
            if selected_pulses:
                pos_t = [d["t"] for d in selected_pulses if x0 <= d["t"] <= x1]
                pos_v = []
                for d in selected_pulses:
                    if x0 <= d["t"] <= x1:
                        pos_v.append(
                            self.e_det[d["idx"]] if self.e_det is not None else self.e[d["idx"]]
                        )
                if pos_t:
                    self.ax.scatter(
                        pos_t,
                        pos_v,
                        s=50,
                        marker="^",
                        color="red",
                        label="Selected Pulses",
                        zorder=5,
                    )

                    # Add annotations for selected pulses
                    for orig_idx, pulse in enumerate(self.pulses, start=1):
                        if pulse["selected"].get() and x0 <= pulse["t"] <= x1:
                            idx = pulse["idx"]
                            self.ax.annotate(
                                str(orig_idx),
                                xy=(
                                    self.t[idx],
                                    self.e_det[idx] if self.e_det is not None else self.e[idx],
                                ),
                                xytext=(3, 3),
                                textcoords="offset points",
                                fontsize=9,
                                color="red",
                            )
                    self.ax.legend(frameon=False)

        # Apply user limits
        try:
            self.apply_x_limits()
            self.apply_y_limits()
        except Exception:
            pass

        self.canvas.draw_idle()

    # ---------- Exports ----------
    def _output_base(self):
        if self.cur_file is None:
            return os.getcwd(), "preview"
        out_dir = str(self.cur_file.parent)
        base = self.cur_file.stem
        return out_dir, base

    def export_pulses_csv(self):
        """Export summary CSV and individual pulse segments with ±50ms windows (only selected pulses)."""
        if self.cur_file is None or self.t is None or self.e is None:
            messagebox.showinfo("Info", "No data to export.")
            return

        # Get selected pulses only
        selected_pulses = [p for p in self.pulses if p["selected"].get()]
        if not selected_pulses:
            messagebox.showinfo(
                "Info", "No pulses selected for export. Select pulses in the middle panel."
            )
            return

        out_dir, base = self._output_base()

        # Create output folder with the same name as the analyzed file
        output_folder = Path(out_dir) / base
        try:
            output_folder.mkdir(parents=True, exist_ok=True)
        except Exception as ex:
            messagebox.showerror(
                "Folder Creation Error", f"Cannot create folder:\n{output_folder}\n{ex}"
            )
            return

        # Export summary CSV (only selected pulses)
        summary_path = output_folder / f"{base}_pulses_summary.csv"
        rows = []
        export_idx = 1
        for orig_idx, d in enumerate(self.pulses, start=1):
            if not d["selected"].get():
                continue
            gi = d["idx"]
            rows.append(
                [
                    export_idx,
                    orig_idx,
                    d["t"],
                    float(self.e[gi]),
                    float(self.e_det[gi]) if self.e_det is not None else math.nan,
                    d["width_ms"],
                    float(self.win_t0) if self.win_t0 is not None else math.nan,
                    float(self.win_t1) if self.win_t1 is not None else math.nan,
                    float(self.peak_min_var.get()),
                    float(self.min_width_ms_var.get()),
                    float(self.min_spacing_ms_var.get()),
                    self.bl_method_var.get(),
                    float(self.bl_win_ms_var.get()),
                    float(self.sg_win_ms_var.get()),
                    int(self.sg_poly_var.get()),
                ]
            )
            export_idx += 1

        header = (
            "export_index,original_index,peak_t_s,peak_V_raw,peak_V_detrended,halfwidth_ms,"
            "window_start_s,window_end_s,peak_min_V,min_width_ms,min_spacing_ms,"
            "baseline_method,baseline_win_ms,sg_window_ms,sg_poly"
        )
        try:
            with summary_path.open("w", encoding="utf-8") as f:
                f.write(header + "\n")
                for r in rows:
                    f.write(
                        ",".join(f"{v:.9g}" if isinstance(v, (float, int)) else str(v) for v in r)
                        + "\n"
                    )
        except Exception as ex:
            messagebox.showerror("Write error", str(ex))
            return

        # Export individual pulses with ±50ms window around the peak (only selected)
        saved_count = 0
        window_ms = 50.0  # ±50ms window

        export_idx = 1
        for orig_idx, d in enumerate(self.pulses, start=1):
            if not d["selected"].get():
                continue

            tp = d["t"]

            # Calculate time window: ±50ms around peak
            t_start = tp - (window_ms / 1000.0)
            t_end = tp + (window_ms / 1000.0)

            # Find indices within this window
            mask = (self.t >= t_start) & (self.t <= t_end)
            if not np.any(mask):
                export_idx += 1
                continue

            # Extract data (use detrended if available)
            t_segment = self.t[mask]
            y = self.e_det if self.e_det is not None else self.e
            v_segment = y[mask]

            # Export to CSV (use export_idx for filename)
            pulse_filename = f"{base}_pulse_{export_idx:03d}.csv"
            pulse_path = output_folder / pulse_filename

            try:
                with pulse_path.open("w", encoding="utf-8") as f:
                    f.write("time_s,voltage_V\n")
                    for t_val, v_val in zip(t_segment, v_segment):
                        f.write(f"{t_val:.9g},{v_val:.9g}\n")
                saved_count += 1
            except Exception as ex:
                messagebox.showwarning("Export Warning", f"Failed to save pulse {export_idx}: {ex}")

            export_idx += 1

        self.status_var.set(f"Exported {saved_count} selected pulses to folder: {base}")
        messagebox.showinfo(
            "Export Complete",
            f"Saved summary and {saved_count} individual pulses to:\n{output_folder}",
        )

    def save_png(self):
        """Export the current figure as PNG with selected pulses marked."""
        if self.t is None or self.e is None:
            messagebox.showinfo("Info", "Load a file first.")
            return

        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_preview.png")

        try:
            # Get current view limits
            try:
                x0 = float(self.xmin_var.get())
                x1 = float(self.xmax_var.get())
                if x1 <= x0:
                    raise ValueError
            except Exception:
                x0, x1 = float(self.t[0]), float(self.t[-1])

            y0, y1 = self.ax.get_ylim()

            # Create new figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)

            # Plot data
            y = self.e_det if (self.show_detrended_var.get() and self.e_det is not None) else self.e
            ax.plot(self.t, y, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Voltage (V)")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)

            # Add markers for selected pulses
            selected_pulses = [p for p in self.pulses if p["selected"].get()]
            if selected_pulses:
                pos_t = [d["t"] for d in selected_pulses if x0 <= d["t"] <= x1]
                pos_v = []
                for d in selected_pulses:
                    if x0 <= d["t"] <= x1:
                        idx = d["idx"]
                        pos_v.append(self.e_det[idx] if self.e_det is not None else self.e[idx])
                if pos_t:
                    ax.scatter(pos_t, pos_v, s=50, marker="^", color="red", zorder=5)

            dpi = int(self.png_dpi_var.get())
            FigureCanvasAgg(fig)
            fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="white")

            self.status_var.set(f"Saved figure: {base}_preview.png")
            messagebox.showinfo("Export", f"Saved:\n{out_path}")
        except Exception as ex:
            messagebox.showerror("Save error", str(ex))

    def save_svg_signal_only(self):
        """Export only the curve within current preview X-window as SVG (no axes/ticks/text)."""
        if self.t is None or self.e is None:
            messagebox.showinfo("Export", "Load a file first.")
            return
        try:
            x0 = float(self.xmin_var.get())
            x1 = float(self.xmax_var.get())
        except Exception:
            x0, x1 = float(self.t[0]), float(self.t[-1])
        mask = (self.t >= x0) & (self.t <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return

        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_preview_signal.svg")

        try:
            from matplotlib.backends.backend_svg import FigureCanvasSVG
            from matplotlib.figure import Figure

            fig = Figure(figsize=(8, 3), dpi=100)
            ax = fig.add_subplot(111)
            y = self.e_det if (self.show_detrended_var.get() and self.e_det is not None) else self.e
            ax.plot(self.t[mask], y[mask], lw=1.0, color=LINE_COLOR)

            # Preserve current y-limits
            y0, y1 = self.ax.get_ylim()
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)

            # Remove everything except the path
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
        except Exception as e:
            messagebox.showerror("Export SVG", str(e))
            return

        self.status_var.set(f"Exported SVG (signal-only): {base}_preview_signal.svg")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")


# --------------------------- Main ---------------------------
def main() -> None:
    app = EchemPhotovoltageApp()
    app.mainloop()


if __name__ == "__main__":
    main()
