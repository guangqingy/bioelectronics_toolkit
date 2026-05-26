from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import pandas as pd

from services import rhd as rhd_service
from services import rhd_processing
from services.output_naming import sanitize_name_part


@dataclass(slots=True)
class RhdViewerService:
    """Payload builders for the RHD viewer Web API."""

    has_rhd: bool
    rhd_module: Any
    fig_to_b64: Callable[[Any], str]
    float_or: Callable[[Any, float | None], float | None]
    bool_value: Callable[[Any], bool]
    mode_is_save: Callable[[Any], bool]
    clean_trace_svg: Callable[..., bytes]
    next_numbered_path: Callable[[Path], Path]
    line_color: str

    def metadata_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        path = Path(str(data.get("path", "") or "").strip())
        do_merge = self._merge_enabled(data, default=False)
        return rhd_service.recording_metadata_with_merge_option(path, self.rhd_module, do_merge)

    def plot_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        t, _fs, _ch_names, y, ch, ch_label, base_stem, used_pair, _segment_count = (
            self._load_view_trace(data)
        )
        y_min = self.float_or(data.get("y_min"), None)
        y_max = self.float_or(data.get("y_max"), None)
        downsample = data.get("downsample", data.get("dsf", "auto"))
        fig_params = rhd_processing.figure_params(
            data, default_line_color=self.line_color, default_show_title=True
        )
        dsf = rhd_processing.downsample_factor(downsample, len(t))
        t_d = t[::dsf]
        y_d = y[::dsf]

        fig, ax = plt.subplots(
            figsize=(fig_params["width_in"], fig_params["height_in"]), dpi=fig_params["dpi"]
        )
        try:
            ax.plot(t_d, y_d, color=fig_params["line_color"], lw=fig_params["line_width"])
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            if fig_params["show_title"]:
                title_name = (
                    f"{base_stem} (merged)" if used_pair else Path(data.get("path", "")).name
                )
                ax.set_title(f"{title_name} - Ch {ch}: {ch_label}", fontsize=10, color="#5C5E62")
            rhd_processing.finish_axis(ax, t_d, y_min, y_max, grid=fig_params["show_grid"])
            fig.tight_layout()
            return {
                "img": self.fig_to_b64(fig),
                "downsample": dsf,
                "plotted_points": int(len(t_d)),
                "inverted_y": rhd_processing.y_inversion_enabled(data),
            }
        finally:
            plt.close(fig)

    def processing_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        _src, _out_stem, _ch_name, result = self._processing_result(data)
        try:
            return {"img": self.fig_to_b64(result.figure), **result.metadata}
        finally:
            plt.close(result.figure)

    def export_channel_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        src, out_stem, ch_name, t, fs, y = self._load_export_trace(data)
        fmt = str(data.get("fmt", "csv") or "csv").lower()
        mode = data.get("mode", "download")

        if fmt == "csv":
            y = rhd_processing.apply_y_polarity(y, data)
            payload = self._trace_csv_bytes(t, y)
            filename = f"{out_stem}_{ch_name}.csv"
            if self.mode_is_save(mode):
                out_path = src.with_name(filename)
                out_path.write_bytes(payload)
                return self._save_result(out_path, "rhd_channel_csv")
            return self._download_result(payload, "text/csv", filename)

        t_view, y_view = self._filtered_export_view(data, t, fs, y)
        fig_params = rhd_processing.figure_params(
            data, default_line_color=self.line_color, default_show_title=False
        )
        filename = f"{out_stem}_{ch_name}.{fmt}"

        if fmt == "svg":
            payload = self.clean_trace_svg(
                t_view,
                y_view,
                y_min=self.float_or(data.get("y_min"), None),
                y_max=self.float_or(data.get("y_max"), None),
                width=fig_params["width_in"] * 72.0,
                height=fig_params["height_in"] * 72.0,
                line_color=fig_params["line_color"],
                line_width=fig_params["line_width"],
            )
            if self.mode_is_save(mode):
                out_path = self.next_numbered_path(src.with_name(filename))
                out_path.write_bytes(payload)
                return self._save_result(out_path, "rhd_channel_svg")
            return self._download_result(payload, "image/svg+xml", filename)

        fig, ax = plt.subplots(
            figsize=(fig_params["width_in"], fig_params["height_in"]), dpi=fig_params["dpi"]
        )
        try:
            ax.plot(t_view, y_view, color=fig_params["line_color"], lw=fig_params["line_width"])
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            if fig_params["show_title"]:
                title_name = f"{out_stem} (merged)" if data.get("_used_pair") else src.name
                ax.set_title(f"{title_name} - {ch_name}", fontsize=10, color="#5C5E62")
            rhd_processing.finish_axis(
                ax,
                t_view,
                self.float_or(data.get("y_min"), None),
                self.float_or(data.get("y_max"), None),
                grid=fig_params["show_grid"],
            )
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(
                buf,
                format=fmt,
                dpi=fig_params["dpi"] if fmt == "png" else None,
                bbox_inches="tight",
            )
            payload = buf.getvalue()
        finally:
            plt.close(fig)

        if self.mode_is_save(mode):
            out_path = self.next_numbered_path(src.with_name(filename))
            out_path.write_bytes(payload)
            return self._save_result(out_path, "rhd_channel_export")
        mimetype = "image/png" if fmt == "png" else "image/svg+xml"
        return self._download_result(payload, mimetype, filename)

    def export_processing_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        fmt = str(data.get("fmt", "csv") or "csv").strip().lower()
        if fmt not in {"csv", "png", "svg"}:
            raise ValueError("Processing export format must be csv, png, or svg.")

        mode = data.get("mode", "download")
        src, out_stem, ch_name, result = self._processing_result(data)
        stem = sanitize_name_part(f"{out_stem}_{ch_name}_{result.kind}", "rhd_processing")
        try:
            if fmt == "csv":
                payload = rhd_processing.dataframe_csv_bytes(result.table)
                mimetype = "text/csv"
            else:
                fig_params = rhd_processing.figure_params(
                    data, default_line_color=self.line_color, default_show_title=False
                )
                payload = rhd_processing.figure_bytes(result.figure, fmt, dpi=fig_params["dpi"])
                mimetype = "image/png" if fmt == "png" else "image/svg+xml"
        finally:
            plt.close(result.figure)

        filename = f"{stem}.{fmt}"
        if self.mode_is_save(mode):
            out_path = self.next_numbered_path(src.with_name(filename))
            out_path.write_bytes(payload)
            outputs = [self._output(out_path, f"rhd_processing_{fmt}")]
            data_out = {
                "ok": True,
                "saved_path": str(out_path),
                "saved_paths": [str(out_path)],
                "outputs": outputs,
                "process_type": result.kind,
                **result.metadata,
            }
            return {"kind": "save", "data": data_out, "outputs": outputs}
        return self._download_result(payload, mimetype, filename, metadata=result.metadata)

    def export_processing_job_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        save_body = dict(data or {})
        save_body["mode"] = "save"
        return self.export_processing_payload(save_body)["data"]

    def export_all_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        src = Path(str(data.get("path", "") or "").strip())
        if not src.is_file():
            raise ValueError(f"RHD file not found: {src}")
        do_merge = self.bool_value(data.get("merge_pair"))
        wide_csv = self.bool_value(data.get("wide_csv"))
        t_all, _fs, ch_all, amp_all, base_stem, _used_pair = rhd_service.load_with_merge_option(
            src, self.rhd_module, do_merge
        )

        if self.mode_is_save(data.get("mode", "download")):
            return self._save_all_channels(src, base_stem, t_all, ch_all, amp_all, wide_csv)

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, name in enumerate(ch_all):
                zf.writestr(
                    f"{base_stem}_{name}.csv",
                    pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]})
                    .to_csv(index=False)
                    .encode("utf-8"),
                )
        return self._download_result(
            out.getvalue(), "application/zip", f"{base_stem}_all_channels.zip"
        )

    def export_all_job_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        save_body = dict(data or {})
        save_body["mode"] = "save"
        return self.export_all_payload(save_body)["data"]

    def export_queue_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        self._require_rhd()
        paths = data.get("paths", [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("Queue is empty.")

        do_merge = self.bool_value(data.get("merge_pair"))
        wide_csv = self.bool_value(data.get("wide_csv"))
        total, ok = 0, 0
        warnings: list[str] = []
        saved_paths: list[str] = []
        processed_recordings: set[tuple[str, ...]] = set()

        for raw in paths:
            total += 1
            p = Path(str(raw))
            try:
                recording_key = self._recording_key(p, do_merge)
                if do_merge and recording_key in processed_recordings:
                    continue
                t_all, _fs, ch_all, amp_all, base_stem, _used_pair = (
                    rhd_service.load_with_merge_option(p, self.rhd_module, do_merge)
                )
                written = self._write_all_channels(p, base_stem, t_all, ch_all, amp_all, wide_csv)
                saved_paths.append(written[0])
                if do_merge:
                    processed_recordings.add(recording_key)
                ok += 1
            except Exception as exc:
                warnings.append(f"{p}: {exc}")

        return {
            "ok": True,
            "saved_count": ok,
            "total": total,
            "saved_paths": saved_paths,
            "warnings": warnings,
            "outputs": [self._output(path, "rhd_queue_export") for path in saved_paths],
        }

    def _require_rhd(self) -> None:
        if not self.has_rhd or self.rhd_module is None:
            raise ValueError("Intan RHD parser is not available")

    def _merge_enabled(self, data: dict[str, Any], *, default: bool) -> bool:
        return self.bool_value(data.get("merge_pair", data.get("preview_merge_pair", default)))

    def _load_view_trace(self, data: dict[str, Any]):
        path = Path(str(data.get("path", "") or "").strip())
        ch_in = data.get("channel", 0)
        do_merge = self._merge_enabled(data, default=False)
        t, fs, ch_names, y, ch, ch_label, base_stem, used_pair, segment_count = (
            rhd_service.load_channel_with_merge_option(path, self.rhd_module, ch_in, do_merge)
        )
        t, y = rhd_processing.apply_time_window(
            t, y, self.float_or(data.get("x_min"), None), self.float_or(data.get("x_max"), None)
        )
        y = rhd_processing.apply_filter(y, fs, rhd_processing.filter_params(data))
        y = rhd_processing.apply_y_polarity(y, data)
        return t, fs, ch_names, y, ch, ch_label, base_stem, used_pair, segment_count

    def _load_export_trace(self, data: dict[str, Any]):
        src = Path(str(data.get("path", "") or "").strip())
        t, fs, _ch_names, y, _ch, ch_name, base_stem, used_pair, _segment_count = (
            rhd_service.load_channel_with_merge_option(
                src,
                self.rhd_module,
                data.get("channel", 0),
                self._merge_enabled(data, default=False),
            )
        )
        data["_used_pair"] = used_pair
        return src, base_stem if used_pair else src.stem, ch_name, t, fs, y

    def _filtered_export_view(self, data: dict[str, Any], t, fs, y):
        t_view, y_view = rhd_processing.apply_time_window(
            t, y, self.float_or(data.get("x_min"), None), self.float_or(data.get("x_max"), None)
        )
        y_view = rhd_processing.apply_filter(y_view, fs, rhd_processing.filter_params(data))
        y_view = rhd_processing.apply_y_polarity(y_view, data)
        dsf = rhd_processing.downsample_factor(
            data.get("downsample", data.get("dsf", "auto")), len(t_view)
        )
        return t_view[::dsf], y_view[::dsf]

    def _processing_result(self, data: dict[str, Any]):
        src = Path(str(data.get("path", "") or "").strip())
        t, fs, _ch_names, y, _ch, ch_name, base_stem, used_pair, _segment_count = (
            self._load_view_trace(data)
        )
        result = rhd_processing.process_trace(t, y, fs, data, default_line_color=self.line_color)
        result.metadata["inverted_y"] = rhd_processing.y_inversion_enabled(data)
        return src, base_stem if used_pair else src.stem, ch_name, result

    def _save_all_channels(
        self, src: Path, base_stem: str, t_all, ch_all, amp_all, wide_csv: bool
    ) -> dict[str, Any]:
        saved_paths = self._write_all_channels(src, base_stem, t_all, ch_all, amp_all, wide_csv)
        if wide_csv:
            out_path = Path(saved_paths[0])
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(out_path),
                    "saved_paths": saved_paths,
                    "outputs": [self._output(out_path, "rhd_all_channels_wide")],
                },
            }
        target_dir = Path(saved_paths[0])
        outputs = [self._output(target_dir, "rhd_channel_folder")]
        outputs.extend(self._output(path, "rhd_channel_csv") for path in saved_paths[1:])
        return {
            "kind": "save",
            "data": {
                "ok": True,
                "saved_path": str(target_dir),
                "saved_count": len(saved_paths) - 1,
                "saved_paths": saved_paths[1:],
                "outputs": outputs,
            },
        }

    def _write_all_channels(
        self, src: Path, base_stem: str, t_all, ch_all, amp_all, wide_csv: bool
    ) -> list[str]:
        if wide_csv:
            out_path = src.parent / f"{base_stem}.csv"
            rhd_service.all_channels_wide_frame(t_all, ch_all, amp_all).to_csv(
                out_path, index=False, sep="\t"
            )
            return [str(out_path)]
        target_dir = src.parent / base_stem
        target_dir.mkdir(parents=True, exist_ok=True)
        saved_paths = [str(target_dir)]
        for i, name in enumerate(ch_all):
            out_path = target_dir / f"{base_stem}_{name}.csv"
            pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(out_path, index=False)
            saved_paths.append(str(out_path))
        return saved_paths

    @staticmethod
    def _trace_csv_bytes(t, y) -> bytes:
        buf = io.BytesIO()
        pd.DataFrame({"time_s": t, "value_uV": y}).to_csv(buf, index=False)
        return buf.getvalue()

    @staticmethod
    def _output(path: str | Path, role: str = "rhd_export") -> dict[str, str]:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else (p.suffix.lower().lstrip(".") or "file"),
            "role": role,
        }

    def _save_result(self, out_path: Path, role: str) -> dict[str, Any]:
        outputs = [self._output(out_path, role)]
        return {
            "kind": "save",
            "data": {
                "ok": True,
                "saved_path": str(out_path),
                "saved_paths": [str(out_path)],
                "outputs": outputs,
            },
            "outputs": outputs,
        }

    @staticmethod
    def _download_result(payload: bytes, mimetype: str, filename: str, **extra) -> dict[str, Any]:
        return {
            "kind": "download",
            "payload": payload,
            "mimetype": mimetype,
            "download_name": filename,
            **extra,
        }

    @staticmethod
    def _recording_key(path: Path, do_merge: bool) -> tuple[str, ...]:
        if not do_merge:
            return (str(path),)
        return tuple(str(p) for p in rhd_service.recording_files_for_path(path, True))
