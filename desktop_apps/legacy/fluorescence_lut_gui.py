# tiff_stack_lut_gui_rangebar.py
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import tifffile as tiff

from services.fluorescence import stack as fl_stack

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

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

# =================== EDIT THIS: PANELS + work function ===================
APP_TITLE = "TIFF Stack LUT GUI"

LUT_OPTIONS = fl_stack.LUT_OPTIONS
DENOISE_OPTIONS = fl_stack.DENOISE_OPTIONS
BACKGROUND_OPTIONS = fl_stack.BACKGROUND_OPTIONS

DEFAULT_LUT_BY_INDEX = fl_stack.DEFAULT_LUT_BY_INDEX
DEFAULT_DENOISE_BY_INDEX = fl_stack.DEFAULT_DENOISE_BY_INDEX
DEFAULT_BACKGROUND_BY_INDEX = fl_stack.DEFAULT_BACKGROUND_BY_INDEX

PREVIEW_FIG_W = 5.2
PREVIEW_FIG_H = 5.2
PREVIEW_DPI = 100
# =========================================================================


# --------------------------- Helper functions ---------------------------
def read_tiff_as_pages(tiff_path: Path) -> list[np.ndarray]:
    return fl_stack.read_tiff_as_pages(tiff_path, tiff)


compute_default_min_max = fl_stack.compute_default_min_max


