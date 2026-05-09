# photocurrent_preview_GUI.py
# -*- coding: utf-8 -*-
"""
Photocurrent / ABF Viewer (macOS-safe; no resampling, no scroll-zoom)

NEW (this version):
- Reads ABF embedded metadata (sweeps, channels, channel names/units).
- User can select:
    * Sweep number
    * Display channel
    * I channel (for R recognition)
    * V channel (for R recognition)
  with Auto-detection fallback.
- "Resistance Recognition (R normalization)" toggle (default OFF):
    * If ON: estimate R from V/I "signal gap" and normalize display by (signal * R(MΩ)).
    * Edge-centered method if V edges detected, else dual-window fallback.
- Keeps your existing:
    * folder browser
    * baseline subtraction
    * SVG/PNG/CSV export
    * export downsampling (exports only)
    * batch export queue

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


# --------------------------- Helpers: safe slicing ---------------------------
def _safe_slice(n: int, a, b) -> slice:
    """Clamp indices [a,b) into array length n; return full slice if invalid."""
    a = max(0, int(a))
    b = max(0, int(b))
    if n <= 1:
        return slice(0, n)
    a = min(a, n - 1)
    b = min(b, n)
    if b <= a:
        return slice(0, n)
    return slice(a, b)


def _parse_pulse_ms_from_name(path: str):
    """Extract pulse width like '50ms' or '50_0ms' from filename; return float(ms) or None."""
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(\d+(?:_\d+)?)ms", base, flags=re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1).replace("_", "."))


# --------------------------- ABF Introspection ---------------------------
def abf_describe(file_path: str) -> dict:
    """
    Load ABF and return robust metadata needed for UI:
    - sweep_list
    - channel_count
    - channel names/units
    - per-channel display labels like: "0: IN 0 [pA]"
    """
    abf = pyabf.ABF(file_path)

    sweep_list = list(getattr(abf, "sweepList", []))
    if not sweep_list:
        # fallback if sweepList missing/unexpected
        sweep_count = int(getattr(abf, "sweepCount", 1))
        sweep_list = list(range(max(1, sweep_count)))

    channel_count = int(getattr(abf, "channelCount", 0))
    # pyABF often provides these:
    ch_names = list(getattr(abf, "adcNames", [])) if hasattr(abf, "adcNames") else []
    ch_units = list(getattr(abf, "adcUnits", [])) if hasattr(abf, "adcUnits") else []

    # robust count fallback
    if channel_count <= 0:
        channel_count = max(len(ch_names), len(ch_units), 1)

    # pad lists
    if len(ch_names) < channel_count:
        ch_names += [f"ch{idx}" for idx in range(len(ch_names), channel_count)]
    if len(ch_units) < channel_count:
        ch_units += ["" for _ in range(len(ch_units), channel_count)]

    labels = []
    for i in range(channel_count):
        nm = (ch_names[i] or f"ch{i}").strip()
        un = (ch_units[i] or "").strip()
        if un:
            labels.append(f"{i}: {nm} [{un}]")
        else:
            labels.append(f"{i}: {nm}")

    return {
        "abf": abf,
        "sweep_list": sweep_list,
        "sweep_count": len(sweep_list),
        "channel_count": channel_count,
        "ch_names": ch_names,
        "ch_units": ch_units,
        "labels": labels,
        "dataRate": getattr(abf, "dataRate", None),
        "abfID": getattr(abf, "abfID", None),
        "protocol": getattr(abf, "protocol", None),
    }


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def autodetect_IV_channels(ch_names, ch_units):
    """
    Heuristic:
    - unit-first: current in pA/nA/A, voltage in mV/V
    - name fallback: contains 'current'/'im'/'i' etc, voltage 'voltage'/'vm'/'command'
    """
    units = [_norm(u) for u in ch_units]
    names = [_norm(n) for n in ch_names]

    i_candidates = [i for i, u in enumerate(units) if u in ("pa", "na", "a")]
    v_candidates = [i for i, u in enumerate(units) if u in ("mv", "v")]

    if not i_candidates:
        i_candidates = [
            i
            for i, n in enumerate(names)
            if any(k in n for k in ("current", "im", "ipsc", "epsc", "i "))
        ]
    if not v_candidates:
        v_candidates = [
            i
            for i, n in enumerate(names)
            if any(k in n for k in ("voltage", "vm", "command", "v "))
        ]

    i_ch = i_candidates[0] if i_candidates else None
    v_ch = v_candidates[0] if v_candidates else None
    return i_ch, v_ch


# --------------------------- Resistance Recognition (R estimation) ---------------------------
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
    Windowed ΔV/ΔI around voltage falling edge -> convert to MΩ.
      - Windows: [pd_V-1000 : pd_V-1000+500] and [pd_V+1000 : pd_V+1000+500]
      - Sum-based ratio (equivalent to mean-based)
      - Convert: pA -> A (×1e-12), mV -> V (×1e-3), Ω -> MΩ (/1e6)
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
    Fallback: use two broad windows to estimate ΔV(mV), ΔI(pA),
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


def estimate_resistance_MOhm(I_pA: np.ndarray, V_mV: np.ndarray) -> tuple[float, dict]:
    """
    Resistance Recognition:
    - Try edge-centered detection on V trace
    - Else fallback dual-window method
    Returns: (R_MOhm, meta)
    """
    pu_V, pd_V = _find_voltage_edges(V_mV)
    if pd_V is not None:
        R = _compute_R_MOhm_from_edges(I_pA, V_mV, pd_V)
        meta = {"method": "edge-centered (±1000, N=500)", "pu_V_idx": pu_V, "pd_V_idx": pd_V}
    else:
        R = _compute_R_MOhm_fallback(I_pA, V_mV)
        meta = {"method": "dual-window fallback", "pu_V_idx": None, "pd_V_idx": None}
    return R, meta


# --------------------------- Load & process ---------------------------
def load_and_process_abf(
    file_path: str,
    sweep: int | None = None,
    disp_ch: int | None = None,
    i_ch: int | None = None,
    v_ch: int | None = None,
    enable_Rnorm: bool = False,
):
    """
    Load ABF and produce:
        time_s:    (N,) seconds
        y_proc:    (N,) baseline-corrected; if enable_Rnorm, y_proc *= R(MΩ)
        info:      dict with meta fields (including R_MOhm and channels)
        meta_abf:  dict returned by abf_describe (for UI)
    """
    meta_abf = abf_describe(file_path)
    abf = meta_abf["abf"]

    # sweep selection
    sweep_list = meta_abf["sweep_list"]
    if sweep is None or sweep not in sweep_list:
        sweep = sweep_list[0]

    # channel selection (auto)
    if (i_ch is None) or (v_ch is None):
        ai, av = autodetect_IV_channels(meta_abf["ch_names"], meta_abf["ch_units"])
        if i_ch is None:
            i_ch = ai
        if v_ch is None:
            v_ch = av

    # display channel default
    if disp_ch is None:
        disp_ch = i_ch if i_ch is not None else 0

    ch_count = meta_abf["channel_count"]
    disp_ch = int(max(0, min(int(disp_ch), ch_count - 1)))
    i_ch = None if i_ch is None else int(max(0, min(int(i_ch), ch_count - 1)))
    v_ch = None if v_ch is None else int(max(0, min(int(v_ch), ch_count - 1)))

    # load display channel
    abf.setSweep(sweep, channel=disp_ch)
    time_s = abf.sweepX.copy()
    raw_y = abf.sweepY.copy()

    n = len(time_s)
    if n < 10:
        raise RuntimeError("Not enough data points to plot.")

    # baseline subtraction on display trace (keep your original behavior)
    bsl = _safe_slice(n, 19000, 20000)
    if (bsl.stop - bsl.start) < 5:
        bsl = _safe_slice(n, int(0.38 * n), int(0.40 * n))
    baseline = float(np.mean(raw_y[bsl]))
    y_proc = raw_y - baseline

    # Resistance Recognition (default OFF)
    R_MOhm = float("nan")
    R_meta = {"method": "disabled", "pu_V_idx": None, "pd_V_idx": None}
    scale = 1.0

    if enable_Rnorm:
        if i_ch is None or v_ch is None:
            # cannot estimate resistance without both channels
            pass
        else:
            abf.setSweep(sweep, channel=i_ch)
            raw_i = abf.sweepY.copy()
            abf.setSweep(sweep, channel=v_ch)
            raw_v = abf.sweepY.copy()

            R_MOhm, R_meta = estimate_resistance_MOhm(raw_i, raw_v)
            if np.isfinite(R_MOhm):
                scale = float(R_MOhm)
                y_proc = y_proc * scale  # normalize by R (pA·MΩ ≈ mV if y is pA)

    info = {
        "pulse_ms": _parse_pulse_ms_from_name(file_path),
        "enable_Rnorm": bool(enable_Rnorm),
        "R_MOhm": float(R_MOhm) if np.isfinite(R_MOhm) else float("nan"),
        "R_method": R_meta.get("method", "disabled"),
        "scale": float(scale),
        "baseline_raw_y": baseline,
        "file_base": os.path.splitext(os.path.basename(file_path))[0],
        "dir": os.path.dirname(file_path),
        "path": file_path,
        "n_points": n,
        "sweep": int(sweep),
        "disp_ch": int(disp_ch),
        "i_ch": i_ch,
        "v_ch": v_ch,
        "disp_label": meta_abf["labels"][disp_ch] if meta_abf.get("labels") else f"ch{disp_ch}",
        "i_label": (
            meta_abf["labels"][i_ch] if (i_ch is not None and meta_abf.get("labels")) else None
        ),
        "v_label": (
            meta_abf["labels"][v_ch] if (v_ch is not None and meta_abf.get("labels")) else None
        ),
        "pu_V_idx": R_meta.get("pu_V_idx", None),
        "pd_V_idx": R_meta.get("pd_V_idx", None),
    }
    return time_s, y_proc, info, meta_abf


# --------------------------- GUI ---------------------------
class AbfSweepViewerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABF — Sweep Viewer")
        self.geometry("1320x860")
        self.minsize(1020, 660)

        # State
        self.folder_path = tk.StringVar(value=DEFAULT_START_DIR)
        self.files = []  # list[str] of full paths
        self.queue_files: list[str] = []  # batch export queue (full paths)

        self.time_s = None
        self.y_proc = None
        self.info = {}
        self.meta_abf = None
        self.png_dpi_var = tk.IntVar(value=300)

        # NEW: Resistance Recognition toggle (default OFF)
        self.enable_Rnorm_var = tk.IntVar(value=0)

        # NEW: selectors
        self.sweep_var = tk.StringVar(value="0")
        self.disp_ch_var = tk.StringVar(value="0")
        self.i_ch_var = tk.StringVar(value="Auto")
        self.v_ch_var = tk.StringVar(value="Auto")

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
        self.folder_entry = ttk.Entry(row1, textvariable=self.folder_path, width=34)
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

        # Row B: ABF selectors
        rowB = ttk.Frame(self.top_controls)
        rowB.grid(row=1, column=0, sticky="w", pady=(0, 4))

        ttk.Label(rowB, text="Sweep:").pack(side="left")
        self.sweep_combo = ttk.Combobox(
            rowB, width=6, textvariable=self.sweep_var, state="readonly"
        )
        self.sweep_combo.pack(side="left", padx=(4, 10))
        self.sweep_combo.bind("<<ComboboxSelected>>", lambda e: self.load_selected_file())

        ttk.Label(rowB, text="Display:").pack(side="left")
        self.disp_combo = ttk.Combobox(
            rowB, width=30, textvariable=self.disp_ch_var, state="readonly"
        )
        self.disp_combo.pack(side="left", padx=(4, 10))
        self.disp_combo.bind("<<ComboboxSelected>>", lambda e: self.load_selected_file())

        ttk.Label(rowB, text="I ch:").pack(side="left")
        self.i_combo = ttk.Combobox(rowB, width=18, textvariable=self.i_ch_var, state="readonly")
        self.i_combo.pack(side="left", padx=(4, 10))
        self.i_combo.bind("<<ComboboxSelected>>", lambda e: self.load_selected_file())

        ttk.Label(rowB, text="V ch:").pack(side="left")
        self.v_combo = ttk.Combobox(rowB, width=18, textvariable=self.v_ch_var, state="readonly")
        self.v_combo.pack(side="left", padx=(4, 10))
        self.v_combo.bind("<<ComboboxSelected>>", lambda e: self.load_selected_file())

        # Row C: Processing (Resistance Recognition) + Downsampling
        rowC = ttk.Frame(self.top_controls)
        rowC.grid(row=2, column=0, sticky="w", pady=(0, 4))

        ttk.Checkbutton(
            rowC,
            text="Resistance Recognition (R normalization, default OFF)",
            variable=self.enable_Rnorm_var,
            command=self.on_toggle_Rnorm,
        ).pack(side="left")

        ttk.Label(rowC, text="Downsample ×").pack(side="left", padx=(16, 4))
        ds_combo = ttk.Combobox(
            rowC,
            width=6,
            textvariable=self.ds_factor_var,
            values=["1", "2", "3", "5", "10"],
            state="readonly",
        )
        ds_combo.pack(side="left")

        # Row D: Data export
        rowD = ttk.Frame(self.top_controls)
        rowD.grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Button(rowD, text="Export CSV (preview)", command=self.export_csv_preview).pack(
            side="left"
        )
        ttk.Button(rowD, text="Export CSV (full file)", command=self.export_csv_full).pack(
            side="left", padx=8
        )

        # Row E: Segment
        rowE = ttk.Frame(self.top_controls)
        rowE.grid(row=4, column=0, sticky="w", pady=(2, 2))
        ttk.Label(rowE, text="Segment Start (s):").pack(side="left")
        ttk.Entry(rowE, width=10, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowE, text="End (s):").pack(side="left")
        ttk.Entry(rowE, width=10, textvariable=self.seg_end_var).pack(side="left", padx=(4, 8))
        ttk.Button(rowE, text="Apply", command=self.apply_segment_window).pack(
            side="left", padx=(6, 8)
        )
        ttk.Button(rowE, text="Full View", command=self.reset_axes).pack(side="left")

        # Row F1: X-axis
        rowF1 = ttk.Frame(self.top_controls)
        rowF1.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowF1, text="X min (s):").pack(side="left")
        ttk.Entry(rowF1, width=10, textvariable=self.xmin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowF1, text="X max (s):").pack(side="left")
        ttk.Entry(rowF1, width=10, textvariable=self.xmax_var).pack(side="left", padx=(4, 12))

        # Row F2: Y-axis + buttons
        rowF2 = ttk.Frame(self.top_controls)
        rowF2.grid(row=6, column=0, sticky="w", pady=(2, 0))
        ttk.Label(rowF2, text="Y min:").pack(side="left")
        ttk.Entry(rowF2, width=10, textvariable=self.ymin_var).pack(side="left", padx=(4, 8))
        ttk.Label(rowF2, text="Y max:").pack(side="left")
        ttk.Entry(rowF2, width=10, textvariable=self.ymax_var).pack(side="left", padx=(4, 12))
        ttk.Button(rowF2, text="Apply", command=self.apply_axes_from_inputs).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowF2, text="Grab", command=self.update_axis_inputs_from_view).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(rowF2, text="Reset", command=self.reset_axes).pack(side="left")

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

    def _refresh_selectors_from_meta(self, meta_abf: dict, keep_values: bool = True):
        # Sweep selector
        sweep_vals = [str(s) for s in meta_abf["sweep_list"]]
        self.sweep_combo["values"] = sweep_vals

        # Channel selectors
        labels = meta_abf["labels"]
        disp_vals = [f"{i}" for i in range(meta_abf["channel_count"])]
        # display combo shows label text but stores index
        self.disp_combo["values"] = labels

        # I/V combos allow Auto + labels
        iv_vals = ["Auto"] + labels
        self.i_combo["values"] = iv_vals
        self.v_combo["values"] = iv_vals

        if not keep_values:
            self.sweep_var.set(sweep_vals[0] if sweep_vals else "0")
            # default display: try auto I, else 0
            i_ch, v_ch = autodetect_IV_channels(meta_abf["ch_names"], meta_abf["ch_units"])
            if i_ch is None:
                self.disp_ch_var.set(labels[0] if labels else "0")
            else:
                self.disp_ch_var.set(labels[i_ch])
            self.i_ch_var.set("Auto")
            self.v_ch_var.set("Auto")
            return

        # keep current, but clamp if invalid
        if self.sweep_var.get() not in sweep_vals:
            self.sweep_var.set(sweep_vals[0] if sweep_vals else "0")

        # display must be one of labels
        if self.disp_ch_var.get() not in labels:
            self.disp_ch_var.set(labels[0] if labels else "0")

        # I/V either Auto or label
        if self.i_ch_var.get() not in iv_vals:
            self.i_ch_var.set("Auto")
        if self.v_ch_var.get() not in iv_vals:
            self.v_ch_var.set("Auto")

    def _label_to_index(self, meta_abf: dict, label_or_auto: str) -> int | None:
        if label_or_auto == "Auto":
            return None
        labels = meta_abf.get("labels", [])
        try:
            return labels.index(label_or_auto)
        except ValueError:
            # user might give "3" etc; try parse
            try:
                i = int(str(label_or_auto).strip())
                if 0 <= i < meta_abf["channel_count"]:
                    return i
            except Exception:
                pass
        return None

    def load_selected_file(self):
        """Load and process currently selected ABF using selected sweep/channels and Rnorm toggle."""
        if not self.files:
            return
        idxs = self.file_listbox.curselection()
        if not idxs:
            return

        path = self.files[int(idxs[0])]

        # read meta first to populate selectors (and keep previous selections if possible)
        try:
            meta_tmp = abf_describe(path)
        except Exception as e:
            messagebox.showerror("Load error", f"Failed to read ABF metadata:\n{e}")
            return

        # update selector values, keep if same file series
        self.meta_abf = meta_tmp
        self._refresh_selectors_from_meta(meta_tmp, keep_values=True)

        # parse sweep
        try:
            sweep = int(self.sweep_var.get())
        except Exception:
            sweep = meta_tmp["sweep_list"][0]

        # display channel index
        disp_ch = self._label_to_index(meta_tmp, self.disp_ch_var.get())
        if disp_ch is None:
            disp_ch = 0

        # I/V channels
        i_ch = self._label_to_index(meta_tmp, self.i_ch_var.get())
        v_ch = self._label_to_index(meta_tmp, self.v_ch_var.get())

        # load/process
        try:
            time_s, y_proc, info, meta_abf = load_and_process_abf(
                path,
                sweep=sweep,
                disp_ch=disp_ch,
                i_ch=i_ch,
                v_ch=v_ch,
                enable_Rnorm=bool(self.enable_Rnorm_var.get()),
            )
        except Exception as e:
            messagebox.showerror("Load error", str(e))
            return

        self.time_s = time_s
        self.y_proc = y_proc
        self.info = info
        self.meta_abf = meta_abf

        # ensure selectors reflect exact loaded values
        self._refresh_selectors_from_meta(meta_abf, keep_values=True)
        # set display label exactly
        self.disp_ch_var.set(meta_abf["labels"][info["disp_ch"]])

        self._replot_full()

        # Status text
        r_on = "ON" if info.get("enable_Rnorm", False) else "OFF"
        Rtxt = "nan" if not np.isfinite(info.get("R_MOhm", np.nan)) else f"{info['R_MOhm']:.3g}"
        disp_lab = info.get("disp_label", f"ch{info.get('disp_ch', 0)}")
        i_lab = info.get("i_label", None)
        v_lab = info.get("v_label", None)

        iv_note = ""
        if r_on == "ON":
            iv_note = f" | I={i_lab or 'Auto?'} | V={v_lab or 'Auto?'}"

        self.status_var.set(
            f"Loaded: {os.path.basename(path)} | sweep={info['sweep']} | {disp_lab} | "
            f"N={info['n_points']} | Rnorm={r_on} | R(MΩ)={Rtxt} | method={info.get('R_method','?')}{iv_note} | "
            f"baseline={info['baseline_raw_y']:.3g}"
        )

    def on_toggle_Rnorm(self):
        if self.files and self.file_listbox.curselection():
            self.load_selected_file()

    # ---------- Plot helpers ----------
    def _clear_plot(self):
        self.ax.clear()
        self.canvas.draw_idle()

    def _ylabel_from_disp(self) -> str:
        if not self.meta_abf or not self.info:
            return "Signal"
        disp_ch = self.info.get("disp_ch", 0)
        unit = ""
        try:
            unit = (self.meta_abf["ch_units"][disp_ch] or "").strip()
        except Exception:
            unit = ""
        if self.info.get("enable_Rnorm", False) and np.isfinite(self.info.get("R_MOhm", np.nan)):
            # normalization applied
            if unit:
                return f"Signal ({unit}·MΩ)"
            return "Signal (×MΩ)"
        else:
            if unit:
                return f"Signal ({unit})"
            return "Signal"

    def _replot_full(self):
        if self.time_s is None:
            self._clear_plot()
            return
        self.ax.clear()
        self.ax.plot(self.time_s, self.y_proc, color=LINE_COLOR, lw=1.0)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel(self._ylabel_from_disp())
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

    # ---------- Axis controls ----------
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
        y = self.y_proc[mask]
        k = self._get_ds_factor()
        t, y = self._downsample_xy(t, y, k)
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
            ax.plot(t, y, color=LINE_COLOR, lw=1.0)
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
        t_ds, y_ds = self._downsample_xy(self.time_s, self.y_proc, k)
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
            ax.plot(t_ds, y_ds, color=LINE_COLOR, lw=1.0)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel(self._ylabel_from_disp())
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
        y = self.y_proc[mask]
        k = self._get_ds_factor()
        t, y = self._downsample_xy(t, y, k)
        df = pd.DataFrame({"time_s": t, "signal": y})
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
        t, y = self._downsample_xy(self.time_s, self.y_proc, k)
        df = pd.DataFrame({"time_s": t, "signal": y})
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
        """
        Batch export current processed SIGNAL (baseline corrected; and Rnorm if toggle ON)
        for all files in queue, as full-file CSV (downsample applies).
        Uses the current selector values (sweep/display/I/V) for every file.
        """
        if not self.queue_files:
            messagebox.showinfo("Export Queue", "Queue is empty.")
            return

        # parse selection settings once (apply to all)
        sweep = None
        disp_label = self.disp_ch_var.get()
        i_label = self.i_ch_var.get()
        v_label = self.v_ch_var.get()

        try:
            sweep = int(self.sweep_var.get())
        except Exception:
            sweep = None

        total = 0
        ok = 0
        k = self._get_ds_factor()

        for f in self.queue_files:
            total += 1
            try:
                meta_abf = abf_describe(f)

                # map labels to indices for this file (labels may differ)
                disp_ch = self._label_to_index(meta_abf, disp_label)
                i_ch = self._label_to_index(meta_abf, i_label)
                v_ch = self._label_to_index(meta_abf, v_label)

                time_s, y_proc, info, _ = load_and_process_abf(
                    f,
                    sweep=sweep,
                    disp_ch=disp_ch,
                    i_ch=i_ch,
                    v_ch=v_ch,
                    enable_Rnorm=bool(self.enable_Rnorm_var.get()),
                )
                t_ds, y_ds = self._downsample_xy(time_s, y_proc, k)

                out_dir = info.get("dir", os.path.dirname(f))
                base = info.get("file_base", Path(f).stem)
                out_path = os.path.join(out_dir, f"{base}.csv")
                pd.DataFrame({"time_s": t_ds, "signal": y_ds}).to_csv(out_path, index=False)
                ok += 1
            except Exception as e:
                print(f"[WARN] failed to export {os.path.basename(f)}: {e}")

        messagebox.showinfo("Export Queue", f"Exported {ok}/{total} file(s) to CSV.")


# --------------------------- Main ---------------------------
def main() -> None:
    app = AbfSweepViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
