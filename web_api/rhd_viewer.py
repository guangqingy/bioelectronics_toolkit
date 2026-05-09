import io
import traceback
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from flask import Response, jsonify, request

from services import rhd as rhd_service

from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .response import api_ok


def register_rhd_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    browse_files_recursive = ctx["browse_files_recursive"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    has_rhd = ctx["HAS_RHD"]
    rhd = ctx.get("rhd")
    request_data = ctx["request_data"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _resolve_channel_index(result, ch_in):
        return rhd_service.resolve_channel_index(result, ch_in, default=0)

    def _load_rhd_with_merge_option(path, do_merge):
        return rhd_service.load_with_merge_option(path, rhd, do_merge)

    def _df_all_channels_wide(time_s, ch_names, amp):
        return rhd_service.all_channels_wide_frame(time_s, ch_names, amp)

    def _rhd_output(path: str | Path, role: str = "rhd_export") -> dict:
        p = Path(path)
        return {
            "path": str(p),
            "type": "directory" if p.is_dir() else "csv",
            "role": role,
        }

    def _rhd_export_all_payload(d: dict) -> dict:
        if not has_rhd:
            raise ValueError("importrhdutilities.py not found")

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
            raise ValueError("importrhdutilities.py not found")

        paths = d.get("paths", [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("Queue is empty.")

        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)

        total = 0
        ok = 0
        warnings = []
        saved_paths = []
        processed_bases = set()

        for raw in paths:
            total += 1
            p = Path(str(raw))
            try:
                t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(p, do_merge)

                if do_merge and base_stem in processed_bases:
                    continue

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
                    processed_bases.add(base_stem)
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
            return err("importrhdutilities.py not found in DataProcess folder")

        path = (request.json or {}).get("path", "")
        try:
            _t, fs, ch_names, amp, data = rhd_service.load_rhd_arrays(Path(path), rhd)
            ch_list = []
            if "amplifier_channels" in data:
                for i, ch in enumerate(data["amplifier_channels"]):
                    name = ch.get("custom_channel_name") or f"ch{i}"
                    ch_list.append(
                        {
                            "idx": i,
                            "name": name,
                            "native_name": ch.get("native_channel_name", f"ch{i}"),
                            "label": name,
                            "type": "amplifier",
                        }
                    )

            n_samples = amp.shape[1]
            duration = round(n_samples / fs, 2) if fs > 0 else 0

            return jsonify(
                {
                    "channels": ch_names,
                    "channels_meta": ch_list,
                    "sample_rate": fs,
                    "sampling_rate": fs,
                    "n_samples": n_samples,
                    "duration_s": duration,
                    "duration": duration,
                    "num_amplifiers": len(ch_list),
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/plot", methods=["POST"])
    def api_rhd_plot():
        if not has_rhd:
            return err("importrhdutilities.py not found")

        d = request.json or {}
        path = d.get("path", "")
        ch_in = d.get("channel", 0)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)

        try:
            t, fs, _ch_names, amp_data, data = rhd_service.load_rhd_arrays(Path(path), rhd)
            ch = _resolve_channel_index(data, ch_in)
            ch = max(0, min(ch, amp_data.shape[0] - 1))

            y = amp_data[ch]

            if x_min is not None:
                mask = t >= x_min
                t, y = t[mask], y[mask]
            if x_max is not None:
                mask = t <= x_max
                t, y = t[mask], y[mask]

            dsf = max(1, len(t) // 50000)
            t_d = t[::dsf]
            y_d = y[::dsf]

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t_d, y_d, color=line_color, lw=0.6)
            ch_info = data["amplifier_channels"][ch]
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            ax.set_title(
                f"{Path(path).name} - Ch {ch}: {ch_info.get('custom_channel_name') or f'ch{ch}'}",
                fontsize=10,
                color="#5C5E62",
            )
            ax.grid(True, alpha=0.4)
            apply_axes_limits(ax, None, None, y_min, y_max)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_channel", methods=["GET", "POST"])
    def api_rhd_export_channel():
        if not has_rhd:
            return err("importrhdutilities.py not found")

        d = request_data()
        path = d.get("path", "")
        fmt = d.get("fmt", "csv")
        mode = d.get("mode", "download")
        ch_in = d.get("channel", 0)

        try:
            src = Path(path)
            t, _fs, _ch_names, amp_data, data = rhd_service.load_rhd_arrays(src, rhd)
            ch = _resolve_channel_index(data, ch_in)
            ch = max(0, min(ch, amp_data.shape[0] - 1))

            y = amp_data[ch]
            ch_name = data["amplifier_channels"][ch].get("custom_channel_name") or f"ch{ch}"

            if fmt == "csv":
                buf = io.BytesIO()
                pd.DataFrame({"time_s": t, "value_uV": y}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    out_path = src.with_name(f"{src.stem}_{ch_name}.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={Path(path).stem}_{ch_name}.csv"},
                )

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t, y, color=line_color, lw=0.6)
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Amplitude (uV)")
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, dpi=300 if fmt == "png" else None, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            payload = buf.getvalue()
            if _mode_is_save(mode):
                out_path = src.with_name(f"{src.stem}_{ch_name}.{fmt}")
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            mt = "image/png" if fmt == "png" else "image/svg+xml"
            return Response(
                payload,
                mimetype=mt,
                headers={"Content-Disposition": f"attachment; filename={Path(path).stem}_{ch_name}.{fmt}"},
            )
        except Exception:
            return err(traceback.format_exc())

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
