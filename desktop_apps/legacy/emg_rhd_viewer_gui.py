# EMG_preview_GUI.py
# -*- coding: utf-8 -*-
"""
RHD Folder + Channel Viewer (macOS-safe; no scroll-zoom)
- Left: pick a folder, pick an .rhd file, then pick a channel.
- Right: preview the selected channel (time vs amplitude).
- Exports:
    * SVG (signal-only in current preview window; no axes/ticks/text/spines; tight bbox; transparent)  [OFF-SCREEN, SAFE]
    * PNG (full preview with axes in current view; configurable DPI)                                   [OFF-SCREEN, SAFE]
    * CSV (preview window only: time_s,value_uV)
    * CSV (full channel: time_s,value_uV)
    * Save ALL channels as CSV into a folder named after the .rhd file (next to the file)
- Batch Export Queue (under Channels):
    * Add / Remove (row 1), Move Up / Move Down (row 2), Add ALL (row 3), Export Queue (row 4)
- NEW: Swap two segments of the signal by specifying time ranges A and B (supports unequal lengths; swaps blocks).

Dependencies: numpy, pandas, matplotlib, tkinter, importrhdutilities
"""

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ---------- RHD import ----------
try:
    import importrhdutilities as rhd
except Exception as e:
    raise RuntimeError(
        "Cannot import importrhdutilities.py. Place it beside this script or on PYTHONPATH."
    ) from e

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
PLOT_INIT_H = 520  # px

LINE_COLOR = "tab:blue"


# --------------------------- RHD helpers ---------------------------
def list_rhd_files(folder: Path):
    """Return sorted list of .rhd file paths under folder (non-recursive)."""
    if not folder.is_dir():
        return []
    names = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".rhd"]
    return sorted(names, key=lambda p: p.name.lower())


def list_rhd_files_recursive(folder: Path):
    """Return sorted list of .rhd file paths under folder (recursive)."""
    if not folder.is_dir():
        return []
    names = [p for p in folder.rglob("*.rhd") if p.is_file()]
    return sorted(names, key=lambda p: str(p).lower())


def load_rhd(path: Path):
    """
    Load an RHD file using importrhdutilities.
    Returns: (time_s: np.ndarray, fs: float, ch_names: list[str], data: np.ndarray[n_ch, n_samp])
    """
    result, data_present = rhd.load_file(str(path))
    if not data_present:
        raise RuntimeError("No data in RHD file.")
    fs = float(result["frequency_parameters"]["amplifier_sample_rate"])
    t = np.asarray(result["t_amplifier"], dtype=float)
    ch_hdrs = result.get("amplifier_channels", [])
    ch_names = [h.get("custom_channel_name", f"ch{i}") or f"ch{i}" for i, h in enumerate(ch_hdrs)]
    amp = np.asarray(result["amplifier_data"], dtype=float)  # shape (n_ch, n_samples)
    if amp.ndim != 2 or amp.shape[0] != len(ch_names):
        raise RuntimeError("Amplifier data shape mismatch.")
    return t, fs, ch_names, amp


def safe_filename_token(val: float) -> str:
    """12.345678 -> '12p345678'; -0.5 -> 'm0p5' for filenames."""
    s = f"{val:.6f}"
    s = s.replace("-", "m").replace(".", "p")
    return s


def _find_split_partner(path: Path) -> tuple[Path | None, Path | None]:
    """
    Given /dir/xxx.rhd, detect if there is a 'split partner' in the SAME folder
    whose stem differs ONLY in the last four characters (all digits), and the
    integer value formed by these last four digits differs by EXACTLY 100
    (i.e., ±100 relative to the current file).

    Returns:
      (earlier, later)  -> if a partner exists (the one with the smaller last-4 value is returned as 'earlier')
      (None, None)      -> if no valid partner is found
    """
    stem = path.stem
    if len(stem) < 4:
        return None, None

    last4 = stem[-4:]
    if not last4.isdigit():
        return None, None

    cur_val = int(last4)

    candidates: list[Path] = []
    for delta in (-100, +100):
        target = cur_val + delta
        if 0 <= target <= 9999:
            target_str = f"{target:04d}"
            cand_stem = stem[:-4] + target_str
            cand_path = path.with_name(cand_stem + path.suffix)
            if cand_path.exists():
                candidates.append(cand_path)

    if not candidates:
        return None, None

    filtered: list[Path] = []
    for q in candidates:
        if len(q.stem) == len(stem) and q.stem[:-4] == stem[:-4]:
            filtered.append(q)

    if not filtered:
        return None, None

    partner = filtered[0]
    cur_last4 = int(stem[-4:])
    ptn_last4 = int(partner.stem[-4:])

    if abs(ptn_last4 - cur_last4) != 100:
        return None, None

    earlier = path if cur_last4 < ptn_last4 else partner
    later = partner if cur_last4 < ptn_last4 else path
    return earlier, later


