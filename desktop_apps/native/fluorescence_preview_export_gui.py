from __future__ import annotations

import argparse
import json
import math
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageTk

from services.fluorescence.preview_export import (
    DEFAULT_CHANNEL_COLORS,
    DEFAULT_FOLDER,
    LOCK_MASK,
    SCROLL_PIXELS_PER_NOTCH,
    SHIFT_MASK,
    TRACKPAD_SCROLL_MULTIPLIER,
    ZOOM_MAX,
    ZOOM_MIN,
    ChannelBC,
    composite_preview_image,
    find_tiff_files,
    load_tiff_channels,
    natural_sort_key,
    rotate_image_for_preview,
    single_channel_preview_image,
)
from services.fluorescence.preview_export import (
    fmt_float as _fmt_float,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "temp" / "fluorescence_preview_export_settings.json"


def _default_folder() -> Path:
    return DEFAULT_FOLDER if DEFAULT_FOLDER.exists() else PROJECT_ROOT


class BrightnessContrastWindow(tk.Toplevel):
    def __init__(self, app: "FluorescencePreviewExportApp") -> None:
        super().__init__(app.root)
        self.app = app
        self.title("Brightness / Contrast")
        self.geometry("520x420")
        self.transient(app.root)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        outer = ttk.Frame(self, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        self.channel_var = tk.IntVar(value=1)
        chooser = ttk.Frame(outer)
        chooser.grid(row=0, column=0, sticky="ew")
        ttk.Label(chooser, text="Channel").pack(side=tk.LEFT)
        self.channel_combo = ttk.Combobox(chooser, width=8, state="readonly")
        self.channel_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.channel_combo.bind("<<ComboboxSelected>>", self._on_channel_selected)

        self.rows: list[tuple[str, tk.DoubleVar, tk.Scale, ttk.Entry]] = []
        specs = [
            ("Black %", "black_percent", 0.0, 30.0, 0.1),
            ("White %", "white_percent", 70.0, 100.0, 0.1),
            ("Brightness", "brightness", -0.75, 0.75, 0.01),
            ("Contrast", "contrast", 0.1, 4.0, 0.01),
            ("Gamma", "gamma", 0.2, 4.0, 0.01),
        ]
        for row_index, (label, attr, low, high, resolution) in enumerate(specs, start=1):
            row = ttk.Frame(outer)
            row.grid(row=row_index, column=0, sticky="ew", pady=(10, 0))
            row.columnconfigure(1, weight=1)
            var = tk.DoubleVar()
            ttk.Label(row, text=label, width=12).grid(row=0, column=0, sticky="w")
            slider = tk.Scale(
                row,
                from_=low,
                to=high,
                resolution=resolution,
                orient=tk.HORIZONTAL,
                variable=var,
                showvalue=False,
                command=lambda _value, name=attr: self._on_slider_changed(name),
            )
            slider.grid(row=0, column=1, sticky="ew", padx=8)
            entry = ttk.Entry(row, width=8)
            entry.grid(row=0, column=2, sticky="e")
            entry.bind("<Return>", lambda _event, name=attr: self._on_entry_changed(name))
            entry.bind("<FocusOut>", lambda _event, name=attr: self._on_entry_changed(name))
            self.rows.append((attr, var, slider, entry))

        buttons = ttk.Frame(outer)
        buttons.grid(row=10, column=0, sticky="ew", pady=(16, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Auto 1/99.8", command=self.auto_percentiles).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Reset B&C", command=self.reset_channel).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.refresh_channels()

    def refresh_channels(self) -> None:
        count = self.app.channel_count
        values = [f"Ch{i}" for i in range(1, count + 1)]
        self.channel_combo.configure(values=values)
        if values:
            current = min(max(1, self.channel_var.get()), count)
            self.channel_var.set(current)
            self.channel_combo.set(f"Ch{current}")
        self._load_current_values()

    def _current_settings(self) -> ChannelBC:
        return self.app.channel_settings[self.channel_var.get() - 1]

    def _on_channel_selected(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        text = self.channel_combo.get().replace("Ch", "")
        try:
            value = int(text)
        except ValueError:
            value = 1
        self.channel_var.set(max(1, min(value, self.app.channel_count)))
        self._load_current_values()

    def _load_current_values(self) -> None:
        settings = self._current_settings()
        for attr, var, _slider, entry in self.rows:
            value = float(getattr(settings, attr))
            var.set(value)
            entry.delete(0, tk.END)
            entry.insert(0, _fmt_float(value))

    def _on_slider_changed(self, attr: str) -> None:
        settings = self._current_settings()
        for name, var, _slider, entry in self.rows:
            if name == attr:
                value = float(var.get())
                setattr(settings, attr, value)
                entry.delete(0, tk.END)
                entry.insert(0, _fmt_float(value))
                break
        self.app.schedule_render()

    def _on_entry_changed(self, attr: str) -> None:
        settings = self._current_settings()
        for name, var, slider, entry in self.rows:
            if name != attr:
                continue
            try:
                value = float(entry.get())
            except ValueError:
                value = float(getattr(settings, attr))
            low = float(slider.cget("from"))
            high = float(slider.cget("to"))
            value = min(max(value, low), high)
            setattr(settings, attr, value)
            var.set(value)
            entry.delete(0, tk.END)
            entry.insert(0, _fmt_float(value))
            break
        self.app.schedule_render()

    def auto_percentiles(self) -> None:
        settings = self._current_settings()
        settings.black_percent = 1.0
        settings.white_percent = 99.8
        self._load_current_values()
        self.app.schedule_render()

    def reset_channel(self) -> None:
        settings = self._current_settings()
        settings.black_percent = 1.0
        settings.white_percent = 99.8
        settings.brightness = 0.0
        settings.contrast = 1.0
        settings.gamma = 1.0
        self._load_current_values()
        self.app.schedule_render()


class FluorescencePreviewExportApp:
    def __init__(self, root: tk.Tk, start_paths: Sequence[str] | None = None) -> None:
        self.root = root
        self.root.title("Fluorescence Preview Export")
        self.root.geometry("1380x900")
        self.root.minsize(1040, 680)

        self.image_paths: list[Path] = []
        self.current_index = -1
        self.channels: np.ndarray | None = None
        self.channel_settings = [ChannelBC(color=color) for color in DEFAULT_CHANNEL_COLORS]
        self.channel_count = 0

        self.render_image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.canvas_zoom = 1.0
        self.render_after_id: str | None = None
        self.bc_window: BrightnessContrastWindow | None = None
        self.pan_start: tuple[int, int] | None = None
        self.settings_path = SETTINGS_PATH

        self.folder_var = tk.StringVar(value=str(_default_folder()))
        self.status_var = tk.StringVar(value="Load an FL folder to start adjusting previews.")
        self.info_var = tk.StringVar(value="No image loaded")
        self.rotation_var = tk.StringVar(value="0")
        self.output_width_var = tk.StringVar(value="")
        self.output_height_var = tk.StringVar(value="")
        self.output_scale_var = tk.StringVar(value="1")
        self.view_zoom_var = tk.StringVar(value="100")
        self.channel_enabled_vars = [tk.BooleanVar(value=True) for _ in DEFAULT_CHANNEL_COLORS]
        self.channel_color_vars = [tk.StringVar(value=color) for color in DEFAULT_CHANNEL_COLORS]
        self.channel_widgets: list[tuple[ttk.Checkbutton, tk.Button]] = []
        self._load_saved_settings()

        self._build_ui()
        self._bind_events()
        if start_paths:
            self.root.after(100, lambda: self.load_images(start_paths))
        else:
            initial_folder = Path(self.folder_var.get())
            if initial_folder.exists():
                self.root.after(100, lambda path=initial_folder: self.load_images([path]))

    def _load_saved_settings(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Could not load preview export settings: {exc}")
            return

        folder = payload.get("folder")
        if isinstance(folder, str) and Path(folder).exists():
            self.folder_var.set(folder)
        for key, var in (
            ("rotation", self.rotation_var),
            ("output_width", self.output_width_var),
            ("output_height", self.output_height_var),
            ("output_scale", self.output_scale_var),
            ("view_zoom", self.view_zoom_var),
        ):
            value = payload.get(key)
            if value is not None:
                var.set(str(value))

        channels = payload.get("channels", [])
        if not isinstance(channels, list):
            return
        for index, saved in enumerate(channels[: len(self.channel_settings)]):
            if not isinstance(saved, dict):
                continue
            settings = self.channel_settings[index]
            settings.enabled = bool(saved.get("enabled", settings.enabled))
            color = saved.get("color", settings.color)
            if isinstance(color, str) and color:
                settings.color = color
            for attr in ("black_percent", "white_percent", "brightness", "contrast", "gamma"):
                if attr not in saved:
                    continue
                try:
                    setattr(settings, attr, float(saved[attr]))
                except (TypeError, ValueError):
                    pass
            self.channel_enabled_vars[index].set(settings.enabled)
            self.channel_color_vars[index].set(settings.color)

    def save_settings(self) -> None:
        payload = {
            "version": 1,
            "folder": self.folder_var.get(),
            "rotation": self.rotation_var.get(),
            "output_width": self.output_width_var.get(),
            "output_height": self.output_height_var.get(),
            "output_scale": self.output_scale_var.get(),
            "view_zoom": self.view_zoom_var.get(),
            "channels": [
                {
                    "enabled": bool(settings.enabled),
                    "color": settings.color,
                    "black_percent": float(settings.black_percent),
                    "white_percent": float(settings.white_percent),
                    "brightness": float(settings.brightness),
                    "contrast": float(settings.contrast),
                    "gamma": float(settings.gamma),
                }
                for settings in self.channel_settings
            ],
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"Could not save preview export settings: {exc}")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer, width=320)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self._build_controls(left)
        self._build_canvas(right)
        ttk.Label(outer, textvariable=self.status_var, anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        quick = ttk.Frame(parent)
        quick.pack(fill=tk.X, pady=(0, 8))
        quick.columnconfigure(0, weight=1)
        quick.columnconfigure(1, weight=1)
        quick.columnconfigure(2, weight=1)
        ttk.Button(quick, text="B&C...", command=self.open_bc_window).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(quick, text="Export TIFF Set", command=self.export_viewport_tiff).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(quick, text="Fit Window", command=self.fit_to_window).grid(row=0, column=2, sticky="ew", padx=(3, 0))

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)
        file_frame = ttk.Frame(notebook, padding=8)
        transform = ttk.Frame(notebook, padding=8)
        channels = ttk.Frame(notebook, padding=8)
        export = ttk.Frame(notebook, padding=8)
        notebook.add(file_frame, text="Files")
        notebook.add(transform, text="Transform")
        notebook.add(channels, text="Channel")
        notebook.add(export, text="Export")

        file_frame.columnconfigure(0, weight=1)
        ttk.Entry(file_frame, textvariable=self.folder_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(file_frame, text="Browse", command=self.browse_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(file_frame, text="Load", command=lambda: self.load_images([self.folder_var.get()])).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.image_combo = ttk.Combobox(file_frame, state="readonly")
        self.image_combo.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.image_combo.bind("<<ComboboxSelected>>", self._on_image_selected)
        nav = ttk.Frame(file_frame)
        nav.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        nav.columnconfigure(0, weight=1)
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Previous", command=self.prev_image).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(nav, text="Next", command=self.next_image).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(file_frame, textvariable=self.info_var, wraplength=280).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        transform.columnconfigure(1, weight=1)
        ttk.Label(transform, text="Rotation deg").grid(row=0, column=0, sticky="w")
        ttk.Entry(transform, textvariable=self.rotation_var, width=9).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(transform, text="Apply", command=self.schedule_render).grid(row=0, column=2, padx=(6, 0))
        rotate_row = ttk.Frame(transform)
        rotate_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        for idx, (label, degrees) in enumerate((("-90°", -90), ("+90°", 90), ("0°", None))):
            rotate_row.columnconfigure(idx, weight=1)
            if degrees is None:
                command = self.reset_rotation
            else:
                command = partial(self.rotate_by, degrees)
            ttk.Button(rotate_row, text=label, command=command).grid(row=0, column=idx, sticky="ew", padx=2)

        pixel_frame = ttk.LabelFrame(transform, text="Output Pixels / Scale", padding=8)
        pixel_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        pixel_frame.columnconfigure(0, weight=1)
        pixel_frame.columnconfigure(1, weight=1)
        pixel_frame.columnconfigure(2, weight=1)
        ttk.Label(pixel_frame, text="Width px").grid(row=0, column=0, sticky="w")
        ttk.Label(pixel_frame, text="Height px").grid(row=0, column=1, sticky="w")
        ttk.Label(pixel_frame, text="Scale").grid(row=0, column=2, sticky="w")
        for col, var in enumerate((self.output_width_var, self.output_height_var, self.output_scale_var)):
            entry = ttk.Entry(pixel_frame, textvariable=var, width=8)
            entry.grid(row=1, column=col, sticky="ew", padx=(0 if col == 0 else 3, 0 if col == 2 else 3))
            entry.bind("<KeyRelease>", lambda _event: self.schedule_render())
            entry.bind("<Return>", lambda _event: self.schedule_render(immediate=True))
        ttk.Label(pixel_frame, text="View zoom %").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(pixel_frame, textvariable=self.view_zoom_var, width=8).grid(row=3, column=0, sticky="ew")
        ttk.Button(pixel_frame, text="Apply View Zoom", command=self.apply_view_zoom).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(6, 0))

        ttk.Button(channels, text="Brightness / Contrast...", command=self.open_bc_window).pack(fill=tk.X, pady=(0, 8))
        for index in range(len(DEFAULT_CHANNEL_COLORS)):
            row = ttk.Frame(channels)
            row.pack(fill=tk.X, pady=2)
            check = ttk.Checkbutton(row, text=f"Ch{index + 1}", variable=self.channel_enabled_vars[index], command=self._sync_channel_settings_and_render)
            check.pack(side=tk.LEFT)
            button = tk.Button(row, text="Color", width=8, command=lambda channel_index=index: self.choose_color(channel_index))
            button.pack(side=tk.RIGHT)
            self.channel_widgets.append((check, button))

        ttk.Button(export, text="Export Current View TIFF Set", command=self.export_viewport_tiff).pack(fill=tk.X)
        ttk.Button(export, text="Export Full Preview PNG", command=self.export_current).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(export, text="Batch Export PNGs", command=self.export_all).pack(fill=tk.X, pady=(6, 0))
        ttk.Label(export, text="The TIFF set exports the current view as Ch1/Ch2/Ch3/composite; settings are saved automatically.", wraplength=260).pack(fill=tk.X, pady=(10, 0))

    def _build_canvas(self, parent: ttk.Frame) -> None:
        self.canvas = tk.Canvas(parent, bg="#101010", highlightthickness=0)
        xbar = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ybar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")

    def _bind_events(self) -> None:
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self._on_button_wheel(event, 1))
        self.canvas.bind("<Button-5>", lambda event: self._on_button_wheel(event, -1))
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._drag_pan)
        self.root.bind("<Left>", self.prev_image)
        self.root.bind("<Right>", self.next_image)
        self.root.bind("b", lambda _event: self.open_bc_window())
        self.root.bind("B", lambda _event: self.open_bc_window())
        self.root.bind("<Command-b>", lambda _event: self.open_bc_window())
        self.root.bind("<Control-b>", lambda _event: self.open_bc_window())

    def browse_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(_default_folder()))
        if folder:
            self.folder_var.set(folder)
            self.load_images([folder])

    def load_images(self, paths: Iterable[str | Path]) -> None:
        image_paths = sorted(find_tiff_files(paths), key=natural_sort_key)
        if not image_paths:
            messagebox.showwarning("No Images", "No TIFF images were found.")
            return
        self.image_paths = image_paths
        self.current_index = 0
        self.folder_var.set(str(image_paths[0].parent))
        self.image_combo.configure(values=[path.name for path in image_paths])
        self.image_combo.current(0)
        self.save_settings()
        self.load_current_image(fit=True)

    def load_current_image(self, fit: bool = False) -> None:
        if not (0 <= self.current_index < len(self.image_paths)):
            return
        path = self.image_paths[self.current_index]
        try:
            self.channels = load_tiff_channels(path)
        except Exception as exc:
            messagebox.showerror("Read Failed", f"{path}\n\n{exc}")
            return
        self.channel_count = int(self.channels.shape[0])
        self._update_channel_widgets()
        if self.bc_window is not None:
            self.bc_window.refresh_channels()
        height, width = self.channels.shape[1:]
        self.info_var.set(f"{self.current_index + 1}/{len(self.image_paths)}: {path.name}\n{width}x{height}px, {self.channel_count} channels")
        self.render_now()
        if fit:
            self.root.after(30, self.fit_to_window)

    def _on_image_selected(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        selected = self.image_combo.current()
        if selected >= 0 and selected != self.current_index:
            self.current_index = selected
            self.load_current_image(fit=True)

    def prev_image(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        if not self.image_paths:
            return
        self.current_index = (self.current_index - 1) % len(self.image_paths)
        self.image_combo.current(self.current_index)
        self.load_current_image(fit=True)

    def next_image(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        if not self.image_paths:
            return
        self.current_index = (self.current_index + 1) % len(self.image_paths)
        self.image_combo.current(self.current_index)
        self.load_current_image(fit=True)

    def _update_channel_widgets(self) -> None:
        for index, (check, button) in enumerate(self.channel_widgets):
            active = index < self.channel_count
            check.configure(state=tk.NORMAL if active else tk.DISABLED)
            button.configure(state=tk.NORMAL if active else tk.DISABLED)
            color = self.channel_color_vars[index].get()
            button.configure(bg=color, activebackground=color)
            self.channel_settings[index].color = color
            self.channel_settings[index].enabled = bool(self.channel_enabled_vars[index].get())

    def choose_color(self, channel_index: int) -> None:
        current = self.channel_color_vars[channel_index].get()
        _rgb, color = colorchooser.askcolor(color=current, title=f"Ch{channel_index + 1} color")
        if not color:
            return
        self.channel_color_vars[channel_index].set(str(color))
        self.channel_settings[channel_index].color = str(color)
        self._update_channel_widgets()
        self.schedule_render()

    def _sync_channel_settings_and_render(self) -> None:
        for index in range(len(self.channel_settings)):
            self.channel_settings[index].enabled = bool(self.channel_enabled_vars[index].get())
            self.channel_settings[index].color = self.channel_color_vars[index].get()
        self.schedule_render()

    def open_bc_window(self) -> None:
        if self.bc_window is None or not self.bc_window.winfo_exists():
            self.bc_window = BrightnessContrastWindow(self)
        else:
            self.bc_window.deiconify()
            self.bc_window.lift()
        self.bc_window.refresh_channels()

    def schedule_render(self, immediate: bool = False) -> None:
        self.save_settings()
        if self.render_after_id is not None:
            self.root.after_cancel(self.render_after_id)
            self.render_after_id = None
        if immediate:
            self.render_now()
        else:
            self.render_after_id = self.root.after(120, self.render_now)

    def render_now(self) -> None:
        self.render_after_id = None
        if self.channels is None:
            return
        try:
            image = composite_preview_image(self.channels, self.channel_settings[: self.channel_count])
            self.render_image = self._transform_for_output(image)
        except Exception as exc:
            self.status_var.set(f"Preview failed: {exc}")
            return
        self._redraw_canvas()
        self.status_var.set(f"Preview: {self.render_image.width}x{self.render_image.height}px")

    def _transform_for_output(self, image: Image.Image) -> Image.Image:
        rotated, _geometry = rotate_image_for_preview(image, self._rotation_degrees())
        return self._resize_for_output(rotated)

    def _rotation_degrees(self) -> float:
        try:
            value = float(self.rotation_var.get())
        except ValueError:
            value = 0.0
        return value % 360.0 if np.isfinite(value) else 0.0

    def rotate_by(self, degrees: float) -> None:
        self.rotation_var.set(_fmt_float((self._rotation_degrees() + degrees) % 360.0))
        self.schedule_render(immediate=True)

    def reset_rotation(self) -> None:
        self.rotation_var.set("0")
        self.schedule_render(immediate=True)

    def _resize_for_output(self, image: Image.Image) -> Image.Image:
        width = self._positive_int_or_none(self.output_width_var.get())
        height = self._positive_int_or_none(self.output_height_var.get())
        try:
            scale = float(self.output_scale_var.get() or "1")
        except ValueError:
            scale = 1.0
        if not np.isfinite(scale) or scale <= 0:
            scale = 1.0
        source_width, source_height = image.size
        if width is not None and height is not None:
            target = (width, height)
        elif width is not None:
            target = (width, max(1, int(round(width * source_height / source_width))))
        elif height is not None:
            target = (max(1, int(round(height * source_width / source_height))), height)
        else:
            target = (max(1, int(round(source_width * scale))), max(1, int(round(source_height * scale))))
        if target == image.size:
            return image
        return image.resize(target, Image.Resampling.LANCZOS)

    @staticmethod
    def _positive_int_or_none(text: str) -> int | None:
        if not text.strip():
            return None
        try:
            value = int(float(text))
        except ValueError:
            return None
        return value if value > 0 else None

    def _redraw_canvas(self) -> None:
        self.canvas.delete("all")
        if self.render_image is None:
            self.photo = None
            return
        display_width = max(1, int(round(self.render_image.width * self.canvas_zoom)))
        display_height = max(1, int(round(self.render_image.height * self.canvas_zoom)))
        shown = self.render_image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(shown)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, display_width, display_height))
        self.view_zoom_var.set(str(round(self.canvas_zoom * 100)))

    def apply_view_zoom(self) -> None:
        try:
            percent = float(self.view_zoom_var.get())
        except ValueError:
            return
        if not np.isfinite(percent) or percent <= 0:
            return
        self.canvas_zoom = max(ZOOM_MIN, min(ZOOM_MAX, percent / 100.0))
        self._redraw_canvas()
        self.save_settings()

    def fit_to_window(self) -> None:
        if self.render_image is None:
            return
        self.root.update_idletasks()
        width = max(100, self.canvas.winfo_width() - 24)
        height = max(100, self.canvas.winfo_height() - 24)
        self.canvas_zoom = max(ZOOM_MIN, min(1.0, width / self.render_image.width, height / self.render_image.height))
        self._redraw_canvas()
        self.save_settings()

    def _on_mousewheel(self, event: tk.Event[tk.Widget]) -> str:
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return "break"
        if self._wheel_should_zoom(event):
            factor = max(0.2, min(5.0, 1.0015**delta))
            self.zoom_by(factor, event)
            return "break"
        pixels = self._scroll_pixels_from_wheel_delta(delta)
        if self._wheel_should_scroll_horizontally(event):
            self._pan_by(pixels, 0)
        else:
            self._pan_by(0, pixels)
        return "break"

    def _on_button_wheel(self, event: tk.Event[tk.Widget], direction: int) -> str:
        if self._wheel_should_zoom(event):
            self.zoom_by(1.1 if direction > 0 else 1 / 1.1, event)
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

    def zoom_by(self, factor: float, event: tk.Event[tk.Widget] | None = None) -> None:
        if self.render_image is None:
            return
        old_zoom = self.canvas_zoom
        if event is not None:
            anchor_x = self.canvas.canvasx(event.x) / old_zoom
            anchor_y = self.canvas.canvasy(event.y) / old_zoom
        else:
            anchor_x = self.render_image.width / 2
            anchor_y = self.render_image.height / 2
        self.canvas_zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.canvas_zoom * factor))
        self._redraw_canvas()
        if event is not None:
            width = max(1, int(self.render_image.width * self.canvas_zoom))
            height = max(1, int(self.render_image.height * self.canvas_zoom))
            left = max(0, anchor_x * self.canvas_zoom - event.x)
            top = max(0, anchor_y * self.canvas_zoom - event.y)
            self.canvas.xview_moveto(min(1.0, left / width))
            self.canvas.yview_moveto(min(1.0, top / height))
        self.save_settings()

    def _pan_by(self, dx_pixels: float, dy_pixels: float) -> None:
        if dx_pixels:
            self._move_canvas_view("x", dx_pixels)
        if dy_pixels:
            self._move_canvas_view("y", dy_pixels)

    def _move_canvas_view(self, axis: str, delta_pixels: float) -> None:
        if self.render_image is None:
            return
        self.root.update_idletasks()
        if axis == "x":
            total = max(1.0, self.render_image.width * self.canvas_zoom)
            visible = max(1.0, float(self.canvas.winfo_width()))
            first, _last = self.canvas.xview()
            max_left = max(0.0, total - visible)
            new_left = min(max(first * total + delta_pixels, 0.0), max_left)
            self.canvas.xview_moveto(new_left / total)
        else:
            total = max(1.0, self.render_image.height * self.canvas_zoom)
            visible = max(1.0, float(self.canvas.winfo_height()))
            first, _last = self.canvas.yview()
            max_top = max(0.0, total - visible)
            new_top = min(max(first * total + delta_pixels, 0.0), max_top)
            self.canvas.yview_moveto(new_top / total)

    def _start_pan(self, event: tk.Event[tk.Widget]) -> None:
        self.pan_start = (event.x, event.y)
        self.canvas.scan_mark(event.x, event.y)

    def _drag_pan(self, event: tk.Event[tk.Widget]) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _visible_preview_box(self) -> tuple[int, int, int, int] | None:
        if self.render_image is None:
            return None
        self.root.update_idletasks()
        zoom = max(1e-6, float(self.canvas_zoom))
        left = max(0, int(math.floor(self.canvas.canvasx(0) / zoom)))
        top = max(0, int(math.floor(self.canvas.canvasy(0) / zoom)))
        right = min(self.render_image.width, int(math.ceil(self.canvas.canvasx(self.canvas.winfo_width()) / zoom)))
        bottom = min(self.render_image.height, int(math.ceil(self.canvas.canvasy(self.canvas.winfo_height()) / zoom)))
        if right <= left or bottom <= top:
            return (0, 0, self.render_image.width, self.render_image.height)
        return (left, top, right, bottom)

    def _visible_preview_crop(self) -> Image.Image | None:
        if self.render_image is None:
            return None
        box = self._visible_preview_box()
        if box is None:
            return self.render_image.copy()
        return self.render_image.crop(box)

    def _channel_output_image(self, channel_zero: int) -> Image.Image | None:
        if self.channels is None:
            return None
        image = single_channel_preview_image(self.channels, channel_zero, self.channel_settings[: self.channel_count])
        return self._transform_for_output(image)

    @staticmethod
    def _save_tiff(image: Image.Image, output_path: Path) -> None:
        image.convert("RGB").save(output_path, compression="tiff_lzw")

    def export_viewport_tiff(self) -> None:
        if self.render_image is None or not (0 <= self.current_index < len(self.image_paths)):
            return
        self.save_settings()
        box = self._visible_preview_box()
        if box is None:
            return
        output_dir = self.image_paths[self.current_index].parent / "preview_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = self.image_paths[self.current_index].stem
        saved_paths: list[Path] = []

        composite_crop = self.render_image.crop(box)
        composite_path = output_dir / f"{stem}_viewport_composite.tif"
        self._save_tiff(composite_crop, composite_path)
        saved_paths.append(composite_path)

        for channel_zero in range(min(3, self.channel_count)):
            channel_image = self._channel_output_image(channel_zero)
            if channel_image is None:
                continue
            output_path = output_dir / f"{stem}_viewport_ch{channel_zero + 1}.tif"
            self._save_tiff(channel_image.crop(box), output_path)
            saved_paths.append(output_path)

        self.status_var.set(f"Exported {len(saved_paths)} current-view TIFF file(s) to {output_dir}")

    def export_current(self) -> None:
        if self.render_image is None or not (0 <= self.current_index < len(self.image_paths)):
            return
        output_dir = self.image_paths[self.current_index].parent / "preview_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.image_paths[self.current_index].stem}_preview.png"
        self.render_image.save(output_path)
        self.status_var.set(f"Exported: {output_path}")

    def export_all(self) -> None:
        if not self.image_paths:
            return
        output_dir = self.image_paths[0].parent / "preview_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        current = self.current_index
        for index, path in enumerate(self.image_paths):
            self.status_var.set(f"Exporting {index + 1}/{len(self.image_paths)}: {path.name}")
            self.root.update_idletasks()
            try:
                channels = load_tiff_channels(path)
                image = composite_preview_image(channels, self.channel_settings[: channels.shape[0]])
                out = self._transform_for_output(image)
                out.save(output_dir / f"{path.stem}_preview.png")
                saved += 1
            except Exception as exc:
                print(f"Failed {path}: {exc}")
        self.current_index = current
        self.status_var.set(f"Exported {saved} preview image(s) to {output_dir}")


def run_smoke_test(target: str | Path) -> int:
    paths = sorted(find_tiff_files([target]), key=natural_sort_key)
    print(f"Found {len(paths)} TIFF file(s)")
    if not paths:
        return 1
    path = paths[0]
    channels = load_tiff_channels(path)
    settings = [ChannelBC(color=color) for color in DEFAULT_CHANNEL_COLORS]
    image = composite_preview_image(channels, settings[: channels.shape[0]])
    rotated, _geometry = rotate_image_for_preview(image, 15)
    resized = rotated.resize((512, max(1, round(512 * rotated.height / rotated.width))))
    print(f"{path.name}: channels={channels.shape[0]} source={image.size} preview={resized.size}")
    print("Smoke test OK")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fluorescence preview/export GUI with trackpad pan/zoom and B&C controls.")
    parser.add_argument("paths", nargs="*", help="Optional folder or TIFF path(s) loaded on startup.")
    parser.add_argument("--smoke-test", metavar="PATH", help="Validate TIFF preview rendering without GUI.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.smoke_test:
        return run_smoke_test(args.smoke_test)
    root = tk.Tk()
    FluorescencePreviewExportApp(root, start_paths=args.paths)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
