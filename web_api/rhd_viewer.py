import io
import traceback
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify, request


from web_api.common import as_bool, mode_is_save
from .jobs import submit_flask_route_job


def register_rhd_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    browse_files_recursive = ctx["browse_files_recursive"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    has_rhd = ctx["HAS_RHD"]
    rhd = ctx.get("rhd")
    request_data = ctx["request_data"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _channel_display_names(result):
        names = []
        for i, ch in enumerate(result.get("amplifier_channels", [])):
            nm = ch.get("custom_channel_name")
            if not nm:
                nm = f"ch{i}"
            names.append(str(nm))
        return names

    def _channel_native_names(result):
        names = []
        for i, ch in enumerate(result.get("amplifier_channels", [])):
            nm = ch.get("native_channel_name") or f"ch{i}"
            names.append(str(nm))
        return names

    def _resolve_channel_index(result, ch_in):
        if isinstance(ch_in, str) and not ch_in.isdigit():
            names_disp = _channel_display_names(result)
            if ch_in in names_disp:
                return names_disp.index(ch_in)
            names_native = _channel_native_names(result)
            if ch_in in names_native:
                return names_native.index(ch_in)
            return 0
        return int_or(ch_in, 0)

    def _find_split_partner(path):
        stem = path.stem
        if len(stem) < 4:
            return None, None
        last4 = stem[-4:]
        if not last4.isdigit():
            return None, None
        cur_val = int(last4)

        candidates = []
        for delta in (-100, 100):
            target = cur_val + delta
            if 0 <= target <= 9999:
                target_str = f"{target:04d}"
                cand_stem = stem[:-4] + target_str
                cand_path = path.with_name(cand_stem + path.suffix)
                if cand_path.exists():
                    candidates.append(cand_path)

        if not candidates:
            return None, None

        filtered = []
        for q in candidates:
            if len(q.stem) == len(stem) and q.stem[:-4] == stem[:-4]:
                filtered.append(q)

        if not filtered:
            return None, None

        partner = filtered[0]
        cur_last4 = int(stem[-4:])
        ptn_last4 = int(partner.stem[-4:])

        if abs(ptn_last4 - cur_last4) != 100:
            return None, None

        earlier = path if cur_last4 < ptn_last4 else partner
        later = partner if cur_last4 < ptn_last4 else path
        return earlier, later

    def _load_rhd_arrays(path):
        result, _ = rhd.load_file(str(path))
        fs = float(result.get("frequency_parameters", {}).get("amplifier_sample_rate", 0.0) or 0.0)
        amp = np.asarray(result.get("amplifier_data", np.empty((0, 0))), dtype=float)
        if amp.ndim != 2:
            raise RuntimeError("Amplifier data shape mismatch.")

        t_raw = result.get("t_amplifier", None)
        if t_raw is not None:
            t = np.asarray(t_raw, dtype=float)
            if t.ndim != 1 or t.size != amp.shape[1]:
                t = np.arange(amp.shape[1], dtype=float) / (fs if fs > 0 else 1.0)
        else:
            t = np.arange(amp.shape[1], dtype=float) / (fs if fs > 0 else 1.0)

        ch_names = _channel_display_names(result)
        if len(ch_names) != amp.shape[0]:
            ch_names = [f"ch{i}" for i in range(amp.shape[0])]

        return t, fs, ch_names, amp, result

    def _load_merged_if_pair(path):
        earlier, later = _find_split_partner(path)
        if earlier is None or later is None:
            t, fs, ch, amp, _ = _load_rhd_arrays(path)
            return t, fs, ch, amp, path.stem, False

        t1, fs1, ch1, a1, _ = _load_rhd_arrays(earlier)
        t2, fs2, ch2, a2, _ = _load_rhd_arrays(later)

        if abs(fs1 - fs2) > 1e-9 or len(ch1) != len(ch2) or any(x != y for x, y in zip(ch1, ch2)):
            t, fs, ch, amp, _ = _load_rhd_arrays(path)
            return t, fs, ch, amp, path.stem, False

        if fs1 > 0:
            dt = 1.0 / fs1
        elif t1.size > 1:
            dt = float(t1[1] - t1[0])
        else:
            dt = 0.0

        if t1.size > 0 and t2.size > 0:
            offset = float(t1[-1]) + dt - float(t2[0])
        else:
            offset = 0.0

        t_merged = np.concatenate([t1, t2 + offset], axis=0)
        a_merged = np.concatenate([a1, a2], axis=1)
        return t_merged, fs1, ch1, a_merged, earlier.stem, True

    def _load_rhd_with_merge_option(path, do_merge):
        if do_merge:
            return _load_merged_if_pair(path)
        t, fs, ch, amp, _ = _load_rhd_arrays(path)
        return t, fs, ch, amp, path.stem, False

    def _df_all_channels_wide(time_s, ch_names, amp):
        out = {"time": np.asarray(time_s, dtype=float)}
        for i, name in enumerate(ch_names):
            out[str(name)] = np.asarray(amp[i, :], dtype=float)
        return pd.DataFrame(out)

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
            data, _ = rhd.load_file(path)
            ch_list = []
            ch_names = []
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
                    ch_names.append(name)

            fs = data.get("frequency_parameters", {}).get("amplifier_sample_rate", 0)
            n_samples = data.get("amplifier_data", np.array([[]])).shape[1] if "amplifier_data" in data else 0
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
            data, _ = rhd.load_file(path)
            amp_data = data["amplifier_data"]
            ch = _resolve_channel_index(data, ch_in)
            ch = max(0, min(ch, amp_data.shape[0] - 1))

            fs = data["frequency_parameters"]["amplifier_sample_rate"]
            y = amp_data[ch]
            t = np.arange(len(y)) / fs

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
            data, _ = rhd.load_file(path)
            ch = _resolve_channel_index(data, ch_in)
            ch = max(0, min(ch, data["amplifier_data"].shape[0] - 1))

            fs = data["frequency_parameters"]["amplifier_sample_rate"]
            y = data["amplifier_data"][ch]
            t = np.arange(len(y)) / fs
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
        if not has_rhd:
            return err("importrhdutilities.py not found")

        d = request.json or {}
        path = d.get("path", "")
        mode = d.get("mode", "download")
        do_merge = _as_bool(d.get("merge_pair"), True)
        wide_csv = _as_bool(d.get("wide_csv"), False)
        try:
            src = Path(path)
            if not src.is_file():
                return err(f"RHD file not found: {path}")
            t_all, _fs, ch_all, amp_all, base_stem, _used_pair = _load_rhd_with_merge_option(src, do_merge)

            if _mode_is_save(mode):
                base_dir = src.parent
                if wide_csv:
                    out_path = base_dir / f"{base_stem}.csv"
                    dfw = _df_all_channels_wide(t_all, ch_all, amp_all)
                    dfw.to_csv(out_path, index=False, sep="\t")
                    return jsonify({"ok": True, "saved_path": str(out_path), "saved_paths": [str(out_path)]})

                target_dir = base_dir / base_stem
                target_dir.mkdir(parents=True, exist_ok=True)
                saved = 0
                saved_paths = []
                for i, name in enumerate(ch_all):
                    out_path = target_dir / f"{base_stem}_{name}.csv"
                    pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(out_path, index=False)
                    saved += 1
                    saved_paths.append(str(out_path))
                return jsonify({"ok": True, "saved_path": str(target_dir), "saved_count": saved, "saved_paths": saved_paths})

            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i, name in enumerate(ch_all):
                    csv_bytes = pd.DataFrame({"time_s": t_all, "value_uV": amp_all[i, :]}).to_csv(index=False).encode("utf-8")
                    zf.writestr(f"{base_stem}_{name}.csv", csv_bytes)
            out.seek(0)
            payload = out.getvalue()
            return Response(
                payload,
                mimetype="application/zip",
                headers={"Content-Disposition": f"attachment; filename={base_stem}_all_channels.zip"},
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/rhd/export_all_job", methods=["POST"])
    def api_rhd_export_all_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/rhd/export_all",
            "rhd.export_all",
            "Export all RHD channels",
            api_rhd_export_all,
            request.json or {},
        )

    @app.route("/api/rhd/export_queue", methods=["POST"])
    def api_rhd_export_queue():
        if not has_rhd:
            return err("importrhdutilities.py not found")

        d = request.json or {}
        paths = d.get("paths", [])
        if not isinstance(paths, list) or not paths:
            return err("Queue is empty.")

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

        return jsonify(
            {
                "ok": True,
                "saved_count": ok,
                "total": total,
                "saved_paths": saved_paths,
                "warnings": warnings,
            }
        )

    @app.route("/api/rhd/export_queue_job", methods=["POST"])
    def api_rhd_export_queue_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/rhd/export_queue",
            "rhd.export_queue",
            "Export RHD queue",
            api_rhd_export_queue,
            request.json or {},
        )