def _load_merged_if_pair(path: Path):
    """
    If a split partner exists, load BOTH and concatenate along time.
    Returns:
      time_s, fs, ch_names, data, base_stem, used_pair
    where base_stem is the stem of the *earlier* file (naming base).
    """
    earlier, later = _find_split_partner(path)
    if earlier is None or later is None:
        t, fs, ch, amp = load_rhd(path)
        return t, fs, ch, amp, path.stem, False

    t1, fs1, ch1, a1 = load_rhd(earlier)
    t2, fs2, ch2, a2 = load_rhd(later)

    if abs(fs1 - fs2) > 1e-9 or len(ch1) != len(ch2) or any(x != y for x, y in zip(ch1, ch2)):
        t, fs, ch, amp = load_rhd(path)
        return t, fs, ch, amp, path.stem, False

    dt = 1.0 / fs1
    offset = float(t1[-1]) + dt - float(t2[0])
    t_merged = np.concatenate([t1, t2 + offset], axis=0)
    a_merged = np.concatenate([a1, a2], axis=1)

    return t_merged, fs1, ch1, a_merged, earlier.stem, True


def _load_rhd_with_merge_option(path: Path, do_merge: bool):
    if do_merge:
        return _load_merged_if_pair(path)
    t, fs, ch, amp = load_rhd(path)
    return t, fs, ch, amp, path.stem, False


def _df_all_channels_wide(time_s: np.ndarray, ch_names: list[str], amp: np.ndarray) -> pd.DataFrame:
    out = {"time": np.asarray(time_s, dtype=float)}
    for i, name in enumerate(ch_names):
        out[str(name)] = np.asarray(amp[i, :], dtype=float)
    return pd.DataFrame(out)