def normalize_for_display(img: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """
    Normalize an image into [0, 1] for display.
    """
    arr = np.asarray(img, dtype=np.float32)

    if vmax <= vmin:
        return np.zeros_like(arr, dtype=np.float32)

    out = (arr - vmin) / (vmax - vmin)
    out = np.clip(out, 0.0, 1.0)
    return out.astype(np.float32)


def lut_to_rgb_weights(lut_name: str) -> np.ndarray:
    """
    Convert a LUT name into RGB weights.
    """
    name = lut_name.strip().lower()

    if name == "red":
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if name == "blue":
        return np.array([0.0, 0.0, 1.0], dtype=np.float32)
    if name == "gray":
        return np.array([1.0, 1.0, 1.0], dtype=np.float32)
    if name == "green":
        return np.array([0.0, 1.0, 0.0], dtype=np.float32)
    if name == "magenta":
        return np.array([1.0, 0.0, 1.0], dtype=np.float32)
    if name == "cyan":
        return np.array([0.0, 1.0, 1.0], dtype=np.float32)
    if name == "yellow":
        return np.array([1.0, 1.0, 0.0], dtype=np.float32)

    return np.array([1.0, 1.0, 1.0], dtype=np.float32)


def compose_rgb_preview(
    pages: list[np.ndarray],
    channel_settings: list[dict]
) -> np.ndarray:
    """
    Compose an RGB preview using additive blending.
    """
    h, w = pages[0].shape
    rgb = np.zeros((h, w, 3), dtype=np.float32)

    for s in channel_settings:
        if not s["include"]:
            continue

        idx = s["page_index"]
        lut = s["lut"]
        vmin = s["min"]
        vmax = s["max"]

        proc = preprocess_stack_image(
            pages[idx],
            background_mode=s.get("background", "Off"),
            denoise_mode=s.get("denoise", "Off"),
        )
        gray = normalize_for_display(proc, vmin, vmax)
        weights = lut_to_rgb_weights(lut)

        for c in range(3):
            rgb[..., c] += gray * weights[c]

    rgb = np.clip(rgb, 0.0, 1.0)
    return rgb


convert_to_export_dtype = fl_stack.convert_to_export_dtype
_box_blur2d = fl_stack.box_blur2d
apply_background_suppression = fl_stack.apply_background_suppression
apply_optional_denoise = fl_stack.apply_optional_denoise
preprocess_stack_image = fl_stack.preprocess_stack_image
compute_auto_range_with_processing = fl_stack.compute_auto_range_with_processing
imagej_lut_command = fl_stack.imagej_lut_command
to_macro_path = fl_stack.to_macro_path
build_fiji_macro = fl_stack.build_fiji_macro


# --------------------------- Dual range slider widget ---------------------------
class DualRangeSlider(ttk.Frame):
    """
    A simple dual-handle range slider implemented with Tkinter Canvas.
    """
    def __init__(
        self,
        parent: tk.Widget,
        min_value: float,
        max_value: float,
        init_low: float,
        init_high: float,
        command=None,
        width: int = 260,
        height: int = 34,
    ):
        super().__init__(parent)

        self.min_value = float(min_value)
        self.max_value = float(max_value)
        self.low_value = float(init_low)
        self.high_value = float(init_high)
        self.command = command

        if self.max_value <= self.min_value:
            self.max_value = self.min_value + 1.0

        if self.low_value < self.min_value:
            self.low_value = self.min_value
        if self.high_value > self.max_value:
            self.high_value = self.max_value
        if self.low_value >= self.high_value:
            self.high_value = min(self.max_value, self.low_value + self._small_step())

        self.width = width
        self.height = height
        self.pad = 12
        self.handle_r = 6
        self.active_handle = None

        self.canvas = tk.Canvas(
            self,
            width=self.width,
            height=self.height,
            highlightthickness=0,
            bg="white"
        )
        self.canvas.pack(fill="x", expand=True)

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self._draw()

    def _small_step(self) -> float:
        step = (self.max_value - self.min_value) / 1000.0
        return max(step, 1e-6)

    def _value_to_x(self, value: float) -> float:
        frac = (value - self.min_value) / (self.max_value - self.min_value)
        frac = min(max(frac, 0.0), 1.0)
        return self.pad + frac * (self.width - 2 * self.pad)

    def _x_to_value(self, x: float) -> float:
        frac = (x - self.pad) / (self.width - 2 * self.pad)
        frac = min(max(frac, 0.0), 1.0)
        return self.min_value + frac * (self.max_value - self.min_value)

    def _draw(self) -> None:
        self.canvas.delete("all")

        y = self.height / 2
        x1 = self._value_to_x(self.low_value)
        x2 = self._value_to_x(self.high_value)

        # Full track
        self.canvas.create_line(
            self.pad, y, self.width - self.pad, y,
            fill="#c8c8c8", width=6, capstyle="round"
        )

        # Selected range
        self.canvas.create_line(
            x1, y, x2, y,
            fill="#4a90e2", width=6, capstyle="round"
        )

        # Handles
        self.canvas.create_oval(
            x1 - self.handle_r, y - self.handle_r,
            x1 + self.handle_r, y + self.handle_r,
            fill="white", outline="black", width=1, tags="low_handle"
        )
        self.canvas.create_oval(
            x2 - self.handle_r, y - self.handle_r,
            x2 + self.handle_r, y + self.handle_r,
            fill="white", outline="black", width=1, tags="high_handle"
        )

    def _on_click(self, event) -> None:
        x = event.x
        low_x = self._value_to_x(self.low_value)
        high_x = self._value_to_x(self.high_value)

        if abs(x - low_x) <= abs(x - high_x):
            self.active_handle = "low"
        else:
            self.active_handle = "high"

        self._update_from_x(x)

    def _on_drag(self, event) -> None:
        self._update_from_x(event.x)

    def _on_release(self, event) -> None:
        self.active_handle = None

    def _update_from_x(self, x: float) -> None:
        value = self._x_to_value(x)

        if self.active_handle == "low":
            self.low_value = min(value, self.high_value - self._small_step())
            self.low_value = max(self.low_value, self.min_value)

        elif self.active_handle == "high":
            self.high_value = max(value, self.low_value + self._small_step())
            self.high_value = min(self.high_value, self.max_value)

        self._draw()

        if callable(self.command):
            self.command(self.low_value, self.high_value)

    def set_values(self, low: float, high: float) -> None:
        self.low_value = float(low)
        self.high_value = float(high)

        if self.low_value < self.min_value:
            self.low_value = self.min_value
        if self.high_value > self.max_value:
            self.high_value = self.max_value
        if self.low_value >= self.high_value:
            self.high_value = min(self.max_value, self.low_value + self._small_step())

        self._draw()

        if callable(self.command):
            self.command(self.low_value, self.high_value)

    def get_values(self) -> tuple[float, float]:
        return float(self.low_value), float(self.high_value)


# --------------------------- Stack row widget ---------------------------
class StackControlRow(ttk.Frame):
    """
    One compact control row for one TIFF stack/page.
    """
    def __init__(
        self,
        parent: tk.Widget,
        page_index: int,
        img: np.ndarray,
        default_include: bool,
        default_lut: str,
        default_background: str,
        default_denoise: str,
        should_auto_rerange,
        on_change_callback
    ):
        super().__init__(parent)

        self.page_index = page_index
        self.img = np.asarray(img)
        self.should_auto_rerange = should_auto_rerange
        self.on_change_callback = on_change_callback

        self.data_min = float(np.min(self.img))
        self.data_max = float(np.max(self.img))
        if self.data_max <= self.data_min:
            self.data_max = self.data_min + 1.0

        self.default_min, self.default_max = compute_default_min_max(self.img)

        self.var_include = tk.BooleanVar(value=default_include)
        self.var_lut = tk.StringVar(value=default_lut)
        self.var_background = tk.StringVar(value=default_background)
        self.var_denoise = tk.StringVar(value=default_denoise)
        self.current_min = float(self.default_min)
        self.current_max = float(self.default_max)

        self._build()

    def _build(self) -> None:
        self.columnconfigure(6, weight=1)

        chk = ttk.Checkbutton(
            self,
            variable=self.var_include,
            command=self._notify_change
        )
        chk.grid(row=0, column=0, padx=(2, 4), sticky="w")

        lbl_name = ttk.Label(self, text=f"Stack {self.page_index + 1}", width=8)
        lbl_name.grid(row=0, column=1, padx=(0, 6), sticky="w")

        cmb = ttk.Combobox(
            self,
            textvariable=self.var_lut,
            values=LUT_OPTIONS,
            state="readonly",
            width=9
        )
        cmb.grid(row=0, column=2, padx=(0, 6), sticky="w")
        cmb.bind("<<ComboboxSelected>>", lambda e: self._notify_change())

        cmb_background = ttk.Combobox(
            self,
            textvariable=self.var_background,
            values=BACKGROUND_OPTIONS,
            state="readonly",
            width=8,
        )
        cmb_background.grid(row=0, column=3, padx=(0, 6), sticky="w")
        cmb_background.bind("<<ComboboxSelected>>", self._on_preprocess_selected)

        cmb_denoise = ttk.Combobox(
            self,
            textvariable=self.var_denoise,
            values=DENOISE_OPTIONS,
            state="readonly",
            width=8,
        )
        cmb_denoise.grid(row=0, column=4, padx=(0, 6), sticky="w")
        cmb_denoise.bind("<<ComboboxSelected>>", self._on_preprocess_selected)

        self.lbl_min = ttk.Label(self, text=f"{self.current_min:.1f}", width=8)
        self.lbl_min.grid(row=0, column=5, padx=(0, 4), sticky="e")

        self.range_slider = DualRangeSlider(
            self,
            min_value=self.data_min,
            max_value=self.data_max,
            init_low=self.default_min,
            init_high=self.default_max,
            command=self._on_range_change,
            width=260,
            height=30,
        )
        self.range_slider.grid(row=0, column=6, padx=(0, 4), sticky="ew")

        self.lbl_max = ttk.Label(self, text=f"{self.current_max:.1f}", width=8)
        self.lbl_max.grid(row=0, column=7, padx=(0, 6), sticky="w")

        btn_auto = ttk.Button(self, text="Auto", width=7, command=self.auto_range)
        btn_auto.grid(row=0, column=8, padx=(0, 4))

        btn_reset = ttk.Button(self, text="Reset", width=7, command=self.reset_range)
        btn_reset.grid(row=0, column=9)

    def _on_range_change(self, low: float, high: float) -> None:
        self.current_min = float(low)
        self.current_max = float(high)
        self.lbl_min.config(text=f"{self.current_min:.1f}")
        self.lbl_max.config(text=f"{self.current_max:.1f}")
        self._notify_change()

    def auto_range(self) -> None:
        vmin, vmax = compute_auto_range_with_processing(
            self.img,
            self.var_background.get(),
            self.var_denoise.get(),
        )
        self.range_slider.set_values(vmin, vmax)

    def reset_range(self) -> None:
        self.range_slider.set_values(self.data_min, self.data_max)

    def _notify_change(self) -> None:
        if callable(self.on_change_callback):
            self.on_change_callback()

    def _on_preprocess_selected(self, _event=None) -> None:
        auto_rerange = bool(self.should_auto_rerange()) if callable(self.should_auto_rerange) else True
        if auto_rerange:
            self.auto_range()
        else:
            self._notify_change()

    def get_state(self) -> dict:
        low, high = self.range_slider.get_values()
        return {
            "include": bool(self.var_include.get()),
            "page_index": self.page_index,
            "lut": self.var_lut.get().strip(),
            "background": self.var_background.get().strip(),
            "denoise": self.var_denoise.get().strip(),
            "min": float(low),
            "max": float(high),
        }


# --------------------------- Main GUI ---------------------------
class TIFFStackLUTGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1220x820")
        self.minsize(980, 640)

        self.script_dir = Path(__file__).resolve().parent
        self.current_folder = self.script_dir
        self.current_tiff_path: Path | None = None
        self.current_pages: list[np.ndarray] = []
        self.stack_rows: list[StackControlRow] = []

        self._build_layout()
        self._refresh_file_list()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # Left panel: smaller file browser
        left = ttk.Frame(self, padding=8)
        left.grid(row=0, column=0, sticky="nsw")
        left.rowconfigure(2, weight=1)

        ttk.Label(left, text="Folder").grid(row=0, column=0, sticky="w")

        folder_bar = ttk.Frame(left)
        folder_bar.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        folder_bar.columnconfigure(0, weight=1)

        self.var_folder = tk.StringVar(value=str(self.current_folder))
        self.ent_folder = ttk.Entry(folder_bar, textvariable=self.var_folder, width=28)
        self.ent_folder.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        btn_browse = ttk.Button(folder_bar, text="Browse", command=self._browse_folder)
        btn_browse.grid(row=0, column=1, sticky="ew")

        self.file_listbox = tk.Listbox(left, width=28, height=32)
        self.file_listbox.grid(row=2, column=0, sticky="nsew")
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_selected)

        # Right panel
        right = ttk.Frame(self, padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        self.lbl_current_file = ttk.Label(right, text="No TIFF file selected.")
        self.lbl_current_file.grid(row=0, column=0, sticky="w", pady=(0, 6))

        # Control area above preview
        top_controls = ttk.LabelFrame(right, text="Display settings", padding=6)
        top_controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        top_controls.columnconfigure(0, weight=1)

        bar_top = ttk.Frame(top_controls)
        bar_top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar_top.columnconfigure(0, weight=1)

        ttk.Label(
            bar_top,
            text="Use | Stack | LUT | BG Suppress | Denoise | Min | Range slider | Max"
        ).grid(row=0, column=0, sticky="w")

        btn_export = ttk.Button(
            bar_top,
            text="Export TIFF + Fiji macro",
            command=self._export_current_selection
        )
        btn_export.grid(row=0, column=1, sticky="e")

        btn_export_all = ttk.Button(
            bar_top,
            text="Export All Listed Files",
            command=self._export_all_listed_files
        )
        btn_export_all.grid(row=0, column=2, padx=(8, 0), sticky="e")

        batch_opts = ttk.Frame(top_controls)
        batch_opts.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.var_batch_use_template = tk.BooleanVar(value=True)
        self.var_batch_lock_ranges = tk.BooleanVar(value=False)
        self.var_auto_rerange_on_denoise = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            batch_opts,
            text="Batch apply current include/LUT/background/denoise",
            variable=self.var_batch_use_template,
        ).grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Checkbutton(
            batch_opts,
            text="Batch also lock current min/max",
            variable=self.var_batch_lock_ranges,
        ).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(
            batch_opts,
            text="Auto re-range after denoise",
            variable=self.var_auto_rerange_on_denoise,
        ).grid(row=0, column=2, padx=(12, 0), sticky="w")

        rows_holder = ttk.Frame(top_controls)
        rows_holder.grid(row=2, column=0, sticky="ew")
        rows_holder.columnconfigure(0, weight=1)

        self.rows_container = rows_holder

        # Preview area below controls
        preview_outer = ttk.LabelFrame(right, text="Composite preview", padding=6)
        preview_outer.grid(row=2, column=0, sticky="n")
        self.fig = Figure(figsize=(PREVIEW_FIG_W, PREVIEW_FIG_H), dpi=PREVIEW_DPI)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()

        self.canvas = FigureCanvasTkAgg(self.fig, master=preview_outer)
        self.canvas.get_tk_widget().pack()

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select folder containing TIFF files",
            initialdir=str(self.current_folder)
        )
        if not folder:
            return

        self.current_folder = Path(folder)
        self.var_folder.set(str(self.current_folder))
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_listbox.delete(0, tk.END)

        if not self.current_folder.exists():
            return

        tiff_files = sorted(
            list(self.current_folder.glob("*.tif")) +
            list(self.current_folder.glob("*.tiff"))
        )

        # Hide TIFF files generated by this GUI
        tiff_files = [
            p for p in tiff_files
            if not self._is_generated_tiff(p)
        ]

        for p in tiff_files:
            self.file_listbox.insert(tk.END, p.name)

    def _on_file_selected(self, event=None) -> None:
        selection = self.file_listbox.curselection()
        if not selection:
            return

        filename = self.file_listbox.get(selection[0])
        tiff_path = self.current_folder / filename

        try:
            pages = read_tiff_as_pages(tiff_path)
        except Exception as e:
            messagebox.showerror("Read error", f"Failed to read TIFF:\n{e}")
            return

        self.current_tiff_path = tiff_path
        self.current_pages = pages
        self.lbl_current_file.config(
            text=f"Selected file: {tiff_path.name}   |   Stacks: {len(pages)}   |   Shape: {pages[0].shape}"
        )

        self._rebuild_stack_rows()
        self._refresh_preview()

    def _clear_stack_rows(self) -> None:
        for widget in self.rows_container.winfo_children():
            widget.destroy()
        self.stack_rows = []

    def _rebuild_stack_rows(self) -> None:
        self._clear_stack_rows()

        for i, page in enumerate(self.current_pages):
            default_lut = DEFAULT_LUT_BY_INDEX.get(i, "Gray")
            default_background = DEFAULT_BACKGROUND_BY_INDEX.get(i, "Off")
            default_denoise = DEFAULT_DENOISE_BY_INDEX.get(i, "Off")
            default_include = True if i < 3 else False

            row = StackControlRow(
                self.rows_container,
                page_index=i,
                img=page,
                default_include=default_include,
                default_lut=default_lut,
                default_background=default_background,
                default_denoise=default_denoise,
                should_auto_rerange=self._should_auto_rerange_on_denoise,
                on_change_callback=self._refresh_preview
            )
            row.grid(row=i, column=0, sticky="ew", pady=3)
            self.stack_rows.append(row)

    def _should_auto_rerange_on_denoise(self) -> bool:
        return bool(self.var_auto_rerange_on_denoise.get())

    def _get_channel_settings(self) -> list[dict]:
        return [row.get_state() for row in self.stack_rows]
    
    def _is_generated_tiff(self, path: Path) -> bool:
        return fl_stack.is_generated_tiff(path)

    def _refresh_preview(self) -> None:
        self.ax.clear()
        self.ax.set_axis_off()

        if not self.current_pages:
            self.ax.text(0.5, 0.5, "No image loaded", ha="center", va="center", fontsize=12)
            self.canvas.draw()
            return

        try:
            preview_rgb = compose_rgb_preview(self.current_pages, self._get_channel_settings())
            self.ax.imshow(preview_rgb, interpolation="nearest")
        except Exception as e:
            self.ax.text(0.5, 0.5, f"Preview error:\n{e}", ha="center", va="center", fontsize=11)

        self.canvas.draw()

    def _export_current_selection(self) -> None:
        if self.current_tiff_path is None or not self.current_pages:
            messagebox.showwarning("No file", "Please select a TIFF file first.")
            return

        settings = self._get_channel_settings()
        self._export_with_settings(
            tiff_path=self.current_tiff_path,
            pages=self.current_pages,
            settings=settings,
            show_message=True,
        )

    def _build_default_settings_for_pages(self, pages: list[np.ndarray]) -> list[dict]:
        return fl_stack.build_default_settings_for_pages(pages)

    def _build_settings_from_template(
        self,
        pages: list[np.ndarray],
        template_settings: list[dict],
        lock_ranges: bool,
    ) -> list[dict]:
        """Build per-file settings by applying current UI template onto each page index."""
        return fl_stack.build_settings_from_template(pages, template_settings, lock_ranges)

    def _export_with_settings(
        self,
        tiff_path: Path,
        pages: list[np.ndarray],
        settings: list[dict],
        show_message: bool = True,
    ) -> dict:
        result = fl_stack.export_with_settings(tiff_path, pages, settings, tiff)

        if show_message:
            msg_lines = [
                "Export completed.",
                "",
                "Individual stack TIFF files:",
            ]
            msg_lines.extend(result["stack_files"])
            msg_lines.extend(
                [
                    "",
                    f"Combined TIFF:\n{result['combined_tiff']}",
                    "",
                    f"Fiji macro:\n{result['macro']}",
                    "",
                    f"Settings JSON:\n{result['json']}",
                ]
            )
            messagebox.showinfo("Export finished", "\n".join(msg_lines))

        return result

    def _export_all_listed_files(self) -> None:
        file_count = self.file_listbox.size()
        if file_count == 0:
            messagebox.showwarning("No files", "No TIFF files are listed in the left panel.")
            return

        ok = messagebox.askyesno(
            "Batch export",
            (
                f"Export all {file_count} TIFF files listed on the left?\n\n"
                "Batch mode can apply current GUI settings by stack index "
                "(include/LUT/background/denoise; optional min/max lock)."
            ),
        )
        if not ok:
            return

        success_files: list[str] = []
        failed_files: list[tuple[str, str]] = []

        use_template = bool(self.var_batch_use_template.get()) and bool(self.stack_rows)
        lock_ranges = bool(self.var_batch_lock_ranges.get())
        template_settings = self._get_channel_settings() if use_template else []

        for i in range(file_count):
            filename = self.file_listbox.get(i)
            tiff_path = self.current_folder / filename

            try:
                pages = read_tiff_as_pages(tiff_path)
                if use_template:
                    settings = self._build_settings_from_template(
                        pages=pages,
                        template_settings=template_settings,
                        lock_ranges=lock_ranges,
                    )
                else:
                    settings = self._build_default_settings_for_pages(pages)
                self._export_with_settings(
                    tiff_path=tiff_path,
                    pages=pages,
                    settings=settings,
                    show_message=False,
                )
                success_files.append(filename)
            except Exception as e:
                failed_files.append((filename, str(e)))

        msg_lines = [
            f"Batch export finished.",
            f"Success: {len(success_files)}",
            f"Failed: {len(failed_files)}",
        ]
        if failed_files:
            msg_lines.append("")
            msg_lines.append("Failed files:")
            for fname, err in failed_files[:10]:
                msg_lines.append(f"- {fname}: {err}")
            if len(failed_files) > 10:
                msg_lines.append(f"... and {len(failed_files) - 10} more.")

        messagebox.showinfo("Batch export finished", "\n".join(msg_lines))


def main() -> None:
    app = TIFFStackLUTGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
