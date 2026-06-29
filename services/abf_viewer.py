from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services import abf as abf_service
from services.matplotlib_utils import close_figure, new_subplots
from services.trace_decimate import DEFAULT_MAX_POINTS, decimate_xy


class AbfViewerService:
    """Metadata, plotting, detection, and export helpers for ABF viewer routes."""

    def __init__(
        self,
        *,
        pyabf_mod: Any,
        find_peaks: Callable | None,
        fig_to_b64: Callable[[Any], str],
        mode_is_save: Callable[[Any], bool],
        apply_axes_limits: Callable[..., None],
        clean_trace_svg: Callable[..., bytes],
        next_numbered_path: Callable[[Path], Path],
        line_color: str,
    ):
        self.pyabf_mod = pyabf_mod
        self.find_peaks = find_peaks
        self.fig_to_b64 = fig_to_b64
        self.mode_is_save = mode_is_save
        self.apply_axes_limits = apply_axes_limits
        self.clean_trace_svg = clean_trace_svg
        self.next_numbered_path = next_numbered_path
        self.line_color = line_color

    def _require_abf(self) -> None:
        if self.pyabf_mod is None:
            raise ValueError("pyabf not installed")

    def _require_scipy(self) -> None:
        if self.find_peaks is None:
            raise ValueError("scipy not installed")

    @staticmethod
    def _num(value: Any, default: float) -> float:
        """Apply a numeric default for blank/unset typed request fields.

        Type coercion now happens at the request schema boundary
        (OptFloat/OptInt); this only fills the default when a field is None.
        """
        return default if value is None else value

    @staticmethod
    def _float(value: Any, default: float | None = None) -> float | None:
        """Coerce an item from an untyped list (peaks/window) to float."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int(value: Any, default: int) -> int:
        """Coerce an item from an untyped list (peaks) to int."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _abf_output(path: str | Path, role: str) -> dict[str, str]:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else (p.suffix.lower().lstrip(".") or "file"),
            "role": role,
        }

    def browse_tree_payload(self, folder: str) -> dict[str, Any]:
        path = Path(folder)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {folder}")

        subdirs = []
        try:
            for item in sorted(path.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    has_abf_files = any(child.suffix.lower() == ".abf" for child in item.iterdir())
                    subdirs.append({"name": item.name, "path": str(item), "has_abf": has_abf_files})
        except Exception:
            pass
        return {"subdirs": subdirs}

    def info_payload(self, path: str) -> dict[str, Any]:
        self._require_abf()
        abf = self.pyabf_mod.ABF(path)
        channels = []
        for index in range(abf.channelCount):
            ch_name = abf.adcNames[index] if index < len(abf.adcNames) else f"Ch{index}"
            ch_unit = abf.adcUnits[index] if index < len(abf.adcUnits) else ""
            channels.append(
                {
                    "index": index,
                    "label": f"{index}: {ch_name} [{ch_unit}]",
                    "name": ch_name,
                    "unit": ch_unit,
                }
            )
        return {
            "num_sweeps": abf.sweepCount,
            "channels": channels,
            "channel_count": abf.channelCount,
            "sample_rate": abf.dataRate,
            "duration_s": round(abf.sweepLengthSec, 3),
        }

    def plot_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_abf()
        path = data.get("path", "")
        sweep = self._num(data.get("sweep"), 0)
        channel = self._num(data.get("channel"), 0)
        i_ch = self._num(data.get("i_ch"), 0)
        v_ch = self._num(data.get("v_ch"), 1)
        r_norm = bool(data.get("r_norm", False))
        bl0 = data.get("bl_pre0")
        bl1 = data.get("bl_pre1")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        downsample = self._num(data.get("dsf"), 1)

        abf = self.pyabf_mod.ABF(path)
        sweep_index = min(sweep, abf.sweepCount - 1)
        channel = min(channel, abf.channelCount - 1)
        abf.setSweep(sweep_index, channel=channel)
        t = abf.sweepX[::downsample]
        y = abf.sweepY[::downsample].copy()
        y_unit = abf.adcUnits[channel] if channel < abf.channelCount else ""

        r_val = None
        if r_norm and abf.channelCount >= 2:
            abf.setSweep(sweep_index, channel=i_ch)
            i_full = abf.sweepY
            abf.setSweep(sweep_index, channel=v_ch)
            v_full = abf.sweepY
            dt = abf.sweepX[1] - abf.sweepX[0]
            r_val = abf_service.estimate_resistance(i_full, v_full, dt)

            abf.setSweep(sweep_index, channel=channel)
            y = abf.sweepY[::downsample].copy()
            if r_val is not None:
                y = y * (r_val * 1e3)
                y_unit = "mV (xR)"

        y = abf_service.baseline_subtract(y, t, bl0, bl1)
        t, y = self._clip_xy(t, y, x_min, x_max)

        fig, ax = new_subplots(figsize=(9, 4))
        ax.plot(t, y, color=self.line_color, lw=0.7)
        ax.margins(x=0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(y_unit)
        ch_name = abf.adcNames[channel] if channel < abf.channelCount else f"Ch{channel}"
        ch_name = str(ch_name).replace("\x00", "").strip() or f"Ch{channel}"
        title = f"{Path(path).name} · sweep {sweep} · {ch_name}"
        if r_val:
            title += f" · R={r_val * 1e3:.1f} MOhm"
        ax.set_title(title, fontsize=10, color="#5C5E62")
        ax.grid(True, alpha=0.4)
        self.apply_axes_limits(ax, None, None, y_min, y_max)
        fig.tight_layout()
        return {"img": self.fig_to_b64(fig), "r_val": r_val}

    def trace_data_payload(
        self, data: dict[str, Any], max_points: int = DEFAULT_MAX_POINTS
    ) -> dict[str, Any]:
        """Return decimated ABF trace samples for client-side interactive plotting."""
        self._require_abf()
        path = data.get("path", "")
        sweep = self._num(data.get("sweep"), 0)
        channel = self._num(data.get("channel"), 0)
        i_ch = self._num(data.get("i_ch"), 0)
        v_ch = self._num(data.get("v_ch"), 1)
        r_norm = data.get("r_norm", False)
        bl0 = data.get("bl_pre0")
        bl1 = data.get("bl_pre1")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        downsample = max(1, self._num(data.get("dsf"), 1))

        abf = self.pyabf_mod.ABF(path)
        sweep_index = min(sweep, abf.sweepCount - 1)
        channel = min(channel, abf.channelCount - 1)
        abf.setSweep(sweep_index, channel=channel)
        t = abf.sweepX[::downsample]
        y = abf.sweepY[::downsample].copy()
        y_unit = abf.adcUnits[channel] if channel < abf.channelCount else ""
        ch_name = abf.adcNames[channel] if channel < abf.channelCount else f"Ch{channel}"
        ch_name = str(ch_name).replace("\x00", "").strip() or f"Ch{channel}"

        r_val = None
        if r_norm and abf.channelCount >= 2:
            abf.setSweep(sweep_index, channel=min(i_ch, abf.channelCount - 1))
            i_full = abf.sweepY
            abf.setSweep(sweep_index, channel=min(v_ch, abf.channelCount - 1))
            v_full = abf.sweepY
            dt_full = abf.sweepX[1] - abf.sweepX[0] if len(abf.sweepX) > 1 else 1e-4
            r_val = abf_service.estimate_resistance(i_full, v_full, dt_full)

            abf.setSweep(sweep_index, channel=channel)
            y = abf.sweepY[::downsample].copy()
            if r_val is not None:
                y = y * (r_val * 1e3)
                y_unit = "mV (xR)"

        y = abf_service.baseline_subtract(y, t, bl0, bl1)
        t, y = self._clip_xy(t, y, x_min, x_max)
        n_full = int(t.shape[0])
        td, yd = decimate_xy(t, y, max_points=max_points)
        title = f"{Path(path).name} · sweep {sweep_index} · {ch_name}"
        if r_val:
            title += f" · R={r_val * 1e3:.1f} MOhm"
        return {
            "x": td.tolist(),
            "y": yd.tolist(),
            "x_label": "Time (s)",
            "y_label": y_unit,
            "title": title,
            "y_min": y_min,
            "y_max": y_max,
            "n_full": n_full,
            "n_points": int(td.shape[0]),
            "decimated": int(td.shape[0]) < n_full,
            "r_val": r_val,
        }

    def detect_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_abf()
        self._require_scipy()

        path = data.get("path", "")
        sweep = self._num(data.get("sweep"), 0)
        channel = self._num(data.get("channel"), 0)
        i_ch = self._num(data.get("i_ch"), 0)
        v_ch = self._num(data.get("v_ch"), 1)
        r_norm = data.get("r_norm", False)
        bl0 = data.get("bl_pre0")
        bl1 = data.get("bl_pre1")
        t0 = data.get("t0")
        t1 = data.get("t1")
        use_all = data.get("use_all", False)
        polarity = data.get("polarity", "positive")
        height = data.get("height")
        prominence = data.get("prominence")
        distance_ms = self._num(data.get("distance"), 2.0)

        if height is not None and height <= 0:
            height = None
        if prominence is not None and prominence <= 0:
            prominence = None

        abf = self.pyabf_mod.ABF(path)
        sweep_index = min(sweep, abf.sweepCount - 1)
        channel = min(channel, abf.channelCount - 1)
        abf.setSweep(sweep_index, channel=channel)
        t_full = abf.sweepX.copy()
        y_full = abf.sweepY.copy()
        y_unit = abf.adcUnits[channel] if channel < abf.channelCount else ""

        r_val = None
        r_method = ""
        baseline_raw_i = np.nan

        if r_norm and abf.channelCount >= 2:
            i_ch = min(i_ch, abf.channelCount - 1)
            v_ch = min(v_ch, abf.channelCount - 1)
            abf.setSweep(sweep_index, channel=i_ch)
            i_full = abf.sweepY.copy()
            abf.setSweep(sweep_index, channel=v_ch)
            v_full = abf.sweepY.copy()
            dt_full = abf.sweepX[1] - abf.sweepX[0] if len(abf.sweepX) > 1 else 1e-4
            r_val = abf_service.estimate_resistance(i_full, v_full, dt_full)
            r_method = "dV/dI edge" if r_val is not None else "unavailable"

            i_norm, baseline_raw_i = abf_service.baseline_apply(
                i_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
            )
            if channel == i_ch:
                y_full = i_norm
                if r_val is not None:
                    y_full = y_full * (r_val * 1e3)
                    y_unit = "I_norm (pA·MΩ)"
                else:
                    y_unit = "I_baseline_sub (pA)"
            else:
                y_full, _ = abf_service.baseline_apply(
                    y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=False
                )
        else:
            y_full, baseline_raw_i = abf_service.baseline_apply(
                y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
            )

        peaks, window_bounds = abf_service.detect_peaks(
            t_full,
            y_full,
            t0,
            t1,
            use_all,
            polarity,
            distance_ms,
            self.find_peaks,
            height=height,
            prominence=prominence,
        )
        t0_plot, t1_plot = window_bounds
        pol_out = peaks[0]["polarity"] if peaks else str(polarity or "positive").upper()

        fig, ax = new_subplots(figsize=(9, 4))
        ax.plot(t_full, y_full, color=self.line_color, lw=0.7)
        ax.margins(x=0)

        if not use_all:
            ax.axvspan(t0_plot, t1_plot, alpha=0.12, color="gray")
            ax.axvline(t0_plot, ls="--", lw=0.8, color="gray")
            ax.axvline(t1_plot, ls="--", lw=0.8, color="gray")

        if peaks:
            peak_indices = np.array([int(peak["idx"]) for peak in peaks], dtype=int)
            ax.scatter(
                t_full[peak_indices],
                y_full[peak_indices],
                color="#d62728",
                marker="^",
                s=22,
                zorder=5,
            )

        ax.set_xlabel("Time (s)")
        ax.set_ylabel(y_unit)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        return {
            "img": self.fig_to_b64(fig),
            "peaks": peaks,
            "window": [t0_plot, t1_plot],
            "meta": {
                "polarity": pol_out,
                "R_MOhm": (float(r_val * 1e3) if r_val is not None else None),
                "R_method": r_method,
                "baseline_raw_i_pA": float(baseline_raw_i),
                "y_unit": y_unit,
            },
        }

    def export_peaks_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_abf()

        path = data.get("path", "")
        mode = data.get("mode", "download")
        peaks = data.get("peaks", [])
        if not peaks:
            raise ValueError("No peaks selected")

        sweep = self._num(data.get("sweep"), 0)
        channel = self._num(data.get("channel"), 0)
        i_ch = self._num(data.get("i_ch"), 0)
        v_ch = self._num(data.get("v_ch"), 1)
        r_norm = data.get("r_norm", False)
        bl0 = data.get("bl_pre0")
        bl1 = data.get("bl_pre1")
        export_window_ms = self._num(data.get("export_window_ms"), 50.0)
        if export_window_ms is None or export_window_ms <= 0:
            export_window_ms = 50.0

        polarity = str(data.get("polarity", "POS")).upper()
        window = data.get("window", [])
        win_t0 = (
            self._float(window[0], np.nan)
            if isinstance(window, list) and len(window) > 0
            else np.nan
        )
        win_t1 = (
            self._float(window[1], np.nan)
            if isinstance(window, list) and len(window) > 1
            else np.nan
        )

        src = Path(path)
        abf = self.pyabf_mod.ABF(path)
        sweep_index = min(sweep, abf.sweepCount - 1)
        channel = min(channel, abf.channelCount - 1)

        abf.setSweep(sweep_index, channel=channel)
        t_full = abf.sweepX.copy()
        y_full = abf.sweepY.copy()

        r_val = None
        r_method = ""
        baseline_raw_i = np.nan

        if r_norm and abf.channelCount >= 2:
            i_ch = min(i_ch, abf.channelCount - 1)
            v_ch = min(v_ch, abf.channelCount - 1)
            abf.setSweep(sweep_index, channel=i_ch)
            i_full = abf.sweepY.copy()
            abf.setSweep(sweep_index, channel=v_ch)
            v_full = abf.sweepY.copy()
            dt_full = abf.sweepX[1] - abf.sweepX[0] if len(abf.sweepX) > 1 else 1e-4
            r_val = abf_service.estimate_resistance(i_full, v_full, dt_full)
            r_method = "dV/dI edge" if r_val is not None else "unavailable"

            i_norm, baseline_raw_i = abf_service.baseline_apply(
                i_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
            )
            if channel == i_ch:
                y_full = i_norm
                if r_val is not None:
                    y_full = y_full * (r_val * 1e3)
            else:
                y_full, _ = abf_service.baseline_apply(
                    y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=False
                )
        else:
            y_full, baseline_raw_i = abf_service.baseline_apply(
                y_full, t_full, pre0_ms=bl0, pre1_ms=bl1, use_default=True
            )

        out_folder = src.parent / src.stem
        out_folder.mkdir(parents=True, exist_ok=True)

        rows = []
        selected = []
        for export_index, peak in enumerate(peaks, start=1):
            global_index = self._int(peak.get("idx", peak.get("global_index")), -1)
            if global_index < 0 or global_index >= len(t_full):
                peak_time = self._float(peak.get("time", peak.get("t")), None)
                if peak_time is None:
                    continue
                global_index = int(np.argmin(np.abs(t_full - peak_time)))

            selected.append((export_index, global_index))
            rows.append(
                {
                    "export_index": export_index,
                    "global_index": global_index,
                    "t_s": float(t_full[global_index]),
                    "y_norm": float(y_full[global_index]),
                    "polarity": polarity,
                    "R_MOhm": (float(r_val * 1e3) if r_val is not None else np.nan),
                    "R_method": r_method,
                    "baseline_raw_i_pA": float(baseline_raw_i),
                    "window_start_s": win_t0,
                    "window_end_s": win_t1,
                }
            )

        summary_path = out_folder / f"{src.stem}_peaks_summary.csv"
        pd.DataFrame(rows).to_csv(summary_path, index=False, encoding="utf-8-sig")

        half = float(export_window_ms) / 1000.0
        saved = 0
        segment_paths = []
        for export_index, global_index in selected:
            peak_time = float(t_full[global_index])
            mask = (t_full >= peak_time - half) & (t_full <= peak_time + half)
            if not np.any(mask):
                continue
            seg_path = out_folder / f"{src.stem}_peak_{export_index:03d}.csv"
            pd.DataFrame({"time_s": t_full[mask], "I_norm": y_full[mask]}).to_csv(
                seg_path, index=False
            )
            saved += 1
            segment_paths.append(str(seg_path))

        if self.mode_is_save(mode):
            outputs = [self._abf_output(out_folder, "abf_peak_folder")]
            outputs.append(self._abf_output(summary_path, "abf_peak_summary"))
            outputs.extend(self._abf_output(path, "abf_peak_segment") for path in segment_paths)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_folder),
                    "summary_path": str(summary_path),
                    "saved_count": saved,
                    "saved_paths": [str(summary_path)] + segment_paths,
                    "outputs": outputs,
                },
            }

        return {
            "kind": "download",
            "payload": summary_path.read_bytes(),
            "mimetype": "text/csv",
            "download_name": f"{src.stem}_peaks_summary.csv",
        }

    def export_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_abf()
        path = data.get("path", "")
        fmt = str(data.get("fmt", "png") or "png").lower()
        mode = data.get("mode", "download")
        sweep = self._num(data.get("sweep"), 0)
        channel = self._num(data.get("channel"), 0)
        i_ch = self._num(data.get("i_ch"), 0)
        v_ch = self._num(data.get("v_ch"), 1)
        r_norm = data.get("r_norm", False)
        bl0 = data.get("bl_pre0")
        bl1 = data.get("bl_pre1")
        x_min = data.get("x_min")
        x_max = data.get("x_max")
        y_min = data.get("y_min")
        y_max = data.get("y_max")
        downsample = max(1, self._num(data.get("dsf"), 1))
        signal_only = data.get("signal_only", False)
        src = Path(path)
        abf = self.pyabf_mod.ABF(path)
        sweep_index = min(sweep, abf.sweepCount - 1)
        channel = min(channel, abf.channelCount - 1)
        abf.setSweep(sweep_index, channel=channel)
        t = abf.sweepX[::downsample]
        y = abf.sweepY.copy()[::downsample]
        y_unit = abf.adcUnits[channel] if channel < abf.channelCount else ""

        if r_norm and abf.channelCount >= 2:
            abf.setSweep(sweep_index, channel=min(i_ch, abf.channelCount - 1))
            i_full = abf.sweepY
            abf.setSweep(sweep_index, channel=min(v_ch, abf.channelCount - 1))
            v_full = abf.sweepY
            dt_full = abf.sweepX[1] - abf.sweepX[0]
            r_val = abf_service.estimate_resistance(i_full, v_full, dt_full)

            abf.setSweep(sweep_index, channel=channel)
            y = abf.sweepY[::downsample].copy()
            if r_val is not None:
                y = y * (r_val * 1e3)
                y_unit = "mV (xR)"

        y = abf_service.baseline_subtract(y, t, bl0, bl1)
        t, y = self._clip_xy(t, y, x_min, x_max)

        if fmt == "csv":
            buf = io.BytesIO()
            pd.DataFrame({"time_s": t, "value": y}).to_csv(buf, index=False)
            buf.seek(0)
            payload = buf.getvalue()
            if self.mode_is_save(mode):
                out_path = src.with_name(f"{src.stem}_s{sweep}_ch{channel}.csv")
                out_path.write_bytes(payload)
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "outputs": [self._abf_output(out_path, "abf_trace_csv")],
                    },
                }
            return {
                "kind": "download",
                "payload": payload,
                "mimetype": "text/csv",
                "download_name": "abf_export.csv",
            }

        if fmt == "svg":
            payload = self.clean_trace_svg(
                t, y, y_min=y_min, y_max=y_max, line_color=self.line_color
            )
            base_name = (
                f"{src.stem}_preview_signal.svg"
                if signal_only
                else f"{src.stem}_s{sweep}_ch{channel}.svg"
            )
            if self.mode_is_save(mode):
                out_path = self.next_numbered_path(src.with_name(base_name))
                out_path.write_bytes(payload)
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "outputs": [self._abf_output(out_path, "abf_trace_export")],
                    },
                }
            return {
                "kind": "download",
                "payload": payload,
                "mimetype": "image/svg+xml",
                "download_name": base_name,
            }

        fig, ax = new_subplots(figsize=(9, 4))
        ax.plot(t, y, color=self.line_color, lw=0.7)
        ax.margins(x=0)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(y_unit)
        self.apply_axes_limits(ax, None, None, y_min, y_max)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        buf = io.BytesIO()
        save_kw = {"format": fmt, "bbox_inches": "tight"}
        if fmt == "png":
            save_kw["dpi"] = 300
        fig.savefig(buf, **save_kw)
        close_figure(fig)
        buf.seek(0)
        payload = buf.getvalue()
        if self.mode_is_save(mode):
            out_path = self.next_numbered_path(
                src.with_name(f"{src.stem}_s{sweep}_ch{channel}.{fmt}")
            )
            out_path.write_bytes(payload)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_path),
                    "outputs": [self._abf_output(out_path, "abf_trace_export")],
                },
            }
        return {
            "kind": "download",
            "payload": payload,
            "mimetype": "image/png" if fmt == "png" else "image/svg+xml",
            "download_name": f"abf_export.{fmt}",
        }

    @staticmethod
    def _clip_xy(
        x: np.ndarray,
        y: np.ndarray,
        x_min: float | None,
        x_max: float | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        if x_min is not None:
            mask = x >= x_min
            x, y = x[mask], y[mask]
        if x_max is not None:
            mask = x <= x_max
            x, y = x[mask], y[mask]
        return x, y