# --------------------------- GUI ---------------------------
class EmgRhdViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EMG — RHD Channel Viewer")
        self.geometry("1280x820")
        self.minsize(980, 640)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files: list[Path] = []  # .rhd files in current folder
        self.channels: list[str] = []  # channels for currently loaded file
        self.cur_file: Path | None = None
        self.time_s: np.ndarray | None = None
        self.fs: float | None = None
        self.data: np.ndarray | None = None  # shape (n_ch, n_samp)
        self.cur_ch_idx: int | None = None
        self.png_dpi_var = tk.IntVar(value=300)

        self.merge_pair_var = tk.BooleanVar(value=True)
        self.wide_csv_var = tk.BooleanVar(value=False)

        self.base_stem: str | None = None
        self.used_pair: bool = False

        # NEW: swap segments vars
        self.swap_a_start_var = tk.StringVar(value="")
        self.swap_a_end_var = tk.StringVar(value="")
        self.swap_b_start_var = tk.StringVar(value="")
        self.swap_b_end_var = tk.StringVar(value="")
        self.swap_all_channels_var = tk.BooleanVar(value=True)

        # Track whether current in-memory data was edited (swap, etc.)
        self.data_modified: bool = False

        # Batch export queue (absolute Paths)
        self.queue_files: list[Path] = []

        # Axis/segment vars
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.ymin_var = tk.StringVar(value="")
        self.ymax_var = tk.StringVar(value="")
        self.seg_start_var = tk.StringVar(value="")
        self.seg_end_var = tk.StringVar(value="")

        # Layout
        self._build_layout()
        self._build_left()
        self._build_top_controls()
        self._build_plot()

        # Initial scan
        self.scan_folder()
        if self.files:
            self.file_listbox.selection_set(0)
            self.on_select_file(None)

    # ---------- Layout (grid with fixed initial sidebar width) ----------
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

    # ---------- Left sidebar (folder + files + channels + queue) ----------
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

        files_box = ttk.LabelFrame(self.left, text=".rhd files")
        files_box.pack(fill="both", expand=True, pady=(6, 4))
        self.file_listbox = tk.Listbox(files_box, height=8, exportselection=False)
        self.file_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.file_listbox.bind("<<ListboxSelect>>", self.on_select_file)
        sb1 = ttk.Scrollbar(files_box, orient="vertical", command=self.file_listbox.yview)
        sb1.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.file_listbox.config(yscrollcommand=sb1.set)

        ch_box = ttk.LabelFrame(self.left, text="Channels")
        ch_box.pack(fill="both", expand=False, pady=(4, 4))
        self.ch_listbox = tk.Listbox(ch_box, height=6, exportselection=False)
        self.ch_listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        self.ch_listbox.bind("<<ListboxSelect>>", self.on_select_channel)
        sb2 = ttk.Scrollbar(ch_box, orient="vertical", command=self.ch_listbox.yview)
        sb2.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.ch_listbox.config(yscrollcommand=sb2.set)

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

        qctrl3 = ttk.Frame(queue_box)
        qctrl3.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(qctrl3, text="Add ALL", command=self.queue_add_all_files).pack(side="left")
        ttk.Button(
            qctrl3, text="Add ALL (recursive)", command=self.queue_add_all_files_recursive
        ).pack(side="left", padx=6)

        qact = ttk.Frame(queue_box)
        qact.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(qact, text="Export Queue → CSV folders", command=self.queue_export_all).pack(
            side="left"
        )

    # ---------- Top controls ----------
    def _build_top_controls(self):
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

        rowA2 = ttk.Frame(self.top_controls)
        rowA2.grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Checkbutton(
            rowA2,
            text="Auto-merge split pair (±100)",
            variable=self.merge_pair_var,
            command=self._reload_current_file_if_any,
        ).pack(side="left")
        ttk.Checkbutton(rowA2, text="All channels in one file", variable=self.wide_csv_var).pack(
            side="left", padx=12
        )

        rowB = ttk.Frame(self.top_controls)
        rowB.grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowB, text="Export CSV (preview)", command=self.export_csv_preview).pack(
            side="left"
        )
        ttk.Button(
            rowB, text="Export CSV (full channel)", command=self.export_csv_full_channel
        ).pack(side="left", padx=8)
        ttk.Button(
            rowB, text="Save ALL channels to CSV folder", command=self.export_all_channels_folder
        ).pack(side="left", padx=8)

        rowC = ttk.Frame(self.top_controls)
        rowC.grid(row=3, column=0, sticky="w", pady=(2, 2))
        ttk.Label(rowC, text="Segment Start (s):").pack(side="left")
        ttk.Entry(rowC, width=12, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowC, text="End (s):").pack(side="left")
        ttk.Entry(rowC, width=12, textvariable=self.seg_end_var).pack(side="left", padx=(4, 8))
        ttk.Button(rowC, text="Apply", command=self.apply_segment_window).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(rowC, text="Full View", command=self.reset_axes).pack(side="left")

        # NEW: Swap segments controls
        rowSwap = ttk.Frame(self.top_controls)
        rowSwap.grid(row=4, column=0, sticky="w", pady=(2, 2))
        ttk.Label(rowSwap, text="Swap A (s):").pack(side="left")
        ttk.Entry(rowSwap, width=10, textvariable=self.swap_a_start_var).pack(
            side="left", padx=(4, 4)
        )
        ttk.Label(rowSwap, text="to").pack(side="left")
        ttk.Entry(rowSwap, width=10, textvariable=self.swap_a_end_var).pack(
            side="left", padx=(4, 10)
        )

        ttk.Label(rowSwap, text="Swap B (s):").pack(side="left")
        ttk.Entry(rowSwap, width=10, textvariable=self.swap_b_start_var).pack(
            side="left", padx=(4, 4)
        )
        ttk.Label(rowSwap, text="to").pack(side="left")
        ttk.Entry(rowSwap, width=10, textvariable=self.swap_b_end_var).pack(
            side="left", padx=(4, 10)
        )

        ttk.Checkbutton(rowSwap, text="All channels", variable=self.swap_all_channels_var).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(rowSwap, text="Swap", command=self.swap_signal_segments).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowSwap, text="Clear", command=self.clear_swap_inputs).pack(side="left")

        rowD1 = ttk.Frame(self.top_controls)
        rowD1.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowD1, text="X min (s):").pack(side="left")
        ttk.Entry(rowD1, width=12, textvariable=self.xmin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowD1, text="X max (s):").pack(side="left")
        ttk.Entry(rowD1, width=12, textvariable=self.xmax_var).pack(side="left", padx=(4, 12))

        rowD2 = ttk.Frame(self.top_controls)
        rowD2.grid(row=6, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowD2, text="Y min (µV):").pack(side="left")
        ttk.Entry(rowD2, width=12, textvariable=self.ymin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowD2, text="Y max (µV):").pack(side="left")
        ttk.Entry(rowD2, width=12, textvariable=self.ymax_var).pack(side="left", padx=(4, 12))
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
        status = ttk.Label(self.plot_area, textvariable=self.status_var, foreground="#444")
        status.grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.fig, self.ax = plt.subplots(figsize=(9, 4.8))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_area)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.plot_area, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # ---------- Folder / File / Channel selection ----------
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
        folder = Path(self.folder_path.get())
        self.files = list_rhd_files(folder)
        self.file_listbox.delete(0, tk.END)
        for p in self.files:
            self.file_listbox.insert(tk.END, p.name)
        self.channels = []
        self.ch_listbox.delete(0, tk.END)
        self.cur_file = None
        self.time_s = None
        self.data = None
        self.cur_ch_idx = None
        self.base_stem = None
        self.used_pair = False
        self.data_modified = False
        self._clear_plot()
        self.status_var.set(f"Scanned: {folder}")

    def _reload_current_file_if_any(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        self.on_select_file(None)

    def on_select_file(self, _evt):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        path = self.files[idx]
        try:
            t, fs, ch_names, amp, base_stem, used_pair = _load_rhd_with_merge_option(
                path, bool(self.merge_pair_var.get())
            )
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return
        self.cur_file = path
        self.time_s = t
        self.fs = fs
        self.channels = ch_names
        self.data = amp
        self.base_stem = base_stem
        self.used_pair = used_pair
        self.data_modified = False

        self.ch_listbox.delete(0, tk.END)
        for name in self.channels:
            self.ch_listbox.insert(tk.END, name)

        self.cur_ch_idx = None
        self._clear_plot()
        self._clear_inputs()
        self.clear_swap_inputs()
        note = " (merged pair)" if used_pair else ""
        self.status_var.set(
            f"Loaded file: {path.name}{note} | fs={fs:g} Hz | channels={len(ch_names)}"
        )

    def on_select_channel(self, _evt):
        sel = self.ch_listbox.curselection()
        if not sel or self.data is None:
            return
        ch_idx = int(sel[0])
        self.cur_ch_idx = ch_idx
        self._replot_full()
        self.status_var.set(f"Channel: {self.channels[ch_idx]}")

    # ---------- Queue management ----------
    def _refresh_queue_listbox(self):
        self.queue_listbox.delete(0, tk.END)
        for p in self.queue_files:
            self.queue_listbox.insert(tk.END, p.name)

    def queue_add_current_file(self):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        p = self.files[int(sel[0])]
        if p not in self.queue_files:
            self.queue_files.append(p)
            self._refresh_queue_listbox()

    def queue_add_all_files(self):
        if not self.files:
            return
        added = 0
        for p in self.files:
            if p not in self.queue_files:
                self.queue_files.append(p)
                added += 1
        if added > 0:
            self._refresh_queue_listbox()

    def queue_add_all_files_recursive(self):
        folder = Path(self.folder_path.get())
        files = list_rhd_files_recursive(folder)
        if not files:
            return

        added = 0
        for p in files:
            if p not in self.queue_files:
                self.queue_files.append(p)
                added += 1

        if added > 0:
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

        do_merge = bool(self.merge_pair_var.get())
        wide = bool(self.wide_csv_var.get())

        processed_bases: set[str] = set()

        for f in self.queue_files:
            total += 1
            try:
                t_all, fs, ch_all, amp_all, base_stem, used_pair = _load_rhd_with_merge_option(
                    f, do_merge
                )

                if do_merge:
                    if base_stem in processed_bases:
                        continue

                base_dir = f.parent

                if wide:
                    out_path = base_dir / f"{base_stem}.csv"
                    dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                    dfw.to_csv(out_path, index=False, sep="\t")
                else:
                    target_dir = base_dir / base_stem
                    target_dir.mkdir(parents=True, exist_ok=True)
                    for i, name in enumerate(ch_all):
                        out_path = target_dir / f"{base_stem}_{name}.csv"
                        pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(
                            out_path, index=False
                        )

                if do_merge:
                    processed_bases.add(base_stem)
                ok += 1
            except Exception as e:
                print(f"[WARN] failed to export {f}: {e}")

        messagebox.showinfo("Export Queue", f"Exported {ok}/{total} item(s).")

    # ---------- Plot helpers ----------
    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _clear_inputs(self):
        for v in (
            self.xmin_var,
            self.xmax_var,
            self.ymin_var,
            self.ymax_var,
            self.seg_start_var,
            self.seg_end_var,
        ):
            v.set("")

    def _replot_current_channel(self, preserve_view: bool = False, clear_inputs: bool = True):
        if self.time_s is None or self.cur_ch_idx is None or self.data is None:
            self._clear_plot()
            return

        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        y = self.data[self.cur_ch_idx, :]
        self.ax.clear()
        self.ax.plot(self.time_s, y, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Amplitude (µV)")

        if preserve_view:
            self.ax.set_xlim(xlim[0], xlim[1])
            self.ax.set_ylim(ylim[0], ylim[1])
        else:
            self.ax.relim()
            self.ax.autoscale()

        self.canvas.draw_idle()
        if clear_inputs:
            self._clear_inputs()

    def _replot_full(self):
        self._replot_current_channel(preserve_view=False, clear_inputs=True)

    # ---------- Segment swap (NEW) ----------
    def clear_swap_inputs(self):
        for v in (
            self.swap_a_start_var,
            self.swap_a_end_var,
            self.swap_b_start_var,
            self.swap_b_end_var,
        ):
            v.set("")

    def _parse_time_range(
        self, start_var: tk.StringVar, end_var: tk.StringVar, label: str
    ) -> tuple[float, float]:
        if self.time_s is None:
            raise ValueError("No signal loaded.")
        s_txt = start_var.get().strip()
        e_txt = end_var.get().strip()
        if s_txt == "" or e_txt == "":
            raise ValueError(f"{label}: start/end cannot be empty.")
        try:
            t0 = float(s_txt)
            t1 = float(e_txt)
        except Exception:
            raise ValueError(f"{label}: start/end must be numeric (seconds).")
        if t0 == t1:
            raise ValueError(f"{label}: start and end must be different.")
        if t0 > t1:
            t0, t1 = t1, t0

        tmin = float(self.time_s[0])
        tmax = float(self.time_s[-1])
        if t0 < tmin or t1 > tmax:
            raise ValueError(f"{label}: range must be within data time [{tmin:.6g}, {tmax:.6g}].")

        return t0, t1

    def swap_signal_segments(self):
        """
        Swap two disjoint time ranges A and B.
        - Supports unequal lengths by swapping blocks in the sample array:
            prefix + A + mid + B + suffix  ->  prefix + B + mid + A + suffix
        - If 'All channels' is checked, the swap is applied to all channels.
          Otherwise, it applies only to the currently selected channel.
        """
        if self.time_s is None or self.data is None:
            messagebox.showinfo("Swap", "Load an .rhd file first.")
            return
        if self.cur_ch_idx is None:
            messagebox.showinfo("Swap", "Select a channel first (for preview).")
            return

        try:
            a0_t, a1_t = self._parse_time_range(
                self.swap_a_start_var, self.swap_a_end_var, "Swap A"
            )
            b0_t, b1_t = self._parse_time_range(
                self.swap_b_start_var, self.swap_b_end_var, "Swap B"
            )
        except Exception as e:
            messagebox.showerror("Swap", str(e))
            return

        # Convert time to sample indices (inclusive-ish endpoints)
        t = self.time_s
        a0 = int(np.searchsorted(t, a0_t, side="left"))
        a1 = int(np.searchsorted(t, a1_t, side="right"))
        b0 = int(np.searchsorted(t, b0_t, side="left"))
        b1 = int(np.searchsorted(t, b1_t, side="right"))

        # Ensure proper ordering and non-overlap
        if a0 >= a1 or b0 >= b1:
            messagebox.showerror("Swap", "Swap ranges must include at least one sample.")
            return

        # Normalize so A is the earlier block
        if b0 < a0:
            a0, a1, b0, b1 = b0, b1, a0, a1
            a0_t, a1_t, b0_t, b1_t = (
                b0_t,
                b1_t,
                a0_t,
                a1_t,
            )  # only for display; not strictly required

        if a1 > b0:
            messagebox.showerror("Swap", "Swap A and Swap B must be disjoint (non-overlapping).")
            return

        n = int(self.data.shape[1])
        if not (0 <= a0 < a1 <= n and 0 <= b0 < b1 <= n):
            messagebox.showerror("Swap", "Swap indices out of range. Please re-check inputs.")
            return

        preserve_xlim = self.ax.get_xlim()
        preserve_ylim = self.ax.get_ylim()

        apply_all = bool(self.swap_all_channels_var.get())
        try:
            if apply_all:
                amp = self.data
                new_amp = np.concatenate(
                    [amp[:, :a0], amp[:, b0:b1], amp[:, a1:b0], amp[:, a0:a1], amp[:, b1:]], axis=1
                )
                self.data = new_amp
            else:
                y = self.data[self.cur_ch_idx, :]
                new_y = np.concatenate([y[:a0], y[b0:b1], y[a1:b0], y[a0:a1], y[b1:]], axis=0)
                self.data[self.cur_ch_idx, :] = new_y

            self.data_modified = True
        except Exception as e:
            messagebox.showerror("Swap", f"Swap failed: {e}")
            return

        # Replot (preserve current view)
        self._replot_current_channel(preserve_view=False, clear_inputs=False)
        self.ax.set_xlim(preserve_xlim[0], preserve_xlim[1])
        self.ax.set_ylim(preserve_ylim[0], preserve_ylim[1])
        self.canvas.draw_idle()

        self.status_var.set(
            f"Swapped segments: [{a0_t:.6g},{a1_t:.6g}]s ↔ [{b0_t:.6g},{b1_t:.6g}]s"
            + (" (all channels)" if apply_all else " (current channel)")
        )

    # ---------- Segment / axes ----------
    def apply_segment_window(self):
        if self.time_s is None or self.cur_ch_idx is None:
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
        t0 = float(self.time_s[0])
        t1 = float(self.time_s[-1])
        s0 = max(t0, min(s0, t1))
        s1 = max(t0, min(s1, t1))
        if s1 - s0 <= 0:
            messagebox.showerror(
                "Segment", "Segment length must be positive and within data range."
            )
            return
        self.ax.set_xlim(s0, s1)
        self.canvas.draw_idle()

    def apply_axes_from_inputs(self):
        if self.time_s is None or self.cur_ch_idx is None:
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

    # ---------- Exports (single file / channel) ----------
    def _current_view_limits(self):
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        return x0, x1, y0, y1

    def _export_dir_and_base(self):
        """Return (export_dir, base_name, ch_name) for exports."""
        if self.cur_file is None:
            return os.getcwd(), "preview", "ch"
        out_dir = str(self.cur_file.parent)
        base = self.base_stem if self.base_stem else self.cur_file.stem
        ch = (
            self.channels[self.cur_ch_idx]
            if (self.cur_ch_idx is not None and self.channels)
            else "ch"
        )
        return out_dir, base, ch

    def export_svg_signal_only(self):
        if self.time_s is None or self.cur_ch_idx is None or self.data is None:
            messagebox.showinfo("Export", "Choose a file and a channel first.")
            return
        x0, x1, _, _ = self._current_view_limits()
        y = self.data[self.cur_ch_idx, :]
        mask = (self.time_s >= x0) & (self.time_s <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return

        out_dir, base, ch = self._export_dir_and_base()
        out_path = os.path.join(out_dir, f"{base}_{ch}_preview_signal.svg")

        try:
            from matplotlib.backends.backend_svg import FigureCanvasSVG
            from matplotlib.figure import Figure

            fig = Figure(figsize=(8, 3), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(self.time_s[mask], y[mask], color=LINE_COLOR, lw=1.0)

            y0, y1 = self.ax.get_ylim()
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
        except Exception as e:
            messagebox.showerror("Export SVG", str(e))
            return

        self.status_var.set(f"Exported SVG (signal-only): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_png_preview(self):
        if self.time_s is None or self.cur_ch_idx is None or self.data is None:
            messagebox.showinfo("Export", "Choose a file and a channel first.")
            return
        x0, x1, y0, y1 = self._current_view_limits()
        y = self.data[self.cur_ch_idx, :]

        out_dir, base, ch = self._export_dir_and_base()
        out_path = os.path.join(out_dir, f"{base}_{ch}_preview.png")

        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            fig = Figure(figsize=(9, 4.8), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(self.time_s, y, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (µV)")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)

            dpi = int(self.png_dpi_var.get())

            FigureCanvasAgg(fig)
            fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="white")
        except Exception as e:
            messagebox.showerror("Export PNG", str(e))
            return

        self.status_var.set(f"Exported PNG (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_preview(self):
        if self.time_s is None or self.cur_ch_idx is None or self.data is None:
            messagebox.showinfo("Export", "Choose a file and a channel first.")
            return
        x0, x1, _, _ = self._current_view_limits()
        mask = (self.time_s >= x0) & (self.time_s <= x1)
        if not np.any(mask):
            messagebox.showwarning("Export", "No points in the current preview window.")
            return

        out_dir, base, ch = self._export_dir_and_base()
        tag = f"{safe_filename_token(x0)}-{safe_filename_token(x1)}"
        out_path = os.path.join(out_dir, f"{base}_{ch}_preview_{tag}s.csv")

        df = pd.DataFrame(
            {"time_s": self.time_s[mask], "value_uV": self.data[self.cur_ch_idx, :][mask]}
        )
        try:
            df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return

        self.status_var.set(f"Exported CSV (preview): {out_path}")
        messagebox.showinfo("Export", f"Saved:\n{out_path}")

    def export_csv_full_channel(self):
        # NOTE: export from current in-memory arrays so swaps are preserved
        if (
            self.cur_file is None
            or self.time_s is None
            or self.data is None
            or self.cur_ch_idx is None
        ):
            messagebox.showinfo("Export", "Choose a file and a channel first.")
            return

        out_dir, base, ch = self._export_dir_and_base()
        out_path = os.path.join(out_dir, f"{base}_{ch}.csv")

        df = pd.DataFrame({"time_s": self.time_s, "value_uV": self.data[self.cur_ch_idx, :]})
        try:
            df.to_csv(out_path, index=False)
        except Exception as e:
            messagebox.showerror("Export", str(e))
            return

        note = ""
        if self.used_pair:
            note += " (merged pair)"
        if self.data_modified:
            note += " (modified)"
        self.status_var.set(f"Exported CSV (full channel){note}: {out_path}")
        messagebox.showinfo("Export", f"Saved{note}:\n{out_path}")

    def export_all_channels_folder(self):
        # NOTE: export from current in-memory arrays so swaps are preserved
        if self.cur_file is None or self.time_s is None or self.data is None:
            messagebox.showinfo("Export", "Load an .rhd file first.")
            return

        wide = bool(self.wide_csv_var.get())
        base_stem = self.base_stem if self.base_stem else self.cur_file.stem
        base_dir = self.cur_file.parent

        if wide:
            out_path = base_dir / f"{base_stem}.csv"
            dfw = _df_all_channels_wide(self.time_s, self.channels, self.data)
            try:
                dfw.to_csv(out_path, index=False, sep="\t")
            except Exception as e:
                messagebox.showerror("Export", f"Failed to save:\n{out_path}\n{e}")
                return
            note = ""
            if self.used_pair:
                note += " (merged pair)"
            if self.data_modified:
                note += " (modified)"
            messagebox.showinfo("Export", f"Saved 1/1 all-channel CSV{note} to:\n{out_path}")
            return

        target_dir = base_dir / base_stem
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Export", f"Cannot create folder:\n{target_dir}\n{e}")
            return

        saved = 0
        for i, name in enumerate(self.channels):
            out_path = target_dir / f"{base_stem}_{name}.csv"
            df = pd.DataFrame({"time_s": self.time_s, "value_uV": self.data[i, :]})
            try:
                df.to_csv(out_path, index=False)
                saved += 1
            except Exception as e:
                messagebox.showwarning("Export", f"Failed to save {name}: {e}")

        note = ""
        if self.used_pair:
            note += " (merged pair)"
        if self.data_modified:
            note += " (modified)"
        messagebox.showinfo(
            "Export", f"Saved {saved}/{len(self.channels)} channel CSVs{note} to:\n{target_dir}"
        )


# --------------------------- Main ---------------------------
def main() -> None:
    app = EmgRhdViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
