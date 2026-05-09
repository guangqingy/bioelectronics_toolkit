# CSV_preview_GUI.py
# -*- coding: utf-8 -*-
"""
CSV Folder Viewer (macOS-safe; no scroll-zoom)
- Folder-based .csv browser with left sidebar (fixed pixel width) and right preview.
- Display a line plot of chosen X/Y columns (combobox selection).
- NEW (lower-left): Merge Queue
    * Add/remove/reorder files from the upper list into a queue
    * Plot Merge (overlay all queued series with current X/Y)
    * Export Merge (preview window; concatenated CSV without source column; drop the first data row of each subsequent file)
- Exports:
    * SVG (signal-only, current preview window; no axes/ticks/text/spines; tight bbox; transparent)
    * PNG (full preview figure with axes; current preview window; configurable DPI)
    * CSV (preview window only; chosen X/Y columns)
    * CSV (full file; saved as a simple copy with _copy.csv suffix)
- UI compact rows; Y-axis controls placed on a new line after X-axis inputs.
- No mouse wheel zoom; use Axis inputs, Segment, or toolbar buttons for navigation.

Dependencies: numpy, pandas, matplotlib, tkinter (built-in)
    pip install numpy pandas matplotlib
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
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

# Initial pixel layout (fixed at start, still resizable overall)
SIDEBAR_W = 340  # px
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"


# --------------------------- CSV helpers ---------------------------
def _list_csv_files(folder: str):
    """Return sorted list of CSV file paths under folder (non-recursive)."""
    try:
        names = sorted(f for f in os.listdir(folder) if f.lower().endswith(".csv"))
        return [os.path.join(folder, n) for n in names]
    except Exception:
        return []


def _numeric_columns(df: pd.DataFrame):
    """
    Return a list of column names that can be reasonably plotted:
    - Prefer numeric dtype; also allow columns that can be coerced to numeric (some NaNs ok).
    We don't modify the DataFrame; coercion happens on the fly when plotting.
    """
    numeric_cols = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            numeric_cols.append(c)
        else:
            coerced = pd.to_numeric(s, errors="coerce")
            valid_ratio = np.isfinite(coerced).sum() / max(1, len(coerced))
            if valid_ratio >= 0.5:
                numeric_cols.append(c)
    return numeric_cols


def _coerce_xy(df: pd.DataFrame, xname: str, yname: str):
    """Coerce the requested columns to numeric arrays and return (x, y) with NaNs removed."""
    x = pd.to_numeric(df[xname], errors="coerce").to_numpy()
    y = pd.to_numeric(df[yname], errors="coerce").to_numpy()
    m = np.isfinite(x) & np.isfinite(y)
    if not np.any(m):
        return None, None
    return x[m], y[m]


# --------------------------- GUI ---------------------------
class CsvFolderViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CSV Folder Viewer")
        self.geometry("1280x820")
        self.minsize(980, 640)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files = []  # list of CSV paths (upper list)
        self.merge_queue = []  # list of CSV paths (lower list)
        self.df = None  # loaded DataFrame (current single-file view)
        self.cur_path = None
        self.png_dpi_var = tk.IntVar(value=300)

        # Axis/segment vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")
        self.seg_start_var = tk.StringVar(value="")
        self.seg_end_var = tk.StringVar(value="")

        # Column selectors
        self.x_col_var = tk.StringVar(value="")
        self.y_col_var = tk.StringVar(value="")

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

    # ---------- Layout (grid with fixed initial sidebar width) ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, minsize=SIDEBAR_W, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, minsize=PLOT_INIT_H, weight=1)

        # Left column
        self.left = ttk.Frame(self, padding=(8, 8, 8, 8))
        self.left.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.left.update_idletasks()
        self.left.grid_propagate(False)

        # Right: controls + plot
        self.top_controls = ttk.Frame(self, padding=(8, 8, 8, 0))
        self.top_controls.grid(row=0, column=1, sticky="ew")
        self.top_controls.grid_columnconfigure(0, weight=1)

        self.plot_area = ttk.Frame(self, padding=(8, 6, 8, 8))
        self.plot_area.grid(row=1, column=1, sticky="nsew")
        self.plot_area.grid_columnconfigure(0, weight=1)
        self.plot_area.grid_rowconfigure(1, weight=1)

    # ---------- Left sidebar (folder + file list + merge queue) ----------
    def _build_left(self):
        # Folder chooser
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

        # Files list (upper)
        files_box = ttk.LabelFrame(self.left, text=".csv files")
        files_box.pack(fill="both", expand=True, pady=(6, 4))

        self.file_listbox = tk.Listbox(files_box, height=12, exportselection=False)
        self.file_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.file_listbox.bind("<<ListboxSelect>>", lambda e: self.load_selected_file())

        sb = ttk.Scrollbar(files_box, orient="vertical", command=self.file_listbox.yview)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.file_listbox.config(yscrollcommand=sb.set)

        # Merge Queue (lower)
        merge_box = ttk.LabelFrame(self.left, text="Merge Queue")
        merge_box.pack(fill="both", expand=True, pady=(4, 0))

        self.merge_listbox = tk.Listbox(merge_box, height=8, exportselection=False)
        self.merge_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(6, 2))

        sbq = ttk.Scrollbar(merge_box, orient="vertical", command=self.merge_listbox.yview)
        sbq.pack(side="right", fill="y", padx=(0, 6), pady=(6, 2))
        self.merge_listbox.config(yscrollcommand=sbq.set)

        # Row 1: Add / Remove
        qctrl1 = ttk.Frame(merge_box)
        qctrl1.pack(fill="x", padx=6, pady=(2, 2))
        ttk.Button(qctrl1, text="Add (↓)", command=self._merge_add_selected).pack(side="left")
        ttk.Button(qctrl1, text="Remove (↑)", command=self._merge_remove_selected).pack(
            side="left", padx=6
        )

        # Row 2: Move Up / Move Down
        qctrl2 = ttk.Frame(merge_box)
        qctrl2.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(qctrl2, text="Move Up", command=lambda: self._merge_move(-1)).pack(side="left")
        ttk.Button(qctrl2, text="Move Down", command=lambda: self._merge_move(+1)).pack(
            side="left", padx=6
        )

        # Row 3: Plot Merge (overlay) — its own line
        qact_plot = ttk.Frame(merge_box)
        qact_plot.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(qact_plot, text="Plot Merge (overlay)", command=self._plot_merge_overlay).pack(
            side="left"
        )

        # Row 4: Export Merge (preview) — its own line
        qact_export = ttk.Frame(merge_box)
        qact_export.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(
            qact_export, text="Export Merge (preview)", command=self._export_merge_preview
        ).pack(side="left")

        # Row 5: Clear — its own line
        qact_clear = ttk.Frame(merge_box)
        qact_clear.pack(fill="x", padx=6, pady=(0, 8))
        ttk.Button(qact_clear, text="Clear", command=self._merge_clear).pack(side="left")

    # ---------- Top controls ----------
    def _build_top_controls(self):
        # Row A: Column pickers
        rowA = ttk.Frame(self.top_controls)
        rowA.grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(rowA, text="X:").pack(side="left")
        self.x_combo = ttk.Combobox(rowA, textvariable=self.x_col_var, state="readonly", width=24)
        self.x_combo.pack(side="left", padx=(4, 12))
        ttk.Label(rowA, text="Y:").pack(side="left")
        self.y_combo = ttk.Combobox(rowA, textvariable=self.y_col_var, state="readonly", width=24)
        self.y_combo.pack(side="left", padx=(4, 12))
        ttk.Button(rowA, text="Apply Columns", command=self.replot).pack(side="left")

        # Row B: Exports (graphics)
        rowB = ttk.Frame(self.top_controls)
        rowB.grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowB, text="Export SVG (signal only)", command=self.export_svg_signal_only).pack(
            side="left"
        )
        ttk.Button(rowB, text="Export PNG (preview)", command=self.export_png_preview).pack(
            side="left", padx=8
        )
        ttk.Label(rowB, text="PNG DPI:").pack(side="left", padx=(12, 4))
        tk.Spinbox(
            rowB, from_=72, to=600, increment=10, width=6, textvariable=self.png_dpi_var
        ).pack(side="left")

        # Row C: Exports (data)
        rowC = ttk.Frame(self.top_controls)
        rowC.grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowC, text="Export CSV (preview)", command=self.export_csv_preview).pack(
            side="left"
        )
        ttk.Button(rowC, text="Export CSV (full file copy)", command=self.export_csv_fullcopy).pack(
            side="left", padx=8
        )

        # Row D: Segment window
        rowD = ttk.Frame(self.top_controls)
        rowD.grid(row=3, column=0, sticky="w", pady=(2, 2))
        ttk.Label(rowD, text="Segment Start (X):").pack(side="left")
        ttk.Entry(rowD, width=12, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowD, text="End (X):").pack(side="left")
        ttk.Entry(rowD, width=12, textvariable=self.seg_end_var).pack(side="left", padx=(4, 8))
        ttk.Button(rowD, text="Apply", command=self.apply_segment_window).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(rowD, text="Full View", command=self.reset_axes).pack(side="left")

        # Row E1: X-axis
        rowE1 = ttk.Frame(self.top_controls)
        rowE1.grid(row=4, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowE1, text="X min:").pack(side="left")
        ttk.Entry(rowE1, width=12, textvariable=self.xmin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowE1, text="X max:").pack(side="left")
        ttk.Entry(rowE1, width=12, textvariable=self.xmax_var).pack(side="left", padx=(4, 12))

        # Row E2: Y-axis + buttons
        rowE2 = ttk.Frame(self.top_controls)
        rowE2.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowE2, text="Y min:").pack(side="left")
        ttk.Entry(rowE2, width=12, textvariable=self.ymin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowE2, text="Y max:").pack(side="left")
        ttk.Entry(rowE2, width=12, textvariable=self.ymax_var).pack(side="left", padx=(4, 12))
        ttk.Button(rowE2, text="Apply", command=self.apply_axes_from_inputs).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowE2, text="Grab", command=self.update_axis_inputs_from_view).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowE2, text="Reset", command=self.reset_axes).pack(side="left")

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
        csvs = _list_csv_files(path)
        self.files = csvs
        self.file_listbox.delete(0, tk.END)
        for p in self.files:
            self.file_listbox.insert(tk.END, os.path.basename(p))
        self._refresh_merge_listbox()
        self.status_var.set(f"Scanned: {path}  |  {len(self.files)} .csv files found")
        if not self.files:
            self._clear_plot()

    def load_selected_file(self):
        if not self.files:
            return
        try:
            idx = self.file_listbox.curselection()
            if not idx:
                return
            path = self.files[idx[0]]
            df = pd.read_csv(path)  # Do not preprocess; read as-is
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return

        self.cur_path = path
        self.df = df

        # Populate column selectors (prefer numeric-ish columns)
        cols = list(self.df.columns)
        numeric_cols = _numeric_columns(self.df)
        default_x = numeric_cols[0] if len(numeric_cols) >= 1 else (cols[0] if cols else "")
        default_y = (
            numeric_cols[1]
            if len(numeric_cols) >= 2
            else (cols[1] if len(cols) >= 2 else default_x)
        )

        self.x_combo["values"] = cols
        self.y_combo["values"] = cols
        self.x_col_var.set(default_x)
        self.y_col_var.set(default_y)

        self.reset_axes()
        self.status_var.set(f"Loaded: {os.path.basename(path)}  (columns={len(cols)})")

    # ---------- Plot helpers ----------
    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _get_xy_series(self):
        if self.df is None:
            return None, None
        xname = self.x_col_var.get().strip()
        yname = self.y_col_var.get().strip()
        if xname == "" or yname == "":
            return None, None
        try:
            return _coerce_xy(self.df, xname, yname)
        except Exception:
            return None, None

    def replot(self):
        x, y = self._get_xy_series()
        if x is None:
            self._clear_plot()
            return
        self.ax.clear()
        self.ax.plot(x, y, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel(self.x_col_var.get())
        self.ax.set_ylabel(self.y_col_var.get())
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
        x, _ = self._get_xy_series()
        if x is None:
            return
        try:
            s0 = float(self.seg_start_var.get().strip())
            s1 = float(self.seg_end_var.get().strip())
        except Exception:
            messagebox.showerror("Segment", "Start and End must be numbers (in X units).")
            return
        if s0 == s1:
            messagebox.showerror("Segment", "Start and End must be different.")
            return
        if s0 > s1:
            s0, s1 = s1, s0
        xmin = max(np.min(x), s0)
        xmax = min(np.max(x), s1)
        if xmax - xmin <= 0:
            messagebox.showerror(
                "Segment", "Segment must be within data range and positive length."
            )
            return
        self.ax.set_xlim(xmin, xmax)
        self.canvas.draw_idle()

    def apply_axes_from_inputs(self):
        if self.df is None:
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
        self.replot()

    # ---------- Merge Queue ops ----------
    def _refresh_merge_listbox(self):
        self.merge_listbox.delete(0, tk.END)
        for p in self.merge_queue:
            self.merge_listbox.insert(tk.END, os.path.basename(p))

    def _merge_add_selected(self):
        sel = list(self.file_listbox.curselection())
        if not sel:
            return
        for i in sel:
            p = self.files[i]
            if p not in self.merge_queue:
                self.merge_queue.append(p)
        self._refresh_merge_listbox()

    def _merge_remove_selected(self):
        sel = list(self.merge_listbox.curselection())
        if not sel:
            return
        for i in sorted(sel, reverse=True):
            del self.merge_queue[i]
        self._refresh_merge_listbox()

    def _merge_move(self, delta: int):
        n = len(self.merge_queue)
        if n == 0:
            return
        sel = list(self.merge_listbox.curselection())
        if not sel:
            return
        idxs = sel if delta < 0 else sel[::-1]
        new_sel = []
        for i in idxs:
            j = i + delta
            if 0 <= j < n:
                self.merge_queue[i], self.merge_queue[j] = self.merge_queue[j], self.merge_queue[i]
                new_sel.append(j)
            else:
                new_sel.append(i)
        self._refresh_merge_listbox()
        self.merge_listbox.selection_clear(0, tk.END)
        for j in new_sel:
            self.merge_listbox.selection_set(j)

    def _merge_clear(self):
        self.merge_queue.clear()
        self._refresh_merge_listbox()

    def _plot_merge_overlay(self):
        if not self.merge_queue:
            messagebox.showinfo("Merge", "Merge Queue is empty.")
            return

        xname = self.x_col_var.get().strip()
        yname = self.y_col_var.get().strip()

        if (xname == "" or yname == "") and self.merge_queue:
            try:
                df0 = pd.read_csv(self.merge_queue[0])
                cols = list(df0.columns)
                num = _numeric_columns(df0)
                xname = num[0] if len(num) >= 1 else (cols[0] if cols else "")
                yname = num[1] if len(num) >= 2 else (cols[1] if len(cols) >= 2 else xname)
                self.x_col_var.set(xname)
                self.y_col_var.set(yname)
            except Exception:
                messagebox.showerror(
                    "Merge",
                    "Cannot infer X/Y columns from first queued file. Please choose columns.",
                )
                return

        if xname == "" or yname == "":
            messagebox.showinfo("Merge", "Choose X and Y columns first.")
            return

        self.ax.clear()
        any_plotted = False
        color_cycle = (
            plt.rcParams["axes.prop_cycle"]
            .by_key()
            .get(
                "color",
                [
                    "tab:blue",
                    "tab:orange",
                    "tab:green",
                    "tab:red",
                    "tab:purple",
                    "tab:brown",
                    "tab:pink",
                    "tab:gray",
                    "tab:olive",
                    "tab:cyan",
                ],
            )
        )

        for k, path in enumerate(self.merge_queue):
            try:
                dfk = pd.read_csv(path)
            except Exception:
                continue
            if xname not in dfk.columns or yname not in dfk.columns:
                continue
            x, y = _coerce_xy(dfk, xname, yname)
            if x is None:
                continue
            c = color_cycle[k % len(color_cycle)]
            self.ax.plot(x, y, lw=1.0, label=os.path.basename(path), color=c)
            any_plotted = True

        if not any_plotted:
            messagebox.showwarning("Merge", "No valid series to plot with current X/Y.")
            return

        self.ax.set_xlabel(xname)
        self.ax.set_ylabel(yname)
        self.ax.legend(frameon=False, fontsize=9, loc="best")
        self.ax.relim()
        self.ax.autoscale()
        self.canvas.draw_idle()
        self.status_var.set("Plotted merge overlay.")

    def _export_merge_preview(self):
        """
        Concatenate points from all queued files within the CURRENT X window into a CSV.
        Columns are ONLY the selected X and Y (no source column). For files after the first,
        the first data row (after window filtering) is dropped to avoid duplicated seam rows.
        The output is saved next to the first queued file as: merged_preview_<xmin>_<xmax>.csv
        """
        if not self.merge_queue:
            messagebox.showinfo("Merge", "Merge Queue is empty.")
            return

        xname = self.x_col_var.get().strip()
        yname = self.y_col_var.get().strip()

        if xname == "" or yname == "":
            messagebox.showinfo("Merge", "Choose X and Y columns first.")
            return

        x0, x1 = self.ax.get_xlim()
        rows = []
        for k, path in enumerate(self.merge_queue):
            try:
                dfk = pd.read_csv(path)
            except Exception:
                continue
            if xname not in dfk.columns or yname not in dfk.columns:
                continue
            try:
                x = pd.to_numeric(dfk[xname], errors="coerce")
                y = pd.to_numeric(dfk[yname], errors="coerce")
            except Exception:
                continue

            m = np.isfinite(x) & np.isfinite(y) & (x >= x0) & (x <= x1)
            if not m.any():
                continue

            sub = dfk.loc[m, [xname, yname]].reset_index(drop=True)

            # Drop first data row for every file after the first
            if k > 0 and len(sub) > 0:
                sub = sub.iloc[1:].reset_index(drop=True)
                if sub.empty:
                    continue

            rows.append(sub)

        if not rows:
            messagebox.showwarning(
                "Merge", "No rows fall in the current X window for queued files."
            )
            return

        out_df = pd.concat(rows, axis=0, ignore_index=True)

        out_dir = os.path.dirname(self.merge_queue[0])

        def _tag(val: float) -> str:
            return f"{val:.6f}".replace(".", "p")

        out_name = f"merged_preview_{_tag(x0)}-{_tag(x1)}.csv"
        out_path = os.path.join(out_dir, out_name)

        try:
            out_df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Merge Export", str(e))
            return

        self.status_var.set(f"Exported merged preview: {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    # ---------- Exports (SAFE: off-screen canvases, no pyplot during save) ----------
    def _current_view_limits(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return x0, x1, y0, y1

    def _output_base(self):
        if self.cur_path is None:
            return os.getcwd(), "preview"
        out_dir = os.path.dirname(self.cur_path)
        base = os.path.splitext(os.path.basename(self.cur_path))[0]
        return out_dir, base

    def export_svg_signal_only(self):
        # Off-screen SVG export to avoid Tk/TkAgg interaction
        x, y = self._get_xy_series()
        if x is None:
            messagebox.showinfo("Export", "Load a CSV and choose X/Y columns first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        mask = (x >= x0) & (x <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return

        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_preview_signal.svg")

        # --- OFF-SCREEN SVG ---
        try:
            from matplotlib.backends.backend_svg import FigureCanvasSVG
            from matplotlib.figure import Figure

            fig = Figure(figsize=(8, 3), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(x[mask], y[mask], color=LINE_COLOR, lw=1.0)

            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)

            # Signal-only
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

            FigureCanvasSVG(fig)  # bind off-screen canvas
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
        # Off-screen PNG export to avoid Tk/TkAgg interaction
        x, y = self._get_xy_series()
        if x is None:
            messagebox.showinfo("Export", "Load a CSV and choose X/Y columns first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()

        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_preview.png")

        # --- OFF-SCREEN AGG ---
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(x, y, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel(self.x_col_var.get())
            ax.set_ylabel(self.y_col_var.get())
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            dpi = int(self.png_dpi_var.get())

            FigureCanvasAgg(fig)  # bind off-screen canvas
            fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="white")
        except Exception as e:
            messagebox.showerror("Export PNG", str(e))
            return

        self.status_var.set(f"Exported PNG (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_preview(self):
        if self.df is None or self.cur_path is None:
            messagebox.showinfo("Export", "Load a CSV first.")
            return
        xname = self.x_col_var.get().strip()
        yname = self.y_col_var.get().strip()
        if xname == "" or yname == "":
            messagebox.showinfo("Export", "Choose X and Y columns first.")
            return
        try:
            x_numeric = pd.to_numeric(self.df[xname], errors="coerce")
        except Exception:
            messagebox.showerror("Export", "X column cannot be parsed numerically for windowing.")
            return

        x0, x1, _, _ = self._current_view_limits()
        mask = (x_numeric >= x0) & (x_numeric <= x1)
        if not mask.any():
            messagebox.showwarning("Export", "No rows fall inside the current X window.")
            return

        out_df = self.df.loc[mask, [xname, yname]].copy()
        out_dir, base = self._output_base()

        def _tag(val: float) -> str:
            return f"{val:.6f}".replace(".", "p")

        out_path = os.path.join(out_dir, f"{base}_preview_{_tag(x0)}-{_tag(x1)}.csv")

        try:
            out_df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return

        self.status_var.set(f"Exported CSV (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_fullcopy(self):
        if self.df is None or self.cur_path is None:
            messagebox.showinfo("Export", "Load a CSV first.")
            return
        out_dir, base = self._output_base()
        out_path = os.path.join(out_dir, f"{base}_copy.csv")
        try:
            self.df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return

        self.status_var.set(f"Exported CSV (full copy): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")


# --------------------------- Main ---------------------------
def main() -> None:
    app = CsvFolderViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
