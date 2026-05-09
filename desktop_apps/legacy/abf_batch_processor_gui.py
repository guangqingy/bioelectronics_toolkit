# photocurrent_multiple_process_GUI.py
# -*- coding: utf-8 -*-
"""
ABF Batch Analyzer GUI (compact; macOS-safe; no in-GUI counts; CSV-only)
- Left: folder & .abf list. Right: compact controls.
- No dynamic "counts" displayed inside the GUI; completion is reported via dialogs only.
- Parsing:
    {main}_{treat}_sample_{sample}_{spot}_{seq}.abf (case-insensitive)
    * Auto-scan suggested MAIN/TREAT from filenames; user can edit.
- Power series:
    * Preset dropdown (common series) OR manual entry (comma-separated floats).
    * Any length is supported (index -> power mapping).
- Processing:
    * Optional reorganization to {base}/{main}_{treat}/sample_{id}/
    * Optional renumber: if a sequence doesn't start at 0000, remap & rename to start at 0
    * Per-file CSV segment saved next to each file (_segment.csv)
    * Per-(main,treat) summary CSV saved under {base}/{main}_{treat}/summary_{main}_{treat}.csv
- No wheel zoom, no image exports, minimal row density.

Requires: pyabf, numpy, pandas, matplotlib (for internal debug only), tkinter
    pip install pyabf numpy pandas matplotlib
"""

import glob
import os
import re
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt  # used only in pulse debug paths (not exposed)
import numpy as np
import pandas as pd
import pyabf

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

TOKEN_KEYS = ["sample", "electrode", "freestanding", "thermal", "decay"]

from config import DEFAULT_START_DIR  # noqa: E402  (load from config.py)


# --------------------------- ABF low-level helpers ---------------------------
def read_abf_sweep(filename: str):
    """Read first sweep: return time, current, voltage, analog (numpy arrays)."""
    abf = pyabf.ABF(filename)
    sweep0 = abf.sweepList[0]
    abf.setSweep(sweep0, channel=0)
    time = abf.sweepX.copy()
    I = abf.sweepY.copy()
    abf.setSweep(sweep0, channel=1)
    V = abf.sweepY.copy()
    abf.setSweep(sweep0, channel=2)
    analog = abf.sweepY.copy()
    return time, I, V, analog


def find_all_pulses(analog: np.ndarray, V: np.ndarray):
    """
    Detect analog (TTL-like) and voltage pulses.
    Returns first pu_a, pd_a, AA, pu_V, pd_V. If TTL missing, falls back to V pulses.
    """
    pu_a, pd_a, AA = [], [], []

    tmp = 0
    UP, DOWN = 1, 0
    direction = UP
    for n, _ in enumerate(analog):
        if direction == UP and analog[n] - analog[tmp] > 0.1:
            pu_a.append(n)
            AA.append(analog[n])
            tmp = n
            direction = DOWN
        elif direction == DOWN and analog[n] - analog[tmp] > 0.1:
            tmp = n
            AA.pop()
            AA.append(analog[n])
        elif direction == DOWN and analog[n] - analog[tmp] < -0.1:
            pd_a.append(n)
            tmp = n
            direction = UP
        elif direction == UP and analog[n] - analog[tmp] < -0.1:
            tmp = n

    pu_V, pd_V = [], []
    tmp = 0
    UP, DOWN = 1, 0
    direction = DOWN
    for n, _ in enumerate(V[:-10]):
        tmpval = float(np.mean(V[tmp : tmp + 10]))
        currentval = float(np.mean(V[n : n + 10]))
        if direction == UP and currentval - tmpval > 0.4:
            pu_V.append(n)
            tmp = n
            direction = DOWN
        elif direction == DOWN and currentval - tmpval > 0.4:
            tmp = n
        elif direction == DOWN and currentval - tmpval < -0.4:
            pd_V.append(n)
            tmp = n
            direction = UP
        elif direction == UP and currentval - tmpval < -0.4:
            tmp = n

    if len(pu_V) < 1:
        raise RuntimeError("Voltage pulse not found; check V trace.")

    if len(pu_a) < 1:
        # TTL missing: use V pulses as a fallback window
        return -1, (pu_V[0] + 1), -1, pu_V[0], (pd_V[0] if pd_V else pu_V[0] + 1)

    return (
        pu_a[0],
        (pd_a[0] if pd_a else pu_a[0] + 1),
        AA[0],
        pu_V[0],
        (pd_V[0] if pd_V else pu_V[0] + 1),
    )


