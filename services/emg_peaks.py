from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services import emg as emg_service
from services.matplotlib_utils import new_subplots
from services.trace_decimate import decimate_xy

# EMG/RHD traces are dense high-frequency waveforms. The generic 4k-point
# envelope preview is fine for slow traces, but it turns full-window burst
# trains into barcode-like min/max columns. Match the legacy PNG path's
# approximate 50k rendered-sample budget for this page.
EMG_TRACE_MAX_POINTS = 50000


class EmgPeaksService:
    """Load, detect, plot, and export EMG peak-selector data."""

    def __init__(
        self,
        *,
        has_scipy: bool,
        find_peaks: Callable | None,
        peak_widths: Callable | None,
        fig_to_b64: Callable[[Any], str],
        float_or: Callable[[Any, float | None], float | None],
        line_color: str,
        mode_is_save: Callable[[Any], bool],
    ):
        self.has_scipy = has_scipy
        self.find_peaks = find_peaks
        self.peak_widths = peak_widths
        self.fig_to_b64 = fig_to_b64
        self.float_or = float_or
        self.line_color = line_color
        self.mode_is_save = mode_is_save

    def _require_scipy(self) -> None:
        if not self.has_scipy or self.find_peaks is None or self.peak_widths is None:
            raise ValueError("scipy not installed")

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _invert_signal_enabled(self, data: dict[str, Any]) -> bool:
        return self._as_bool(data.get("invert_signal"), False)

    @staticmethod
    def _peak_outputs(
        saved_path: str | Path, role: str, extra_paths: list[str] | None = None
    ) -> list[dict]:
        output_path = Path(saved_path)
        outputs = [
            {
                "path": str(output_path),
                "type": "directory" if output_path.is_dir() else "csv",
                "role": role,
            }
        ]
        for path in extra_paths or []:
            p = Path(path)
            outputs.append(
                {
                    "path": str(p),
                    "type": "directory" if p.is_dir() else "csv",
                    "role": "emg_peak_segment" if p.suffix.lower() == ".csv" else "emg_peak_output",
                }
            )
        return outputs

    @staticmethod
    def _finish_trace_figure(fig: Any) -> None:
        fig.tight_layout(pad=1.1)
        fig.subplots_adjust(left=0.10, right=0.985, bottom=0.18, top=0.92)

    @staticmethod
    def _linked_channel_sources(src: Path, linked_channels: list[Any]) -> list[Path]:
        parent = src.parent.resolve()
        seen = {src.resolve()}
        out = []
        for value in linked_channels or []:
            name = Path(str(value or "")).name
            if not name:
                continue
            candidate = src.parent / name
            if not candidate.exists() or candidate.suffix.lower() != ".csv":
                continue
            resolved = candidate.resolve()
            if resolved.parent != parent or resolved in seen:
                continue
            seen.add(resolved)
            out.append(candidate)
        return out

    def browse_payload(self, folder: str) -> dict[str, Any]:
        subfolders = []
        path = Path(folder)
        if path.is_dir():
            for subdir in sorted(path.iterdir()):
                csvs = list(subdir.glob("*.csv")) if subdir.is_dir() else []
                if csvs:
                    subfolders.append(
                        {
                            "name": subdir.name,
                            "path": str(subdir),
                            "csvs": [{"name": csv.name, "path": str(csv)} for csv in sorted(csvs)],
                        }
                    )
        return {
            "subfolders": [subfolder["name"] for subfolder in subfolders],
            "subfolders_meta": subfolders,
        }

    @staticmethod
    def channel_payload(folder: str, subfolder: str) -> dict[str, list[str]]:
        path = Path(folder) / subfolder
        if not path.is_dir():
            return {"channels": []}
        return {"channels": [csv.name for csv in sorted(path.glob("*.csv"))]}

    def load_duration_payload(self, folder: str, subfolder: str, channel: str) -> dict[str, float]:
        path = str(Path(folder) / subfolder / channel)
        df = pd.read_csv(path)
        t_col, v_col = emg_service.pick_columns(df)
        t_raw, _, valid = emg_service.numeric_signal(df, t_col, v_col)
        t = t_raw[valid]
        return {"duration": round(float(t[-1] - t[0]), 3) if len(t) else 0}

    def plot_payload(self, data: dict[str, Any]) -> dict[str, str]:
        path = data.get("path", "")
        x_min = self.float_or(data.get("x_min"), None)
        x_max = self.float_or(data.get("x_max"), None)
        invert_signal = self._invert_signal_enabled(data)
        t, v, t_col, v_col = self._load_windowed_signal(
            path, x_min, x_max, invert_signal=invert_signal
        )
        dsf = max(1, len(t) // 50000)
        fig, ax = new_subplots(figsize=(10, 3.5))
        ax.plot(t[::dsf], v[::dsf], color=self.line_color, lw=0.6)
        ax.set_xlabel(t_col)
        ax.set_ylabel(v_col)
        ax.grid(True, alpha=0.4)
        self._finish_trace_figure(fig)
        return {"img": self.fig_to_b64(fig)}

    def trace_data_payload(
        self, data: dict[str, Any], max_points: int = EMG_TRACE_MAX_POINTS
    ) -> dict[str, Any]:
        """Return decimated EMG samples for client-side interactive plotting."""
        path = data.get("path", "")
        x_min = self.float_or(data.get("x_min"), None)
        x_max = self.float_or(data.get("x_max"), None)
        y_min = self.float_or(data.get("y_min"), None)
        y_max = self.float_or(data.get("y_max"), None)
        invert_signal = self._invert_signal_enabled(data)
        t, v, t_col, v_col = self._load_windowed_signal(
            path, x_min, x_max, invert_signal=invert_signal
        )
        n_full = int(t.shape[0])
        td, vd = decimate_xy(t, v, max_points=max_points)
        return {
            "x": td.tolist(),
            "y": vd.tolist(),
            "x_label": t_col,
            "y_label": v_col,
            "title": Path(path).name,
            "y_min": y_min,
            "y_max": y_max,
            "n_full": n_full,
            "n_points": int(td.shape[0]),
            "decimated": int(td.shape[0]) < n_full,
            "inverted_signal": invert_signal,
        }

    def detect_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_scipy()

        path = data.get("path", "")
        height = self.float_or(data.get("pk_height"), None)
        prom = self.float_or(data.get("pk_prom"), None)
        dist = self.float_or(data.get("pk_dist", 100), 100)
        minw = self.float_or(data.get("pk_minw"), None)
        wlen = self.float_or(data.get("pk_wlen"), None)
        x_min = self.float_or(data.get("x_min"), None)
        x_max = self.float_or(data.get("x_max"), None)
        polarity = str(data.get("polarity", "both")).strip().lower()
        if polarity not in {"positive", "negative", "both"}:
            polarity = "both"
        adaptive_sigma = self._as_bool(data.get("adaptive_sigma"), False)
        invert_signal = self._invert_signal_enabled(data)
        sigma_prom = self.float_or(data.get("sigma_prom"), 1.0)
        sigma_height = self.float_or(data.get("sigma_height"), 1.0)
        dur = self.float_or(data.get("pk_dur"), None)

        df = pd.read_csv(path)
        t_col, v_col = emg_service.pick_columns(df)
        t_raw, v_raw, valid = emg_service.numeric_signal(df, t_col, v_col)
        if invert_signal:
            v_raw = -v_raw

        t = t_raw[valid]
        v = v_raw[valid]
        src_idx = np.arange(len(t_raw))[valid]

        wmask = np.ones_like(t, dtype=bool)
        if x_min is not None:
            wmask &= t >= x_min
        if x_max is not None:
            wmask &= t <= x_max
        if np.count_nonzero(wmask) < 3:
            raise ValueError("Current detection window is too small")

        tw = t[wmask]
        vw = v[wmask]
        src_w = src_idx[wmask]

        fs = emg_service.infer_sampling_rate(t)
        params = dict(
            min_peak_distance_ms=float(dist if dist is not None else 100.0),
            min_width_ms=minw,
            wlen_ms=wlen,
            min_prominence_uV=prom,
            min_height_uV=height,
            use_adaptive_sigma=bool(adaptive_sigma),
            sigma_for_prom=float(sigma_prom if sigma_prom is not None else 1.0),
            sigma_for_height=float(sigma_height if sigma_height is not None else 1.0),
        )
        peaks_local, widths_ms, _ = emg_service.detect_with_polarity(
            vw,
            fs,
            params,
            polarity,
            self.find_peaks,
            self.peak_widths,
        )

        rows = []
        for index, peak_index_local in enumerate(peaks_local):
            dur_ms = float(widths_ms[index]) if index < len(widths_ms) else np.nan
            if dur is not None and dur_ms > dur:
                continue
            src_i = int(src_w[int(peak_index_local)])
            rows.append(
                {
                    "peak_idx": src_i,
                    "time": float(t_raw[src_i]),
                    "time_s": float(t_raw[src_i]),
                    "height": float(v_raw[src_i]),
                    "height_uV": float(v_raw[src_i]),
                    "duration": float(dur_ms),
                    "fwhm_ms": float(dur_ms),
                    "group": "",
                    "removed": False,
                }
            )
        rows.sort(key=lambda row: row.get("time_s", 0.0))

        fig, ax = new_subplots(figsize=(10, 3.5))
        ax.plot(tw, vw, color=self.line_color, lw=0.6)
        ax.scatter(
            [row["time"] for row in rows], [row["height"] for row in rows], color="#e06c00", s=18
        )
        ax.set_xlabel(t_col)
        ax.set_ylabel(v_col)
        ax.grid(True, alpha=0.4)
        self._finish_trace_figure(fig)
        return {"img": self.fig_to_b64(fig), "peaks": rows}

    def load_csv_payload(self, path: str) -> dict[str, Any]:
        df = pd.read_csv(path)
        t_col, v_col = emg_service.pick_columns(df)
        t_raw, v_raw, valid = emg_service.numeric_signal(df, t_col, v_col)
        t = t_raw[valid]
        v = v_raw[valid]
        dsf = max(1, len(t) // 50000)
        fig, ax = new_subplots(figsize=(10, 3.5))
        ax.plot(t[::dsf], v[::dsf], color=self.line_color, lw=0.6)
        ax.set_xlabel(t_col)
        ax.set_ylabel(v_col)
        ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
        ax.grid(True, alpha=0.4)
        self._finish_trace_figure(fig)
        return {
            "img": self.fig_to_b64(fig),
            "t_col": t_col,
            "v_col": v_col,
            "duration_s": round(float(t[-1] - t[0]), 3),
        }

    def detect_peaks_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_scipy()

        path = data.get("path", "")
        height = self.float_or(data.get("height"), None)
        prom = self.float_or(data.get("prominence"), None)
        dist = self.float_or(data.get("distance", 100), 100)
        dur = self.float_or(data.get("duration"), None)

        df = pd.read_csv(path)
        t_col, v_col = emg_service.pick_columns(df)
        t_raw, v_raw, valid = emg_service.numeric_signal(df, t_col, v_col)
        t = t_raw[valid]
        v = v_raw[valid]
        fs = emg_service.infer_sampling_rate(t)
        dist_pts = max(1, emg_service.ms_to_samples(dist, fs))
        kwargs = {"distance": dist_pts}
        if height is not None:
            kwargs["height"] = height
        if prom is not None:
            kwargs["prominence"] = prom
        peaks, _ = self.find_peaks(np.abs(v), **kwargs)
        widths, _, _, _ = self.peak_widths(np.abs(v), peaks, rel_height=0.5)
        peak_rows = []
        for index, peak_index in enumerate(peaks):
            dur_ms = (widths[index] / fs) * 1000
            if dur is not None and dur_ms > dur:
                continue
            peak_rows.append(
                {
                    "idx": len(peak_rows),
                    "time_s": round(float(t[peak_index]), 5),
                    "height": round(float(v[peak_index]), 4),
                    "duration_ms": round(dur_ms, 2),
                    "group": "",
                    "removed": False,
                }
            )

        fig, ax = new_subplots(figsize=(10, 3.5))
        ax.plot(t, v, color=self.line_color, lw=0.6, zorder=1)
        ax.scatter(
            [row["time_s"] for row in peak_rows],
            [row["height"] for row in peak_rows],
            color="#e06c00",
            s=20,
            zorder=3,
        )
        ax.set_xlabel(t_col)
        ax.set_ylabel(v_col)
        ax.set_title(f"{Path(path).name} - {len(peak_rows)} peaks", fontsize=10, color="#5C5E62")
        ax.grid(True, alpha=0.4)
        self._finish_trace_figure(fig)
        return {"img": self.fig_to_b64(fig), "peaks": peak_rows}

    def grouped_export_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        peaks = data.get("peaks", [])
        mode = data.get("mode", "download")
        if not peaks:
            raise ValueError("No peaks to export")

        active = [peak for peak in peaks if not bool(peak.get("removed", False))]
        if not active:
            raise ValueError("No peaks to export after removals")

        df = pd.DataFrame(active)
        payload = df.to_csv(index=False).encode("utf-8")
        if self.mode_is_save(mode):
            src = emg_service.source_path(data)
            if src is not None and src.exists() and src.suffix.lower() == ".csv":
                return self._save_grouped_segments(src, data, active)

            out_path = (
                src.with_name(f"{src.stem}_peaks.csv")
                if src is not None
                else Path.cwd() / "emg_peaks.csv"
            )
            out_path.write_bytes(payload)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_path),
                    "outputs": self._peak_outputs(out_path, "emg_peaks_csv"),
                },
            }
        return {
            "kind": "download",
            "payload": payload,
            "mimetype": "text/csv",
            "download_name": "emg_peaks.csv",
        }

    def export_peaks_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        peaks = [peak for peak in data.get("peaks", []) if not peak.get("removed")]
        path = data.get("path", "")
        mode = data.get("mode", "download")
        if not peaks:
            raise ValueError("No peaks to export")
        df = pd.DataFrame(peaks)
        payload = df.to_csv(index=False).encode("utf-8")
        stem = Path(path).stem if path else "peaks"
        if self.mode_is_save(mode):
            src = Path(path) if path else emg_service.source_path(data)
            out_path = (
                src.with_name(f"{src.stem}_peaks_grouped.csv")
                if src is not None
                else Path.cwd() / f"{stem}_peaks_grouped.csv"
            )
            out_path.write_bytes(payload)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_path),
                    "outputs": self._peak_outputs(out_path, "emg_peaks_grouped_csv"),
                },
            }
        return {
            "kind": "download",
            "payload": payload,
            "mimetype": "text/csv",
            "download_name": f"{stem}_peaks.csv",
        }

    def _save_grouped_segments(
        self,
        src: Path,
        data: dict[str, Any],
        active_peaks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw = pd.read_csv(src)
        t_col, v_col = emg_service.pick_columns(raw)
        t_raw, v_raw, valid = emg_service.numeric_signal(raw, t_col, v_col)
        invert_signal = self._invert_signal_enabled(data)
        if invert_signal:
            v_raw = -v_raw
        t = t_raw[valid]
        v = v_raw[valid]

        half_ms = self.float_or(data.get("half_ms"), 100.0)
        if half_ms is None or half_ms <= 0:
            half_ms = 100.0
        half_s = float(half_ms) / 1000.0

        channel = emg_service.channel_label_from_source(src)
        summary_rows = []
        prepared = []
        nonbaseline_durations = []
        for peak in active_peaks:
            source_kind = str(peak.get("source_kind") or "").strip().lower()
            if bool(peak.get("baseline")) or source_kind.startswith("baseline"):
                continue
            duration = self.float_or(
                peak.get("duration", peak.get("duration_ms", peak.get("fwhm_ms"))), np.nan
            )
            if np.isfinite(duration) and duration > 0:
                nonbaseline_durations.append(float(duration))
        baseline_duration_ms = (
            float(np.median(nonbaseline_durations)) if nonbaseline_durations else 2.0
        )

        for peak in active_peaks:
            source_kind = str(peak.get("source_kind") or "").strip().lower()
            is_baseline = bool(peak.get("baseline")) or source_kind.startswith("baseline")
            baseline_source_start = self.float_or(
                peak.get("baseline_source_start_s", peak.get("baseline_start_s")), None
            )
            baseline_source_end = self.float_or(
                peak.get("baseline_source_end_s", peak.get("baseline_end_s")), None
            )
            if is_baseline and baseline_source_start is None:
                baseline_source_start = self.float_or(peak.get("segment_start_s"), None)
            if is_baseline and baseline_source_end is None:
                baseline_source_end = self.float_or(peak.get("segment_end_s"), None)
            if (
                baseline_source_start is not None
                and baseline_source_end is not None
                and baseline_source_end < baseline_source_start
            ):
                baseline_source_start, baseline_source_end = (
                    baseline_source_end,
                    baseline_source_start,
                )

            peak_index = (
                int(peak.get("peak_idx", peak.get("idx", -1)))
                if peak.get("peak_idx", peak.get("idx", None)) is not None
                else -1
            )
            peak_time = self.float_or(peak.get("time_s", peak.get("time")), None)
            if is_baseline and baseline_source_start is not None and baseline_source_end is not None:
                peak_time = (
                    float(peak_time)
                    if peak_time is not None
                    else (float(baseline_source_start) + float(baseline_source_end)) / 2.0
                )
            if peak_index < 0 or peak_index >= t.size:
                if peak_time is None:
                    continue
                peak_index = int(np.argmin(np.abs(t - peak_time)))
            if not is_baseline:
                peak_time = float(t[peak_index])
            segment_start = float(peak_time) - half_s
            segment_end = float(peak_time) + half_s
            group = str(peak.get("group", "")).strip()
            duration = self.float_or(
                peak.get("duration", peak.get("duration_ms", peak.get("fwhm_ms"))), np.nan
            )
            if is_baseline and (not np.isfinite(duration) or duration <= 0):
                duration = baseline_duration_ms
            height = (
                float(v[peak_index])
                if is_baseline
                else self.float_or(peak.get("height", peak.get("height_uV")), float(v[peak_index]))
            )
            baseline_fill_seed = peak.get("baseline_fill_seed", peak.get("baseline_seed"))
            baseline_rep = peak.get("baseline_rep")

            summary_rows.append(
                {
                    "peak_idx": int(peak_index),
                    "peak_time_s": peak_time,
                    "height_uV": float(height),
                    "fwhm_ms": float(duration),
                    "group_id": group,
                }
            )
            prepared.append(
                {
                    "peak_idx": int(peak_index),
                    "peak_time_s": float(peak_time),
                    "group_id": group,
                    "source_kind": "baseline" if is_baseline else "peak",
                    "segment_start_s": float(segment_start),
                    "segment_end_s": float(segment_end),
                    "baseline_source_start_s": baseline_source_start,
                    "baseline_source_end_s": baseline_source_end,
                    "baseline_fill_seed": baseline_fill_seed,
                    "baseline_rep": baseline_rep,
                }
            )

        summary_path = src.parent / f"{src.parent.name}_{channel}_peaks_summary.csv"
        pd.DataFrame(summary_rows).to_csv(summary_path, index=False)

        file_count = 0
        segment_paths = []
        segment_channels = []
        grouped = [row for row in prepared if str(row.get("group_id", "")).strip() != ""]
        if grouped:
            groups = {}
            for row in grouped:
                groups.setdefault(str(row["group_id"]), []).append(row)

            linked_sources = self._linked_channel_sources(src, data.get("linked_channels", []))
            for channel_src in [src] + linked_sources:
                channel_count, channel_paths = self._save_same_time_segments_for_channel(
                    channel_src, groups, half_s, invert_signal=invert_signal
                )
                if channel_count:
                    segment_channels.append(emg_service.channel_label_from_source(channel_src))
                file_count += channel_count
                segment_paths.extend(channel_paths)

        saved_paths = [str(summary_path)] + segment_paths
        return {
            "kind": "save",
            "data": {
                "ok": True,
                "saved_path": str(src.parent),
                "summary_path": str(summary_path),
                "segment_count": file_count,
                "segment_paths": segment_paths,
                "linked_channel_count": len(self._linked_channel_sources(src, data.get("linked_channels", []))),
                "segment_channels": segment_channels,
                "saved_paths": saved_paths,
                "inverted_signal": invert_signal,
                "outputs": self._peak_outputs(src.parent, "emg_peak_folder", saved_paths),
            },
        }

    def _save_same_time_segments_for_channel(
        self,
        channel_src: Path,
        groups: dict[str, list[dict[str, Any]]],
        half_s: float,
        *,
        invert_signal: bool = False,
    ) -> tuple[int, list[str]]:
        t, v, _t_col, _v_col = self._load_signal(
            channel_src, invert_signal=invert_signal
        )
        channel = emg_service.channel_label_from_source(channel_src)
        file_count = 0
        segment_paths = []
        for group_id, rows in groups.items():
            sorted_rows = sorted(rows, key=lambda row: row["peak_time_s"])
            group_dir = channel_src.parent / f"{emg_service.sanitize_name(group_id)}_{channel}"
            group_dir.mkdir(parents=True, exist_ok=True)
            for index, row in enumerate(sorted_rows):
                peak_time = float(row["peak_time_s"])
                source_kind = str(row.get("source_kind") or "peak")
                segment_start = self.float_or(row.get("segment_start_s"), None)
                segment_end = self.float_or(row.get("segment_end_s"), None)
                if segment_start is None or segment_end is None or segment_end <= segment_start:
                    segment_start = peak_time - half_s
                    segment_end = peak_time + half_s
                edge_eps = max(1e-12, np.finfo(float).eps * max(abs(segment_start), abs(segment_end), 1.0) * 64)
                mask = (t >= segment_start - edge_eps) & (t <= segment_end + edge_eps)
                if not np.any(mask):
                    continue
                out_file = group_dir / f"peak_{channel}_{index:04d}_t{peak_time:.6f}s.csv"
                pd.DataFrame(
                    {
                        "t_abs_s": t[mask],
                        "t_rel_ms": (t[mask] - peak_time) * 1e3,
                        "value_uV": v[mask],
                        "source_channel": channel,
                        "source_kind": source_kind,
                        "segment_start_s": float(segment_start),
                        "segment_end_s": float(segment_end),
                        "baseline_source_start_s": row.get("baseline_source_start_s"),
                        "baseline_source_end_s": row.get("baseline_source_end_s"),
                        "baseline_fill_seed": row.get("baseline_fill_seed"),
                        "baseline_rep": row.get("baseline_rep"),
                    }
                ).to_csv(out_file, index=False)
                file_count += 1
                segment_paths.append(str(out_file))
        return file_count, segment_paths

    @staticmethod
    def _load_signal(
        path: str | Path, *, invert_signal: bool = False
    ) -> tuple[np.ndarray, np.ndarray, str, str]:
        df = pd.read_csv(path)
        t_col, v_col = emg_service.pick_columns(df)
        t_raw, v_raw, valid = emg_service.numeric_signal(df, t_col, v_col)
        if invert_signal:
            v_raw = -v_raw
        return t_raw[valid], v_raw[valid], t_col, v_col

    def _load_windowed_signal(
        self,
        path: str | Path,
        x_min: float | None,
        x_max: float | None,
        *,
        invert_signal: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, str, str]:
        t, v, t_col, v_col = self._load_signal(path, invert_signal=invert_signal)
        if x_min is not None:
            mask = t >= x_min
            t, v = t[mask], v[mask]
        if x_max is not None:
            mask = t <= x_max
            t, v = t[mask], v[mask]
        return t, v, t_col, v_col
