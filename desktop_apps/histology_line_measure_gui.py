from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk
from services.histology_line_measure import (
    Calibration,
    ImageMeasurements,
    MeasurementLine,
    default_export_path,
    distance_px,
    find_image_files,
    fmt_float as _fmt_float,
    measurement_rows,
    write_measurements_csv,
)


ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
SHIFT_MASK = 0x0001
LOCK_MASK = 0x0002
SCROLL_PIXELS_PER_NOTCH = 80.0
TRACKPAD_SCROLL_MULTIPLIER = 3.0


class HistologyLineMeasureApp:
    def __init__(self, root: tk.Tk, start_paths: Sequence[str] | None = None) -> None:
        self.root = root
        self.root.title("Histology Line Measurement")
        self.root.geometry("1280x850")
        self.root.minsize(980, 650)

        self.image_paths: list[Path] = []
        self.states: list[ImageMeasurements] = []
        self.current_index = -1
        self.original_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.zoom = 1.0

        self.drag_start: tuple[float, float] | None = None
        self.preview_item: int | None = None
        self.pending_scale_line: tuple[float, float, float, float] | None = None

        self.mode_var = tk.StringVar(value="measure")
        self.known_length_var = tk.StringVar(value="100")
        self.unit_var = tk.StringVar(value="um")
        self.apply_all_var = tk.BooleanVar(value=True)
        self.show_line_numbers_var = tk.BooleanVar(value=False)
        self.image_info_var = tk.StringVar(value="No image loaded")
        self.scale_info_var = tk.StringVar(value="Scale: not calibrated")
        self.line_info_var = tk.StringVar(value="Lines: 0")
        self.status_var = tk.StringVar(value="Load a folder or select images to begin.")
        self.zoom_var = tk.StringVar(value="100%")

        self._build_ui()
        self._bind_events()

        if start_paths:
            self.root.after(100, lambda: self.load_images(start_paths))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, width=290)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.grid_propagate(False)

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_image_controls(left)
        self._build_scale_controls(left)
        self._build_line_controls(left)
        self._build_view_controls(left)
        self._build_canvas(right)

        status = ttk.Label(outer, textvariable=self.status_var, anchor="w")
        status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_image_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Images", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        ttk.Button(frame, text="Load Folder", command=self.browse_folder).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(frame, text="Select Images", command=self.browse_images).grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )

        self.image_combo = ttk.Combobox(frame, state="readonly", width=28)
        self.image_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.image_combo.bind("<<ComboboxSelected>>", self._on_image_selected)

        nav = ttk.Frame(frame)
        nav.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Previous", command=self.prev_image).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(nav, text="Next", command=self.next_image).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )

        ttk.Label(frame, textvariable=self.image_info_var, wraplength=260).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_scale_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Scale bar", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            frame, text="Calibrate", value="calibrate", variable=self.mode_var, command=self._mode_changed
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            frame, text="Measure", value="measure", variable=self.mode_var, command=self._mode_changed
        ).grid(row=0, column=1, sticky="w")

        ttk.Label(frame, text="Known length").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.known_length_var, width=10).grid(
            row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0)
        )
        ttk.Entry(frame, textvariable=self.unit_var, width=8).grid(
            row=1, column=2, sticky="ew", padx=(6, 0), pady=(8, 0)
        )

        ttk.Checkbutton(frame, text="Apply to all images", variable=self.apply_all_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        ttk.Button(frame, text="Set Scale", command=self.apply_calibration).grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        ttk.Label(frame, textvariable=self.scale_info_var, wraplength=260).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

    def _build_line_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Lines", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, textvariable=self.line_info_var).grid(row=0, column=0, sticky="w")
        ttk.Button(frame, text="Undo Last Line", command=self.undo_current_line).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(frame, text="Clear Current Image", command=self.clear_current_lines).grid(
            row=2, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(frame, text="Clear All", command=self.clear_all_lines).grid(
            row=3, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Checkbutton(
            frame,
            text="Show line numbers",
            variable=self.show_line_numbers_var,
            command=self._redraw_canvas,
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frame, text="Export Current CSV", command=self.export_csv).grid(
            row=5, column=0, sticky="ew", pady=(10, 0)
        )

    def _build_view_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="View", padding=8)
        frame.pack(fill=tk.X)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Button(frame, text="Fit Window", command=self.fit_to_window).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(frame, text="100%", command=self.actual_size).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(frame, text="Zoom In", command=lambda: self.zoom_by(1.2)).grid(
            row=1, column=0, sticky="ew", padx=(0, 3), pady=(6, 0)
        )
        ttk.Button(frame, text="Zoom Out", command=lambda: self.zoom_by(1 / 1.2)).grid(
            row=1, column=1, sticky="ew", padx=(3, 0), pady=(6, 0)
        )
        ttk.Label(frame, textvariable=self.zoom_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def _build_canvas(self, parent: ttk.Frame) -> None:
        self.canvas = tk.Canvas(parent, bg="#202020", highlightthickness=0, cursor="crosshair")
        x_scroll = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self._on_button_wheel(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self._on_button_wheel(event, -1))
        self.root.bind("<Left>", self.prev_image)
        self.root.bind("<Right>", self.next_image)

    @property
    def current_state(self) -> ImageMeasurements | None:
        if 0 <= self.current_index < len(self.states):
            return self.states[self.current_index]
        return None

    def browse_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=str(Path.cwd()))
        if folder:
            self.load_images([folder])

    def browse_images(self) -> None:
        filenames = filedialog.askopenfilenames(
            initialdir=str(Path.cwd()),
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if filenames:
            self.load_images(filenames)

    def load_images(self, paths: Sequence[str | Path]) -> None:
        image_paths = find_image_files(paths)
        if not image_paths:
            messagebox.showwarning("No Images", "No jpg/png/tif/bmp images were found.")
            return

        self.image_paths = image_paths
        self.states = [ImageMeasurements(path=path) for path in image_paths]
        self.current_index = 0
        self.image_combo.configure(values=[path.name for path in image_paths])
        self.image_combo.current(0)
        self.status_var.set(f"Loaded {len(image_paths)} image(s).")
        self._load_current_image(fit=True)

    def _load_current_image(self, fit: bool = False) -> None:
        state = self.current_state
        if state is None:
            self.original_image = None
            self.photo = None
            self.canvas.delete("all")
            self._update_readouts()
            return

        try:
            with Image.open(state.path) as image:
                self.original_image = ImageOps.exif_transpose(image).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Read Failed", f"Could not read image:\n{state.path}\n\n{exc}")
            return

        self.pending_scale_line = None
        self.drag_start = None
        self._delete_preview()
        if fit:
            self._set_fit_zoom()
        self._redraw_canvas()
        self._update_readouts()

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

    def _mode_changed(self) -> None:
        self._delete_preview()
        mode = "calibration" if self.mode_var.get() == "calibrate" else "measurement"
        self.status_var.set(f"Current mode: {mode}.")

    def _event_to_image_xy(self, event: tk.Event[tk.Widget]) -> tuple[float, float] | None:
        if self.original_image is None:
            return None
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        x = canvas_x / self.zoom
        y = canvas_y / self.zoom
        width, height = self.original_image.size
        if x < 0 or y < 0 or x > width or y > height:
            return None
        return x, y

    def _on_canvas_press(self, event: tk.Event[tk.Widget]) -> None:
        point = self._event_to_image_xy(event)
        if point is None:
            self.drag_start = None
            return
        self.drag_start = point
        self._delete_preview()

    def _on_canvas_drag(self, event: tk.Event[tk.Widget]) -> None:
        if self.drag_start is None:
            return
        point = self._event_to_image_xy(event)
        if point is None:
            return
        self._draw_preview(self.drag_start, point)

    def _on_canvas_release(self, event: tk.Event[tk.Widget]) -> None:
        if self.drag_start is None:
            return
        end = self._event_to_image_xy(event)
        start = self.drag_start
        self.drag_start = None
        self._delete_preview()
        if end is None:
            return

        px_length = distance_px(start[0], start[1], end[0], end[1])
        if px_length < 2:
            return

        state = self.current_state
        if state is None:
            return

        if self.mode_var.get() == "calibrate":
            self.pending_scale_line = (start[0], start[1], end[0], end[1])
            self.apply_calibration(silent=True)
            self._redraw_canvas()
            self.status_var.set(
                f"Scale bar line: {_fmt_float(px_length, 2)} px. Edit the known length and set scale again if needed."
            )
            return

        label = f"L{len(state.lines) + 1}"
        state.lines.append(MeasurementLine.from_points(start[0], start[1], end[0], end[1], label))
        self._redraw_canvas()
        self._update_readouts()
        if state.calibration is None:
            self.status_var.set(f"Added {label}: {_fmt_float(px_length, 2)} px (not calibrated).")
        else:
            length = px_length * state.calibration.real_per_pixel
            self.status_var.set(
                f"Added {label}: {_fmt_float(length, 3)} {state.calibration.unit}."
            )

    def _draw_preview(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        self._delete_preview()
        color = "#f59e0b" if self.mode_var.get() == "calibrate" else "#22d3ee"
        self.preview_item = self.canvas.create_line(
            start[0] * self.zoom,
            start[1] * self.zoom,
            end[0] * self.zoom,
            end[1] * self.zoom,
            fill=color,
            width=3,
            dash=(6, 3),
        )

    def _delete_preview(self) -> None:
        if self.preview_item is not None:
            self.canvas.delete(self.preview_item)
            self.preview_item = None

    def apply_calibration(self, silent: bool = False) -> None:
        state = self.current_state
        if state is None:
            return

        line = self.pending_scale_line
        if line is None and state.calibration is not None and state.calibration.has_drawable_line:
            line = (
                state.calibration.x1_px or 0.0,
                state.calibration.y1_px or 0.0,
                state.calibration.x2_px or 0.0,
                state.calibration.y2_px or 0.0,
            )
        if line is None:
            if not silent:
                messagebox.showwarning("Scale Bar Required", "Switch to Calibrate mode and drag a scale bar line first.")
            return

        try:
            real_length = float(self.known_length_var.get())
        except ValueError:
            if not silent:
                messagebox.showwarning("Invalid Length", "Enter the real length as a number.")
            return
        if real_length <= 0:
            if not silent:
                messagebox.showwarning("Invalid Length", "The real length must be greater than 0.")
            return

        pixel_length = distance_px(*line)
        if pixel_length <= 0:
            return
        unit = self.unit_var.get().strip() or "um"
        calibration = Calibration(
            pixel_length=pixel_length,
            real_length=real_length,
            unit=unit,
            x1_px=line[0],
            y1_px=line[1],
            x2_px=line[2],
            y2_px=line[3],
            source_image=state.path.name,
        )

        if self.apply_all_var.get():
            for other_state in self.states:
                if other_state is state:
                    other_state.calibration = calibration
                else:
                    other_state.calibration = replace(
                        calibration, x1_px=None, y1_px=None, x2_px=None, y2_px=None
                    )
            scope = "all images"
        else:
            state.calibration = calibration
            scope = "current image"

        self._redraw_canvas()
        self._update_readouts()
        self.status_var.set(
            f"Set scale for {scope}: {_fmt_float(pixel_length, 2)} px = "
            f"{_fmt_float(real_length, 4)} {unit}."
        )

    def undo_current_line(self) -> None:
        state = self.current_state
        if state is None or not state.lines:
            return
        removed = state.lines.pop()
        self._redraw_canvas()
        self._update_readouts()
        self.status_var.set(f"Undid {removed.label or 'last line'}.")

    def clear_current_lines(self) -> None:
        state = self.current_state
        if state is None or not state.lines:
            return
        if not messagebox.askyesno("Clear Current Image", "Clear all measurement lines on the current image?"):
            return
        state.lines.clear()
        self._redraw_canvas()
        self._update_readouts()
        self.status_var.set("Cleared lines on the current image.")

    def clear_all_lines(self) -> None:
        total = sum(len(state.lines) for state in self.states)
        if total == 0:
            return
        if not messagebox.askyesno("Clear All", "Clear measurement lines on all images?"):
            return
        for state in self.states:
            state.lines.clear()
        self._redraw_canvas()
        self._update_readouts()
        self.status_var.set("Cleared all lines.")

    def export_csv(self) -> None:
        state = self.current_state
        if state is None:
            self.status_var.set("Load images first.")
            return
        if not state.lines:
            self.status_var.set("The current image has no measurement lines to export.")
            return

        output_path = default_export_path(state)
        try:
            row_count = write_measurements_csv([state], output_path)
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not write CSV:\n{output_path}\n\n{exc}")
            return
        self.status_var.set(f"Exported {row_count} line(s) for the current image to {output_path}")

    def fit_to_window(self) -> None:
        if self.original_image is None:
            return
        self._set_fit_zoom()
        self._redraw_canvas()
        self._update_readouts()

    def actual_size(self) -> None:
        if self.original_image is None:
            return
        self.zoom = 1.0
        self._redraw_canvas()
        self._update_readouts()

    def zoom_by(self, factor: float, event: tk.Event[tk.Widget] | None = None) -> str | None:
        if self.original_image is None:
            return None
        old_zoom = self.zoom
        if event is not None:
            anchor_x = self.canvas.canvasx(event.x) / old_zoom
            anchor_y = self.canvas.canvasy(event.y) / old_zoom
        else:
            anchor_x = self.original_image.width / 2
            anchor_y = self.original_image.height / 2

        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
        self._redraw_canvas()
        self._update_readouts()

        if event is not None:
            width = max(1, int(self.original_image.width * self.zoom))
            height = max(1, int(self.original_image.height * self.zoom))
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
            self.zoom_by(self._zoom_factor_from_wheel_delta(event.delta), event)
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

    def _zoom_factor_from_wheel_delta(self, delta: int) -> float:
        return max(0.2, min(5.0, 1.0015**delta))

    def _scroll_pixels_from_wheel_delta(self, delta: int) -> float:
        if abs(delta) >= 120:
            return -(delta / 120.0) * SCROLL_PIXELS_PER_NOTCH
        return -delta * TRACKPAD_SCROLL_MULTIPLIER

    def _pan_by(self, dx_pixels: float, dy_pixels: float) -> None:
        if self.original_image is None:
            return
        if dx_pixels:
            self._move_canvas_view("x", dx_pixels)
        if dy_pixels:
            self._move_canvas_view("y", dy_pixels)

    def _move_canvas_view(self, axis: str, delta_pixels: float) -> None:
        if self.original_image is None:
            return
        self.root.update_idletasks()
        if axis == "x":
            total = max(1.0, self.original_image.width * self.zoom)
            visible = max(1.0, float(self.canvas.winfo_width()))
            first, _last = self.canvas.xview()
            max_left = max(0.0, total - visible)
            new_left = min(max(first * total + delta_pixels, 0.0), max_left)
            self.canvas.xview_moveto(new_left / total)
        else:
            total = max(1.0, self.original_image.height * self.zoom)
            visible = max(1.0, float(self.canvas.winfo_height()))
            first, _last = self.canvas.yview()
            max_top = max(0.0, total - visible)
            new_top = min(max(first * total + delta_pixels, 0.0), max_top)
            self.canvas.yview_moveto(new_top / total)

    def _set_fit_zoom(self) -> None:
        if self.original_image is None:
            return
        self.root.update_idletasks()
        canvas_width = max(200, self.canvas.winfo_width() - 24)
        canvas_height = max(200, self.canvas.winfo_height() - 24)
        image_width, image_height = self.original_image.size
        self.zoom = min(canvas_width / image_width, canvas_height / image_height, 1.0)
        self.zoom = max(ZOOM_MIN, self.zoom)

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        if self.original_image is None:
            self.photo = None
            return

        display_width = max(1, int(round(self.original_image.width * self.zoom)))
        display_height = max(1, int(round(self.original_image.height * self.zoom)))
        display_image = self.original_image.resize(
            (display_width, display_height), Image.Resampling.LANCZOS
        )
        self.photo = ImageTk.PhotoImage(display_image)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, display_width, display_height))

        state = self.current_state
        if state is None:
            return
        if state.calibration is not None and state.calibration.has_drawable_line:
            self._draw_calibration(state.calibration)
        for index, line in enumerate(state.lines, start=1):
            self._draw_measurement_line(index, line, state.calibration)

    def _draw_calibration(self, calibration: Calibration) -> None:
        if not calibration.has_drawable_line:
            return
        x1 = (calibration.x1_px or 0.0) * self.zoom
        y1 = (calibration.y1_px or 0.0) * self.zoom
        x2 = (calibration.x2_px or 0.0) * self.zoom
        y2 = (calibration.y2_px or 0.0) * self.zoom
        self.canvas.create_line(x1, y1, x2, y2, fill="#f59e0b", width=4)
        self._draw_endpoint(x1, y1, "#f59e0b")
        self._draw_endpoint(x2, y2, "#f59e0b")
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        label = f"{_fmt_float(calibration.real_length, 4)} {calibration.unit}"
        self.canvas.create_text(
            mid_x + 8,
            mid_y - 8,
            text=label,
            fill="#ffffff",
            anchor="w",
            font=("TkDefaultFont", 11, "bold"),
        )

    def _draw_measurement_line(
        self, index: int, line: MeasurementLine, _calibration: Calibration | None
    ) -> None:
        x1 = line.x1_px * self.zoom
        y1 = line.y1_px * self.zoom
        x2 = line.x2_px * self.zoom
        y2 = line.y2_px * self.zoom
        self.canvas.create_line(x1, y1, x2, y2, fill="#22d3ee", width=3)
        self._draw_endpoint(x1, y1, "#22d3ee")
        self._draw_endpoint(x2, y2, "#22d3ee")

        if not self.show_line_numbers_var.get():
            return

        label = str(index)
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        self.canvas.create_text(
            mid_x + 8,
            mid_y - 8,
            text=label,
            fill="#ffffff",
            anchor="w",
            font=("TkDefaultFont", 9, "bold"),
        )

    def _draw_endpoint(self, x: float, y: float, color: str) -> None:
        radius = 4
        self.canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline="#111111",
            fill=color,
            width=1,
        )

    def _update_readouts(self) -> None:
        total_lines = sum(len(state.lines) for state in self.states)
        state = self.current_state
        if state is None:
            self.image_info_var.set("No image loaded")
            self.scale_info_var.set("Scale: not calibrated")
            self.line_info_var.set("Lines: 0")
            self.zoom_var.set("100%")
            return

        size_text = ""
        if self.original_image is not None:
            size_text = f"  {self.original_image.width}x{self.original_image.height}px"
        self.image_info_var.set(
            f"{self.current_index + 1}/{len(self.states)}: {state.path.name}{size_text}"
        )
        self.zoom_var.set(f"Zoom: {round(self.zoom * 100)}%")
        self.line_info_var.set(f"Current image: {len(state.lines)} / All: {total_lines}")

        calibration = state.calibration
        if calibration is None:
            self.scale_info_var.set("Scale: not calibrated")
        else:
            self.scale_info_var.set(
                "Scale: "
                f"{_fmt_float(calibration.pixel_length, 2)} px = "
                f"{_fmt_float(calibration.real_length, 4)} {calibration.unit}; "
                f"{_fmt_float(calibration.real_per_pixel, 6)} {calibration.unit}/px"
            )


def run_smoke_test(target: str | Path) -> int:
    image_paths = find_image_files([target])
    print(f"Found {len(image_paths)} image(s)")
    if not image_paths:
        return 1

    failures: list[str] = []
    for path in image_paths:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.load()
                print(f"{path.name}: {image.width}x{image.height} {image.mode}")
        except Exception as exc:
            failures.append(f"{path}: {exc}")

    if failures:
        print("Failed images:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    sample = ImageMeasurements(path=image_paths[0])
    sample.calibration = Calibration(pixel_length=100, real_length=200, unit="um")
    sample.lines.append(MeasurementLine.from_points(0, 0, 3, 4, "test"))
    rows = measurement_rows([sample])
    if rows[0]["pixel_length"] != "5" or rows[0]["calibrated_length"] != "10":
        print("Measurement export math failed")
        return 1
    if default_export_path(sample).name != f"{image_paths[0].stem}_line_measurements.csv":
        print("Default export path failed")
        return 1

    print("Smoke test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Temporary histology scale-bar line measurement GUI.")
    parser.add_argument("paths", nargs="*", help="Optional folder or image path(s) loaded on startup.")
    parser.add_argument(
        "--smoke-test",
        metavar="PATH",
        help="Validate image loading and CSV math without opening the Tk GUI.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.smoke_test:
        return run_smoke_test(args.smoke_test)

    root = tk.Tk()
    HistologyLineMeasureApp(root, start_paths=args.paths)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