def getR(I: np.ndarray, V: np.ndarray, pd_V_idx: int) -> float:
    """Estimate pipette resistance (MΩ) using windows around the V step."""
    start = max(0, pd_V_idx - 1000)
    end = min(len(V) - 1, pd_V_idx + 1000)
    N = 500

    I1 = np.sum(I[start : start + N])
    I2 = np.sum(I[end : end + N])
    V1 = np.sum(V[start : start + N])
    V2 = np.sum(V[end : end + N])

    Ip = abs(I2 - I1) * 1e-12  # pA -> A
    Vp = abs(V2 - V1) * 1e-3  # mV -> V
    if Ip <= 0:
        return float("nan")
    return (Vp / Ip) / 1e6  # Ohm -> MΩ


def calc_curr(I: np.ndarray, pu_a: int, pd_a: int):
    """
    Compute capacitive peak, faradaic (late) current, and integrated charge within the pulse.
    Follows the constants used in your original notebook (0.01 ms per sample assumptions).
    """
    if pu_a == -1:
        return 0.0, 0.0, 0.0

    t_d = int(10 / 0.01)  # samples before TTL
    lo = max(0, pu_a - 2 * t_d)
    hi = max(0, pu_a - t_d)
    avg_init = float(np.mean(I[lo:hi]))

    seg = I[pu_a:pd_a]
    max_I = float(np.max(seg))
    min_I = float(np.min(seg))
    c1 = max_I - avg_init
    c2 = min_I - avg_init
    capacitive = c1 if abs(c1) > abs(c2) else c2

    far_raw = float(np.mean(I[pu_a + int(8 / 0.01) : pu_a + int(9 / 0.01)]))
    far = far_raw - avg_init

    shifted = seg - avg_init
    integral_pC = float(np.sum(shifted) * 0.01 / 1000.0)  # pA*ms/1000 = pC
    return capacitive, far, integral_pC


def calc_PCs(I: np.ndarray, pu_a: int, pd_a: int, V: np.ndarray, pu_V: int):
    """Return (capacitance_peak_norm, faradaic_current_norm, integral_charge_norm, R)."""
    R = getR(I, V, pu_V)
    cap, far, integ = calc_curr(I, pu_a, pd_a)
    cap_n = cap * R if np.isfinite(R) else float("nan")
    far_n = far * R if np.isfinite(R) else float("nan")
    integ_n = integ * R if np.isfinite(R) else float("nan")
    return cap_n, far_n, integ_n, R


# --------------------------- Filename parsing ---------------------------
def build_filename_regex(
    mains: List[str], treats: List[str], tokens: List[str] = None
) -> re.Pattern:
    """Build case-insensitive regex: {main}_{treat}_sample_{sample}_{spot}_{seq}.abf"""
    if tokens is None:
        tokens = TOKEN_KEYS

    mains_sorted = sorted([m.strip() for m in mains if m.strip()], key=len, reverse=True)
    treats_sorted = sorted([t.strip() for t in treats if t.strip()], key=len, reverse=True)
    mains_part = "(?:" + "|".join(map(re.escape, mains_sorted)) + ")"
    treats_part = "(?:" + "|".join(map(re.escape, treats_sorted)) + ")"
    tokens_part = "(?:" + "|".join(map(re.escape, [t.strip() for t in tokens if t.strip()])) + ")"

    pattern = (
        rf"^(?P<main>{mains_part})_(?P<treat>{treats_part})_(?P<label>{tokens_part})_"
        rf"(?P<sample>\d+)_(?P<spot>\d+?)_(?P<seq>\d+)\.abf$"
    )
    return re.compile(pattern, flags=re.IGNORECASE)


