from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from services.fluorescence.manual_roi import (
    ROI_COLORS,
    ImageRois,
    RoiPolygon,
    analyze_image,
    summarize_measurements,
    write_measurements_csv,
    write_summary_csv,
)
from services.fluorescence.preview_export import (
    DEFAULT_CHANNEL_COLORS,
    DEFAULT_FOLDER,
    LOCK_MASK,
    SCROLL_PIXELS_PER_NOTCH,
    SHIFT_MASK,
    TRACKPAD_SCROLL_MULTIPLIER,
    ZOOM_MAX,
    ZOOM_MIN,
    RotationGeometry,
    display_image_for_channels,
    find_tiff_files,
    image_point_to_rotated_view,
    load_tiff_channels,
    natural_sort_key,
    parse_fluorescence_name,
    rotate_image_for_preview,
    rotated_view_to_image_point,
)
from services.fluorescence.preview_export import (
    fmt_float as _fmt_float,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_folder() -> Path:
    return DEFAULT_FOLDER if DEFAULT_FOLDER.exists() else PROJECT_ROOT


class FluorescenceManualRoiApp:
    def __init__(self, root: tk.Tk, start_paths: Sequence[str] | None = None) -> None:
        self.root = root
        self.root.title("Fluorescence Manual ROI Analysis")
        self.root.geometry("1320x880")
        self.root.minsize(1040, 680)

        self.image_paths: list[Path] = []
        self.states: list[ImageRois] = []
        self.current_index = -1
        self.current_channels: np.ndarray | None = None
        self.base_display_image: Image.Image | None = None
        self.display_image: Image.Image | None = None
        self.view_geometry: RotationGeometry | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.zoom = 1.0

        self.drawing = False
        self.pending_points: list[tuple[float, float]] = []
        self.preview_item: int | None = None

        self.folder_var = tk.StringVar(value=str(_default_folder()))
        self.image_info_var = tk.StringVar(value="No image loaded")
        self.status_var = tk.StringVar(value="Load a TIFF folder to start drawing ROIs.")
        self.zoom_var = tk.StringVar(value="Zoom: 100%")
        self.channel_var = tk.StringVar(value="Composite")
        self.channel_color_vars = [tk.StringVar(value=color) for color in DEFAULT_CHANNEL_COLORS]
        self.channel_enabled_vars = [tk.BooleanVar(value=True) for _ in DEFAULT_CHANNEL_COLORS]
        self.channel_color_buttons: list[tk.Button] = []
        self.channel_check_buttons: list[ttk.Checkbutton] = []
        self.low_percent_var = tk.StringVar(value="1")
        self.high_percent_var = tk.StringVar(value="99.8")
        self.rotation_degrees_var = tk.StringVar(value="0")
        self.zoom_input_var = tk.StringVar(value="100")
        self.preview_width_var = tk.StringVar(value="")
        self.preview_height_var = tk.StringVar(value="")
        self.preview_scale_var = tk.StringVar(value="1")
        self.preview_include_rois_var = tk.BooleanVar(value=False)
        self.roi_label_var = tk.StringVar(value="ROI 1")
        self.roi_kind_var = tk.StringVar(value="signal")

        self._build_ui()
        self._bind_events()
        self._update_channel_color_buttons(3)

        if start_paths:
            self.root.after(100, lambda: self.load_images(start_paths))
        elif DEFAULT_FOLDER.exists():
            self.root.after(100, lambda: self.load_images([DEFAULT_FOLDER]))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, width=360)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.grid_propagate(False)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.left_scroll_canvas = tk.Canvas(left, highlightthickness=0, borderwidth=0)
        left_scrollbar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.left_scroll_canvas.yview)
        self.left_scroll_canvas.configure(yscrollcommand=left_scrollbar.set)
        self.left_scroll_canvas.grid(row=0, column=0, sticky="nsew")
        left_scrollbar.grid(row=0, column=1, sticky="ns")

        left_inner = ttk.Frame(self.left_scroll_canvas)
        left_window = self.left_scroll_canvas.create_window((0, 0), window=left_inner, anchor="nw")
        left_inner.bind(
            "<Configure>",
            lambda _event: self.left_scroll_canvas.configure(scrollregion=self.left_scroll_canvas.bbox("all")),
        )
        self.left_scroll_canvas.bind(
            "<Configure>",
            lambda event: self.left_scroll_canvas.itemconfigure(left_window, width=event.width),
        )

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_file_controls(left_inner)
        self._build_display_controls(left_inner)
        self._build_analysis_controls(left_inner)
        self._build_roi_controls(left_inner)
        self._bind_left_scroll_wheel(left)
        self._build_canvas(right)

        status = ttk.Label(outer, textvariable=self.status_var, anchor="w")
        status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_file_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Files", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        ttk.Entry(frame, textvariable=self.folder_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(frame, text="Browse", command=self.browse_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(frame, text="Load", command=lambda: self.load_images([self.folder_var.get()])).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        self.image_combo = ttk.Combobox(frame, state="readonly", width=30)
        self.image_combo.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.image_combo.bind("<<ComboboxSelected>>", self._on_image_selected)

        nav = ttk.Frame(frame)
        nav.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Previous", command=self.prev_image).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(nav, text="Next", command=self.next_image).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        ttk.Label(frame, textvariable=self.image_info_var, wraplength=290).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_display_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Display", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Channel").grid(row=0, column=0, sticky="w")
        self.channel_combo = ttk.Combobox(frame, state="readonly", textvariable=self.channel_var)
        self.channel_combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0))
        self.channel_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_display())

        ttk.Label(frame, text="Percentile").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.low_percent_var, width=8).grid(
            row=1, column=1, sticky="ew", padx=(6, 3), pady=(8, 0)
        )
        ttk.Entry(frame, textvariable=self.high_percent_var, width=8).grid(
            row=1, column=2, sticky="ew", padx=(3, 0), pady=(8, 0)
        )
        ttk.Label(frame, text="Colors").grid(row=2, column=0, sticky="w", pady=(8, 0))
        color_frame = ttk.Frame(frame)
        color_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(8, 0))
        for index in range(len(DEFAULT_CHANNEL_COLORS)):
            item = ttk.Frame(color_frame)
            item.grid(row=index // 4, column=index % 4, sticky="ew", padx=2, pady=2)
            check = ttk.Checkbutton(
                item,
                variable=self.channel_enabled_vars[index],
                command=self.refresh_display,
            )
            check.pack(side=tk.LEFT)
            self.channel_check_buttons.append(check)
            button = tk.Button(
                item,
                text=f"Ch{index + 1}",
                width=5,
                command=lambda channel_index=index: self.choose_channel_color(channel_index),
            )
            button.pack(side=tk.LEFT)
            self.channel_color_buttons.append(button)
        ttk.Button(frame, text="Refresh Display", command=self.refresh_display).grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        ttk.Label(frame, text="Rotation (deg)").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.rotation_degrees_var, width=8).grid(
            row=4, column=1, sticky="ew", padx=(6, 3), pady=(8, 0)
        )
        rotate_tools = ttk.Frame(frame)
        rotate_tools.grid(row=4, column=2, sticky="ew", pady=(8, 0))
        ttk.Button(rotate_tools, text="Apply", command=self.apply_rotation_input).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(rotate_tools, text="0", command=self.reset_rotation).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(frame, text="Zoom %").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.zoom_input_var, width=8).grid(
            row=5, column=1, sticky="ew", padx=(6, 3), pady=(8, 0)
        )
        ttk.Button(frame, text="Apply", command=self.apply_zoom_input).grid(row=5, column=2, sticky="ew", pady=(8, 0))

        rotate_quick = ttk.Frame(frame)
        rotate_quick.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        rotate_quick.columnconfigure(0, weight=1)
        rotate_quick.columnconfigure(1, weight=1)
        rotate_quick.columnconfigure(2, weight=1)
        ttk.Button(rotate_quick, text="-90°", command=lambda: self.rotate_by(-90)).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(rotate_quick, text="+90°", command=lambda: self.rotate_by(90)).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(rotate_quick, text="+1°", command=lambda: self.rotate_by(1)).grid(row=0, column=2, sticky="ew", padx=(3, 0))

        zoom = ttk.Frame(frame)
        zoom.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        zoom.columnconfigure(0, weight=1)
        zoom.columnconfigure(1, weight=1)
        ttk.Button(zoom, text="Fit Window", command=self.fit_to_window).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(zoom, text="100%", command=self.actual_size).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(frame, textvariable=self.zoom_var).grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_roi_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="ROI", padding=8)
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.roi_label_var).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(frame, text="Type").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            frame,
            state="readonly",
            textvariable=self.roi_kind_var,
            values=("signal", "background"),
            width=14,
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))

        ttk.Button(frame, text="Start Polygon", command=self.start_roi).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Button(frame, text="Finish ROI", command=self.finish_roi).grid(
            row=3, column=0, sticky="ew", pady=(6, 0), padx=(0, 3)
        )
        ttk.Button(frame, text="Cancel Current", command=self.cancel_roi).grid(
            row=3, column=1, sticky="ew", pady=(6, 0), padx=(3, 0)
        )

        self.roi_list = tk.Listbox(frame, height=8, exportselection=False)
        self.roi_list.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        ttk.Button(frame, text="Undo Last ROI", command=self.undo_roi).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Button(frame, text="Clear Current ROIs", command=self.clear_current_rois).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(frame, text="Copy Current ROIs to All Images", command=self.copy_current_rois_to_all).grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

    def _build_analysis_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Save / Analyze", padding=8)
        frame.pack(fill=tk.X)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        ttk.Button(frame, text="Export ROI JSON", command=self.save_rois_json).grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Button(frame, text="Load ROI JSON", command=self.load_rois_json).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )
        ttk.Button(frame, text="Export Current ROI PNG", command=self.export_current_roi_overlay).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0)
        )

        ttk.Separator(frame).grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(frame, text="Preview W").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text="H").grid(row=4, column=1, sticky="w")
        ttk.Label(frame, text="Scale").grid(row=4, column=2, sticky="w")
        ttk.Entry(frame, textvariable=self.preview_width_var, width=8).grid(row=5, column=0, sticky="ew", padx=(0, 3))
        ttk.Entry(frame, textvariable=self.preview_height_var, width=8).grid(row=5, column=1, sticky="ew", padx=3)
        ttk.Entry(frame, textvariable=self.preview_scale_var, width=8).grid(row=5, column=2, sticky="ew", padx=(3, 0))
        ttk.Checkbutton(frame, text="Overlay ROIs", variable=self.preview_include_rois_var).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Button(frame, text="Export Current Preview PNG", command=self.export_current_preview).grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )
        ttk.Button(frame, text="Batch Export Preview PNGs", command=self.export_all_previews).grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

        ttk.Separator(frame).grid(row=9, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Button(frame, text="Analyze Current Image CSV", command=self.analyze_current).grid(
            row=10, column=0, columnspan=3, sticky="ew"
        )
        ttk.Button(frame, text="Analyze All CSV", command=self.analyze_all).grid(
            row=11, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

    def _build_canvas(self, parent: ttk.Frame) -> None:
        self.canvas = tk.Canvas(parent, bg="#111111", highlightthickness=0, cursor="crosshair")
        x_scroll = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_left_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.finish_roi())
        self.canvas.bind("<Button-2>", lambda _event: self.finish_roi())
        self.canvas.bind("<Button-3>", lambda _event: self.finish_roi())
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self._on_button_wheel(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self._on_button_wheel(event, -1))
        self.root.bind("<Return>", lambda _event: self.finish_roi())
        self.root.bind("<Escape>", lambda _event: self.cancel_roi())
        self.root.bind("<Left>", self.prev_image)
        self.root.bind("<Right>", self.next_image)

    def _bind_left_scroll_wheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self._on_left_mousewheel, add="+")
        widget.bind("<Button-4>", lambda event: self._on_left_button_wheel(event, 1), add="+")
        widget.bind("<Button-5>", lambda event: self._on_left_button_wheel(event, -1), add="+")
        for child in widget.winfo_children():
            self._bind_left_scroll_wheel(child)

    def _on_left_mousewheel(self, event: tk.Event[tk.Widget]) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        if delta:
            self._left_scroll_by_pixels(self._scroll_pixels_from_wheel_delta(delta))
        return "break"

    def _on_left_button_wheel(self, _event: tk.Event[tk.Widget], direction: int) -> str:
        self._left_scroll_by_pixels(-direction * SCROLL_PIXELS_PER_NOTCH)
        return "break"

    def _left_scroll_by_pixels(self, delta_pixels: float) -> None:
        canvas = getattr(self, "left_scroll_canvas", None)
        if canvas is None:
            return
        bbox = canvas.bbox("all")
        if not bbox:
            return
        content_height = max(1.0, float(bbox[3] - bbox[1]))
        visible_height = max(1.0, float(canvas.winfo_height()))
        if content_height <= visible_height:
            canvas.yview_moveto(0.0)
            return
        first, _last = canvas.yview()
        max_top = max(0.0, content_height - visible_height)
        current_top = first * content_height
        new_top = min(max(current_top + delta_pixels, 0.0), max_top)
        canvas.yview_moveto(new_top / content_height)

    @property
    def current_state(self) -> ImageRois | None:
        if 0 <= self.current_index < len(self.states):
            return self.states[self.current_index]
        return None

    @property
    def default_output_dir(self) -> Path:
        state = self.current_state
        if state is not None:
            return state.path.parent
        text = self.folder_var.get().strip()
        return Path(text).expanduser() if text else _default_folder()

    def browse_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(_default_folder()))
        if folder:
            self.folder_var.set(folder)
            self.load_images([folder])

    def load_images(self, paths: Sequence[str | Path]) -> None:
        image_paths = sorted(find_tiff_files(paths), key=natural_sort_key)
        if not image_paths:
            messagebox.showwarning("No Images", "No TIFF images were found.")
            return
        self.image_paths = image_paths
        self.states = [ImageRois(path=path) for path in image_paths]
        self.current_index = 0
        self.folder_var.set(str(image_paths[0].parent))
        self.image_combo.configure(values=[path.name for path in image_paths])
        self.image_combo.current(0)
        self.status_var.set(f"Loaded {len(image_paths)} TIFF image(s).")
        self._load_current_image(fit=True)

    def _load_current_image(self, fit: bool = False) -> None:
        state = self.current_state
        if state is None:
            return
        try:
            self.current_channels = load_tiff_channels(state.path)
        except Exception as exc:
            messagebox.showerror("Read Failed", f"Could not read TIFF:\n{state.path}\n\n{exc}")
            return
        channel_count, height, width = self.current_channels.shape
        values = ["Composite"] if channel_count >= 3 else []
        values.extend([f"Ch{i}" for i in range(1, channel_count + 1)])
        self.channel_combo.configure(values=values)
        if self.channel_var.get() not in values:
            self.channel_var.set(values[0])
        self._update_channel_color_buttons(channel_count)
        self.pending_points.clear()
        self.drawing = False
        self._delete_preview()
        self.refresh_display(redraw_only=False)
        if fit:
            self._set_fit_zoom()
        self._redraw_canvas()
        self._refresh_roi_list()
        self._update_readouts(width=width, height=height, channel_count=channel_count)

    def refresh_display(self, redraw_only: bool = False) -> None:
        if self.current_channels is None:
            return
        try:
            low = float(self.low_percent_var.get())
            high = float(self.high_percent_var.get())
        except ValueError:
            self.status_var.set("Percentile values must be numeric.")
            return
        low = max(0.0, min(100.0, low))
        high = max(0.0, min(100.0, high))
        if high <= low:
            self.status_var.set("High percentile must be greater than low percentile.")
            return
        if not redraw_only:
            self.base_display_image = display_image_for_channels(
                self.current_channels,
                self.channel_var.get(),
                low,
                high,
                self._channel_colors(),
                self._channel_enabled(),
            )
        self._apply_display_transform()
        self._redraw_canvas()

    def choose_channel_color(self, channel_index: int) -> None:
        if channel_index < 0 or channel_index >= len(self.channel_color_vars):
            return
        current = self.channel_color_vars[channel_index].get()
        _rgb, color = colorchooser.askcolor(color=current, title=f"Choose Ch{channel_index + 1} Color")
        if not color:
            return
        self.channel_color_vars[channel_index].set(str(color))
        self._update_channel_color_buttons()
        self.refresh_display()

    def _channel_colors(self) -> list[str]:
        return [var.get() for var in self.channel_color_vars]

    def _channel_enabled(self) -> list[bool]:
        return [bool(var.get()) for var in self.channel_enabled_vars]

    def _update_channel_color_buttons(self, channel_count: int | None = None) -> None:
        if channel_count is None and self.current_channels is not None:
            channel_count = int(self.current_channels.shape[0])
        if channel_count is None:
            channel_count = 3
        for index, button in enumerate(self.channel_color_buttons):
            color = self.channel_color_vars[index].get()
            active = index < channel_count
            button.configure(
                bg=color,
                activebackground=color,
                fg="#000000" if color.lower() in {"#ffffff", "#ffcc00", "#34c759"} else "#ffffff",
                state=tk.NORMAL if active else tk.DISABLED,
                text=f"Ch{index + 1}",
            )
            if index < len(self.channel_check_buttons):
                self.channel_check_buttons[index].configure(state=tk.NORMAL if active else tk.DISABLED)

    def _current_rotation_degrees(self) -> float:
        try:
            value = float(self.rotation_degrees_var.get())
        except ValueError:
            value = 0.0
        if not np.isfinite(value):
            value = 0.0
        return value % 360.0

    def _apply_display_transform(self) -> None:
        if self.base_display_image is None:
            self.display_image = None
            self.view_geometry = None
            return
        self.display_image, self.view_geometry = rotate_image_for_preview(
            self.base_display_image,
            self._current_rotation_degrees(),
        )

    def apply_rotation_input(self) -> None:
        self.rotation_degrees_var.set(_fmt_float(self._current_rotation_degrees(), digits=3) or "0")
        self.refresh_display(redraw_only=True)

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees_var.set(_fmt_float((self._current_rotation_degrees() + degrees) % 360.0, digits=3) or "0")
        self.refresh_display(redraw_only=True)

    def reset_rotation(self) -> None:
        self.rotation_degrees_var.set("0")
        self.refresh_display(redraw_only=True)

    def apply_zoom_input(self) -> None:
        try:
            percent = float(self.zoom_input_var.get())
        except ValueError:
            self.status_var.set("Zoom must be a numeric percentage.")
            return
        if not np.isfinite(percent) or percent <= 0:
            self.status_var.set("Zoom must be greater than 0.")
            return
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, percent / 100.0))
        self._redraw_canvas()

    def _on_image_selected(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        selected = self.image_combo.current()
        if selected >= 0 and selected != self.current_index:
            self.current_index = selected
            self._load_current_image(fit=True)

    def prev_image(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        if not self.image_paths:
            return
        self.current_index = (self.current_index - 1) % len(self.image_paths)
        self.image_combo.current(self.current_index)
        self._load_current_image(fit=True)

    def next_image(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        if not self.image_paths:
            return
        self.current_index = (self.current_index + 1) % len(self.image_paths)
        self.image_combo.current(self.current_index)
        self._load_current_image(fit=True)

    def start_roi(self) -> None:
        if self.current_state is None:
            return
        self.pending_points = []
        self.drawing = True
        self._delete_preview()
        self.status_var.set("Left-click polygon points; double-click, right-click, or press Enter to finish.")

    def finish_roi(self) -> None:
        if not self.drawing:
            return
        if len(self.pending_points) < 3:
            self.status_var.set("ROI needs at least 3 points.")
            return
        state = self.current_state
        if state is None:
            return
        label = self.roi_label_var.get().strip() or f"ROI {len(state.rois) + 1}"
        kind = self.roi_kind_var.get().strip() or "signal"
        state.rois.append(RoiPolygon(label=label, kind=kind, points=list(self.pending_points)))
        self.pending_points = []
        self.drawing = False
        self._delete_preview()
        self._redraw_canvas()
        self._refresh_roi_list()
        self.roi_label_var.set(f"ROI {len(state.rois) + 1}")
        self.status_var.set(f"Added {label} ({kind}).")

    def cancel_roi(self) -> None:
        self.pending_points = []
        self.drawing = False
        self._delete_preview()
        self._redraw_canvas()
        self.status_var.set("Canceled current ROI.")

    def undo_roi(self) -> None:
        state = self.current_state
        if state is None or not state.rois:
            return
        removed = state.rois.pop()
        self._redraw_canvas()
        self._refresh_roi_list()
        self.status_var.set(f"Undid {removed.label}.")

    def clear_current_rois(self) -> None:
        state = self.current_state
        if state is None or not state.rois:
            return
        if not messagebox.askyesno("Clear ROIs", "Clear all ROIs on the current image?"):
            return
        state.rois.clear()
        self._redraw_canvas()
        self._refresh_roi_list()
        self.status_var.set("Cleared ROIs on the current image.")

    def copy_current_rois_to_all(self) -> None:
        state = self.current_state
        if state is None or not state.rois:
            self.status_var.set("The current image has no ROIs to copy.")
            return
        rois = [RoiPolygon.from_dict(roi.to_dict()) for roi in state.rois]
        for other_state in self.states:
            other_state.rois = [RoiPolygon.from_dict(roi.to_dict()) for roi in rois]
        self._refresh_roi_list()
        self._redraw_canvas()
        self.status_var.set(f"Copied {len(rois)} ROI(s) to all {len(self.states)} image(s).")

    def _on_canvas_left_click(self, event: tk.Event[tk.Widget]) -> None:
        if not self.drawing:
            return
        point = self._event_to_image_xy(event)
        if point is None:
            return
        self.pending_points.append(point)
        self._redraw_canvas()

    def _on_canvas_motion(self, event: tk.Event[tk.Widget]) -> None:
        if not self.drawing or not self.pending_points:
            return
        point = self._event_to_image_xy(event)
        self._draw_preview(point)

    def _event_to_image_xy(self, event: tk.Event[tk.Widget]) -> tuple[float, float] | None:
        if self.display_image is None or self.base_display_image is None or self.view_geometry is None:
            return None
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        view_x = canvas_x / self.zoom
        view_y = canvas_y / self.zoom
        if view_x < 0 or view_y < 0 or view_x > self.display_image.width or view_y > self.display_image.height:
            return None
        x, y = rotated_view_to_image_point((view_x, view_y), self.view_geometry)
        width, height = self.base_display_image.size
        if x < 0 or y < 0 or x > width or y > height:
            return None
        return x, y

    def save_rois_json(self) -> None:
        output_path = self.default_output_dir / "fluorescence_manual_rois.json"
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "display": {
                "channel": self.channel_var.get(),
                "low_percent": self.low_percent_var.get(),
                "high_percent": self.high_percent_var.get(),
                "channel_colors": self._channel_colors(),
                "channel_enabled": self._channel_enabled(),
                "rotation_degrees": self.rotation_degrees_var.get(),
            },
            "images": [
                {
                    "image_name": state.path.name,
                    "image_path": str(state.path),
                    "rois": [roi.to_dict() for roi in state.rois],
                }
                for state in self.states
            ],
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved ROI JSON: {output_path}")

    def load_rois_json(self) -> None:
        filename = filedialog.askopenfilename(
            initialdir=str(self.default_output_dir),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        display_settings = payload.get("display", {})
        if isinstance(display_settings, dict):
            colors = display_settings.get("channel_colors")
            if isinstance(colors, list):
                for index, color in enumerate(colors[: len(self.channel_color_vars)]):
                    self.channel_color_vars[index].set(str(color))
            enabled = display_settings.get("channel_enabled")
            if isinstance(enabled, list):
                for index, value in enumerate(enabled[: len(self.channel_enabled_vars)]):
                    self.channel_enabled_vars[index].set(bool(value))
            if display_settings.get("channel"):
                self.channel_var.set(str(display_settings["channel"]))
            if display_settings.get("low_percent") is not None:
                self.low_percent_var.set(str(display_settings["low_percent"]))
            if display_settings.get("high_percent") is not None:
                self.high_percent_var.set(str(display_settings["high_percent"]))
            if display_settings.get("rotation_degrees") is not None:
                self.rotation_degrees_var.set(str(display_settings["rotation_degrees"]))
        rois_by_name = {
            item.get("image_name"): [RoiPolygon.from_dict(roi) for roi in item.get("rois", [])]
            for item in payload.get("images", [])
        }
        count = 0
        for state in self.states:
            if state.path.name in rois_by_name:
                state.rois = rois_by_name[state.path.name]
                count += len(state.rois)
        self._update_channel_color_buttons()
        if self.current_channels is not None:
            self.refresh_display()
        self._refresh_roi_list()
        self._redraw_canvas()
        self.status_var.set(f"Loaded {count} ROI(s).")

    def export_current_roi_overlay(self) -> None:
        state = self.current_state
        if state is None:
            return
        if not state.rois:
            self.status_var.set("The current image has no ROIs to export.")
            return
        try:
            overlay = self._roi_overlay_image(state)
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not generate ROI preview:\n{exc}")
            return
        output_path = state.path.with_name(f"{state.path.stem}_roi_overlay.png")
        overlay.save(output_path)
        self.status_var.set(f"Exported current ROI PNG: {output_path}")

    def _roi_overlay_image(self, state: ImageRois) -> Image.Image:
        image = self._base_preview_image_for_state(state)
        self._draw_rois_on_base_image(image, state)
        rotated, _geometry = rotate_image_for_preview(image, self._current_rotation_degrees())
        return rotated

    def _base_preview_image_for_state(self, state: ImageRois) -> Image.Image:
        if state is self.current_state and self.base_display_image is not None:
            return self.base_display_image.copy()
        channels = load_tiff_channels(state.path)
        try:
            low = float(self.low_percent_var.get())
            high = float(self.high_percent_var.get())
        except ValueError:
            low, high = 1.0, 99.8
        return display_image_for_channels(
            channels,
            self.channel_var.get(),
            low,
            high,
            self._channel_colors(),
            self._channel_enabled(),
        )

    def _draw_rois_on_base_image(self, image: Image.Image, state: ImageRois) -> None:
        draw = ImageDraw.Draw(image)
        line_width = max(2, int(round(max(image.size) / 700)))
        for index, roi in enumerate(state.rois, start=1):
            if len(roi.points) < 2:
                continue
            color = ROI_COLORS.get(roi.kind, "#22d3ee")
            points = [(float(x), float(y)) for x, y in roi.points]
            draw.line(points + [points[0]], fill=color, width=line_width)
            x, y = points[0]
            draw.text((x + 6, y + 6), f"{index}:{roi.label}", fill=color)

    def _preview_image_for_state(self, state: ImageRois, include_rois: bool) -> Image.Image:
        image = self._base_preview_image_for_state(state)
        if include_rois:
            self._draw_rois_on_base_image(image, state)
        rotated, _geometry = rotate_image_for_preview(image, self._current_rotation_degrees())
        return self._resize_preview_image(rotated)

    def _resize_preview_image(self, image: Image.Image) -> Image.Image:
        width_text = self.preview_width_var.get().strip()
        height_text = self.preview_height_var.get().strip()
        scale_text = self.preview_scale_var.get().strip()
        width = self._positive_int_or_none(width_text)
        height = self._positive_int_or_none(height_text)
        try:
            scale = float(scale_text) if scale_text else 1.0
        except ValueError:
            scale = 1.0
        if not np.isfinite(scale) or scale <= 0:
            scale = 1.0

        source_width, source_height = image.size
        if width is not None and height is not None:
            target_width, target_height = width, height
        elif width is not None:
            target_width = width
            target_height = max(1, int(round(width * source_height / source_width)))
        elif height is not None:
            target_height = height
            target_width = max(1, int(round(height * source_width / source_height)))
        else:
            target_width = max(1, int(round(source_width * scale)))
            target_height = max(1, int(round(source_height * scale)))
        if (target_width, target_height) == image.size:
            return image
        return image.resize((target_width, target_height), Image.Resampling.LANCZOS)

    @staticmethod
    def _positive_int_or_none(text: str) -> int | None:
        if not text:
            return None
        try:
            value = int(float(text))
        except ValueError:
            return None
        return value if value > 0 else None

    def export_current_preview(self) -> None:
        state = self.current_state
        if state is None:
            return
        output_dir = state.path.parent / "preview_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            image = self._preview_image_for_state(state, bool(self.preview_include_rois_var.get()))
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not generate current preview:\n{exc}")
            return
        output_path = output_dir / f"{state.path.stem}_preview.png"
        image.save(output_path)
        self.status_var.set(f"Exported current preview: {output_path}")

    def export_all_previews(self) -> None:
        if not self.states:
            return
        output_dir = self.default_output_dir / "preview_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        include_rois = bool(self.preview_include_rois_var.get())
        saved = 0
        failed: list[str] = []
        for index, state in enumerate(self.states, start=1):
            self.status_var.set(f"Exporting preview {index}/{len(self.states)}: {state.path.name}")
            self.root.update_idletasks()
            try:
                image = self._preview_image_for_state(state, include_rois)
                image.save(output_dir / f"{state.path.stem}_preview.png")
                saved += 1
            except Exception as exc:
                failed.append(f"{state.path.name}: {exc}")
        if failed:
            messagebox.showwarning("Some Exports Failed", "\n".join(failed[:12]))
        self.status_var.set(f"Exported {saved} preview image(s) to {output_dir}")

    def analyze_current(self) -> None:
        state = self.current_state
        if state is None:
            return
        if not state.rois:
            self.status_var.set("The current image has no ROIs.")
            return
        rows = analyze_image(state.path, state.rois)
        output_path = state.path.with_name(f"{state.path.stem}_fluorescence_roi_measurements.csv")
        write_measurements_csv(rows, output_path)
        self.status_var.set(f"Exported {len(rows)} row(s) for current image: {output_path}")

    def analyze_all(self) -> None:
        if not self.states:
            return
        states = [state for state in self.states if state.rois]
        if not states:
            self.status_var.set("There are no ROIs yet.")
            return
        rows: list[dict[str, str]] = []
        for index, state in enumerate(states, start=1):
            self.status_var.set(f"Analyzing {index}/{len(states)}: {state.path.name}")
            self.root.update_idletasks()
            rows.extend(analyze_image(state.path, state.rois))
        output_dir = self.default_output_dir
        measurements_path = output_dir / "fluorescence_manual_roi_measurements.csv"
        summary_path = output_dir / "fluorescence_manual_roi_mouse_summary.csv"
        write_measurements_csv(rows, measurements_path)
        write_summary_csv(summarize_measurements(rows), summary_path)
        self.save_rois_json()
        self.status_var.set(
            f"Exported {len(rows)} row(s): {measurements_path}; mouse summary: {summary_path}"
        )

    def fit_to_window(self) -> None:
        self._set_fit_zoom()
        self._redraw_canvas()

    def actual_size(self) -> None:
        self.zoom = 1.0
        self._redraw_canvas()

    def zoom_by(self, factor: float, event: tk.Event[tk.Widget] | None = None) -> str | None:
        if self.display_image is None:
            return None
        old_zoom = self.zoom
        if event is not None:
            anchor_x = self.canvas.canvasx(event.x) / old_zoom
            anchor_y = self.canvas.canvasy(event.y) / old_zoom
        else:
            anchor_x = self.display_image.width / 2
            anchor_y = self.display_image.height / 2

        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
        self._redraw_canvas()
        if event is not None:
            width = max(1, int(self.display_image.width * self.zoom))
            height = max(1, int(self.display_image.height * self.zoom))
            left = max(0, anchor_x * self.zoom - event.x)
            top = max(0, anchor_y * self.zoom - event.y)
            self.canvas.xview_moveto(min(1.0, left / width))
            self.canvas.yview_moveto(min(1.0, top / height))
            return "break"
        return None

    def _on_mousewheel(self, event: tk.Event[tk.Widget]) -> str:
        if event.delta == 0:
            return "break"
        if self._wheel_should_zoom(event):
            self.zoom_by(max(0.2, min(5.0, 1.0015**event.delta)), event)
            return "break"
        pixels = self._scroll_pixels_from_wheel_delta(event.delta)
        if self._wheel_should_scroll_horizontally(event):
            self._pan_by(pixels, 0)
        else:
            self._pan_by(0, pixels)
        return "break"

    def _on_button_wheel(self, event: tk.Event[tk.Widget], direction: int) -> str:
        if self._wheel_should_zoom(event):
            factor = 1.1 if direction > 0 else 1 / 1.1
            self.zoom_by(factor, event)
            return "break"
        pixels = -direction * SCROLL_PIXELS_PER_NOTCH
        if self._wheel_should_scroll_horizontally(event):
            self._pan_by(pixels, 0)
        else:
            self._pan_by(0, pixels)
        return "break"

    def _wheel_should_zoom(self, event: tk.Event[tk.Widget]) -> bool:
        state = int(getattr(event, "state", 0) or 0)
        return bool(state & ~(SHIFT_MASK | LOCK_MASK))

    def _wheel_should_scroll_horizontally(self, event: tk.Event[tk.Widget]) -> bool:
        state = int(getattr(event, "state", 0) or 0)
        return bool(state & SHIFT_MASK)

    def _scroll_pixels_from_wheel_delta(self, delta: int) -> float:
        if abs(delta) >= 120:
            return -(delta / 120.0) * SCROLL_PIXELS_PER_NOTCH
        return -delta * TRACKPAD_SCROLL_MULTIPLIER

    def _pan_by(self, dx_pixels: float, dy_pixels: float) -> None:
        if dx_pixels:
            self._move_canvas_view("x", dx_pixels)
        if dy_pixels:
            self._move_canvas_view("y", dy_pixels)

    def _move_canvas_view(self, axis: str, delta_pixels: float) -> None:
        if self.display_image is None:
            return
        self.root.update_idletasks()
        if axis == "x":
            total = max(1.0, self.display_image.width * self.zoom)
            visible = max(1.0, float(self.canvas.winfo_width()))
            first, _last = self.canvas.xview()
            max_left = max(0.0, total - visible)
            new_left = min(max(first * total + delta_pixels, 0.0), max_left)
            self.canvas.xview_moveto(new_left / total)
        else:
            total = max(1.0, self.display_image.height * self.zoom)
            visible = max(1.0, float(self.canvas.winfo_height()))
            first, _last = self.canvas.yview()
            max_top = max(0.0, total - visible)
            new_top = min(max(first * total + delta_pixels, 0.0), max_top)
            self.canvas.yview_moveto(new_top / total)

    def _set_fit_zoom(self) -> None:
        if self.display_image is None:
            return
        self.root.update_idletasks()
        canvas_width = max(200, self.canvas.winfo_width() - 24)
        canvas_height = max(200, self.canvas.winfo_height() - 24)
        image_width, image_height = self.display_image.size
        self.zoom = min(canvas_width / image_width, canvas_height / image_height, 1.0)
        self.zoom = max(ZOOM_MIN, self.zoom)

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        self.preview_item = None
        if self.display_image is None:
            self.photo = None
            return
        display_width = max(1, int(round(self.display_image.width * self.zoom)))
        display_height = max(1, int(round(self.display_image.height * self.zoom)))
        resized = self.display_image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, display_width, display_height))

        state = self.current_state
        if state is not None:
            for index, roi in enumerate(state.rois, start=1):
                self._draw_roi_polygon(roi, index)
        if self.pending_points:
            self._draw_pending_points()
        self.zoom_var.set(f"Zoom: {round(self.zoom * 100)}%")
        self.zoom_input_var.set(str(round(self.zoom * 100)))

    def _image_point_to_canvas_xy(self, point: tuple[float, float]) -> tuple[float, float]:
        if self.view_geometry is None:
            view_x, view_y = point
        else:
            view_x, view_y = image_point_to_rotated_view(point, self.view_geometry)
        return view_x * self.zoom, view_y * self.zoom

    def _draw_roi_polygon(self, roi: RoiPolygon, index: int) -> None:
        if len(roi.points) < 2:
            return
        color = ROI_COLORS.get(roi.kind, "#22d3ee")
        coords = [coord for point in roi.points for coord in self._image_point_to_canvas_xy(point)]
        self.canvas.create_polygon(coords, outline=color, fill="", width=2)
        x, y = self._image_point_to_canvas_xy(roi.points[0])
        self.canvas.create_text(
            x + 6,
            y - 6,
            text=f"{index}:{roi.label}",
            fill=color,
            anchor="w",
            font=("TkDefaultFont", 10, "bold"),
        )

    def _draw_pending_points(self) -> None:
        color = ROI_COLORS.get(self.roi_kind_var.get(), "#22d3ee")
        scaled = [self._image_point_to_canvas_xy((x, y)) for x, y in self.pending_points]
        for x, y in scaled:
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="#111111")
        if len(scaled) >= 2:
            coords = [coord for point in scaled for coord in point]
            self.canvas.create_line(*coords, fill=color, width=2)

    def _draw_preview(self, point: tuple[float, float] | None) -> None:
        self._delete_preview()
        if point is None or not self.pending_points:
            return
        start = self.pending_points[-1]
        color = ROI_COLORS.get(self.roi_kind_var.get(), "#22d3ee")
        start_x, start_y = self._image_point_to_canvas_xy(start)
        end_x, end_y = self._image_point_to_canvas_xy(point)
        self.preview_item = self.canvas.create_line(
            start_x,
            start_y,
            end_x,
            end_y,
            fill=color,
            width=2,
            dash=(5, 3),
        )

    def _delete_preview(self) -> None:
        if self.preview_item is not None:
            self.canvas.delete(self.preview_item)
        self.preview_item = None

    def _refresh_roi_list(self) -> None:
        self.roi_list.delete(0, tk.END)
        state = self.current_state
        if state is None:
            return
        for index, roi in enumerate(state.rois, start=1):
            self.roi_list.insert(tk.END, f"{index}. {roi.label} [{roi.kind}] {len(roi.points)} pts")

    def _update_readouts(self, width: int, height: int, channel_count: int) -> None:
        state = self.current_state
        if state is None:
            self.image_info_var.set("No image loaded")
            return
        metadata = parse_fluorescence_name(state.path)
        self.image_info_var.set(
            f"{self.current_index + 1}/{len(self.states)}: {state.path.name}\n"
            f"{width}x{height}px, {channel_count} channels, {metadata['mouse_id']} {metadata['group']}"
        )


def run_smoke_test(target: str | Path) -> int:
    paths = sorted(find_tiff_files([target]), key=natural_sort_key)
    print(f"Found {len(paths)} TIFF file(s)")
    if not paths:
        return 1
    for path in paths[:8]:
        channels = load_tiff_channels(path)
        print(f"{path.name}: channels={channels.shape[0]} size={channels.shape[2]}x{channels.shape[1]} dtype={channels.dtype}")
    path = paths[0]
    channels = load_tiff_channels(path)
    _preview = display_image_for_channels(channels, "Composite", 1.0, 99.8)
    roi = RoiPolygon(label="test", kind="signal", points=[(10, 10), (110, 10), (110, 110), (10, 110)])
    bg = RoiPolygon(label="bg", kind="background", points=[(120, 120), (180, 120), (180, 180), (120, 180)])
    rows = analyze_image(path, [roi, bg])
    expected = channels.shape[0] * 2
    if len(rows) != expected:
        print(f"Expected {expected} rows, got {len(rows)}")
        return 1
    print("Smoke test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manual polygon ROI fluorescence analysis GUI.")
    parser.add_argument("paths", nargs="*", help="Optional folder or TIFF path(s) loaded on startup.")
    parser.add_argument("--smoke-test", metavar="PATH", help="Validate TIFF loading and ROI metrics without GUI.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.smoke_test:
        return run_smoke_test(args.smoke_test)
    root = tk.Tk()
    FluorescenceManualRoiApp(root, start_paths=args.paths)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
