import io
import traceback
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from flask import Response, jsonify, request

from services import rhd as rhd_service
from services import rhd_processing
from services.output_naming import sanitize_name_part

from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .response import api_ok


def register_rhd_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    browse_files_recursive = ctx["browse_files_recursive"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    line_color = ctx["LINE_COLOR"]
    has_rhd = ctx["HAS_RHD"]
    rhd = ctx.get("rhd")
    request_data = ctx["request_data"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _load_rhd_with_merge_option(path, do_merge):
        return rhd_service.load_with_merge_option(path, rhd, do_merge)

    def _load_rhd_metadata_with_merge_option(path, do_merge):
        return rhd_service.recording_metadata_with_merge_option(path, rhd, do_merge)

    def _load_rhd_channel_with_merge_option(path, channel, do_merge):
        return rhd_service.load_channel_with_merge_option(path, rhd, channel, do_merge)

    def _df_all_channels_wide(time_s, ch_names, amp):
        return rhd_service.all_channels_wide_frame(time_s, ch_names, amp)

    def _rhd_output(path: str | Path, role: str = "rhd_export") -> dict:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else (p.suffix.lower().lstrip(".") or "file"),
            "role": role,
        }

    def _rhd_recording_key(path: Path, do_merge: bool) -> tuple[str, ...]:
        if not do_merge:
            return (str(path),)
        return tuple(str(p) for p in rhd_service.recording_files_for_path(path, True))

    def _rhd_export_all_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("Intan RHD parser is not available")

        path = d.get("path", "")
        mode = d.get("mode", "download")
        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)
        src = Path(path)
        if not src.is_file():
            raise ValueError(f"RHD file not found: {path}")
        t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(src, do_merge)

        if _mode_is_save(mode):
            base_dir = src.parent
            if wide_csv:
                out_path = base_dir / f"{base_stem}.csv"
                dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                dfw.to_csv(out_path, index=False, sep="\t")
                return {
                    "kind": "save",
                    "data": {
                        "ok": True,
                        "saved_path": str(out_path),
                        "saved_paths": [str(out_path)],
                        "outputs": [_rhd_output(out_path, "rhd_all_channels_wide")],
                    },
                }

            target_dir = base_dir / base_stem
            target_dir.mkdir(parents=True, exist_ok=True)
            saved = 0
            saved_paths = []
            for i, name in enumerate(ch_all):
                out_path = target_dir / f"{base_stem}_{name}.csv"
                pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(out_path, index=False)
                saved += 1
                saved_paths.append(str(out_path))
            outputs = [_rhd_output(target_dir, "rhd_channel_folder")]
            outputs.extend(_rhd_output(path, "rhd_channel_csv") for path in saved_paths)
            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(target_dir),
                    "saved_count": saved,
                    "saved_paths": saved_paths,
                    "outputs": outputs,
                },
            }

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, name in enumerate(ch_all):
                csv_bytes = pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(
                    index=False
                ).encode("utf-8")
                zf.writestr(f"{base_stem}_{name}.csv", csv_bytes)
        out.seek(0)
        return {
            "kind": "download",
            "payload": out.getvalue(),
            "mimetype": "application/zip",
            "download_name": f"{base_stem}_all_channels.zip",
        }

    def _rhd_export_all_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting all RHD channels")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _rhd_export_all_payload(save_body)["data"]

    def _rhd_export_queue_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("Intan RHD parser is not available")

        paths = d.get("paths", [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("Queue is empty.")

        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)

        total = 0
        ok = 0
        warnings = []
        saved_paths = []
        processed_recordings = set()

        for raw in paths:
            total += 1
            p = Path(str(raw))
            try:
                recording_key = _rhd_recording_key(p, do_merge)
                if do_merge and recording_key in processed_recordings:
                    continue

                t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(p, do_merge)

                base_dir = p.parent
                if wide_csv:
                    out_path = base_dir / f"{base_stem}.csv"
                    dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                    dfw.to_csv(out_path, index=False, sep="\t")
                    saved_paths.append(str(out_path))
                else:
                    target_dir = base_dir / base_stem
                    target_dir.mkdir(parents=True, exist_ok=True)
                    for i, name in enumerate(ch_all):
                        out_path = target_dir / f"{base_stem}_{name}.csv"
                        pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(
                            out_path, index=False
                        )
                    saved_paths.append(str(target_dir))

                if do_merge:
                    processed_recordings.add(recording_key)
                ok += 1
            except Exception as e:
                warnings.append(f"{p}: {e}")

        return {
            "ok": True,
            "saved_count": ok,
            "total": total,
            "saved_paths": saved_paths,
            "warnings": warnings,
            "outputs": [_rhd_output(path, "rhd_queue_export") for path in saved_paths],
        }

    def _rhd_export_queue_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting RHD queue")
        return _rhd_export_queue_payload(body)

    def _rhd_processing_result(d: dict):
        path = d.get("path", "")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = rhd_processing.filter_params(d)

        src = Path(path)
        t, fs, _ch_names, y, _ch, ch_name, base_stem, used_pair, _segment_count = (
            _load_rhd_channel_with_merge_option(src, ch_in, do_merge)
        )
        t, y = rhd_processing.apply_time_window(t, y, x_min, x_max)
        y = rhd_processing.apply_filter(y, fs, filter_params)
        result = rhd_processing.process_trace(t, y, fs, d, default_line_color=line_color)
        out_stem = base_stem if used_pair else src.stem
        return src, out_stem, ch_name, result

    def _rhd_export_processing_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("Intan RHD parser is not available")

        fmt = str(d.get("fmt", "csv") or "csv").strip().lower()
        if fmt not in {"csv", "png", "svg"}:
            raise ValueError("Processing export format must be csv, png, or svg.")

        mode = d.get("mode", "download")
        src, out_stem, ch_name, result = _rhd_processing_result(d)
        stem = sanitize_name_part(f"{out_stem}_{ch_name}_{result.kind}", "rhd_processing")

        try:
            if fmt == "csv":
                payload = rhd_processing.dataframe_csv_bytes(result.table)
                mimetype = "text/csv"
            else:
                fig_params = rhd_processing.figure_params(
                    d,
                    default_line_color=line_color,
                    default_show_title=False,
                )
                payload = rhd_processing.figure_bytes(result.figure, fmt, dpi=fig_params["dpi"])
                mimetype = "image/png" if fmt == "png" else "image/svg+xml"
        finally:
            plt.close(result.figure)

        filename = f"{stem}.{fmt}"
        if _mode_is_save(mode):
            out_path = next_numbered_path(src.with_name(filename))
            out_path.write_bytes(payload)
            outputs = [_rhd_output(out_path, f"rhd_processing_{fmt}")]
            data = {
                "ok": True,
                "saved_path": str(out_path),
                "saved_paths": [str(out_path)],
                "outputs": outputs,
                "process_type": result.kind,
                **result.metadata,
            }
            return {
                "kind": "save",
                "data": data,
                "outputs": outputs,
            }

        return {
            "kind": "download",
            "payload": payload,
            "mimetype": mimetype,
            "download_name": filename,
            "metadata": result.metadata,
        }

    def _rhd_export_processing_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting RHD processing output")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        result = _rhd_export_processing_payload(save_body)
        return result["data"]

    @app.route("/api/rhd/browse", methods=["POST"])
    def api_rhd_browse():
        d = request.json or {}
        files = browse_files(d.get("folder", ""), {".rhd"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/rhd/browse_recursive", methods=["POST"])
    def api_rhd_browse_recursive():
        d = request.json or {}
        files = browse_files_recursive(d.get("folder", ""), {".rhd"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/rhd/load", methods=["POST"])
    def api_rhd_load():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        path = d.get("path", "")
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        try:
            return jsonify(_load_rhd_metadata_with_merge_option(Path(path), do_merge))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/plot", methods=["POST"])
    def api_rhd_plot():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        path = d.get("path", "")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        downsample = d.get("downsample", d.get("dsf", "auto"))
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = rhd_processing.filter_params(d)
        fig_params = rhd_processing.figure_params(
            d,
            default_line_color=line_color,
            default_show_title=True,
        )

        try:
            t, fs, _ch_names, y, ch, ch_label, base_stem, used_pair, _segment_count = (
                _load_rhd_channel_with_merge_option(Path(path), ch_in, do_merge)
            )
            t, y = rhd_processing.apply_time_window(t, y, x_min, x_max)
            y = rhd_processing.apply_filter(y, fs, filter_params)

            dsf = rhd_processing.downsample_factor(downsample, len(t))
            t_d = t[::dsf]
            y_d = y[::dsf]

            fig, ax = plt.subplots(
                figsize=(fig_params["width_in"], fig_params["height_in"]),
                dpi=fig_params["dpi"],
            )
            ax.plot(t_d, y_d, color=fig_params["line_color"], lw=fig_params["line_width"])
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            title_name = f"{base_stem} (merged)" if used_pair else Path(path).name
            if fig_params["show_title"]:
                ax.set_title(
                    f"{title_name} - Ch {ch}: {ch_label}",
                    fontsize=10,
                    color="#5C5E62",
                )
            rhd_processing.finish_axis(ax, t_d, y_min, y_max, grid=fig_params["show_grid"])
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig), "downsample": dsf, "plotted_points": int(len(t_d))})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/process", methods=["POST"])
    def api_rhd_process():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request.json or {}
        try:
            _src, _out_stem, _ch_name, result = _rhd_processing_result(d)
            return jsonify({"img": fig_to_b64(result.figure), **result.metadata})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_channel", methods=["GET", "POST"])
    def api_rhd_export_channel():
        if not has_rhd:
            return err("Intan RHD parser is not available")

        d = request_data()
        path = d.get("path", "")
        fmt = d.get("fmt", "csv")
        mode = d.get("mode", "download")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        downsample = d.get("downsample", d.get("dsf", "auto"))
        do_merge = _as_bool(d.get("merge_pair", d.get("preview_merge_pair")), False)
        filter_params = rhd_processing.filter_params(d)
        fig_params = rhd_processing.figure_params(
            d,
            default_line_color=line_color,
            default_show_title=False,
        )

        try:
            src = Path(path)
            t, fs, _ch_names, y, _ch, ch_name, base_stem, used_pair, _segment_count = (
                _load_rhd_channel_with_merge_option(src, ch_in, do_merge)
            )
            out_stem = base_stem if used_pair else src.stem

            if fmt == "csv":
                buf = io.BytesIO()
                pd.DataFrame({"time_s": t, "value_uV": y}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    out_path = src.with_name(f"{out_stem}_{ch_name}.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.csv"},
                )

            t_view, y_view = rhd_processing.apply_time_window(t, y, x_min, x_max)
            y_view = rhd_processing.apply_filter(y_view, fs, filter_params)
            dsf = rhd_processing.downsample_factor(downsample, len(t_view))
            t_view = t_view[::dsf]
            y_view = y_view[::dsf]

            if str(fmt).lower() == "svg":
                payload = clean_trace_svg(
                    t_view,
                    y_view,
                    y_min=y_min,
                    y_max=y_max,
                    width=fig_params["width_in"] * 72.0,
                    height=fig_params["height_in"] * 72.0,
                    line_color=fig_params["line_color"],
                    line_width=fig_params["line_width"],
                )
                if _mode_is_save(mode):
                    base_path = src.with_name(f"{out_stem}_{ch_name}.svg")
                    out_path = next_numbered_path(base_path)
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="image/svg+xml",
                    headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.svg"},
                )

            fig, ax = plt.subplots(
                figsize=(fig_params["width_in"], fig_params["height_in"]),
                dpi=fig_params["dpi"],
            )
            ax.plot(t_view, y_view, color=fig_params["line_color"], lw=fig_params["line_width"])
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            if fig_params["show_title"]:
                title_name = f"{out_stem} (merged)" if used_pair else src.name
                ax.set_title(f"{title_name} - {ch_name}", fontsize=10, color="#5C5E62")
            rhd_processing.finish_axis(ax, t_view, y_min, y_max, grid=fig_params["show_grid"])
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, dpi=fig_params["dpi"] if fmt == "png" else None, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            payload = buf.getvalue()
            if _mode_is_save(mode):
                out_path = next_numbered_path(src.with_name(f"{out_stem}_{ch_name}.{fmt}"))
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            mt = "image/png" if fmt == "png" else "image/svg+xml"
            return Response(
                payload,
                mimetype=mt,
                headers={"Content-Disposition": f"attachment; filename={out_stem}_{ch_name}.{fmt}"},
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_processing", methods=["GET", "POST"])
    def api_rhd_export_processing():
        d = request_data()
        try:
            result = _rhd_export_processing_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=result["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_processing_job", methods=["POST"])
    def api_rhd_export_processing_job():
        return submit_json_task(
            jobs,
            "rhd.export_processing",
            "Export RHD processing output",
            _rhd_export_processing_task,
            request.json or {},
            metadata={"endpoint": "/api/rhd/export_processing"},
        )

    @app.route("/api/rhd/export_all", methods=["POST"])
    def api_rhd_export_all():
        d = request.json or {}
        try:
            result = _rhd_export_all_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"], warnings=data.get("warnings"))
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_all_job", methods=["POST"])
    def api_rhd_export_all_job():
        return submit_json_task(
            jobs,
            "rhd.export_all",
            "Export all RHD channels",
            _rhd_export_all_task,
            request.json or {},
            metadata={"endpoint": "/api/rhd/export_all"},
        )

    @app.route("/api/rhd/export_queue", methods=["POST"])
    def api_rhd_export_queue():
        d = request.json or {}
        try:
            result = _rhd_export_queue_payload(d)
            return api_ok(result, outputs=result["outputs"], warnings=result.get("warnings"))
        except ValueError as exc:
            return err(str(exc))

    @app.route("/api/rhd/export_queue_job", methods=["POST"])
    def api_rhd_export_queue_job():
        return submit_json_task(
            jobs,
            "rhd.export_queue",
            "Export RHD queue",
            _rhd_export_queue_task,
            request.json or {},
            metadata={"endpoint": "/api/rhd/export_queue"},
        )