def parse_filename(name: str, rx: re.Pattern) -> Optional[Dict]:
    """Return dict with main/treat/sample_id/spot_id/seq_index, or None if not matched."""
    m = rx.match(name.strip())
    if not m:
        return None
    seq_raw = m.group("seq")
    seq_three = int(seq_raw[-3:]) if len(seq_raw) >= 3 else int(seq_raw)
    return {
        "main": m.group("main"),
        "treat": m.group("treat"),
        "sample_id": int(m.group("sample")),
        "spot_id": int(m.group("spot")),
        "seq_index": seq_three,
    }


# --------------------------- GUI ---------------------------
class AbfBatchProcessorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ABF — Batch Processor")
        self.geometry("1200x740")
        self.minsize(980, 620)

        # State
        self.folder = tk.StringVar(value=DEFAULT_START_DIR)
        self.files: List[Path] = []
        self.move_files_var = tk.IntVar(value=1)  # reorganize into group/sample dirs?
        self.reindex_seq_var = tk.IntVar(value=0)  # renumber sequences to start at 0?

        # CSV segment window
        self.segment_mode_var = tk.IntVar(value=0)  # 0: auto (peak-centered); 1: manual (t0,t1)
        self.segment_t0_var = tk.StringVar(value="0.1")
        self.segment_t1_var = tk.StringVar(value="0.7")

        # Parsing config (editable)
        self.mains_var = tk.StringVar(value="")
        self.treats_var = tk.StringVar(value="")

        # Power presets and manual series
        self.power_presets = self._build_power_presets()
        self.power_choice = tk.StringVar(value="Custom")
        self.power_series_var = tk.StringVar(value="")

        # Layout
        self._build_layout()
        self._build_left()
        self._build_right()

        # Initial scan (no counts shown in-GUI; any totals only via dialogs if needed)
        self.scan_folder()

    # ---------- Presets ----------
    def _build_power_presets(self) -> Dict[str, List[float]]:
        return {
            "10-step (normal)": [
                3.71,
                2.79,
                1.86,
                0.995,
                0.648,
                0.553,
                0.308,
                0.100,
                0.0574,
                0.0210,
            ],
            "10-step (20x)": [92.75, 69.75, 46.50, 24.875, 16.20, 13.825, 7.70, 2.50, 1.435, 0.525],
            "12-step (old)": [
                99.822,
                86.014,
                44.818,
                16.071,
                8.828,
                3.169,
                0.656,
                0.556,
                0.31,
                0.101,
                0.058,
                0.021,
            ],
            "25-step (wide)": [
                103.6,
                90.9,
                80,
                70,
                60,
                50,
                20,
                15,
                10,
                9,
                8,
                7,
                6,
                5,
                4,
                3,
                2,
                1,
                0.86,
                0.68,
                0.6,
                0.47,
                0.36,
                0.22,
                0.075,
            ],
            "Custom": [],
        }

    # ---------- Layout ----------
    def _build_layout(self):
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
        box = ttk.LabelFrame(self.left, text="Folder")
        box.pack(fill="x")
        r1 = ttk.Frame(box)
        r1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(r1, text="Path:").pack(side="left")
        ttk.Entry(r1, textvariable=self.folder, width=26).pack(
            side="left", padx=6, fill="x", expand=True
        )
        r2 = ttk.Frame(box)
        r2.pack(fill="x", padx=6, pady=(2, 8))
        ttk.Button(r2, text="Browse…", command=self.choose_folder).pack(side="left")
        ttk.Button(r2, text="Refresh", command=self.scan_folder).pack(side="left", padx=6)

        files_box = ttk.LabelFrame(self.left, text=".abf files")
        files_box.pack(fill="both", expand=True, pady=(6, 0))
        self.listbox = tk.Listbox(files_box, height=22, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(files_box, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.listbox.config(yscrollcommand=sb.set)

    def _build_right(self):
        rowA = ttk.LabelFrame(self.right, text="Parsing Config")
        rowA.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        rowA.grid_columnconfigure(1, weight=1)
        rowA.grid_columnconfigure(3, weight=1)

        ttk.Label(rowA, text="Mains (comma):").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        ttk.Entry(rowA, textvariable=self.mains_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(rowA, text="Treats (comma):").grid(row=0, column=2, sticky="e", padx=6)
        ttk.Entry(rowA, textvariable=self.treats_var).grid(
            row=0, column=3, sticky="ew", padx=(0, 8)
        )
        ttk.Button(rowA, text="Auto-scan", command=self.autofill_mains_treats).grid(
            row=0, column=4, padx=6
        )

        rowB = ttk.LabelFrame(self.right, text="Sequence → Power (mW/mm²)")
        rowB.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        rowB.grid_columnconfigure(1, weight=1)

        ttk.Label(rowB, text="Preset:").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        self.preset_combo = ttk.Combobox(
            rowB,
            state="readonly",
            values=list(self.power_presets.keys()),
            textvariable=self.power_choice,
            width=26,
        )
        self.preset_combo.grid(row=0, column=1, sticky="w")
        self.preset_combo.bind("<<ComboboxSelected>>", self.apply_preset)

        ttk.Label(rowB, text="Manual (comma):").grid(
            row=1, column=0, sticky="e", padx=6, pady=(4, 6)
        )
        ttk.Entry(rowB, textvariable=self.power_series_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 8), pady=(4, 6)
        )

        rowC = ttk.LabelFrame(self.right, text="Options")
        rowC.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        ttk.Checkbutton(
            rowC, text="Reorganize into {main}_{treat}/{sample}_{id}/", variable=self.move_files_var
        ).pack(side="top", anchor="w", padx=6, pady=(2, 0))

        ttk.Checkbutton(
            rowC,
            text="Renumber sequences (start at 0 when first > 0)",
            variable=self.reindex_seq_var,
        ).pack(side="top", anchor="w", padx=6, pady=(2, 0))

        rseg = ttk.Frame(rowC)
        rseg.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Label(rseg, text="Segment window:").pack(side="left")
        ttk.Radiobutton(
            rseg,
            text="Auto",
            variable=self.segment_mode_var,
            value=0,
            command=self._sync_segment_entries,
        ).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(
            rseg,
            text="Manual",
            variable=self.segment_mode_var,
            value=1,
            command=self._sync_segment_entries,
        ).pack(side="left", padx=(8, 0))

        rseg2 = ttk.Frame(rowC)
        rseg2.pack(fill="x", padx=6, pady=(2, 4))
        ttk.Label(rseg2, text="t0:").pack(side="left")
        self.segment_t0_entry = ttk.Entry(rseg2, textvariable=self.segment_t0_var, width=8)
        self.segment_t0_entry.pack(side="left", padx=(6, 0))
        ttk.Label(rseg2, text="t1:").pack(side="left", padx=(10, 0))
        self.segment_t1_entry = ttk.Entry(rseg2, textvariable=self.segment_t1_var, width=8)
        self.segment_t1_entry.pack(side="left", padx=(6, 0))
        ttk.Label(rseg2, text="(s)").pack(side="left", padx=(8, 0))

        self._sync_segment_entries()

        rowD = ttk.Frame(self.right)
        rowD.grid(row=3, column=0, sticky="w")
        ttk.Button(
            rowD, text="Process Selected", command=lambda: self.process(selected_only=True)
        ).pack(side="left")
        ttk.Button(
            rowD, text="Process All", command=lambda: self.process(selected_only=False)
        ).pack(side="left", padx=8)
        ttk.Button(rowD, text="Pure CSV Conversion", command=self.pure_csv_conversion).pack(
            side="left", padx=8
        )

        ttk.Frame(self.right).grid(row=4, column=0, sticky="nsew")

    def _sync_segment_entries(self):
        state = "normal" if bool(self.segment_mode_var.get()) else "disabled"
        try:
            self.segment_t0_entry.config(state=state)
            self.segment_t1_entry.config(state=state)
        except Exception:
            pass

    def pure_csv_conversion(self):
        self.process(selected_only=False, pure_csv=True)

    # ---------- Folder ops ----------
    def choose_folder(self):
        try:
            d = filedialog.askdirectory(initialdir=self.folder.get() or DEFAULT_START_DIR)
        except Exception as e:
            messagebox.showerror("Folder", f"Folder dialog failed: {e}")
            return
        if d:
            self.folder.set(d)
            self.scan_folder()

    def scan_folder(self):
        base = Path(self.folder.get())
        if not base.is_dir():
            messagebox.showwarning("Folder", "Please choose a valid folder.")
            return
        self.files = [Path(p) for p in glob.glob(str(base / "*.abf"))]
        self.files.sort()
        self.listbox.delete(0, tk.END)
        for p in self.files:
            self.listbox.insert(tk.END, p.name)
        self.autofill_mains_treats()

    # ---------- Parsing / power helpers ----------
    def autofill_mains_treats(self):
        mains, treats = set(), set()
        token_set = {t.lower() for t in TOKEN_KEYS}
        for p in self.files:
            parts = p.name.split("_")
            if len(parts) >= 2:
                try:
                    idx_token = next(
                        i
                        for i, t in enumerate(parts)
                        if any(t.lower().startswith(k) for k in token_set)
                    )
                    if idx_token >= 2:
                        mains.add(parts[0])
                        treats.add(parts[1])
                    else:
                        mains.add(parts[0])
                        treats.add(parts[1])
                except StopIteration:
                    mains.add(parts[0])
                    treats.add(parts[1])
        if not self.mains_var.get().strip():
            self.mains_var.set(", ".join(sorted(mains)))
        if not self.treats_var.get().strip():
            self.treats_var.set(", ".join(sorted(treats)))

    def apply_preset(self, _event=None):
        key = self.power_choice.get()
        if key == "Custom":
            return
        values = self.power_presets.get(key, [])
        self.power_series_var.set(", ".join(self._format_float(v) for v in values))

    @staticmethod
    def _format_float(x: float) -> str:
        s = f"{x:.6g}"
        return s

    @staticmethod
    def _parse_csv_list(s: str) -> List[str]:
        return [t.strip() for t in s.split(",") if t.strip()]

    @staticmethod
    def _parse_float_list(s: str) -> List[float]:
        out = []
        for tok in s.split(","):
            tok = tok.strip()
            if tok == "":
                continue
            out.append(float(tok))
        return out

    @staticmethod
    def ensure_dir(p: Path):
        p.mkdir(parents=True, exist_ok=True)

    def _reorganize_target_dir(self, base: Path, main: str, treat: str, sample_id: int) -> Path:
        group_dir = base / f"{main}_{treat}"
        sample_dir = group_dir / f"sample_{sample_id}"
        self.ensure_dir(sample_dir)
        return sample_dir

    # ---------- Processing ----------
    def process(self, selected_only: bool, pure_csv: bool = False):
        if not self.files:
            messagebox.showinfo("Process", "No files to process.")
            return

        mains = self._parse_csv_list(self.mains_var.get())
        treats = self._parse_csv_list(self.treats_var.get())
        if not mains or not treats:
            messagebox.showerror("Parsing", "Please provide at least one MAIN and one TREAT.")
            return
        try:
            rx = build_filename_regex(mains, treats, tokens=TOKEN_KEYS)
        except Exception as e:
            messagebox.showerror("Parsing", f"Invalid parsing config: {e}")
            return

        power_series: List[float] = []
        if not pure_csv:
            try:
                power_series = self._parse_float_list(self.power_series_var.get())
            except Exception as e:
                messagebox.showerror("Power series", f"Invalid numbers: {e}")
                return

        indices = (
            list(self.listbox.curselection()) if selected_only else list(range(len(self.files)))
        )
        if not indices:
            messagebox.showinfo("Process", "No files selected.")
            return
        to_process = [self.files[i] for i in indices]

        move_files = bool(self.move_files_var.get())
        renumber = bool(self.reindex_seq_var.get())
        base = Path(self.folder.get())

        manual_segment = bool(self.segment_mode_var.get())
        seg_t0 = None
        seg_t1 = None

        try:
            seg_t0 = float(self.segment_t0_var.get().strip())
            seg_t1 = float(self.segment_t1_var.get().strip())
        except Exception:
            messagebox.showerror(
                "Segment window", "Invalid time range. Use seconds (e.g., 0.1 to 0.7)."
            )
            return
        if not (seg_t1 > seg_t0):
            messagebox.showerror("Segment window", "Time range requires t1 > t0.")
            return

        # ---------- Pre-scan for group-wise minimal seq index ----------
        group_min: Dict[Tuple[str, str, int, int], int] = {}
        if renumber:
            for p in to_process:
                m = rx.match(p.name)
                if not m:
                    continue
                main = m.group("main")
                treat = m.group("treat")
                sample_id = int(m.group("sample"))
                spot_id = int(m.group("spot"))
                seq_raw = m.group("seq")
                seq_idx = int(seq_raw[-3:]) if len(seq_raw) >= 3 else int(seq_raw)
                key = (main, treat, sample_id, spot_id)
                group_min[key] = min(seq_idx, group_min.get(key, seq_idx))

        grouped: Dict[Tuple[str, str], List[Dict]] = {}
        processed = 0

        for abf_path in to_process:
            m_now = rx.match(abf_path.name)
            if not m_now:
                continue

            main = m_now.group("main")
            treat = m_now.group("treat")
            sample_id = int(m_now.group("sample"))
            spot_id = int(m_now.group("spot"))
            seq_raw = m_now.group("seq")
            seq_idx = int(seq_raw[-3:]) if len(seq_raw) >= 3 else int(seq_raw)

            key_group = (main, treat, sample_id, spot_id)
            offset = group_min.get(key_group, 0) if renumber else 0
            seq_eff = seq_idx - offset if offset > 0 else seq_idx

            power_mw = None
            if not pure_csv:
                power_mw = power_series[seq_eff] if (0 <= seq_eff < len(power_series)) else None

            # Destination (reorganize or stay)
            dest_path = abf_path
            if move_files:
                try:
                    sample_dir = self._reorganize_target_dir(base, main, treat, sample_id)
                    dest_path = sample_dir / abf_path.name
                    if abf_path.resolve() != dest_path.resolve():
                        shutil.move(str(abf_path), str(dest_path))
                except Exception as e:
                    messagebox.showerror("Move", f"Failed to move {abf_path.name}: {e}")
                    continue

            # Renumber filename if needed (after move so path is final)
            if renumber and offset > 0:
                try:
                    m2 = rx.match(dest_path.name)
                    if m2:
                        seq_len = len(m2.group("seq"))
                        new_seq_str = f"{max(0, seq_eff):0{seq_len}d}"
                        prefix = dest_path.name[: m2.start("seq")]
                        suffix = dest_path.name[m2.end("seq") :]
                        new_name = prefix + new_seq_str + suffix
                        if new_name != dest_path.name:
                            new_path = dest_path.with_name(new_name)
                            if not new_path.exists():
                                dest_path = dest_path.rename(new_path)
                            else:
                                print(f"[WARN] Target exists, skip rename: {new_name}")
                except Exception as e:
                    print(f"[WARN] Rename failed ({dest_path.name}): {e}")

            # Load traces
            try:
                time, I, V, analog = read_abf_sweep(str(dest_path))
            except Exception as e:
                messagebox.showwarning("Analyze", f"{dest_path.name} failed: {e}")
                continue

            # ===================== CSV EXPORT (segment selection) =====================
            if pure_csv or manual_segment:
                t0 = max(float(time[0]), float(seg_t0))
                t1 = min(float(time[-1]), float(seg_t1))
                if t1 <= t0:
                    messagebox.showwarning(
                        "Segment window", f"{dest_path.name}: time window out of range."
                    )
                    continue
            else:
                try:
                    pu_a, pd_a, AA, pu_V, pd_V = find_all_pulses(analog, V)
                except Exception as e:
                    messagebox.showwarning("Analyze", f"{dest_path.name} failed: {e}")
                    continue

                start_idx = pu_a if pu_a != -1 else pu_V
                end_idx = pd_a if (pd_a != -1) else (pd_V if pd_V is not None else start_idx + 1)
                start_idx = max(0, min(start_idx, len(I) - 1))
                end_idx = max(start_idx + 1, min(end_idx, len(I)))

                seg_I = I[start_idx:end_idx]
                if seg_I.size == 0:
                    peak_idx = int(np.argmax(np.abs(I)))
                else:
                    peak_idx = start_idx + int(np.argmax(np.abs(seg_I)))

                t_peak = float(time[peak_idx])
                t0 = max(float(time[0]), t_peak - 0.1)
                t1 = min(float(time[-1]), t_peak + 0.1)

            mask = (time >= t0) & (time <= t1)
            t_out = time[mask]
            i_out = I[mask]
            v_out = V[mask]
            a_out = analog[mask]

            seg_df = pd.DataFrame(
                {
                    "time_s": t_out,
                    "current_pA": i_out,
                    "voltage_mV": v_out,
                    "analog": a_out,
                }
            )

            try:
                out_csv = dest_path.with_name(f"{dest_path.stem}_segment.csv")
                seg_df.to_csv(out_csv, index=False)
            except Exception as e:
                messagebox.showerror("Write CSV", f"Segment CSV failed for {dest_path.name}: {e}")
                continue
            # =================== END CSV EXPORT ===================

            if not pure_csv:
                try:
                    pu_a, pd_a, AA, pu_V, pd_V = find_all_pulses(analog, V)
                    cap_n, far_n, integ_n, R = calc_PCs(I, pu_a, pd_a, V, pu_V)
                except Exception as e:
                    messagebox.showwarning("Analyze", f"{dest_path.name} failed: {e}")
                    continue

                row_for_summary = {
                    "file": dest_path.name,
                    "main": main,
                    "treat": treat,
                    "sample_id": sample_id,
                    "spot_id": spot_id,
                    "seq_index": seq_eff,
                    "power_mW": power_mw,
                    "pulse_level": AA,
                    "capacitance_peak_norm": cap_n,
                    "faradaic_current_norm": far_n,
                    "integral_charge_norm": integ_n,
                    "pipette_resistance_MOhm": R,
                }
                grouped.setdefault((main, treat), []).append(row_for_summary)

            processed += 1

        if not pure_csv:
            for (main, treat), rows in grouped.items():
                group_dir = base / f"{main}_{treat}"
                self.ensure_dir(group_dir)
                df = pd.DataFrame(rows)
                df.sort_values(
                    by=["sample_id", "spot_id", "seq_index"], inplace=True, kind="mergesort"
                )
                try:
                    df.to_csv(
                        group_dir / f"summary_{main}_{treat}.csv", index=False, encoding="utf-8-sig"
                    )
                except Exception as e:
                    messagebox.showerror("Write CSV", f"Summary CSV failed for {main}_{treat}: {e}")

        messagebox.showinfo("Done", f"Processed {processed} file(s).")


# --------------------------- Main ---------------------------
def main() -> None:
    app = AbfBatchProcessorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
