# EChem_photocurrent_GUI.py
# -*- coding: utf-8 -*-
"""
EChem Photocurrent Pair Detection GUI (macOS-safe; no scroll-zoom)
- Folder-based .txt/.csv browser with left sidebar (fixed pixel width) and right preview.
- Windowed POS→NEG threshold detection for photocurrent analysis.
- Middle panel for peak review and manual export selection.
- Analysis window selection via "Use Current X" or interactive Span tool.
- Exports:
    * SVG (signal-only in current preview window; no axes/ticks/text/spines; tight bbox; transparent)
    * PNG (full preview with axes in current view; configurable DPI)
    * CSV (detected pairs with metadata; summary + individual pairs in subfolder)
- Individual pair exports use ±50ms window around POS peak.
- UI compact rows; Y-axis controls placed on a new line after X-axis inputs.
- No mouse wheel zoom; use Axis inputs, Segment, or toolbar buttons for navigation.

Dependencies: numpy, scipy, matplotlib, tkinter
    pip install numpy scipy matplotlib
"""

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
        "font.size": 10,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    }
)

from config import DEFAULT_START_DIR  # noqa: E402  (load from config.py)

# Initial pixel layout (fixed at start, still resizable overall)
SIDEBAR_W = 340  # px
MIDDLE_INIT_W = 260  # px (new middle panel)
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"

# Robust float pattern (handles scientific notation)
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


