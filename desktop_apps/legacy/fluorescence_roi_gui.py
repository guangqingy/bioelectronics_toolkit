# roi_sequence_analysis_gui.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd
import tifffile as tiff

from services.fluorescence import roi as fl_roi

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
    Image = None
    ImageDraw = None
    ImageFont = None

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle


# =========================
# Global settings
# =========================
APP_TITLE = "ROI Sequence Analysis GUI"

PREVIEW_FIG_W = 8.2
PREVIEW_FIG_H = 8.2
PREVIEW_DPI = 100

LOW_PERCENTILE = 1.0
HIGH_PERCENTILE = 99.8
DEFAULT_PLOT_FONT_SIZE = 14
ROI_COLORS = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#17becf",  # cyan
    "#bcbd22",  # olive
    "#7f7f7f",  # gray
]


# =========================
# Helper functions
# =========================
def read_first_page_as_2d(path: Path) -> np.ndarray:
    """
    Read a TIFF file and return a 2D image.
    If the TIFF contains multiple pages, use the first page.
    """
    arr = tiff.imread(str(path))
    arr = np.asarray(arr)

    if arr.ndim == 2:
        return arr

    if arr.ndim == 3:
        return arr[0]

    raise ValueError(f"Unsupported TIFF shape: {arr.shape}")


def normalize_for_display(
    img: np.ndarray,
    low_p: float = LOW_PERCENTILE,
    high_p: float = HIGH_PERCENTILE
) -> np.ndarray:
    """
    Normalize image to [0, 1] for display using percentile stretch.
    """
    arr = np.asarray(img, dtype=np.float32)

    if arr.size == 0:
        return np.zeros_like(arr, dtype=np.float32)

    vmin = float(np.percentile(arr, low_p))
    vmax = float(np.percentile(arr, high_p))

    if vmax <= vmin:
        vmin = float(np.min(arr))
        vmax = float(np.max(arr))

    if vmax <= vmin:
        return np.zeros_like(arr, dtype=np.float32)

    out = (arr - vmin) / (vmax - vmin)
    out = np.clip(out, 0.0, 1.0)
    return out


def natural_sequence_key_from_name(name: str) -> tuple:
    """
    Extract a numeric sorting key from filename stem.
    Example:
        MitoTrack_MOS_stimulation2001_stack1_red.tif -> (..., 2001)
    """
    stem = Path(name).stem
    nums = re.findall(r"\d+", stem)
    if nums:
        return tuple(int(x) for x in nums)
    return (0,)


def collect_stack_pairs(folder: Path) -> list[dict]:
    """
    Collect paired stack1/stack2 TIFF files from a folder.

    Expected names like:
      xxx_stack1_red.tif
      xxx_stack2_blue.tif
    """
    records = fl_roi.collect_pairs(folder, include_unpaired=False)
    out = []
    for record in records:
        out.append(
            {
                "base": record["base"],
                "stack1": Path(record["stack1"]) if record.get("stack1") else None,
                "stack2": Path(record["stack2"]) if record.get("stack2") else None,
            }
        )
    return out


def safe_ratio(a: float, b: float) -> float:
    return fl_roi.safe_ratio(a, b)


def _unit_to_um_scale(unit: str | None) -> float | None:
    if not unit:
        return None
    u = unit.strip().lower()
    if u in {"um", "micron", "microns", "micrometer", "micrometers"}:
        return 1.0
    if u in {"nm", "nanometer", "nanometers"}:
        return 1e-3
    if u in {"mm", "millimeter", "millimeters"}:
        return 1e3
    if u in {"cm", "centimeter", "centimeters"}:
        return 1e4
    if u in {"m", "meter", "meters"}:
        return 1e6
    if u in {"in", "inch", "inches"}:
        return 25400.0
    return None


def _rational_to_float(v) -> float | None:
    try:
        if isinstance(v, tuple) and len(v) == 2:
            num = float(v[0])
            den = float(v[1])
            if abs(den) < 1e-12:
                return None
            return num / den
        return float(v)
    except Exception:
        return None


def infer_pixel_size_um_from_tiff(path: Path) -> float | None:
    """
    Try to infer pixel size (um/pixel) from TIFF metadata.
    Priority:
      1) Standard TIFF tags: XResolution + ResolutionUnit
      2) OME-XML: PhysicalSizeX + PhysicalSizeXUnit
    """
    try:
        with tiff.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            tags = page.tags

            xres_tag = tags.get("XResolution")
            unit_tag = tags.get("ResolutionUnit")
            xres = _rational_to_float(xres_tag.value) if xres_tag is not None else None
            unit_value = unit_tag.value if unit_tag is not None else None

            # TIFF ResolutionUnit: 2=inches, 3=centimeters
            if xres is not None and xres > 0 and unit_value is not None:
                if int(unit_value) == 2:
                    return 25400.0 / xres
                if int(unit_value) == 3:
                    return 10000.0 / xres

            ome_xml = tf.ome_metadata
            if ome_xml:
                m_val = re.search(r'PhysicalSizeX="([0-9eE+\\-.]+)"', ome_xml)
                m_unit = re.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                if m_val:
                    px_val = float(m_val.group(1))
                    unit_str = m_unit.group(1) if m_unit else "um"
                    scale = _unit_to_um_scale(unit_str)
                    if scale is not None and px_val > 0:
                        return px_val * scale
    except Exception:
        return None

    return None


def compute_roi_metrics(img: np.ndarray, roi_xyxy: tuple[int, int, int, int]) -> dict:
    """
    Compute ROI metrics for one image.
    roi_xyxy = (x1, y1, x2, y2), with x2/y2 exclusive after clipping.
    """
    return fl_roi.metrics_2d(img, roi_xyxy)


