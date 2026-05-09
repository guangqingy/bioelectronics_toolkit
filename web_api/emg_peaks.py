import io
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify, request


from web_api.common import mode_is_save
from .jobs import submit_flask_route_job


def register_emg_peaks_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    line_color = ctx["LINE_COLOR"]

    has_scipy = ctx["HAS_SCIPY"]
    find_peaks = ctx.get("find_peaks")
    peak_widths = ctx.get("peak_widths")
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save

    def _sanitize_name(s):
        s = str(s or "")
        out = []
        for c in s:
            out.append(c if (c.isalnum() or c in "-_") else "_")
        return "".join(out)

    def _emg_source_path(payload):
        path = payload.get("path")
        if path:
            return Path(path)
        folder = payload.get("folder", "")
        subfolder = payload.get("subfolder", "")
        channel = payload.get("channel", "")
        if folder and subfolder and channel:
            return Path(folder) / subfolder / channel
        return None

    def _channel_label_from_src(src):
        parent = src.parent.name
        stem = src.stem
        if stem.startswith(parent + "_"):
            label = stem[len(parent) + 1 :]
        else:
            label = stem
        return _sanitize_name(label) or "channel"

    def _pick_emg_columns(df):
        t_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
        v_col = next(
            (
                c
                for c in df.columns
                if any(k in c.lower() for k in ["value", "uv", "\u00b5v", "amp"])
            ),
            df.columns[1],
        )
        return t_col, v_col

    def _as_bool(v, default=False):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return bool(v)

    def _ms_to_samples(ms, fs):
        return int(round(float(ms) * 1e-3 * float(fs)))

    def _robust_noise_std(x):
        med = np.median(x)
        mad = np.median(np.abs(x - med))
        return float(1.4826 * mad)

    def _build_peak_kwargs(
        sig,
        fs,
        min_peak_distance_ms,
        min_width_ms,
        wlen_ms,
        min_prominence_uV,
        min_height_uV,
        use_adaptive_sigma,
        sigma_for_prom,
        sigma_for_height,
    ):
        kwargs = {"distance": max(1, _ms_to_samples(min_peak_distance_ms, fs))}

        if min_width_ms is not None and min_width_ms > 0:
            kwargs["width"] = max(1, _ms_to_samples(min_width_ms, fs))

        if wlen_ms is not None and wlen_ms > 0:
            w = _ms_to_samples(wlen_ms, fs)
            if 3 <= w < sig.size:
                kwargs["wlen"] = w

        prom_thr = (
            None
            if (min_prominence_uV is None or min_prominence_uV <= 0)
            else float(min_prominence_uV)
        )
        height_thr = None if (min_height_uV is None) else float(min_height_uV)

        if use_adaptive_sigma:
            sigma = _robust_noise_std(sig)
            med = float(np.median(sig))
            prom_adapt = (
                (sigma_for_prom or 0) * sigma if (sigma_for_prom and sigma_for_prom > 0) else None
            )
            height_adapt = (
                med + (sigma_for_height or 0) * sigma
                if (sigma_for_height and sigma_for_height > 0)
                else None
            )
            if prom_adapt is not None:
                prom_thr = prom_adapt if prom_thr is None else max(prom_thr, prom_adapt)
            if height_adapt is not None:
                height_thr = height_adapt if height_thr is None else max(height_thr, height_adapt)

        if prom_thr is not None:
            kwargs["prominence"] = prom_thr
        if height_thr is not None:
            kwargs["height"] = height_thr
        return kwargs

    def _detect_with_polarity(sig, fs, params, polarity):
        pos_idx = np.array([], dtype=int)
        pos_w_ms = np.array([], dtype=float)
        neg_idx = np.array([], dtype=int)
        neg_w_ms = np.array([], dtype=float)

        if polarity in ("positive", "both"):
            kw_pos = _build_peak_kwargs(sig, fs, **params)
            pos_idx, _ = find_peaks(sig, **kw_pos)
            if pos_idx.size:
                w_s, _, _, _ = peak_widths(sig, pos_idx, rel_height=0.5)
                pos_w_ms = (w_s / fs) * 1e3

        if polarity in ("negative", "both"):
            inv = -sig
            kw_neg = _build_peak_kwargs(inv, fs, **params)
            neg_idx, _ = find_peaks(inv, **kw_neg)
            if neg_idx.size:
                w_s, _, _, _ = peak_widths(inv, neg_idx, rel_height=0.5)
                neg_w_ms = (w_s / fs) * 1e3

        if polarity != "both":
            idx = pos_idx if polarity == "positive" else neg_idx
            w_ms = pos_w_ms if polarity == "positive" else neg_w_ms
            sgn = (
                np.ones_like(idx, dtype=int)
                if polarity == "positive"
                else -np.ones_like(idx, dtype=int)
            )
            return idx, w_ms, sgn

        all_idx = np.concatenate([pos_idx, neg_idx])
        all_sgn = np.concatenate([np.ones_like(pos_idx, dtype=int), -np.ones_like(neg_idx, dtype=int)])
        all_w = np.concatenate([pos_w_ms, neg_w_ms])

        if all_idx.size == 0:
            return all_idx, all_w, all_sgn

        order = np.argsort(all_idx)
        all_idx = all_idx[order]
        all_sgn = all_sgn[order]
        all_w = all_w[order]

        keep = np.ones(all_idx.size, dtype=bool)
        min_dist = _ms_to_samples(params["min_peak_distance_ms"], fs)
        for i in range(all_idx.size):
            if not keep[i]:
                continue
            j = i + 1
            while j < all_idx.size and (all_idx[j] - all_idx[i]) < min_dist:
                ai = abs(sig[all_idx[i]])
                aj = abs(sig[all_idx[j]])
                if aj > ai:
                    keep[i] = False
                    break
                keep[j] = False
                j += 1

        return all_idx[keep], all_w[keep], all_sgn[keep]

    @app.route("/api/emg/browse", methods=["POST"])
    def api_emg_browse():
        d = request.json or {}
        subs = []
        folder = d.get("folder", "")
        p = Path(folder)
        if p.is_dir():
            for sub in sorted(p.iterdir()):
                csvs = list(sub.glob("*.csv")) if sub.is_dir() else []
                if csvs:
                    subs.append(
                        {
                            "name": sub.name,
                            "path": str(sub),
                            "csvs": [{"name": c.name, "path": str(c)} for c in sorted(csvs)],
                        }
                    )
        return jsonify({"subfolders": [s["name"] for s in subs], "subfolders_meta": subs})

    @app.route("/api/emg/load_channels", methods=["POST"])
    def api_emg_load_channels_compat():
        d = request.json or {}
        folder = d.get("folder", "")
        subfolder = d.get("subfolder", "")
        p = Path(folder) / subfolder
        if not p.is_dir():
            return jsonify({"channels": []})
        csvs = sorted(p.glob("*.csv"))
        return jsonify({"channels": [c.name for c in csvs]})

    @app.route("/api/emg/load", methods=["POST"])
    def api_emg_load_compat():
        d = request.json or {}
        folder = d.get("folder", "")
        subfolder = d.get("subfolder", "")
        channel = d.get("channel", "")
        path = str(Path(folder) / subfolder / channel)
        try:
            df = pd.read_csv(path)
            t_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
            t = df[t_col].values.astype(float)
            return jsonify({"duration": round(float(t[-1] - t[0]), 3) if len(t) else 0})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/plot", methods=["POST"])
    def api_emg_plot_compat():
        d = request.json or {}
        path = d.get("path", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        try:
            df = pd.read_csv(path)
            t_col, v_col = _pick_emg_columns(df)
            t = df[t_col].values.astype(float)
            v = df[v_col].values.astype(float)
            if x_min is not None:
                m = t >= x_min
                t, v = t[m], v[m]
            if x_max is not None:
                m = t <= x_max
                t, v = t[m], v[m]
            dsf = max(1, len(t) // 50000)
            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t[::dsf], v[::dsf], color=line_color, lw=0.6)
            ax.set_xlabel(t_col)
            ax.set_ylabel(v_col)
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect", methods=["POST"])
    def api_emg_detect_compat():
        if not has_scipy or find_peaks is None or peak_widths is None:
            return err("scipy not installed")
        d = request.json or {}
        path = d.get("path", "")
        height = float_or(d.get("pk_height"), None)
        prom = float_or(d.get("pk_prom"), None)
        dist = float_or(d.get("pk_dist", 100), 100)
        minw = float_or(d.get("pk_minw"), None)
        wlen = float_or(d.get("pk_wlen"), None)
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        polarity = str(d.get("polarity", "both")).strip().lower()
        if polarity not in {"positive", "negative", "both"}:
            polarity = "both"
        adaptive_sigma = _as_bool(d.get("adaptive_sigma"), False)
        sigma_prom = float_or(d.get("sigma_prom"), 1.0)
        sigma_height = float_or(d.get("sigma_height"), 1.0)
        dur = float_or(d.get("pk_dur"), None)
        try:
            df = pd.read_csv(path)
            t_col, v_col = _pick_emg_columns(df)
            t_raw = pd.to_numeric(df[t_col], errors="coerce").to_numpy()
            v_raw = pd.to_numeric(df[v_col], errors="coerce").to_numpy()
            valid = np.isfinite(t_raw) & np.isfinite(v_raw)
            if np.count_nonzero(valid) < 3:
                return err("Not enough numeric rows in source CSV")

            t = t_raw[valid]
            v = v_raw[valid]
            src_idx = np.arange(len(t_raw))[valid]

            wmask = np.ones_like(t, dtype=bool)
            if x_min is not None:
                wmask &= t >= x_min
            if x_max is not None:
                wmask &= t <= x_max
            if np.count_nonzero(wmask) < 3:
                return err("Current detection window is too small")

            tw = t[wmask]
            vw = v[wmask]
            src_w = src_idx[wmask]

            dt = np.diff(t)
            dt = dt[np.isfinite(dt) & (dt > 0)]
            if dt.size == 0:
                return err("Cannot infer sampling rate from time column")
            fs = float(1.0 / np.median(dt))

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

            peaks_local, widths_ms, _ = _detect_with_polarity(vw, fs, params, polarity)

            rows = []
            for i, pi_local in enumerate(peaks_local):
                dur_ms = float(widths_ms[i]) if i < len(widths_ms) else np.nan
                if dur is not None and dur_ms > dur:
                    continue
                src_i = int(src_w[int(pi_local)])
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
            rows.sort(key=lambda r: r.get("time_s", 0.0))

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(tw, vw, color=line_color, lw=0.6)
            ax.scatter([r["time"] for r in rows], [r["height"] for r in rows], color="#e06c00", s=18)
            ax.set_xlabel(t_col)
            ax.set_ylabel(v_col)
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig), "peaks": rows})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export", methods=["POST"])
    def api_emg_export_compat():
        d = request.json or {}
        peaks = d.get("peaks", [])
        mode = d.get("mode", "download")
        if not peaks:
            return err("No peaks to export")

        active = [p for p in peaks if not bool(p.get("removed", False))]
        if not active:
            return err("No peaks to export after removals")

        df = pd.DataFrame(active)
        payload = df.to_csv(index=False).encode("utf-8")
        if _mode_is_save(mode):
            src = _emg_source_path(d)
            if src is not None and src.exists() and src.suffix.lower() == ".csv":
                try:
                    raw = pd.read_csv(src)
                    t_col, v_col = _pick_emg_columns(raw)
                    t_raw = pd.to_numeric(raw[t_col], errors="coerce").to_numpy()
                    v_raw = pd.to_numeric(raw[v_col], errors="coerce").to_numpy()
                    m = np.isfinite(t_raw) & np.isfinite(v_raw)
                    t = t_raw[m]
                    v = v_raw[m]
                    if t.size < 3:
                        return err("Not enough numeric rows in source CSV")

                    half_ms = float_or(d.get("half_ms"), 100.0)
                    if half_ms is None or half_ms <= 0:
                        half_ms = 100.0
                    half_s = float(half_ms) / 1000.0

                    channel = _channel_label_from_src(src)
                    summary_rows = []
                    prepared = []
                    for p in active:
                        pi = int(p.get("peak_idx", p.get("idx", -1))) if p.get("peak_idx", p.get("idx", None)) is not None else -1
                        tp = float_or(p.get("time_s", p.get("time")), None)
                        if pi < 0 or pi >= t.size:
                            if tp is None:
                                continue
                            pi = int(np.argmin(np.abs(t - tp)))
                        tp = float(t[pi])
                        grp = str(p.get("group", "")).strip()
                        dur = float_or(p.get("duration", p.get("duration_ms", p.get("fwhm_ms"))), np.nan)
                        h = float_or(p.get("height", p.get("height_uV")), float(v[pi]))

                        summary_rows.append(
                            {
                                "peak_idx": int(pi),
                                "peak_time_s": tp,
                                "height_uV": float(h),
                                "fwhm_ms": float(dur),
                                "group_id": grp,
                            }
                        )
                        prepared.append({"peak_idx": int(pi), "peak_time_s": tp, "group_id": grp})

                    summary_df = pd.DataFrame(summary_rows)
                    summary_path = src.parent / f"{src.parent.name}_{channel}_peaks_summary.csv"
                    summary_df.to_csv(summary_path, index=False)

                    file_count = 0
                    segment_paths = []
                    grouped = [r for r in prepared if str(r.get("group_id", "")).strip() != ""]
                    if grouped:
                        groups = {}
                        for r in grouped:
                            groups.setdefault(str(r["group_id"]), []).append(r)

                        for gid, rows in groups.items():
                            rows = sorted(rows, key=lambda x: x["peak_time_s"])
                            group_dir = src.parent / f"{_sanitize_name(gid)}_{channel}"
                            group_dir.mkdir(parents=True, exist_ok=True)
                            for k, r in enumerate(rows):
                                i0 = int(r["peak_idx"])
                                tp = float(r["peak_time_s"])
                                mask = (t >= tp - half_s) & (t <= tp + half_s)
                                if not np.any(mask):
                                    continue
                                out_file = group_dir / f"peak_{channel}_{k:04d}_t{tp:.6f}s.csv"
                                pd.DataFrame(
                                    {
                                        "t_rel_ms": (t[mask] - tp) * 1e3,
                                        "value_uV": v[mask],
                                    }
                                ).to_csv(out_file, index=False)
                                file_count += 1
                                segment_paths.append(str(out_file))

                    return jsonify(
                        {
                            "ok": True,
                            "saved_path": str(src.parent),
                            "summary_path": str(summary_path),
                            "segment_count": file_count,
                            "segment_paths": segment_paths,
                            "saved_paths": [str(summary_path)] + segment_paths,
                        }
                    )
                except Exception:
                    return err(traceback.format_exc())

            out_path = src.with_name(f"{src.stem}_peaks.csv") if src is not None else Path.cwd() / "emg_peaks.csv"
            out_path.write_bytes(payload)
            return jsonify({"ok": True, "saved_path": str(out_path)})
        return Response(
            payload,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=emg_peaks.csv"},
        )

    @app.route("/api/emg/export_job", methods=["POST"])
    def api_emg_export_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/emg/export",
            "emg.export",
            "Export EMG grouped peaks",
            api_emg_export_compat,
            request.json or {},
        )

    @app.route("/api/emg/load_csv", methods=["POST"])
    def api_emg_load_csv():
        path = (request.json or {}).get("path", "")
        try:
            df = pd.read_csv(path)
            t_col, v_col = _pick_emg_columns(df)
            t = df[t_col].values.astype(float)
            v = df[v_col].values.astype(float)
            dsf = max(1, len(t) // 50000)
            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t[::dsf], v[::dsf], color=line_color, lw=0.6)
            ax.set_xlabel(t_col)
            ax.set_ylabel(v_col)
            ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "t_col": t_col,
                    "v_col": v_col,
                    "duration_s": round(float(t[-1] - t[0]), 3),
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect_peaks", methods=["POST"])
    def api_emg_detect_peaks():
        if not has_scipy or find_peaks is None or peak_widths is None:
            return err("scipy not installed")
        d = request.json or {}
        path = d.get("path", "")
        height = float_or(d.get("height"), None)
        prom = float_or(d.get("prominence"), None)
        dist = float_or(d.get("distance", 100), 100)
        dur = float_or(d.get("duration"), None)
        try:
            df = pd.read_csv(path)
            t_col, v_col = _pick_emg_columns(df)
            t = df[t_col].values.astype(float)
            v = df[v_col].values.astype(float)
            dt = t[1] - t[0]
            dist_pts = max(1, int(dist / 1000 / dt))
            kw = {"distance": dist_pts}
            if height is not None:
                kw["height"] = height
            if prom is not None:
                kw["prominence"] = prom
            peaks, props = find_peaks(np.abs(v), **kw)
            widths, _, _, _ = peak_widths(np.abs(v), peaks, rel_height=0.5)
            peak_rows = []
            for i, pi in enumerate(peaks):
                dur_ms = widths[i] * dt * 1000
                if dur is not None and dur_ms > dur:
                    continue
                peak_rows.append(
                    {
                        "idx": len(peak_rows),
                        "time_s": round(float(t[pi]), 5),
                        "height": round(float(v[pi]), 4),
                        "duration_ms": round(dur_ms, 2),
                        "group": "",
                        "removed": False,
                    }
                )

            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(t, v, color=line_color, lw=0.6, zorder=1)
            valid_t = [r["time_s"] for r in peak_rows]
            valid_v = [r["height"] for r in peak_rows]
            ax.scatter(valid_t, valid_v, color="#e06c00", s=20, zorder=3)
            ax.set_xlabel(t_col)
            ax.set_ylabel(v_col)
            ax.set_title(f"{Path(path).name} - {len(peak_rows)} peaks", fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig), "peaks": peak_rows})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_peaks", methods=["POST"])
    def api_emg_export_peaks():
        d = request.json or {}
        peaks = [p for p in d.get("peaks", []) if not p.get("removed")]
        path = d.get("path", "")
        mode = d.get("mode", "download")
        if not peaks:
            return err("No peaks to export")
        df = pd.DataFrame(peaks)
        payload = df.to_csv(index=False).encode("utf-8")
        stem = Path(path).stem if path else "peaks"
        if _mode_is_save(mode):
            src = Path(path) if path else _emg_source_path(d)
            if src is not None:
                out_path = src.with_name(f"{src.stem}_peaks_grouped.csv")
            else:
                out_path = Path.cwd() / f"{stem}_peaks_grouped.csv"
            out_path.write_bytes(payload)
            return jsonify({"ok": True, "saved_path": str(out_path)})
        return Response(
            payload,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={stem}_peaks.csv"},
        )

    @app.route("/api/emg/export_peaks_job", methods=["POST"])
    def api_emg_export_peaks_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/emg/export_peaks",
            "emg.export_peaks",
            "Export EMG peaks CSV",
            api_emg_export_peaks,
            request.json or {},
        )