# --------------------------- Parsing helpers ---------------------------
def parse_time_I_mA(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    Parse two-column ASCII with header like: "time/s    I/mA"
    - Accepts whitespace or tab separation
    - Ignores non-numeric / comment lines
    Returns arrays (t_s, i_mA).
    """
    t_list, i_list = [], []
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
                i_val = float(nums[1])
            except ValueError:
                continue
            t_list.append(t_val)
            i_list.append(i_val)

    if not t_list:
        raise ValueError(f"No numeric data detected in: {path.name}")
    t = np.asarray(t_list, dtype=float)
    i = np.asarray(i_list, dtype=float)
    # Ensure strictly increasing time for downstream indexing
    if np.any(np.diff(t) <= 0):
        order = np.argsort(t)
        t = t[order]
        i = i[order]
    return t, i  # seconds, mA


def list_data_files(folder: Path) -> List[Path]:
    """Return sorted list of .txt and .csv files in folder (non-recursive)."""
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".txt", ".csv")]
    return sorted(files, key=lambda p: p.name.lower())


def safe_filename_token(val: float) -> str:
    """12.345678 -> '12p345678'; -0.5 -> 'm0p5' for filenames."""
    s = f"{val:.6f}"
    s = s.replace("-", "m").replace(".", "p")
    return s


# --------------------------- Detection (STRICT AND) ---------------------------
def detect_pairs_in_window(
    t: np.ndarray,
    i: np.ndarray,
    t0: float,
    t1: float,
    pos_min_mA: float,
    neg_min_abs_mA: float,
    min_delay_ms: float,
    max_delay_ms: float,
    min_pos_distance_ms: float = 0.2,
) -> List[Tuple[int, int]]:
    """
    STRICT AND logic:
      POS: peak height >= pos_min_mA (SciPy find_peaks with min spacing)
      NEG: argmin in [POS+min_delay_ms, POS+max_delay_ms] AND |neg| >= neg_min_abs_mA
    Returns global index pairs [(pos_idx, neg_idx), ...]
    """
    if t1 <= t0:
        return []

    s = int(np.searchsorted(t, t0, side="left"))
    e = int(np.searchsorted(t, t1, side="right"))
    s = max(0, s)
    e = min(len(t), e)
    if e - s < 3:
        return []

    tt, ii = t[s:e], i[s:e]
    dt = (
        float(np.median(np.diff(tt)))
        if len(tt) > 1
        else (float(np.median(np.diff(t))) if len(t) > 1 else 1e-3)
    )
    if dt <= 0:
        dt = 1e-3
    fs = 1.0 / dt

    distance = max(1, int((min_pos_distance_ms / 1000.0) * fs))
    pos_loc, props = find_peaks(ii, height=pos_min_mA, distance=distance)

    pairs: List[Tuple[int, int]] = []
    for ip in pos_loc:
        j0 = int(ip + max(1, int((min_delay_ms / 1000.0) * fs)))
        j1 = int(min(len(tt), ip + int((max_delay_ms / 1000.0) * fs)))
        if j1 - j0 < 2:
            continue
        neg_local = j0 + int(np.argmin(ii[j0:j1]))

        pos_val = float(ii[ip])
        neg_val = float(ii[neg_local])
        if (pos_val >= pos_min_mA) and (abs(neg_val) >= neg_min_abs_mA):
            pairs.append((s + ip, s + neg_local))

    return pairs


# --------------------------- GUI ---------------------------
class EchemPhotocurrentApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EChem — Photocurrent Analysis")
        self.geometry("1280x820")
        self.minsize(1000, 660)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files: List[Path] = []
        self.cur_file: Path | None = None
        self.t: np.ndarray | None = None  # seconds
        self.current_pa: np.ndarray | None = None  # mA
        self.png_dpi_var = tk.IntVar(value=300)

        # Analysis window
        self.win_t0: float | None = None
        self.win_t1: float | None = None

        # Axis vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")

        # Detection parameters
        self.pos_min_var = tk.DoubleVar(value=0.01)  # mA
        self.neg_min_var = tk.DoubleVar(value=0.01)  # mA (absolute)
        self.min_delay_ms_var = tk.DoubleVar(value=1.0)
        self.max_delay_ms_var = tk.DoubleVar(value=15.0)
        self.min_pos_dist_ms_var = tk.DoubleVar(value=200)

        # Downsampling
        self.no_ds_var = tk.BooleanVar(value=False)
        self.max_points_var = tk.IntVar(value=300000)

        # Detections (list of dicts {"pi": int, "ni": int, "selected": BooleanVar})
        self.pairs: List[Dict] = []

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
        """Left sidebar: folder, file list, detection params."""
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

        # ---------- Detection parameters ----------
        det_fr = ttk.LabelFrame(left, text="Detection Parameters", padding=5)
        det_fr.grid(row=2, column=0, sticky="ew", pady=(0, 5))

        ttk.Label(det_fr, text="POS min (mA):").grid(row=0, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.pos_min_var, width=10).grid(
            row=0, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="NEG min |abs| (mA):").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.neg_min_var, width=10).grid(
            row=1, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="Min delay (ms):").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.min_delay_ms_var, width=10).grid(
            row=2, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="Max delay (ms):").grid(row=3, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.max_delay_ms_var, width=10).grid(
            row=3, column=1, sticky="ew", pady=1
        )

        ttk.Label(det_fr, text="Min POS dist (ms):").grid(row=4, column=0, sticky="w", pady=1)
        ttk.Entry(det_fr, textvariable=self.min_pos_dist_ms_var, width=10).grid(
            row=4, column=1, sticky="ew", pady=1
        )

        ttk.Button(det_fr, text="Run Detection", command=self.run_detection).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=3
        )

        det_fr.grid_columnconfigure(1, weight=1)

        # ---------- Window controls ----------
        win_fr = ttk.LabelFrame(left, text="Analysis Window", padding=5)
        win_fr.grid(row=3, column=0, sticky="ew", pady=(0, 5))

        self.win_label = tk.StringVar(value="Not set")
        ttk.Label(win_fr, textvariable=self.win_label, anchor="w").grid(
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
        disp_fr.grid(row=4, column=0, sticky="ew", pady=(0, 5))

        ttk.Checkbutton(disp_fr, text="No downsampling (slow)", variable=self.no_ds_var).grid(
            row=0, column=0, sticky="w", pady=1
        )

        row2 = ttk.Frame(disp_fr)
        row2.grid(row=1, column=0, sticky="ew", pady=1)
        ttk.Label(row2, text="Max points:").pack(side=tk.LEFT, padx=(0, 3))
        ttk.Entry(row2, textvariable=self.max_points_var, width=10).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

        disp_fr.grid_columnconfigure(0, weight=1)

        # ---------- Export controls ----------
        export_fr = ttk.LabelFrame(left, text="Export", padding=5)
        export_fr.grid(row=5, column=0, sticky="ew")

        ttk.Button(export_fr, text="Export SVG (signal only)", command=self.export_svg_signal_only).grid(
            row=0, column=0, sticky="ew", pady=1
        )
        ttk.Button(export_fr, text="Export PNG (preview)", command=self.export_png_preview).grid(
            row=1, column=0, sticky="ew", pady=1
        )

        png_row = ttk.Frame(export_fr)
        png_row.grid(row=2, column=0, sticky="ew", pady=1)
        ttk.Label(png_row, text="PNG DPI:").pack(side=tk.LEFT, padx=(0, 3))
        ttk.Entry(png_row, textvariable=self.png_dpi_var, width=6).pack(side=tk.LEFT)

        ttk.Button(export_fr, text="Export Pairs CSV", command=self.export_pairs_csv).grid(
            row=3, column=0, sticky="ew", pady=1
        )

        export_fr.grid_columnconfigure(0, weight=1)

    def _build_middle(self):
        """Middle panel: detected peaks list with selection checkboxes."""
        middle = ttk.Frame(self, padding=5)
        middle.grid(row=0, column=1, rowspan=2, sticky="nsew")
        middle.grid_rowconfigure(1, weight=1)

        # ---------- Header ----------
        header_fr = ttk.LabelFrame(middle, text="Detected Peaks", padding=5)
        header_fr.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self.peaks_count_var = tk.StringVar(value="No peaks detected")
        ttk.Label(header_fr, textvariable=self.peaks_count_var).pack(side=tk.LEFT, padx=5)

        # ---------- Selection controls ----------
        btn_fr = ttk.Frame(header_fr)
        btn_fr.pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_fr, text="Select All", command=self.select_all_peaks, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_fr, text="Deselect All", command=self.deselect_all_peaks, width=10).pack(
            side=tk.LEFT, padx=2
        )

        # ---------- Scrollable peaks list ----------
        list_fr = ttk.LabelFrame(middle, text="Peak List (select to export)", padding=5)
        list_fr.grid(row=1, column=0, sticky="nsew")
        list_fr.grid_rowconfigure(0, weight=1)
        list_fr.grid_columnconfigure(0, weight=1)

        # Canvas with scrollbar
        canvas = tk.Canvas(list_fr, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_fr, orient="vertical", command=canvas.yview)
        self.peaks_frame = ttk.Frame(canvas)

        self.peaks_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.peaks_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Bind mousewheel to scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _build_top_controls(self):
        """Top control bar: axis inputs + segment."""
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
            self.t, self.current_pa = parse_time_I_mA(self.cur_file)
            self.status_var.set(f"Loaded: {self.cur_file.name} ({len(self.t)} points)")
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            self.t, self.current_pa = None, None
            self.status_var.set("Load failed")
            return

        self.pairs.clear()
        self._update_peaks_list()
        self.win_t0 = None
        self.win_t1 = None
        self._update_window_label()
        self._replot_full()

    # ---------- Window handling ----------
    def _update_window_label(self):
        if self.win_t0 is None or self.win_t1 is None:
            self.win_label.set("Not set")
        else:
            self.win_label.set(f"[{self.win_t0:.3f}, {self.win_t1:.3f}] s")

    def use_current_x(self):
        if self.t is None:
            return
        x0, x1 = self.ax.get_xlim()
        self.win_t0, self.win_t1 = float(x0), float(x1)
        self._update_window_label()
        self._replot_full()

    def enable_span_tool(self):
        if self.t is None:
            messagebox.showinfo("Info", "Load a file first.")
            return

        def on_span(xmin, xmax):
            self.win_t0, self.win_t1 = float(xmin), float(xmax)
            self._update_window_label()
            self.span_selector.set_active(False)
            self._replot_full()

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
        self._update_window_label()
        self._replot_full()

    # ---------- Detection ----------
    def run_detection(self):
        if self.t is None or self.current_pa is None:
            messagebox.showinfo("Info", "Load a file first.")
            return
        if self.win_t0 is None or self.win_t1 is None:
            messagebox.showinfo("Info", "Set an analysis window first.")
            return

        try:
            pairs_idx = detect_pairs_in_window(
                self.t,
                self.current_pa,
                float(self.win_t0),
                float(self.win_t1),
                float(self.pos_min_var.get()),
                float(self.neg_min_var.get()),
                float(self.min_delay_ms_var.get()),
                float(self.max_delay_ms_var.get()),
                float(self.min_pos_dist_ms_var.get()),
            )
        except Exception as e:
            messagebox.showerror("Detection error", str(e))
            return

        self.pairs = [
            {"pi": pi, "ni": ni, "selected": tk.BooleanVar(value=True)} for pi, ni in pairs_idx
        ]
        self._update_peaks_list()
        self.status_var.set(f"Detected {len(self.pairs)} pair(s)")
        self._replot_full()

    def _update_peaks_list(self):
        """Update the middle panel peaks list."""
        # Clear existing widgets
        for widget in self.peaks_frame.winfo_children():
            widget.destroy()

        if not self.pairs:
            self.peaks_count_var.set("No peaks detected")
            ttk.Label(
                self.peaks_frame, text="Run detection to see peaks here", foreground="gray"
            ).pack(pady=20)
            return

        self.peaks_count_var.set(f"Total: {len(self.pairs)} peaks")

        # Create header
        header = ttk.Frame(self.peaks_frame)
        header.pack(fill=tk.X, padx=5, pady=(5, 2))
        ttk.Label(header, text="✓", width=3, anchor="center", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="#", width=4, anchor="center", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(
            header, text="POS Time (s)", width=12, anchor="w", font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT)
        ttk.Label(header, text="POS I (mA)", width=12, anchor="w", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(header, text="NEG I (mA)", width=12, anchor="w", font=("Arial", 9, "bold")).pack(
            side=tk.LEFT
        )

        ttk.Separator(self.peaks_frame, orient="horizontal").pack(fill=tk.X, padx=5, pady=2)

        # Create rows for each peak
        for idx, pair in enumerate(self.pairs, start=1):
            pi, ni = pair["pi"], pair["ni"]
            selected_var = pair["selected"]

            row = ttk.Frame(self.peaks_frame)
            row.pack(fill=tk.X, padx=5, pady=1)

            # Checkbox
            cb = ttk.Checkbutton(row, variable=selected_var, command=self._replot_full)
            cb.pack(side=tk.LEFT, padx=(0, 5))

            # Peak number
            ttk.Label(row, text=f"{idx}", width=4, anchor="center").pack(side=tk.LEFT)

            # POS time
            pos_t = float(self.t[pi])
            ttk.Label(row, text=f"{pos_t:.6f}", width=12, anchor="w").pack(side=tk.LEFT)

            # POS current
            pos_i = float(self.current_pa[pi])
            ttk.Label(row, text=f"{pos_i:.6f}", width=12, anchor="w").pack(side=tk.LEFT)

            # NEG current
            neg_i = float(self.current_pa[ni])
            ttk.Label(row, text=f"{neg_i:.6f}", width=12, anchor="w").pack(side=tk.LEFT)

    def select_all_peaks(self):
        """Select all peaks for export."""
        for pair in self.pairs:
            pair["selected"].set(True)
        self._replot_full()

    def deselect_all_peaks(self):
        """Deselect all peaks."""
        for pair in self.pairs:
            pair["selected"].set(False)
        self._replot_full()

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

    def update_axis_inputs_from_view(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.xmin_var.set(f"{x0:.6g}")
        self.xmax_var.set(f"{x1:.6g}")
        self.ymin_var.set(f"{y0:.6g}")
        self.ymax_var.set(f"{y1:.6g}")

    def reset_axes(self):
        self._replot_full()

    # ---------- Plotting ----------
    def _replot_full(self):
        self.ax.clear()
        if self.t is None or self.current_pa is None:
            self.canvas.draw_idle()
            return

        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Current (mA)")
        self.ax.set_title(self.cur_file.name if self.cur_file else "")

        # Determine visible range
        x0, x1 = self.ax.get_xlim()
        if x0 == 0.0 and x1 == 1.0:
            x0, x1 = float(self.t[0]), float(self.t[-1])

        i0 = int(np.searchsorted(self.t, x0, side="left"))
        i1 = int(np.searchsorted(self.t, x1, side="right"))
        i0 = max(0, i0)
        i1 = min(len(self.t), i1)
        tt = self.t[i0:i1]
        ii = self.current_pa[i0:i1]

        # Window-based downsampling
        MAX_POINTS = max(1, int(self.max_points_var.get()))
        if (not self.no_ds_var.get()) and len(tt) > MAX_POINTS:
            step = int(np.ceil(len(tt) / MAX_POINTS))
            tt = tt[::step]
            ii = ii[::step]

        # Draw trace
        self.ax.plot(tt, ii, color=LINE_COLOR, lw=1.0)

        # Analysis window shading
        if self.win_t0 is not None and self.win_t1 is not None:
            self.ax.axvspan(self.win_t0, self.win_t1, alpha=0.12, color="gray")
            self.ax.axvline(self.win_t0, ls="--", lw=0.8, color="gray")
            self.ax.axvline(self.win_t1, ls="--", lw=0.8, color="gray")

        # POS markers with annotations (only for selected peaks)
        if self.pairs:
            selected_pairs = [p for p in self.pairs if p["selected"].get()]
            if selected_pairs:
                pos_t = [float(self.t[d["pi"]]) for d in selected_pairs]
                pos_i = [float(self.current_pa[d["pi"]]) for d in selected_pairs]
                self.ax.scatter(
                    pos_t, pos_i, s=50, marker="^", color="red", label="Selected POS", zorder=5
                )

                # Add annotations for selected peaks
                for orig_idx, pair in enumerate(self.pairs, start=1):
                    if pair["selected"].get():
                        pi = pair["pi"]
                        self.ax.annotate(
                            str(orig_idx),
                            xy=(self.t[pi], self.current_pa[pi]),
                            xytext=(3, 3),
                            textcoords="offset points",
                            fontsize=9,
                            color="red",
                        )
                self.ax.legend(loc="best", frameon=False)

        # Apply axis limits
        try:
            self.apply_x_limits()
            self.apply_y_limits()
        except Exception:
            pass

        self.canvas.draw_idle()

    # ---------- Exports ----------
    def _current_view_limits(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return x0, x1, y0, y1

    def _output_base(self):
        if self.cur_file is None:
            return os.getcwd(), "preview"
        out_dir = str(self.cur_file.parent)
        base = self.cur_file.stem
        return out_dir, base

    def export_svg_signal_only(self):
        """Export only the curve within current preview X-window as SVG (no axes/ticks/text)."""
        if self.t is None or self.current_pa is None:
            messagebox.showinfo("Export", "Load a file first.")
            return
        x0, x1, _, _ = self._current_view_limits()
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
            ax.plot(self.t[mask], self.current_pa[mask], color=LINE_COLOR, lw=1.0)

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

        self.status_var.set(f"Exported SVG (signal-only): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_png_preview(self):
        """Export the full preview (with axes/labels) as PNG for the CURRENT view window."""
        if self.t is None or self.current_pa is None:
            messagebox.showinfo("Export", "Load a file first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()

        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_preview.png")

        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(self.t, self.current_pa, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Current (mA)")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)

            # Add POS markers for selected peaks
            selected_pairs = [p for p in self.pairs if p["selected"].get()]
            if selected_pairs:
                pos_t = [
                    float(self.t[d["pi"]]) for d in selected_pairs if x0 <= self.t[d["pi"]] <= x1
                ]
                pos_i = [
                    float(self.current_pa[d["pi"]]) for d in selected_pairs if x0 <= self.t[d["pi"]] <= x1
                ]
                if pos_t:
                    ax.scatter(pos_t, pos_i, s=50, marker="^", color="red", zorder=5)

            dpi = int(self.png_dpi_var.get())
            FigureCanvasAgg(fig)
            fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="white")
        except Exception as e:
            messagebox.showerror("Export PNG", str(e))
            return

        self.status_var.set(f"Exported PNG (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_pairs_csv(self):
        """Export detected pairs: summary CSV + individual pair CSVs in subfolder (only selected pairs)."""
        if self.cur_file is None or self.t is None or self.current_pa is None:
            messagebox.showinfo("Info", "No data to export.")
            return

        # Get selected pairs only
        selected_pairs = [p for p in self.pairs if p["selected"].get()]
        if not selected_pairs:
            messagebox.showinfo(
                "Info", "No pairs selected for export. Select peaks in the middle panel."
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

        # Export summary CSV (only selected pairs)
        summary_path = output_folder / f"{base}_pairs_summary.csv"
        rows = []
        export_idx = 1
        for orig_idx, d in enumerate(self.pairs, start=1):
            if not d["selected"].get():
                continue
            pi, ni = d["pi"], d["ni"]
            tp, tn = float(self.t[pi]), float(self.t[ni])
            ip, ineg = float(self.current_pa[pi]), float(self.current_pa[ni])
            rows.append(
                [
                    export_idx,
                    orig_idx,
                    tp,
                    ip,
                    tn,
                    ineg,
                    (ip + np.abs(ineg)),
                    (tn - tp),
                    float(self.win_t0) if self.win_t0 is not None else np.nan,
                    float(self.win_t1) if self.win_t1 is not None else np.nan,
                    float(self.pos_min_var.get()),
                    float(self.neg_min_var.get()),
                ]
            )
            export_idx += 1

        header = (
            "export_index,original_index,POS_t_s,POS_I_mA,NEG_t_s,NEG_I_mA,Delta_I_mA,Delta_t_s,"
            "window_start_s,window_end_s,pos_min_mA,neg_min_abs_mA"
        )
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(header + "\n")
                for r in rows:
                    f.write(
                        ",".join(f"{v:.9g}" if isinstance(v, float) else str(v) for v in r) + "\n"
                    )
        except Exception as ex:
            messagebox.showerror("Write error", str(ex))
            return

        # Export individual pairs with ±50ms window around the POS peak (only selected)
        saved_count = 0
        window_ms = 50.0  # ±50ms window

        export_idx = 1
        for orig_idx, d in enumerate(self.pairs, start=1):
            if not d["selected"].get():
                continue

            pi = d["pi"]
            tp = float(self.t[pi])

            # Calculate time window: ±50ms around POS peak
            t_start = tp - (window_ms / 1000.0)
            t_end = tp + (window_ms / 1000.0)

            # Find indices within this window
            mask = (self.t >= t_start) & (self.t <= t_end)
            if not np.any(mask):
                export_idx += 1
                continue

            # Extract data
            t_segment = self.t[mask]
            i_segment = self.current_pa[mask]

            # Export to CSV (use export_idx for filename)
            pair_filename = f"{base}_pair_{export_idx:03d}.csv"
            pair_path = output_folder / pair_filename

            try:
                with open(pair_path, "w", encoding="utf-8") as f:
                    f.write("time_s,current_mA\n")
                    for t_val, i_val in zip(t_segment, i_segment):
                        f.write(f"{t_val:.9g},{i_val:.9g}\n")
                saved_count += 1
            except Exception as ex:
                messagebox.showwarning("Export Warning", f"Failed to save pair {export_idx}: {ex}")

            export_idx += 1

        self.status_var.set(f"Exported {saved_count} selected pairs to folder: {base}")
        messagebox.showinfo(
            "Export Complete",
            f"Saved summary and {saved_count} individual pairs to:\n{output_folder}",
        )


# --------------------------- Main ---------------------------
def main() -> None:
    app = EchemPhotocurrentApp()
    app.mainloop()


if __name__ == "__main__":
    main()