# =========================
# Main GUI
# =========================
class ROISequenceAnalysisGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1360x900")

        self.script_dir = Path(__file__).resolve().parent
        self.current_folder = self.script_dir

        self.sequence_records: list[dict] = []
        self.analysis_indices: list[int] = []
        self.current_preview_index = 0
        self.current_preview_stack = "stack1"  # or stack2

        self.preview_image_raw: np.ndarray | None = None
        self.preview_image_disp: np.ndarray | None = None

        self.roi_rect_patch = None
        self.roi_start_xy = None
        self.roi_order: list[str] = ["ROI1", "ROI2"]
        self.roi_xyxy_map: dict[str, tuple[int, int, int, int] | None] = {
            "ROI1": None,
            "ROI2": None,
        }
        self.var_active_roi = tk.StringVar(value="ROI1")
        self.var_plot_font_size = tk.StringVar(value=str(DEFAULT_PLOT_FONT_SIZE))
        self.latest_df: pd.DataFrame | None = None
        self.adv_heatmap_cbar = None
        self.adv_fig_legend = None

        self._build_layout()
        self._refresh_sequence_list()

    # -------------------------
    # Layout
    # -------------------------
    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # Left panel
        left = ttk.Frame(self, padding=8)
        left.grid(row=0, column=0, sticky="nsw")
        left.rowconfigure(2, weight=1)
        left.rowconfigure(5, weight=1)

        ttk.Label(left, text="Folder").grid(row=0, column=0, sticky="w")

        folder_bar = ttk.Frame(left)
        folder_bar.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        folder_bar.columnconfigure(0, weight=1)

        self.var_folder = tk.StringVar(value=str(self.current_folder))
        self.ent_folder = ttk.Entry(folder_bar, textvariable=self.var_folder, width=34)
        self.ent_folder.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        btn_browse = ttk.Button(folder_bar, text="Browse", command=self._browse_folder)
        btn_browse.grid(row=0, column=1, sticky="ew")

        self.seq_listbox = tk.Listbox(
            left, width=38, height=20, selectmode=tk.EXTENDED, exportselection=False
        )
        self.seq_listbox.grid(row=2, column=0, sticky="nsew")
        self.seq_listbox.bind("<<ListboxSelect>>", self._on_sequence_selected)
        self.seq_listbox.bind("<Double-Button-1>", lambda e: self._add_selected_to_analysis())

        move_controls = ttk.Frame(left)
        move_controls.grid(row=3, column=0, sticky="ew", pady=(6, 2))
        ttk.Button(
            move_controls, text="Add ->", command=self._add_selected_to_analysis
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(
            move_controls, text="<- Remove", command=self._remove_selected_from_analysis
        ).grid(row=0, column=1)

        ttk.Label(left, text="Files to analyze").grid(
            row=4, column=0, sticky="w", pady=(4, 2)
        )
        self.analysis_listbox = tk.Listbox(
            left, width=38, height=11, selectmode=tk.EXTENDED, exportselection=False
        )
        self.analysis_listbox.grid(row=5, column=0, sticky="nsew")
        self.analysis_listbox.bind(
            "<Double-Button-1>", lambda e: self._remove_selected_from_analysis()
        )

        analysis_controls = ttk.Frame(left)
        analysis_controls.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        analysis_controls.columnconfigure(1, weight=1)
        ttk.Button(
            analysis_controls, text="Clear List", command=self._clear_analysis_selection
        ).grid(row=0, column=0, padx=(0, 6))

        # Right panel
        right = ttk.Frame(self, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.lbl_status = ttk.Label(right, text="No sequence loaded.")
        self.lbl_status.grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.right_notebook = ttk.Notebook(right)
        self.right_notebook.grid(row=1, column=0, sticky="nsew")

        self.page_preview = ttk.Frame(self.right_notebook, padding=8)
        self.page_basic = ttk.Frame(self.right_notebook, padding=8)
        self.page_basic_plot = ttk.Frame(self.right_notebook, padding=8)
        self.page_advanced = ttk.Frame(self.right_notebook, padding=8)
        self.right_notebook.add(self.page_preview, text="Page 0: ROI")
        self.right_notebook.add(self.page_basic, text="Page 1: Basic Controls")
        self.right_notebook.add(self.page_basic_plot, text="Page 2: Basic Plots")
        self.right_notebook.add(self.page_advanced, text="Page 3: Advanced Plots")

        self.page_preview.columnconfigure(0, weight=1)
        self.page_preview.rowconfigure(1, weight=1)

        self.page_basic.columnconfigure(0, weight=1)
        self.page_basic.rowconfigure(0, weight=1)

        self.page_basic_plot.columnconfigure(0, weight=1)
        self.page_basic_plot.rowconfigure(0, weight=1)

        # Page 0 controls: ROI selection / drawing target
        preview_controls = ttk.LabelFrame(self.page_preview, text="ROI Controls", padding=6)
        preview_controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        preview_controls.columnconfigure(99, weight=1)

        ttk.Label(preview_controls, text="Preview stack").grid(row=0, column=0, padx=(0, 4))
        self.var_preview_stack = tk.StringVar(value="stack1")
        cmb_stack = ttk.Combobox(
            preview_controls,
            textvariable=self.var_preview_stack,
            values=["stack1", "stack2"],
            state="readonly",
            width=10
        )
        cmb_stack.grid(row=0, column=1, padx=(0, 12))
        cmb_stack.bind("<<ComboboxSelected>>", lambda e: self._change_preview_stack())

        ttk.Label(preview_controls, text="Draw target").grid(row=0, column=2, padx=(0, 4))
        self.cmb_active_roi = ttk.Combobox(
            preview_controls,
            textvariable=self.var_active_roi,
            values=self.roi_order,
            state="readonly",
            width=8
        )
        self.cmb_active_roi.grid(row=0, column=3, padx=(0, 12))

        ttk.Button(preview_controls, text="Add ROI", command=self._add_roi).grid(
            row=0, column=4, padx=(0, 6)
        )
        ttk.Button(preview_controls, text="Remove ROI", command=self._remove_active_roi).grid(
            row=0, column=5, padx=(0, 6)
        )
        ttk.Button(preview_controls, text="Clear ROI", command=self._clear_roi).grid(
            row=0, column=6, padx=(0, 6)
        )
        ttk.Button(preview_controls, text="Clear All ROI", command=self._clear_all_rois).grid(
            row=0, column=7, padx=(0, 6)
        )

        # Control bar (Page 1)
        controls = ttk.LabelFrame(self.page_basic, text="Controls", padding=8)
        controls.grid(row=0, column=0, sticky="nsew")
        controls.columnconfigure(0, weight=1)

        # Row 1/7: main selectors
        controls_row1 = ttk.Frame(controls)
        controls_row1.grid(row=0, column=0, sticky="ew")
        controls_row1.columnconfigure(99, weight=1)

        ttk.Label(controls_row1, text="Output prefix").grid(row=0, column=0, padx=(0, 4))
        self.var_prefix = tk.StringVar(value="roi_analysis")
        ttk.Entry(controls_row1, textvariable=self.var_prefix, width=20).grid(
            row=0, column=1, padx=(0, 8), sticky="w"
        )

        # Row 2/6: action buttons
        controls_row2 = ttk.Frame(controls)
        controls_row2.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls_row2.columnconfigure(99, weight=1)

        ttk.Button(controls_row2, text="Run Analysis", command=self._run_analysis).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(controls_row2, text="Export GIF", command=self._export_gif).grid(
            row=0, column=1, padx=(0, 8)
        )

        # Row 3/6: timing + scale parameters
        controls_row3 = ttk.Frame(controls)
        controls_row3.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls_row3.columnconfigure(99, weight=1)

        ttk.Label(controls_row3, text="Frame ms").grid(row=0, column=0, padx=(0, 4))
        self.var_gif_duration_ms = tk.StringVar(value="2000")
        ttk.Entry(controls_row3, textvariable=self.var_gif_duration_ms, width=12).grid(
            row=0, column=1, padx=(0, 12), sticky="w"
        )

        ttk.Label(controls_row3, text="Scale bar (um)").grid(row=0, column=2, padx=(0, 4))
        self.var_gif_scalebar_um = tk.StringVar(value="200")
        ttk.Entry(controls_row3, textvariable=self.var_gif_scalebar_um, width=10).grid(
            row=0, column=3, padx=(0, 12), sticky="w"
        )

        ttk.Label(controls_row3, text="GIF label scale").grid(row=0, column=4, padx=(0, 4))
        self.var_gif_label_scale = tk.StringVar(value="2.0")
        ttk.Entry(controls_row3, textvariable=self.var_gif_label_scale, width=8).grid(
            row=0, column=5, padx=(0, 8), sticky="w"
        )

        # Row 4/6: GIF overlay details
        controls_row4 = ttk.Frame(controls)
        controls_row4.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        controls_row4.columnconfigure(99, weight=1)

        ttk.Label(controls_row4, text="Pixel size override (um/px)").grid(
            row=0, column=0, padx=(0, 4)
        )
        self.var_gif_pixel_size_um = tk.StringVar(value="")
        ttk.Entry(controls_row4, textvariable=self.var_gif_pixel_size_um, width=12).grid(
            row=0, column=1, padx=(0, 12), sticky="w"
        )

        self.var_gif_show_name = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            controls_row4, text="Show frame name", variable=self.var_gif_show_name
        ).grid(row=0, column=2, padx=(0, 12), sticky="w")

        self.var_gif_show_scalebar = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            controls_row4, text="Show scale bar", variable=self.var_gif_show_scalebar
        ).grid(row=0, column=3, padx=(0, 8), sticky="w")

        # Row 5/6: background settings
        controls_row5 = ttk.Frame(controls)
        controls_row5.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        controls_row5.columnconfigure(99, weight=1)

        self.var_use_background = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls_row5,
            text="Use background ROI",
            variable=self.var_use_background,
            command=self._on_plot_metric_change,
        ).grid(row=0, column=0, padx=(0, 10), sticky="w")

        self.var_bg_mode = tk.StringVar(value="None")
        ttk.Label(controls_row5, text="BG source").grid(row=0, column=1, padx=(0, 4))
        self.cmb_bg_mode = ttk.Combobox(
            controls_row5,
            textvariable=self.var_bg_mode,
            values=["None", "Bottom-right corner", "Top-left corner", "ROI (absolute)"],
            state="readonly",
            width=20
        )
        self.cmb_bg_mode.grid(row=0, column=2, padx=(0, 8), sticky="w")
        self.cmb_bg_mode.bind("<<ComboboxSelected>>", self._on_bg_mode_change)

        self.var_bg_roi_name = tk.StringVar(value="ROI1")
        ttk.Label(controls_row5, text="BG ROI").grid(row=0, column=3, padx=(0, 4))
        self.cmb_bg_roi = ttk.Combobox(
            controls_row5,
            textvariable=self.var_bg_roi_name,
            values=self.roi_order,
            state="readonly",
            width=10,
        )
        self.cmb_bg_roi.grid(row=0, column=4, padx=(0, 8), sticky="w")
        self.cmb_bg_roi.bind("<<ComboboxSelected>>", self._on_plot_metric_change)

        # Row 6/6: plot style
        controls_row6 = ttk.Frame(controls)
        controls_row6.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        controls_row6.columnconfigure(99, weight=1)

        ttk.Label(controls_row6, text="Plot font size").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(controls_row6, textvariable=self.var_plot_font_size, width=8).grid(
            row=0, column=1, padx=(0, 8), sticky="w"
        )

        # Row 7/9: plot metric
        controls_row7 = ttk.Frame(controls)
        controls_row7.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        controls_row7.columnconfigure(99, weight=1)

        ttk.Label(controls_row7, text="Plot metric").grid(row=0, column=0, padx=(0, 4))
        self.var_plot_metric = tk.StringVar(value="Mean (BG-normalized)")
        self.cmb_plot_metric = ttk.Combobox(
            controls_row7,
            textvariable=self.var_plot_metric,
            values=[
                "Mean (absolute)",
                "Mean (BG-subtracted)",
                "Mean (BG-normalized)",
                "Top20 mean (absolute)",
                "Top20 mean (BG-subtracted)",
                "Top20 mean (BG-normalized)",
            ],
            state="readonly",
            width=24,
        )
        self.cmb_plot_metric.grid(row=0, column=1, padx=(0, 8), sticky="w")
        self.cmb_plot_metric.bind("<<ComboboxSelected>>", self._on_plot_metric_change)

        # Row 8/9: reference normalization (keep on a dedicated line)
        controls_row8 = ttk.Frame(controls)
        controls_row8.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        controls_row8.columnconfigure(99, weight=1)

        ttk.Label(controls_row8, text="Ref seq (=1)").grid(row=0, column=0, padx=(0, 4))
        self.var_ref_sequence = tk.StringVar(value="")
        self.ent_ref_sequence = ttk.Entry(controls_row8, textvariable=self.var_ref_sequence, width=8)
        self.ent_ref_sequence.grid(row=0, column=1, padx=(0, 4), sticky="w")
        ttk.Button(controls_row8, text="Apply Ref", command=self._on_plot_metric_change).grid(
            row=0, column=2, padx=(0, 4)
        )

        # Row 9/9: stimulation/event marker list on another line to avoid clipping
        controls_row9 = ttk.Frame(controls)
        controls_row9.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        controls_row9.columnconfigure(99, weight=1)

        ttk.Label(controls_row9, text="Event seq(s)").grid(row=0, column=0, padx=(0, 4))
        self.var_event_sequences = tk.StringVar(value="")
        self.ent_event_sequences = ttk.Entry(
            controls_row9, textvariable=self.var_event_sequences, width=14
        )
        self.ent_event_sequences.grid(row=0, column=1, padx=(0, 4), sticky="w")
        ttk.Button(controls_row9, text="Apply Events", command=self._on_plot_metric_change).grid(
            row=0, column=2, padx=(0, 4)
        )

        # Preview figure (Page 0)
        preview_outer = ttk.LabelFrame(
            self.page_preview, text="Preview (drag to draw ROI)", padding=8
        )
        preview_outer.grid(row=1, column=0, sticky="nsew")
        preview_outer.columnconfigure(0, weight=1)
        preview_outer.rowconfigure(0, weight=1)
        self.fig = Figure(figsize=(PREVIEW_FIG_W, PREVIEW_FIG_H), dpi=PREVIEW_DPI)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()

        self.canvas = FigureCanvasTkAgg(self.fig, master=preview_outer)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)

        # Plot figure (Page 2)
        plot_outer = ttk.LabelFrame(self.page_basic_plot, text="Basic Analysis Plots", padding=8)
        plot_outer.grid(row=0, column=0, sticky="nsew")
        plot_outer.columnconfigure(0, weight=1)
        plot_outer.rowconfigure(0, weight=1)
        self.fig_plot = Figure(figsize=(9.6, 7.6), dpi=100)
        self.ax1 = self.fig_plot.add_subplot(221)
        self.ax2 = self.fig_plot.add_subplot(222)
        self.ax3 = self.fig_plot.add_subplot(223)
        self.ax4 = self.fig_plot.add_subplot(224)

        self.canvas_plot = FigureCanvasTkAgg(self.fig_plot, master=plot_outer)
        self.canvas_plot.get_tk_widget().pack(fill="both", expand=True)

        # Advanced page (Page 3)
        self.page_advanced.columnconfigure(0, weight=1)
        self.page_advanced.rowconfigure(1, weight=1)

        adv_controls = ttk.LabelFrame(self.page_advanced, text="Advanced Controls", padding=8)
        adv_controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        adv_controls.columnconfigure(20, weight=1)

        ttk.Label(adv_controls, text="Center ROI").grid(row=0, column=0, padx=(0, 4))
        self.var_center_roi = tk.StringVar(value="ROI1")
        self.cmb_center_roi = ttk.Combobox(
            adv_controls,
            textvariable=self.var_center_roi,
            values=self.roi_order,
            state="readonly",
            width=10
        )
        self.cmb_center_roi.grid(row=0, column=1, padx=(0, 10))

        ttk.Label(adv_controls, text="Baseline frames").grid(row=0, column=2, padx=(0, 4))
        self.var_baseline_frames = tk.StringVar(value="2")
        ttk.Entry(adv_controls, textvariable=self.var_baseline_frames, width=8).grid(
            row=0, column=3, padx=(0, 10), sticky="w"
        )

        ttk.Label(adv_controls, text="Trend correction").grid(row=0, column=4, padx=(0, 4))
        self.var_adv_trend_mode = tk.StringVar(value="Linear divide")
        self.cmb_adv_trend_mode = ttk.Combobox(
            adv_controls,
            textvariable=self.var_adv_trend_mode,
            values=["None", "Linear divide", "Linear subtract"],
            state="readonly",
            width=16,
        )
        self.cmb_adv_trend_mode.grid(row=0, column=5, padx=(0, 10))
        self.cmb_adv_trend_mode.bind("<<ComboboxSelected>>", self._redraw_advanced_from_latest)

        ttk.Button(
            adv_controls, text="Refresh Advanced", command=self._redraw_advanced_from_latest
        ).grid(row=0, column=6, padx=(0, 10))
        ttk.Label(
            adv_controls,
            text="Repeated-stim evolution mode"
        ).grid(row=0, column=7, sticky="w")
        # Second control row to prevent right-side clipping
        adv_controls_row2 = ttk.Frame(adv_controls)
        adv_controls_row2.grid(row=1, column=0, columnspan=20, sticky="ew", pady=(8, 0))
        adv_controls_row2.columnconfigure(99, weight=1)
        ttk.Button(
            adv_controls_row2, text="Export Advanced Plot", command=self._export_advanced_plot
        ).grid(row=0, column=0, padx=(0, 10), sticky="w")

        adv_plot_outer = ttk.LabelFrame(self.page_advanced, text="Advanced Presentation", padding=8)
        adv_plot_outer.grid(row=1, column=0, sticky="nsew")
        adv_plot_outer.columnconfigure(0, weight=1)
        adv_plot_outer.rowconfigure(0, weight=1)

        self.fig_adv = Figure(figsize=(10.8, 7.4), dpi=100)
        self.ax_adv1 = self.fig_adv.add_subplot(221)
        self.ax_adv2 = self.fig_adv.add_subplot(222)
        self.ax_adv3 = self.fig_adv.add_subplot(223)
        self.ax_adv4 = self.fig_adv.add_subplot(224)

        self.canvas_adv = FigureCanvasTkAgg(self.fig_adv, master=adv_plot_outer)
        self.canvas_adv.get_tk_widget().pack(fill="both", expand=True)

        self._draw_empty_preview()
        self._draw_empty_plots()
        self._draw_empty_advanced_plots()
        self._refresh_roi_selector()
        self.right_notebook.select(self.page_preview)

    # -------------------------
    # Folder / sequence loading
    # -------------------------
    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select folder containing stack1/stack2 TIFF files",
            initialdir=str(self.current_folder)
        )
        if not folder:
            return

        self.current_folder = Path(folder)
        self.var_folder.set(str(self.current_folder))
        self._refresh_sequence_list()

    def _refresh_sequence_list(self) -> None:
        self.seq_listbox.delete(0, tk.END)
        self.analysis_listbox.delete(0, tk.END)
        self.analysis_indices = []
        self.latest_df = None
        self.sequence_records = collect_stack_pairs(self.current_folder)

        for rec in self.sequence_records:
            has1 = "Y" if rec["stack1"] is not None else "-"
            has2 = "Y" if rec["stack2"] is not None else "-"
            item_text = f"{rec['base']}   [stack1:{has1}  stack2:{has2}]"
            self.seq_listbox.insert(tk.END, item_text)

        if self.sequence_records:
            self.seq_listbox.selection_clear(0, tk.END)
            self.seq_listbox.selection_set(0)
            self.seq_listbox.event_generate("<<ListboxSelect>>")
        else:
            self.lbl_status.config(text="No paired stack files found.")
            self.preview_image_raw = None
            self.preview_image_disp = None
            self._draw_empty_preview()
            self._draw_empty_plots()
            self._draw_empty_advanced_plots()

    def _refresh_analysis_listbox(self) -> None:
        self.analysis_listbox.delete(0, tk.END)
        for rec_idx in self.analysis_indices:
            rec = self.sequence_records[rec_idx]
            has1 = "Y" if rec["stack1"] is not None else "-"
            has2 = "Y" if rec["stack2"] is not None else "-"
            self.analysis_listbox.insert(
                tk.END, f"{rec['base']}   [stack1:{has1}  stack2:{has2}]"
            )

    def _add_selected_to_analysis(self) -> None:
        if not self.sequence_records:
            return

        selected_indices = list(self.seq_listbox.curselection())
        if not selected_indices:
            return

        for idx in selected_indices:
            if idx not in self.analysis_indices:
                self.analysis_indices.append(idx)

        self._refresh_analysis_listbox()

    def _clear_analysis_selection(self) -> None:
        self.analysis_indices = []
        self.analysis_listbox.delete(0, tk.END)

    def _remove_selected_from_analysis(self) -> None:
        selected_pos = sorted(self.analysis_listbox.curselection(), reverse=True)
        if not selected_pos:
            return

        for pos in selected_pos:
            del self.analysis_indices[pos]

        self._refresh_analysis_listbox()

    def _on_sequence_selected(self, event=None) -> None:
        selection = self.seq_listbox.curselection()
        if not selection or not self.sequence_records:
            return

        self.current_preview_index = selection[0]
        self._load_current_preview()

    def _change_preview_stack(self) -> None:
        self.current_preview_stack = self.var_preview_stack.get().strip()
        self._load_current_preview()

    def _load_current_preview(self) -> None:
        if not self.sequence_records:
            return

        rec = self.sequence_records[self.current_preview_index]
        path = rec.get(self.current_preview_stack)

        if path is None:
            self.preview_image_raw = None
            self.preview_image_disp = None
            self.lbl_status.config(
                text=f"{rec['base']} | {self.current_preview_stack} not found."
            )
            self._draw_empty_preview()
            return

        try:
            img = read_first_page_as_2d(path)
        except Exception as e:
            messagebox.showerror("Read error", f"Failed to load image:\n{e}")
            return

        self.preview_image_raw = img
        self.preview_image_disp = normalize_for_display(img)

        drawn_roi_count = sum(
            1 for name in self.roi_order if self.roi_xyxy_map.get(name) is not None
        )
        self.lbl_status.config(
            text=(
                f"Preview: {rec['base']} | {self.current_preview_stack} | "
                f"shape={img.shape} | Active={self.var_active_roi.get()} | "
                f"Drawn ROI={drawn_roi_count}/{len(self.roi_order)}"
            )
        )
        self._redraw_preview()

    def _roi_color(self, roi_name: str) -> str:
        try:
            idx = self.roi_order.index(roi_name)
        except ValueError:
            idx = 0
        return ROI_COLORS[idx % len(ROI_COLORS)]

    def _refresh_roi_selector(self) -> None:
        self.cmb_active_roi.configure(values=self.roi_order)
        if self.var_active_roi.get() not in self.roi_order:
            self.var_active_roi.set(self.roi_order[0])
        if hasattr(self, "cmb_bg_roi"):
            self.cmb_bg_roi.configure(values=self.roi_order)
            if self.var_bg_roi_name.get() not in self.roi_order:
                self.var_bg_roi_name.set(self.roi_order[0])
        if hasattr(self, "cmb_center_roi"):
            self.cmb_center_roi.configure(values=self.roi_order)
            if self.var_center_roi.get() not in self.roi_order:
                self.var_center_roi.set(self.roi_order[0])
        self._on_bg_mode_change()

    def _on_bg_mode_change(self, event=None) -> None:
        if not hasattr(self, "cmb_bg_roi"):
            return
        mode = self.var_bg_mode.get().strip()
        if mode == "ROI (absolute)":
            self.cmb_bg_roi.configure(state="readonly")
        else:
            self.cmb_bg_roi.configure(state="disabled")
        self._on_plot_metric_change()

    def _on_plot_metric_change(self, event=None) -> None:
        if self.latest_df is None or self.latest_df.empty:
            self._draw_empty_plots()
            self._draw_empty_advanced_plots()
            return
        self._draw_analysis_plots(self.latest_df)
        self._draw_advanced_plots(self.latest_df)

    def _get_reference_index(self, df: pd.DataFrame) -> int | None:
        s = self.var_ref_sequence.get().strip() if hasattr(self, "var_ref_sequence") else ""
        return fl_roi.resolve_ref_index(df, s)

    def _normalize_to_reference(self, arr: np.ndarray, ref_idx: int | None) -> np.ndarray:
        return fl_roi.normalize_to_reference(arr, ref_idx)

    def _get_event_indices(self, df: pd.DataFrame) -> list[int]:
        if "sequence_number" not in df.columns:
            return []
        if not hasattr(self, "var_event_sequences"):
            return []
        s = self.var_event_sequences.get().strip()
        if not s:
            return []

        seq_vals = pd.to_numeric(df["sequence_number"], errors="coerce").values.astype(float)
        out: set[int] = set()
        for token in [p.strip() for p in s.split(",") if p.strip()]:
            # Prefer sequence-number matching.
            try:
                v = float(token)
                hits = np.where(np.isfinite(seq_vals) & np.isclose(seq_vals, v, atol=1e-9))[0]
                if hits.size > 0:
                    out.add(int(hits[0]))
                    continue
            except Exception:
                pass

            # Fallback: 1-based row index.
            try:
                idx = int(float(token)) - 1
                if 0 <= idx < len(df):
                    out.add(idx)
            except Exception:
                pass

        return sorted(out)

    def _draw_event_markers(self, ax, event_indices: list[int]) -> None:
        for idx in event_indices:
            ax.axvline(idx, color="#9aa0a6", linestyle="--", linewidth=1.1, alpha=0.85)

    def _get_adv_trend_mode(self) -> str:
        if not hasattr(self, "var_adv_trend_mode"):
            return "Linear divide"
        mode = self.var_adv_trend_mode.get().strip()
        if mode in {"None", "Linear divide", "Linear subtract"}:
            return mode
        return "Linear divide"

    def _parse_baseline_frames(self, n_frames: int) -> int:
        try:
            baseline_n = int(float(self.var_baseline_frames.get().strip()))
        except Exception:
            baseline_n = 2
        return max(1, min(max(1, n_frames), baseline_n))

    def _apply_trend_correction(self, arr: np.ndarray, mode: str) -> np.ndarray:
        y = np.asarray(arr, dtype=float)
        out = y.copy()
        if mode == "None":
            return out

        finite = np.isfinite(y)
        if np.count_nonzero(finite) < 2:
            return out

        x = np.arange(y.size, dtype=float)
        try:
            coef = np.polyfit(x[finite], y[finite], deg=1)
            trend = np.polyval(coef, x)
        except Exception:
            return out

        first_idx = int(np.where(finite)[0][0])
        trend_ref = float(trend[first_idx]) if np.isfinite(trend[first_idx]) else np.nan
        if not np.isfinite(trend_ref) or abs(trend_ref) < 1e-12:
            trend_ref = float(np.nanmean(trend[finite]))
        if not np.isfinite(trend_ref) or abs(trend_ref) < 1e-12:
            return out

        out[:] = np.nan
        if mode == "Linear divide":
            scale = trend / trend_ref
            valid = finite & np.isfinite(scale) & (np.abs(scale) > 1e-12)
            out[valid] = y[valid] / scale[valid]
            return out

        if mode == "Linear subtract":
            drift = trend - trend_ref
            valid = finite & np.isfinite(drift)
            out[valid] = y[valid] - drift[valid]
            return out

        return y.copy()

    def _cumulative_auc(self, y: np.ndarray) -> np.ndarray:
        arr = np.asarray(y, dtype=float)
        out = np.full(arr.shape, np.nan, dtype=float)
        if arr.size == 0:
            return out

        out[0] = 0.0
        for i in range(1, arr.size):
            prev = arr[i - 1]
            cur = arr[i]
            if np.isfinite(prev) and np.isfinite(cur):
                step = 0.5 * (prev + cur)
            else:
                step = 0.0
            out[i] = out[i - 1] + step
        return out

    def _add_roi(self) -> None:
        max_idx = 0
        for name in self.roi_order:
            m = re.match(r"ROI(\d+)$", name)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        new_name = f"ROI{max_idx + 1}"
        self.roi_order.append(new_name)
        self.roi_xyxy_map[new_name] = None
        self.var_active_roi.set(new_name)
        self._refresh_roi_selector()
        self._redraw_preview()
        self._redraw_advanced_from_latest()

    def _remove_active_roi(self) -> None:
        active = self.var_active_roi.get()
        if active not in self.roi_order:
            return
        if len(self.roi_order) <= 1:
            messagebox.showwarning("ROI", "At least one ROI must remain.")
            return
        self.roi_order.remove(active)
        self.roi_xyxy_map.pop(active, None)
        self.var_active_roi.set(self.roi_order[0])
        self._refresh_roi_selector()
        self._redraw_preview()
        self._redraw_advanced_from_latest()

    def _clear_all_rois(self) -> None:
        for roi_name in self.roi_order:
            self.roi_xyxy_map[roi_name] = None
        self._redraw_preview()
        self.lbl_status.config(text="All ROI cleared.")
        self._draw_empty_advanced_plots()

    # -------------------------
    # Preview / ROI drawing
    # -------------------------
    def _draw_empty_preview(self) -> None:
        self.ax.clear()
        self.ax.set_axis_off()
        self.ax.text(0.5, 0.5, "No image loaded", ha="center", va="center", fontsize=12)
        self.canvas.draw()

    def _redraw_preview(self) -> None:
        self.ax.clear()
        self.ax.set_axis_off()

        if self.preview_image_disp is None:
            self.ax.text(0.5, 0.5, "No image loaded", ha="center", va="center", fontsize=12)
            self.canvas.draw()
            return

        self.ax.imshow(self.preview_image_disp, cmap="gray", interpolation="nearest")

        for roi_name in self.roi_order:
            roi_xyxy = self.roi_xyxy_map.get(roi_name)
            if roi_xyxy is None:
                continue
            x1, y1, x2, y2 = roi_xyxy
            color = self._roi_color(roi_name)
            rect = Rectangle(
                (x1, y1),
                x2 - x1,
                y2 - y1,
                linewidth=1.5,
                edgecolor=color,
                facecolor="none"
            )
            self.ax.add_patch(rect)
            self.ax.text(x1, max(0, y1 - 4), roi_name, color=color, fontsize=9)

        self.canvas.draw()

    def _on_mouse_press(self, event) -> None:
        if self.preview_image_raw is None:
            return
        if event.inaxes != self.ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        self.roi_start_xy = (int(round(event.xdata)), int(round(event.ydata)))
        active = self.var_active_roi.get()
        if active in self.roi_xyxy_map:
            self.roi_xyxy_map[active] = None

    def _on_mouse_move(self, event) -> None:
        if self.preview_image_raw is None:
            return
        if self.roi_start_xy is None:
            return
        if event.inaxes != self.ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        x0, y0 = self.roi_start_xy
        x1 = int(round(event.xdata))
        y1 = int(round(event.ydata))

        xa, xb = sorted([x0, x1])
        ya, yb = sorted([y0, y1])

        active = self.var_active_roi.get()
        if active in self.roi_xyxy_map:
            self.roi_xyxy_map[active] = (xa, ya, xb, yb)
        self._redraw_preview()

    def _on_mouse_release(self, event) -> None:
        if self.preview_image_raw is None:
            return
        if self.roi_start_xy is None:
            return

        self.roi_start_xy = None
        self._redraw_preview()

    def _clear_roi(self) -> None:
        active = self.var_active_roi.get()
        if active not in self.roi_xyxy_map:
            return
        self.roi_xyxy_map[active] = None
        self._redraw_preview()
        self.lbl_status.config(text=f"{active} cleared.")

    # -------------------------
    # Background estimation
    # -------------------------
    def _estimate_background_value(self, img: np.ndarray) -> float:
        """
        Estimate a simple background mean from a corner ROI if enabled.
        """
        if not self.var_use_background.get():
            return 0.0

        mode = self.var_bg_mode.get().strip()
        if mode == "None":
            return 0.0

        h, w = img.shape
        box_w = max(16, w // 10)
        box_h = max(16, h // 10)

        if mode == "Bottom-right corner":
            roi = img[h - box_h:h, w - box_w:w]
        elif mode == "Top-left corner":
            roi = img[0:box_h, 0:box_w]
        else:
            return 0.0

        return float(np.mean(roi))

    def _parse_gif_durations(self, n_frames: int) -> list[int]:
        s = self.var_gif_duration_ms.get().strip()
        if not s:
            return [200] * n_frames

        if "," not in s:
            val = int(float(s))
            if val <= 0:
                raise ValueError("Frame ms must be > 0.")
            return [val] * n_frames

        parts = [p.strip() for p in s.split(",") if p.strip()]
        vals = [int(float(p)) for p in parts]
        if len(vals) != n_frames:
            raise ValueError(
                f"Duration list length ({len(vals)}) must equal frame count ({n_frames})."
            )
        if any(v <= 0 for v in vals):
            raise ValueError("All frame ms values must be > 0.")
        return vals

    def _get_gif_label_scale(self) -> float:
        try:
            s = float(self.var_gif_label_scale.get().strip())
            if s > 0:
                return s
        except Exception:
            pass
        return 2.0

    def _get_pil_font(self, size_px: int) -> ImageFont.ImageFont:
        size_px = max(10, int(size_px))
        for font_name in ["DejaVuSans-Bold.ttf", "Arial.ttf"]:
            try:
                return ImageFont.truetype(font_name, size_px)
            except Exception:
                continue
        return ImageFont.load_default()

    def _measure_pil_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        stroke_w: int = 0,
    ) -> tuple[int, int]:
        if hasattr(draw, "textbbox"):
            b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
            return int(b[2] - b[0]), int(b[3] - b[1])
        w, h = draw.textsize(text, font=font)
        return int(w), int(h)

    def _build_gif_frame(
        self,
        img2d: np.ndarray,
        frame_name: str,
        pixel_size_um: float | None,
        scalebar_um: float,
    ) -> Image.Image:
        img_disp = (normalize_for_display(img2d) * 255.0).astype(np.uint8)
        pil_img = Image.fromarray(img_disp, mode="L").convert("RGB")
        draw = ImageDraw.Draw(pil_img)
        w, h = pil_img.size
        label_scale = self._get_gif_label_scale()
        base_text_px = max(14, int(min(w, h) * 0.018 * label_scale))
        name_font = self._get_pil_font(base_text_px)
        sb_font = self._get_pil_font(int(base_text_px * 0.9))
        stroke_w = max(1, int(base_text_px * 0.12))
        pad = max(8, int(min(w, h) * 0.012))

        if self.var_gif_show_name.get():
            text_w, text_h = self._measure_pil_text(draw, frame_name, name_font, stroke_w)
            box_x0 = pad
            box_y0 = pad
            box_x1 = box_x0 + text_w + 2 * pad
            box_y1 = box_y0 + text_h + 2 * max(4, pad // 2)
            draw.rectangle((box_x0, box_y0, box_x1, box_y1), fill=(0, 0, 0))
            draw.text(
                (box_x0 + pad, box_y0 + max(2, pad // 3)),
                frame_name,
                fill=(255, 255, 255),
                font=name_font,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
            )

        if self.var_gif_show_scalebar.get() and pixel_size_um is not None and pixel_size_um > 0:
            bar_px = int(round(scalebar_um / pixel_size_um))
            bar_px = max(1, min(bar_px, int(0.6 * w)))
            bar_thick = max(3, int(min(w, h) * 0.004 * label_scale))
            label_text = f"{scalebar_um:g} um"
            _, sb_text_h = self._measure_pil_text(draw, label_text, sb_font, stroke_w)
            x0 = max(pad, int(0.05 * w))
            y0 = h - pad - bar_thick
            y_label = y0 - sb_text_h - max(6, pad // 2)
            x1 = x0 + bar_px

            draw.rectangle(
                (
                    x0 - pad,
                    max(0, y_label - max(4, pad // 2)),
                    min(w - 1, x1 + pad),
                    min(h - 1, y0 + bar_thick + max(4, pad // 2)),
                ),
                fill=(0, 0, 0),
            )
            draw.rectangle((x0, y0, x1, y0 + bar_thick), fill=(255, 255, 255))
            draw.text(
                (x0, y_label),
                label_text,
                fill=(255, 255, 255),
                font=sb_font,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0),
            )

        return pil_img

    def _export_gif(self) -> None:
        if not PIL_AVAILABLE:
            messagebox.showwarning(
                "Missing dependency",
                "Export GIF requires Pillow.\nPlease install: pip install pillow"
            )
            return

        if not self.sequence_records:
            messagebox.showwarning("No data", "No stack pairs found in this folder.")
            return

        if not self.analysis_indices:
            messagebox.showwarning(
                "No files selected",
                "Please move at least one file into the lower analysis list."
            )
            return

        try:
            scalebar_um = float(self.var_gif_scalebar_um.get().strip())
        except Exception:
            messagebox.showwarning("Invalid input", "Scale bar (um) must be a number.")
            return

        if scalebar_um <= 0:
            messagebox.showwarning("Invalid input", "Scale bar (um) must be > 0.")
            return

        selected_records = [self.sequence_records[i] for i in self.analysis_indices]
        frame_paths = []
        frame_names = []
        for rec in selected_records:
            path = rec.get(self.current_preview_stack)
            if path is None:
                continue
            frame_paths.append(path)
            frame_names.append(rec["base"])

        if not frame_paths:
            messagebox.showwarning(
                "No frames",
                f"No '{self.current_preview_stack}' images in selected files."
            )
            return

        try:
            durations = self._parse_gif_durations(len(frame_paths))
        except Exception as e:
            messagebox.showwarning("Invalid frame ms", str(e))
            return

        pixel_size_um = None
        pixel_override_txt = self.var_gif_pixel_size_um.get().strip()
        if pixel_override_txt:
            try:
                pixel_size_um = float(pixel_override_txt)
            except Exception:
                messagebox.showwarning(
                    "Invalid input",
                    "Pixel size override (um/px) must be a number or left empty."
                )
                return
            if pixel_size_um <= 0:
                messagebox.showwarning(
                    "Invalid input",
                    "Pixel size override (um/px) must be > 0."
                )
                return
        else:
            pixel_size_um = infer_pixel_size_um_from_tiff(frame_paths[0])

        if self.var_gif_show_scalebar.get() and pixel_size_um is None:
            messagebox.showwarning(
                "Scale metadata not found",
                "Could not read pixel size metadata from TIFF. GIF will be saved without scale bar."
            )

        frames: list[Image.Image] = []
        for p, name in zip(frame_paths, frame_names):
            img = read_first_page_as_2d(p)
            frame = self._build_gif_frame(img, name, pixel_size_um, scalebar_um)
            frames.append(frame)

        if not frames:
            messagebox.showwarning("No frames", "No valid frames to export.")
            return

        prefix = self.var_prefix.get().strip() or "roi_analysis"
        out_gif = self.current_folder / f"{prefix}_{self.current_preview_stack}.gif"
        frames[0].save(
            out_gif,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0
        )

        pixel_info = (
            f"{pixel_size_um:.6g} um/pixel" if pixel_size_um is not None else "not found"
        )
        messagebox.showinfo(
            "GIF exported",
            f"Saved GIF:\n{out_gif}\n\nFrames: {len(frames)}\nPixel size: {pixel_info}"
        )

    # -------------------------
    # Analysis
    # -------------------------
    def _run_analysis(self) -> None:
        if not self.sequence_records:
            messagebox.showwarning("No data", "No stack pairs found in this folder.")
            return

        if not self.analysis_indices:
            messagebox.showwarning(
                "No files selected",
                "Please move at least one file into the lower analysis list."
            )
            return

        roi_names = [name for name in self.roi_order if self.roi_xyxy_map.get(name) is not None]
        if not roi_names:
            messagebox.showwarning("No ROI", "Please draw at least one ROI before analysis.")
            return

        use_background = bool(self.var_use_background.get())
        bg_mode = self.var_bg_mode.get().strip()
        bg_roi_name = self.var_bg_roi_name.get().strip()
        if use_background and bg_mode == "ROI (absolute)" and bg_roi_name not in roi_names:
            messagebox.showwarning(
                "Background ROI",
                "Selected BG ROI is not drawn. Please draw it first or choose another BG mode.",
            )
            return

        selected_records = [self.sequence_records[i] for i in self.analysis_indices]
        rows = []

        for rec in selected_records:
            img1 = None
            img2 = None

            if rec["stack1"] is not None:
                img1 = read_first_page_as_2d(rec["stack1"])
            if rec["stack2"] is not None:
                img2 = read_first_page_as_2d(rec["stack2"])

            metrics_s1: dict[str, dict] = {}
            metrics_s2: dict[str, dict] = {}

            seq_key = natural_sequence_key_from_name(rec["base"])
            seq_num = seq_key[-1] if len(seq_key) > 0 else np.nan

            row = {
                "base_name": rec["base"],
                "sequence_number": seq_num,

                "stack1_path": str(rec["stack1"]) if rec["stack1"] else "",
                "stack2_path": str(rec["stack2"]) if rec["stack2"] else "",
                "stack1_bg_mean": np.nan,
                "stack2_bg_mean": np.nan,
                "background_mode": bg_mode if use_background else "None",
                "background_roi": bg_roi_name if (use_background and bg_mode == "ROI (absolute)") else "",
            }

            for roi_name in roi_names:
                m1 = {
                    "mean": np.nan,
                    "top20_mean": np.nan,
                    "sum": np.nan,
                    "max": np.nan,
                    "std": np.nan,
                    "area_px": 0,
                }
                m2 = {
                    "mean": np.nan,
                    "top20_mean": np.nan,
                    "sum": np.nan,
                    "max": np.nan,
                    "std": np.nan,
                    "area_px": 0,
                }
                roi_xyxy = self.roi_xyxy_map[roi_name]

                if img1 is not None and roi_xyxy is not None:
                    m1 = compute_roi_metrics(img1, roi_xyxy)
                if img2 is not None and roi_xyxy is not None:
                    m2 = compute_roi_metrics(img2, roi_xyxy)

                metrics_s1[roi_name] = m1
                metrics_s2[roi_name] = m2

            bg1 = 0.0
            bg2 = 0.0
            if use_background:
                if bg_mode in {"Bottom-right corner", "Top-left corner"}:
                    if img1 is not None:
                        bg1 = self._estimate_background_value(img1)
                    if img2 is not None:
                        bg2 = self._estimate_background_value(img2)
                elif bg_mode == "ROI (absolute)":
                    bg1 = float(metrics_s1.get(bg_roi_name, {}).get("mean", np.nan))
                    bg2 = float(metrics_s2.get(bg_roi_name, {}).get("mean", np.nan))
                else:
                    bg1 = 0.0
                    bg2 = 0.0

            row["stack1_bg_mean"] = bg1
            row["stack2_bg_mean"] = bg2

            for roi_name in roi_names:
                roi_key = roi_name.lower()
                m1 = metrics_s1[roi_name]
                m2 = metrics_s2[roi_name]

                mean_bgsub_1 = (
                    m1["mean"] - bg1 if np.isfinite(m1["mean"]) and np.isfinite(bg1) else np.nan
                )
                mean_bgsub_2 = (
                    m2["mean"] - bg2 if np.isfinite(m2["mean"]) and np.isfinite(bg2) else np.nan
                )
                top20_bgsub_1 = (
                    m1["top20_mean"] - bg1
                    if np.isfinite(m1["top20_mean"]) and np.isfinite(bg1)
                    else np.nan
                )
                top20_bgsub_2 = (
                    m2["top20_mean"] - bg2
                    if np.isfinite(m2["top20_mean"]) and np.isfinite(bg2)
                    else np.nan
                )
                sum_bgsub_1 = (
                    m1["sum"] - bg1 * m1["area_px"]
                    if np.isfinite(m1["sum"]) and np.isfinite(bg1)
                    else np.nan
                )
                sum_bgsub_2 = (
                    m2["sum"] - bg2 * m2["area_px"]
                    if np.isfinite(m2["sum"]) and np.isfinite(bg2)
                    else np.nan
                )
                mean_bgnorm_1 = safe_ratio(m1["mean"], bg1)
                mean_bgnorm_2 = safe_ratio(m2["mean"], bg2)
                top20_bgnorm_1 = safe_ratio(m1["top20_mean"], bg1)
                top20_bgnorm_2 = safe_ratio(m2["top20_mean"], bg2)
                sum_bgnorm_1 = safe_ratio(m1["sum"], bg1 * max(1, m1["area_px"]))
                sum_bgnorm_2 = safe_ratio(m2["sum"], bg2 * max(1, m2["area_px"]))

                row[f"stack1_mean_{roi_key}"] = m1["mean"]
                row[f"stack1_top20_mean_{roi_key}"] = m1["top20_mean"]
                row[f"stack1_sum_{roi_key}"] = m1["sum"]
                row[f"stack1_max_{roi_key}"] = m1["max"]
                row[f"stack1_std_{roi_key}"] = m1["std"]
                row[f"stack1_mean_bgsub_{roi_key}"] = mean_bgsub_1
                row[f"stack1_top20_bgsub_{roi_key}"] = top20_bgsub_1
                row[f"stack1_sum_bgsub_{roi_key}"] = sum_bgsub_1
                row[f"stack1_mean_bgnorm_{roi_key}"] = mean_bgnorm_1
                row[f"stack1_top20_bgnorm_{roi_key}"] = top20_bgnorm_1
                row[f"stack1_sum_bgnorm_{roi_key}"] = sum_bgnorm_1

                row[f"stack2_mean_{roi_key}"] = m2["mean"]
                row[f"stack2_top20_mean_{roi_key}"] = m2["top20_mean"]
                row[f"stack2_sum_{roi_key}"] = m2["sum"]
                row[f"stack2_max_{roi_key}"] = m2["max"]
                row[f"stack2_std_{roi_key}"] = m2["std"]
                row[f"stack2_mean_bgsub_{roi_key}"] = mean_bgsub_2
                row[f"stack2_top20_bgsub_{roi_key}"] = top20_bgsub_2
                row[f"stack2_sum_bgsub_{roi_key}"] = sum_bgsub_2
                row[f"stack2_mean_bgnorm_{roi_key}"] = mean_bgnorm_2
                row[f"stack2_top20_bgnorm_{roi_key}"] = top20_bgnorm_2
                row[f"stack2_sum_bgnorm_{roi_key}"] = sum_bgnorm_2

                row[f"roi_area_px_{roi_key}"] = m1["area_px"] if m1["area_px"] > 0 else m2["area_px"]
                row[f"ratio_mean_{roi_key}"] = safe_ratio(m1["mean"], m2["mean"])
                row[f"ratio_top20_mean_{roi_key}"] = safe_ratio(m1["top20_mean"], m2["top20_mean"])
                row[f"ratio_sum_{roi_key}"] = safe_ratio(m1["sum"], m2["sum"])
                row[f"ratio_mean_bgsub_{roi_key}"] = safe_ratio(mean_bgsub_1, mean_bgsub_2)
                row[f"ratio_top20_bgsub_{roi_key}"] = safe_ratio(top20_bgsub_1, top20_bgsub_2)
                row[f"ratio_sum_bgsub_{roi_key}"] = safe_ratio(sum_bgsub_1, sum_bgsub_2)
                row[f"ratio_mean_bgnorm_{roi_key}"] = safe_ratio(mean_bgnorm_1, mean_bgnorm_2)
                row[f"ratio_top20_bgnorm_{roi_key}"] = safe_ratio(top20_bgnorm_1, top20_bgnorm_2)
                row[f"ratio_sum_bgnorm_{roi_key}"] = safe_ratio(sum_bgnorm_1, sum_bgnorm_2)

            # Keep legacy aliases for ROI1 if it exists.
            if "ROI1" in metrics_s1:
                m1 = metrics_s1["ROI1"]
                m2 = metrics_s2["ROI1"]
                mean_bgsub_1 = row["stack1_mean_bgsub_roi1"]
                sum_bgsub_1 = row["stack1_sum_bgsub_roi1"]
                mean_bgsub_2 = row["stack2_mean_bgsub_roi1"]
                sum_bgsub_2 = row["stack2_sum_bgsub_roi1"]
                mean_bgnorm_1 = row["stack1_mean_bgnorm_roi1"]
                sum_bgnorm_1 = row["stack1_sum_bgnorm_roi1"]
                mean_bgnorm_2 = row["stack2_mean_bgnorm_roi1"]
                sum_bgnorm_2 = row["stack2_sum_bgnorm_roi1"]
                top20_bgsub_1 = row["stack1_top20_bgsub_roi1"]
                top20_bgsub_2 = row["stack2_top20_bgsub_roi1"]
                top20_bgnorm_1 = row["stack1_top20_bgnorm_roi1"]
                top20_bgnorm_2 = row["stack2_top20_bgnorm_roi1"]
                row["stack1_mean"] = m1["mean"]
                row["stack1_top20_mean"] = m1["top20_mean"]
                row["stack1_sum"] = m1["sum"]
                row["stack1_max"] = m1["max"]
                row["stack1_std"] = m1["std"]
                row["stack1_mean_bgsub"] = mean_bgsub_1
                row["stack1_top20_bgsub"] = top20_bgsub_1
                row["stack1_sum_bgsub"] = sum_bgsub_1
                row["stack1_mean_bgnorm"] = mean_bgnorm_1
                row["stack1_top20_bgnorm"] = top20_bgnorm_1
                row["stack1_sum_bgnorm"] = sum_bgnorm_1
                row["stack2_mean"] = m2["mean"]
                row["stack2_top20_mean"] = m2["top20_mean"]
                row["stack2_sum"] = m2["sum"]
                row["stack2_max"] = m2["max"]
                row["stack2_std"] = m2["std"]
                row["stack2_mean_bgsub"] = mean_bgsub_2
                row["stack2_top20_bgsub"] = top20_bgsub_2
                row["stack2_sum_bgsub"] = sum_bgsub_2
                row["stack2_mean_bgnorm"] = mean_bgnorm_2
                row["stack2_top20_bgnorm"] = top20_bgnorm_2
                row["stack2_sum_bgnorm"] = sum_bgnorm_2
                row["ratio_mean"] = safe_ratio(m1["mean"], m2["mean"])
                row["ratio_top20_mean"] = safe_ratio(m1["top20_mean"], m2["top20_mean"])
                row["ratio_sum"] = safe_ratio(m1["sum"], m2["sum"])
                row["ratio_mean_bgsub"] = safe_ratio(mean_bgsub_1, mean_bgsub_2)
                row["ratio_top20_bgsub"] = safe_ratio(top20_bgsub_1, top20_bgsub_2)
                row["ratio_sum_bgsub"] = safe_ratio(sum_bgsub_1, sum_bgsub_2)
                row["ratio_mean_bgnorm"] = safe_ratio(mean_bgnorm_1, mean_bgnorm_2)
                row["ratio_top20_bgnorm"] = safe_ratio(top20_bgnorm_1, top20_bgnorm_2)
                row["ratio_sum_bgnorm"] = safe_ratio(sum_bgnorm_1, sum_bgnorm_2)

            rows.append(row)

        df = pd.DataFrame(rows)

        # Sort by sequence number if available
        if "sequence_number" in df.columns:
            try:
                df = df.sort_values(by="sequence_number", kind="stable")
            except Exception:
                pass

        prefix = self.var_prefix.get().strip() or "roi_analysis"
        out_csv = self.current_folder / f"{prefix}_metrics.csv"
        out_plot = self.current_folder / f"{prefix}_plots_page2_basic.png"
        out_plot_adv = self.current_folder / f"{prefix}_plots_page3_advanced.png"
        out_roi = self.current_folder / f"{prefix}_roi_reference.png"

        df.to_csv(out_csv, index=False)

        self._draw_analysis_plots(df)
        self.latest_df = df.copy()
        self._draw_advanced_plots(df)
        self.fig_plot.savefig(out_plot, dpi=200, bbox_inches="tight")
        self.fig_adv.savefig(out_plot_adv, dpi=200, bbox_inches="tight")
        out_summary_csv, out_summary_plot = self._export_summary_outputs(df, prefix)

        # Save ROI reference image
        self._save_roi_reference(out_roi)

        messagebox.showinfo(
            "Analysis finished",
            "Analysis completed."
            f"\n\nCSV:\n{out_csv}"
            f"\n\nPage 2 plot (Basic):\n{out_plot}"
            f"\n\nPage 3 plot (Advanced):\n{out_plot_adv}"
            f"\n\nSummary CSV:\n{out_summary_csv}"
            f"\n\nSummary plot:\n{out_summary_plot}"
            f"\n\nROI reference:\n{out_roi}"
        )

    def _draw_empty_plots(self) -> None:
        try:
            fs = max(8, int(float(self.var_plot_font_size.get().strip())))
        except Exception:
            fs = DEFAULT_PLOT_FONT_SIZE
        for ax in [self.ax1, self.ax2, self.ax3, self.ax4]:
            ax.clear()
            ax.set_title("")
        self.ax1.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=fs)
        self.ax2.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=fs)
        self.ax3.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=fs)
        self.ax4.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=fs)
        self.canvas_plot.draw()

    def _get_plot_metric_spec(self) -> dict:
        mode = self.var_plot_metric.get().strip() if hasattr(self, "var_plot_metric") else "Mean (absolute)"
        use_bg = bool(self.var_use_background.get()) if hasattr(self, "var_use_background") else False
        if not use_bg:
            if mode == "Mean (BG-subtracted)" or mode == "Mean (BG-normalized)":
                mode = "Mean (absolute)"
            if mode == "Top20 mean (BG-subtracted)" or mode == "Top20 mean (BG-normalized)":
                mode = "Top20 mean (absolute)"
        mapping = {
            "Mean (absolute)": {
                "s1": "stack1_mean_",
                "s2": "stack2_mean_",
                "ratio": "ratio_mean_",
                "ylabel_signal": "Mean intensity",
                "ylabel_ratio": "Mean ratio",
            },
            "Mean (BG-subtracted)": {
                "s1": "stack1_mean_bgsub_",
                "s2": "stack2_mean_bgsub_",
                "ratio": "ratio_mean_bgsub_",
                "ylabel_signal": "Mean intensity (BG-subtracted)",
                "ylabel_ratio": "Ratio (BG-subtracted)",
            },
            "Mean (BG-normalized)": {
                "s1": "stack1_mean_bgnorm_",
                "s2": "stack2_mean_bgnorm_",
                "ratio": "ratio_mean_bgnorm_",
                "ylabel_signal": "Mean intensity / BG",
                "ylabel_ratio": "(S1/BG) / (S2/BG)",
            },
            "Top20 mean (absolute)": {
                "s1": "stack1_top20_mean_",
                "s2": "stack2_top20_mean_",
                "ratio": "ratio_top20_mean_",
                "ylabel_signal": "Top20 mean intensity",
                "ylabel_ratio": "Top20 mean ratio",
            },
            "Top20 mean (BG-subtracted)": {
                "s1": "stack1_top20_bgsub_",
                "s2": "stack2_top20_bgsub_",
                "ratio": "ratio_top20_bgsub_",
                "ylabel_signal": "Top20 mean (BG-subtracted)",
                "ylabel_ratio": "Top20 ratio (BG-subtracted)",
            },
            "Top20 mean (BG-normalized)": {
                "s1": "stack1_top20_bgnorm_",
                "s2": "stack2_top20_bgnorm_",
                "ratio": "ratio_top20_bgnorm_",
                "ylabel_signal": "Top20 mean / BG",
                "ylabel_ratio": "(Top20 S1/BG) / (Top20 S2/BG)",
            },
        }
        return mapping.get(mode, mapping["Mean (absolute)"])

    def _draw_analysis_plots(self, df: pd.DataFrame) -> None:
        x = np.arange(len(df))
        x_labels = [str(v) for v in df["sequence_number"].tolist()]
        try:
            fs = max(8, int(float(self.var_plot_font_size.get().strip())))
        except Exception:
            fs = DEFAULT_PLOT_FONT_SIZE
        legend_fs = max(8, fs - 2)
        metric = self._get_plot_metric_spec()
        s1_prefix = metric["s1"]
        s2_prefix = metric["s2"]
        ratio_prefix = metric["ratio"]
        roi_names = [name for name in self.roi_order if f"{ratio_prefix}{name.lower()}" in df.columns]
        if self.var_use_background.get() and self.var_bg_mode.get().strip() == "ROI (absolute)":
            bg_roi = self.var_bg_roi_name.get().strip()
            roi_names = [r for r in roi_names if r != bg_roi]
        ref_idx = self._get_reference_index(df)
        event_indices = self._get_event_indices(df)

        for ax in [self.ax1, self.ax2, self.ax3, self.ax4]:
            ax.clear()

        if not roi_names:
            self.ax1.text(0.5, 0.5, "No ROI to plot", ha="center", va="center", fontsize=fs)
            self.ax2.text(0.5, 0.5, "No ROI to plot", ha="center", va="center", fontsize=fs)
            self.ax3.text(0.5, 0.5, "No ROI to plot", ha="center", va="center", fontsize=fs)
            self.ax4.text(0.5, 0.5, "No ROI to plot", ha="center", va="center", fontsize=fs)
            self.canvas_plot.draw()
            return

        y_stack1_all: list[np.ndarray] = []
        y_stack2_all: list[np.ndarray] = []
        y_diff_all: list[np.ndarray] = []

        for roi_name in roi_names:
            roi_key = roi_name.lower()
            col = f"{s1_prefix}{roi_key}"
            y = self._normalize_to_reference(df[col].astype(float).values, ref_idx)
            y_stack1_all.append(y)
            self.ax1.plot(
                x, y,
                marker="o", label=roi_name, color=self._roi_color(roi_name)
            )
        self.ax1.set_title("Stack1 by ROI", fontsize=fs)
        self.ax1.set_xlabel("Sequence", fontsize=fs)
        ylabel_signal = metric["ylabel_signal"] + (" (ref=1)" if ref_idx is not None else "")
        self.ax1.set_ylabel(ylabel_signal, fontsize=fs)
        self.ax1.set_xticks(x)
        self.ax1.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=fs - 1)
        self.ax1.tick_params(axis="y", labelsize=fs - 1)
        self._draw_event_markers(self.ax1, event_indices)
        self.ax1.legend(
            loc="lower right",
            fontsize=legend_fs,
            frameon=True,
            framealpha=0.85,
        )

        for roi_name in roi_names:
            roi_key = roi_name.lower()
            col = f"{s2_prefix}{roi_key}"
            y = self._normalize_to_reference(df[col].astype(float).values, ref_idx)
            y_stack2_all.append(y)
            self.ax2.plot(
                x, y,
                marker="o", label=roi_name, color=self._roi_color(roi_name)
            )
        self.ax2.set_title("Stack2 by ROI", fontsize=fs)
        self.ax2.set_xlabel("Sequence", fontsize=fs)
        self.ax2.set_ylabel(ylabel_signal, fontsize=fs)
        self.ax2.set_xticks(x)
        self.ax2.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=fs - 1)
        self.ax2.tick_params(axis="y", labelsize=fs - 1)
        self._draw_event_markers(self.ax2, event_indices)
        self.ax2.legend(
            loc="lower right",
            fontsize=legend_fs,
            frameon=True,
            framealpha=0.85,
        )

        # Keep Stack1/Stack2 on the same y range for direct visual comparability.
        y_all = []
        for ys in y_stack1_all + y_stack2_all:
            ys = np.asarray(ys, dtype=float)
            ys = ys[np.isfinite(ys)]
            if ys.size > 0:
                y_all.append(ys)
        if y_all:
            y_concat = np.concatenate(y_all)
            y_min = float(np.min(y_concat))
            y_max = float(np.max(y_concat))
            if abs(y_max - y_min) < 1e-12:
                pad = 0.5 if abs(y_max) < 1e-12 else max(1e-3, abs(y_max) * 0.05)
            else:
                pad = max(1e-3, (y_max - y_min) * 0.06)
            self.ax1.set_ylim(y_min - pad, y_max + pad)
            self.ax2.set_ylim(y_min - pad, y_max + pad)

        for roi_name in roi_names:
            roi_key = roi_name.lower()
            y = self._normalize_to_reference(df[f"{ratio_prefix}{roi_key}"].astype(float).values, ref_idx)
            self.ax3.plot(
                x, y,
                marker="o", label=roi_name, color=self._roi_color(roi_name)
            )
        self.ax3.axhline(1.0, color="#8e8e93", linestyle="--", linewidth=1.1, alpha=0.9)
        self.ax3.set_title("Stack1 / Stack2 by ROI", fontsize=fs)
        self.ax3.set_xlabel("Sequence", fontsize=fs)
        ylabel_ratio = metric["ylabel_ratio"] + (" (ref=1)" if ref_idx is not None else "")
        self.ax3.set_ylabel(ylabel_ratio, fontsize=fs)
        self.ax3.set_xticks(x)
        self.ax3.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=fs - 1)
        self.ax3.tick_params(axis="y", labelsize=fs - 1)
        self._draw_event_markers(self.ax3, event_indices)
        self.ax3.legend(
            loc="lower right",
            fontsize=legend_fs,
            frameon=True,
            framealpha=0.85,
        )

        for roi_name in roi_names:
            roi_key = roi_name.lower()
            y1 = self._normalize_to_reference(df[f"{s1_prefix}{roi_key}"].astype(float).values, ref_idx)
            y2 = self._normalize_to_reference(df[f"{s2_prefix}{roi_key}"].astype(float).values, ref_idx)
            y_diff = y1 - y2
            y_diff_all.append(y_diff)
            self.ax4.plot(
                x, y_diff,
                marker="o", label=roi_name, color=self._roi_color(roi_name)
            )
        self.ax4.axhline(0.0, color="#8e8e93", linestyle="--", linewidth=1.1, alpha=0.9)
        self.ax4.set_title("Stack1 - Stack2 by ROI", fontsize=fs)
        self.ax4.set_xlabel("Sequence", fontsize=fs)
        ylabel_diff = f"{metric['ylabel_signal']} difference"
        self.ax4.set_ylabel(ylabel_diff, fontsize=fs)
        self.ax4.set_xticks(x)
        self.ax4.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=fs - 1)
        self.ax4.tick_params(axis="y", labelsize=fs - 1)
        self._draw_event_markers(self.ax4, event_indices)
        self.ax4.legend(
            loc="lower right",
            fontsize=legend_fs,
            frameon=True,
            framealpha=0.85,
        )

        y_diff_finite = []
        for ys in y_diff_all:
            ys = np.asarray(ys, dtype=float)
            ys = ys[np.isfinite(ys)]
            if ys.size > 0:
                y_diff_finite.append(ys)
        if y_diff_finite:
            y_concat = np.concatenate(y_diff_finite)
            y_min = float(np.min(y_concat))
            y_max = float(np.max(y_concat))
            if abs(y_max - y_min) < 1e-12:
                pad = 0.5 if abs(y_max) < 1e-12 else max(1e-3, abs(y_max) * 0.05)
            else:
                pad = max(1e-3, (y_max - y_min) * 0.06)
            self.ax4.set_ylim(y_min - pad, y_max + pad)

        self.fig_plot.tight_layout()
        self.canvas_plot.draw()

    def _redraw_advanced_from_latest(self) -> None:
        if self.latest_df is None or self.latest_df.empty:
            self._draw_empty_advanced_plots()
            return
        self._draw_advanced_plots(self.latest_df)

    def _export_advanced_plot(self) -> None:
        if self.latest_df is None or self.latest_df.empty:
            messagebox.showwarning("No data", "Run Analysis first to generate advanced plots.")
            return
        prefix = self.var_prefix.get().strip() or "roi_analysis"
        out_path = self.current_folder / f"{prefix}_plots_page3_advanced.png"
        self.fig_adv.savefig(out_path, dpi=200, bbox_inches="tight")
        messagebox.showinfo("Advanced plot exported", f"Saved:\n{out_path}")

    def _draw_empty_advanced_plots(self) -> None:
        try:
            fs = max(8, int(float(self.var_plot_font_size.get().strip())))
        except Exception:
            fs = DEFAULT_PLOT_FONT_SIZE
        if self.adv_heatmap_cbar is not None:
            try:
                self.adv_heatmap_cbar.remove()
            except Exception:
                pass
            self.adv_heatmap_cbar = None
        if self.adv_fig_legend is not None:
            try:
                self.adv_fig_legend.remove()
            except Exception:
                pass
            self.adv_fig_legend = None
        for ax in [self.ax_adv1, self.ax_adv2, self.ax_adv3, self.ax_adv4]:
            ax.clear()
            ax.set_title("")
        self.ax_adv1.text(0.5, 0.5, "No advanced data", ha="center", va="center", fontsize=fs)
        self.ax_adv2.text(0.5, 0.5, "Run Analysis first", ha="center", va="center", fontsize=fs)
        self.ax_adv3.text(0.5, 0.5, "Cumulative response", ha="center", va="center", fontsize=fs)
        self.ax_adv4.text(0.5, 0.5, "Spatial heterogeneity", ha="center", va="center", fontsize=fs)
        self.fig_adv.suptitle("")
        self.canvas_adv.draw()

    def _draw_advanced_plots(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            self._draw_empty_advanced_plots()
            return

        try:
            fs = max(8, int(float(self.var_plot_font_size.get().strip())))
        except Exception:
            fs = DEFAULT_PLOT_FONT_SIZE
        adv_fs = max(8, fs - 1)
        legend_fs = max(7, adv_fs - 2)

        metric = self._get_plot_metric_spec()
        ratio_prefix = metric["ratio"]
        roi_names = [name for name in self.roi_order if f"{ratio_prefix}{name.lower()}" in df.columns]
        if self.var_use_background.get() and self.var_bg_mode.get().strip() == "ROI (absolute)":
            bg_roi = self.var_bg_roi_name.get().strip()
            roi_names = [r for r in roi_names if r != bg_roi]
        if not roi_names:
            self._draw_empty_advanced_plots()
            return

        center_roi = self.var_center_roi.get().strip()
        if center_roi not in roi_names:
            center_roi = roi_names[0]
            self.var_center_roi.set(center_roi)
        peri_rois = [r for r in roi_names if r != center_roi]

        x = np.arange(len(df))
        x_labels = [str(v) for v in df["sequence_number"].tolist()]
        ref_idx = self._get_reference_index(df)
        event_indices = self._get_event_indices(df)
        baseline_n = self._parse_baseline_frames(len(df))
        trend_mode = self._get_adv_trend_mode()

        ratio_series = []
        for r in roi_names:
            y = self._normalize_to_reference(
                df[f"{ratio_prefix}{r.lower()}"].astype(float).values, ref_idx
            )
            y = self._apply_trend_correction(y, trend_mode)
            ratio_series.append(y)
        ratio_mat = np.vstack(ratio_series)

        with np.errstate(invalid="ignore", divide="ignore"):
            base = np.nanmean(ratio_mat[:, :baseline_n], axis=1, keepdims=True)
            dff_mat = np.where(np.abs(base) > 1e-12, ratio_mat / base - 1.0, np.nan)

        center_idx = roi_names.index(center_roi)
        center_dff = dff_mat[center_idx, :]
        if peri_rois:
            peri_indices = [roi_names.index(r) for r in peri_rois]
            peri_mat = dff_mat[peri_indices, :]
            peri_mean = np.nanmean(peri_mat, axis=0)
            peri_std = np.nanstd(peri_mat, axis=0)
            peri_inc = np.diff(peri_mean, prepend=np.nan)
            peri_cum = self._cumulative_auc(peri_mean)
            center_minus_peri = center_dff - peri_mean
        else:
            peri_mean = np.full_like(center_dff, np.nan)
            peri_std = np.full_like(center_dff, np.nan)
            peri_inc = np.full_like(center_dff, np.nan)
            peri_cum = np.full_like(center_dff, np.nan)
            center_minus_peri = np.full_like(center_dff, np.nan)

        center_inc = np.diff(center_dff, prepend=np.nan)
        center_cum = self._cumulative_auc(center_dff)
        roi_std = np.nanstd(dff_mat, axis=0)

        for ax in [self.ax_adv1, self.ax_adv2, self.ax_adv3, self.ax_adv4]:
            ax.clear()
        if self.adv_heatmap_cbar is not None:
            try:
                self.adv_heatmap_cbar.remove()
            except Exception:
                pass
            self.adv_heatmap_cbar = None
        if self.adv_fig_legend is not None:
            try:
                self.adv_fig_legend.remove()
            except Exception:
                pass
            self.adv_fig_legend = None

        # A) Main panel: response vs stimulation count.
        for i, roi_name in enumerate(roi_names):
            self.ax_adv1.plot(
                x, dff_mat[i],
                color=self._roi_color(roi_name), linewidth=1.1, alpha=0.28
            )
        h_center_resp, = self.ax_adv1.plot(
            x, center_dff, marker="o", linewidth=2.3,
            color=self._roi_color(center_roi), label=f"{center_roi} response"
        )
        h_peri_resp = None
        if peri_rois:
            h_peri_resp, = self.ax_adv1.plot(
                x, peri_mean, marker="o", linewidth=2.1,
                color="#4b5563", label="Peripheral mean response"
            )
            self.ax_adv1.fill_between(
                x, peri_mean - peri_std, peri_mean + peri_std,
                color="#9ca3af", alpha=0.22, linewidth=0
            )
        self.ax_adv1.axhline(0.0, color="#8e8e93", linestyle="--", linewidth=1.0, alpha=0.9)
        self.ax_adv1.set_title("Main: Response vs stimulation", fontsize=adv_fs)
        self.ax_adv1.set_xlabel("")
        self.ax_adv1.set_ylabel("dF/F0", fontsize=adv_fs)
        self.ax_adv1.set_xticks(x)
        self.ax_adv1.set_xticklabels([])
        self.ax_adv1.tick_params(axis="y", labelsize=adv_fs - 1)
        self._draw_event_markers(self.ax_adv1, event_indices)

        # B) Main panel: incremental response per stimulation.
        h_center_inc, = self.ax_adv2.plot(
            x, center_inc, marker="o", linewidth=2.2,
            color=self._roi_color(center_roi), label=f"{center_roi} increment"
        )
        h_peri_inc = None
        if peri_rois:
            h_peri_inc, = self.ax_adv2.plot(
                x, peri_inc, marker="o", linewidth=2.0,
                color="#4b5563", label="Peripheral mean increment"
            )
        self.ax_adv2.axhline(0.0, color="gray", linestyle="--", linewidth=1.0)
        self.ax_adv2.set_title("Main: Increment per stimulation", fontsize=adv_fs)
        self.ax_adv2.set_ylabel("Delta dF/F0", fontsize=adv_fs)
        self.ax_adv2.set_xlabel("")
        self.ax_adv2.set_xticks(x)
        self.ax_adv2.set_xticklabels([])
        self.ax_adv2.tick_params(axis="y", labelsize=adv_fs - 1)
        self._draw_event_markers(self.ax_adv2, event_indices)

        # C) Auxiliary panel: cumulative response.
        h_center_cum, = self.ax_adv3.plot(
            x, center_cum, marker="o", linewidth=2.3,
            color=self._roi_color(center_roi), label=f"{center_roi} cumulative"
        )
        h_peri_cum = None
        if peri_rois:
            h_peri_cum, = self.ax_adv3.plot(
                x, peri_cum, marker="o", linewidth=2.1,
                color="#4b5563", label="Peripheral mean cumulative"
            )
        self.ax_adv3.set_title("Aux: Cumulative response", fontsize=adv_fs)
        self.ax_adv3.set_xlabel("Stimulation number", fontsize=adv_fs)
        self.ax_adv3.set_ylabel("Cumulative AUC (a.u.)", fontsize=adv_fs)
        self.ax_adv3.set_xticks(x)
        self.ax_adv3.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=adv_fs - 1)
        self.ax_adv3.tick_params(axis="y", labelsize=adv_fs - 1)
        self._draw_event_markers(self.ax_adv3, event_indices)

        # D) Auxiliary panel: simplified spatial heterogeneity.
        h_center_peri = None
        if peri_rois:
            h_center_peri, = self.ax_adv4.plot(
                x, center_minus_peri, marker="o", linewidth=2.1,
                color="#ef4444", label=f"{center_roi} - peripheral mean"
            )
        h_roi_std, = self.ax_adv4.plot(
            x, roi_std, marker="o", linewidth=2.0,
            color="#0f766e", label="ROI std across field"
        )
        self.ax_adv4.axhline(0.0, color="gray", linestyle="--", linewidth=1.0)
        self.ax_adv4.set_title("Aux: Spatial heterogeneity", fontsize=adv_fs)
        self.ax_adv4.set_xlabel("Stimulation number", fontsize=adv_fs)
        self.ax_adv4.set_ylabel("Index (a.u.)", fontsize=adv_fs)
        self.ax_adv4.set_xticks(np.arange(len(x_labels)))
        self.ax_adv4.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=adv_fs - 1)
        self.ax_adv4.tick_params(axis="y", labelsize=adv_fs - 1)
        self._draw_event_markers(self.ax_adv4, event_indices)

        legend_items: dict[str, object] = {}
        for handle, label in [
            (h_center_resp, h_center_resp.get_label()),
            (h_peri_resp, h_peri_resp.get_label() if h_peri_resp is not None else ""),
            (h_center_inc, h_center_inc.get_label()),
            (h_peri_inc, h_peri_inc.get_label() if h_peri_inc is not None else ""),
            (h_center_cum, h_center_cum.get_label()),
            (h_peri_cum, h_peri_cum.get_label() if h_peri_cum is not None else ""),
            (h_center_peri, h_center_peri.get_label() if h_center_peri is not None else ""),
            (h_roi_std, h_roi_std.get_label()),
        ]:
            if handle is None:
                continue
            if label and label not in legend_items:
                legend_items[label] = handle

        self.fig_adv.suptitle(
            f"Repeated stimulation evolution (baseline={baseline_n}, trend={trend_mode}, peripheral n={len(peri_rois)})",
            fontsize=max(9, adv_fs - 1),
        )
        self.fig_adv.subplots_adjust(left=0.08, right=0.80, top=0.90, bottom=0.10, wspace=0.30, hspace=0.36)
        if legend_items:
            self.adv_fig_legend = self.fig_adv.legend(
                list(legend_items.values()),
                list(legend_items.keys()),
                loc="center left",
                bbox_to_anchor=(0.81, 0.50),
                fontsize=legend_fs,
                frameon=True,
            )
        self.canvas_adv.draw()

    def _export_summary_outputs(self, df: pd.DataFrame, prefix: str) -> tuple[Path, Path]:
        metric = self._get_plot_metric_spec()
        ratio_prefix = metric["ratio"]
        roi_names = [name for name in self.roi_order if f"{ratio_prefix}{name.lower()}" in df.columns]
        if self.var_use_background.get() and self.var_bg_mode.get().strip() == "ROI (absolute)":
            bg_roi = self.var_bg_roi_name.get().strip()
            roi_names = [r for r in roi_names if r != bg_roi]
        ref_idx = self._get_reference_index(df)
        baseline_n = self._parse_baseline_frames(len(df))
        trend_mode = self._get_adv_trend_mode()

        out_summary_csv = self.current_folder / f"{prefix}_summary_metrics.csv"
        out_summary_plot = self.current_folder / f"{prefix}_summary_plots.png"

        seq_vals = df["sequence_number"].values if "sequence_number" in df.columns else np.arange(len(df))
        rows = []

        if roi_names:
            ratio_series = []
            for r in roi_names:
                y = self._normalize_to_reference(
                    df[f"{ratio_prefix}{r.lower()}"].astype(float).values, ref_idx
                )
                y = self._apply_trend_correction(y, trend_mode)
                ratio_series.append(y)
            ratio_mat = np.vstack(ratio_series)
            with np.errstate(invalid="ignore", divide="ignore"):
                base = np.nanmean(ratio_mat[:, :baseline_n], axis=1, keepdims=True)
                dff_mat = np.where(np.abs(base) > 1e-12, ratio_mat / base - 1.0, np.nan)
        else:
            dff_mat = np.empty((0, len(df)), dtype=float)

        for i, roi_name in enumerate(roi_names):
            series = dff_mat[i]
            finite_idx = np.where(np.isfinite(series))[0]
            if finite_idx.size == 0:
                rows.append(
                    {
                        "kind": "ROI",
                        "roi": roi_name,
                        "trend_mode": trend_mode,
                        "baseline_frames": baseline_n,
                        "peak_dff": np.nan,
                        "mean_increment": np.nan,
                        "cumulative_auc_final": np.nan,
                        "time_to_peak": np.nan,
                        "final_dff": np.nan,
                    }
                )
                continue

            y_f = series[finite_idx]
            x_f = finite_idx.astype(float)
            local_peak = int(np.argmax(y_f))
            peak_idx = int(finite_idx[local_peak])
            auc = float(np.trapz(y_f, x_f)) if y_f.size > 1 else float(y_f[0])
            increment = np.diff(series, prepend=np.nan)
            mean_increment = float(np.nanmean(increment)) if np.any(np.isfinite(increment)) else np.nan
            cum = self._cumulative_auc(series)
            cum_final = float(cum[finite_idx[-1]]) if cum.size > 0 else np.nan

            rows.append(
                {
                    "kind": "ROI",
                    "roi": roi_name,
                    "trend_mode": trend_mode,
                    "baseline_frames": baseline_n,
                    "peak_dff": float(series[peak_idx]),
                    "mean_increment": mean_increment,
                    "cumulative_auc_final": cum_final if np.isfinite(cum_final) else auc,
                    "time_to_peak": seq_vals[peak_idx],
                    "final_dff": float(series[finite_idx[-1]]),
                }
            )

        summary_df = pd.DataFrame(rows)
        summary_df.to_csv(out_summary_csv, index=False)

        # Summary figure
        fig = Figure(figsize=(10.8, 7.2), dpi=170)
        ax_a = fig.add_subplot(221)
        ax_b = fig.add_subplot(222)
        ax_c = fig.add_subplot(223)
        ax_d = fig.add_subplot(224)

        if summary_df.empty:
            for ax in [ax_a, ax_b, ax_c, ax_d]:
                ax.text(0.5, 0.5, "No summary data", ha="center", va="center")
                ax.set_axis_off()
            fig.savefig(out_summary_plot, dpi=200, bbox_inches="tight")
            return out_summary_csv, out_summary_plot

        labels = summary_df["roi"].astype(str).tolist()
        colors = [self._roi_color(r) for r in labels]
        x = np.arange(len(labels))

        ax_a.bar(x, summary_df["peak_dff"].astype(float).values, color=colors)
        ax_a.set_title("Peak dF/F0")
        ax_a.set_xticks(x)
        ax_a.set_xticklabels(labels, rotation=35, ha="right")

        ax_b.bar(x, summary_df["cumulative_auc_final"].astype(float).values, color=colors)
        ax_b.set_title("Final cumulative AUC")
        ax_b.set_xticks(x)
        ax_b.set_xticklabels(labels, rotation=35, ha="right")

        mean_inc = pd.to_numeric(summary_df["mean_increment"], errors="coerce").values
        ax_c.bar(x, mean_inc, color=colors)
        ax_c.set_title("Mean frame-to-frame increment")
        ax_c.set_xticks(x)
        ax_c.set_xticklabels(labels, rotation=35, ha="right")

        # Center vs peripheral final dF/F0 summary
        center_roi = self.var_center_roi.get().strip()
        if center_roi not in labels and labels:
            center_roi = labels[0]
        center_final = np.nan
        peri_final = np.nan
        if labels:
            center_final = float(
                summary_df.loc[summary_df["roi"] == center_roi, "final_dff"].astype(float).iloc[0]
            )
            peri_vals = summary_df.loc[summary_df["roi"] != center_roi, "final_dff"].astype(float).values
            if peri_vals.size > 0:
                peri_final = float(np.nanmean(peri_vals))
        comp_labels = [center_roi, "Peripheral mean"]
        comp_vals = [center_final, peri_final]
        comp_colors = [self._roi_color(center_roi), "#6b7280"]
        ax_d.bar(np.arange(2), comp_vals, color=comp_colors)
        ax_d.set_title(f"Final dF/F0 (center vs peripheral n={max(0, len(labels) - 1)})")
        ax_d.set_xticks(np.arange(2))
        ax_d.set_xticklabels(comp_labels, rotation=20, ha="right")

        fig.tight_layout()
        fig.savefig(out_summary_plot, dpi=200, bbox_inches="tight")
        return out_summary_csv, out_summary_plot

    def _save_roi_reference(self, out_path: Path) -> None:
        if self.preview_image_disp is None:
            return

        fig = Figure(figsize=(5.5, 5.5), dpi=150)
        ax = fig.add_subplot(111)
        ax.set_axis_off()
        ax.imshow(self.preview_image_disp, cmap="gray", interpolation="nearest")

        preview_path = None
        preview_name = "Preview"
        if self.sequence_records and 0 <= self.current_preview_index < len(self.sequence_records):
            rec = self.sequence_records[self.current_preview_index]
            preview_path = rec.get(self.current_preview_stack)
            if preview_path is None:
                preview_path = rec.get("stack1") or rec.get("stack2")
            preview_name = preview_path.name if preview_path is not None else rec.get("base", "Preview")

        ax.text(
            10,
            16,
            preview_name,
            color="white",
            fontsize=9,
            va="top",
            bbox=dict(facecolor="black", alpha=0.65, edgecolor="none", boxstyle="round,pad=0.25"),
        )

        for roi_name in self.roi_order:
            roi_xyxy = self.roi_xyxy_map.get(roi_name)
            if roi_xyxy is None:
                continue
            x1, y1, x2, y2 = roi_xyxy
            color = self._roi_color(roi_name)
            rect = Rectangle(
                (x1, y1),
                x2 - x1,
                y2 - y1,
                linewidth=1.5,
                edgecolor=color,
                facecolor="none"
            )
            ax.add_patch(rect)
            ax.text(x1, max(0, y1 - 4), roi_name, color=color, fontsize=9)

        pixel_size_um = None
        pixel_override_txt = self.var_gif_pixel_size_um.get().strip() if hasattr(self, "var_gif_pixel_size_um") else ""
        if pixel_override_txt:
            try:
                pixel_size_um = float(pixel_override_txt)
            except Exception:
                pixel_size_um = None
        elif preview_path is not None:
            pixel_size_um = infer_pixel_size_um_from_tiff(preview_path)

        try:
            scalebar_um = float(self.var_gif_scalebar_um.get().strip())
        except Exception:
            scalebar_um = 0.0

        if (
            pixel_size_um is not None
            and pixel_size_um > 0
            and scalebar_um > 0
        ):
            h, w = self.preview_image_disp.shape[:2]
            bar_px = int(round(scalebar_um / pixel_size_um))
            bar_px = max(1, min(bar_px, int(0.6 * w)))
            pad = max(8, int(min(h, w) * 0.02))
            bar_thick = max(3, int(min(h, w) * 0.006))
            x0 = max(pad, int(0.05 * w))
            y0 = h - pad - bar_thick
            label_text = f"{scalebar_um:g} um"

            ax.add_patch(
                Rectangle(
                    (x0 - pad, y0 - (bar_thick + 18)),
                    bar_px + 2 * pad,
                    bar_thick + 22,
                    facecolor="black",
                    edgecolor="none",
                    alpha=0.65,
                )
            )
            ax.add_patch(
                Rectangle(
                    (x0, y0),
                    bar_px,
                    bar_thick,
                    facecolor="white",
                    edgecolor="none",
                )
            )
            ax.text(
                x0,
                y0 - 4,
                label_text,
                color="white",
                fontsize=8,
                va="bottom",
            )

        fig.savefig(out_path, dpi=150, bbox_inches="tight")


# =========================
# Main
# =========================
def main() -> None:
    app = ROISequenceAnalysisGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
