# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move line-shape loading/plot/export payload assembly into echem services and
# track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import re as _re
import traceback
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Response, jsonify
from pydantic import Field, ValidationError


from web_api.common import mode_is_save

from .jobs import submit_json_task
from .path_policy import ensure_output_parent
from .request_validation import RequestModel, parse_json_payload, request_schema, validation_error_response
from .response import api_ok


class LineshapeBrowseRequest(RequestModel):
    base_dir: str = ""


class LineshapeLoadRequest(RequestModel):
    base_dir: str = ""
    material: str = ""
    index_k: Any = 1
    kind: str = "photocurrent"
    crop_t0: Any = -0.005
    crop_t1: Any = 0.020


class LineshapePlotRequest(RequestModel):
    samples: list[Any] = Field(default_factory=list)
    selected: list[Any] = Field(default_factory=list)
    crop_t0: Any = -0.005
    crop_t1: Any = 0.020
    x_offset: Any = 0.0
    y_min: Any = None
    y_max: Any = None
    kind: str = "photocurrent"


class LineshapeExportAvgRequest(RequestModel):
    source_path: str = ""
    avg_data: dict[str, Any] = Field(default_factory=dict)
    mode: str = "download"


def register_echem_lineshape_routes(app, ctx):
    err = ctx["err"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save

    device_dir_re = _re.compile(r"^(?P<prefix>.*?)(?P<ch>\d+)_(?P<idx>\d+)$")

    def _lineshape_avg_export(d: dict) -> dict:
        source_path = d.get("source_path", "")
        avg_data = d.get("avg_data", {})
        t_ms = avg_data.get("t_ms", [])
        y = avg_data.get("y", [])
        if not t_ms:
            raise ValueError("No averaged data to export")
        df_out = pd.DataFrame({"time_ms": t_ms, "signal": y})
        payload = df_out.to_csv(index=False).encode("utf-8")
        if source_path:
            src = Path(source_path)
            out_path = src.with_name(f"{src.stem}_lineshape_avg.csv")
        else:
            out_path = Path.cwd() / "lineshape_avg.csv"
        return {
            "payload": payload,
            "download_name": "lineshape_avg.csv",
            "out_path": out_path,
        }

    def _save_lineshape_avg_export(export: dict) -> dict:
        out_path = ensure_output_parent(export["out_path"])
        out_path.write_bytes(export["payload"])
        return {
            "ok": True,
            "saved_path": str(out_path),
            "outputs": [{"path": str(out_path), "type": "csv", "role": "lineshape_avg"}],
        }

    def _lineshape_avg_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting averaged lineshape")
        return _save_lineshape_avg_export(_lineshape_avg_export(body))

    def _ls_read_csv(path):
        """Read two-column segment CSV -> (t, y) arrays."""
        df = pd.read_csv(path)
        col_t = next((c for c in df.columns if c.strip().lower() in ("time_s", "time", "t", "t_s")), None)
        col_y = next(
            (
                c
                for c in df.columns
                if c.strip().lower() in ("current_ma", "current", "i_ma", "i", "voltage_v", "voltage", "v")
            ),
            None,
        )
        if col_t and col_y:
            t = pd.to_numeric(df[col_t], errors="coerce").to_numpy()
            y = pd.to_numeric(df[col_y], errors="coerce").to_numpy()
        else:
            t = pd.to_numeric(df.iloc[:, 0], errors="coerce").to_numpy()
            y = pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy()

        mask = np.isfinite(t) & np.isfinite(y)
        t = t[mask]
        y = y[mask]
        if np.any(np.diff(t) <= 0):
            order = np.argsort(t)
            t = t[order]
            y = y[order]
        return t, y

    def _ls_center_crop(t, y, kind, crop_t0=-0.005, crop_t1=0.020, x_offset=0.0):
        """Center at peak, optionally flip PV, then crop to window."""
        if kind == "photovoltage":
            ci = int(np.argmin(y))
            y = -y
        else:
            ci = int(np.argmax(y))
        t_rel = (t - t[ci]) + x_offset
        mask = (t_rel >= crop_t0) & (t_rel <= crop_t1)
        return t_rel[mask], y[mask]

    def _ls_resample(t_rel, y, grid):
        if len(t_rel) < 2:
            return np.full_like(grid, np.nan, dtype=float)
        return np.interp(grid, t_rel, y, left=np.nan, right=np.nan)

    @app.route("/api/echem/lineshape/browse", methods=["POST"])
    @request_schema(LineshapeBrowseRequest)
    def api_ls_browse():
        try:
            payload = parse_json_payload(LineshapeBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        base_dir = payload.base_dir
        p = Path(base_dir)
        if not p.is_dir():
            return err(f"Not a directory: {base_dir}")
        materials = sorted(x.name for x in p.iterdir() if x.is_dir() and not x.name.startswith("."))
        return jsonify({"materials": materials})

    @app.route("/api/echem/lineshape/load", methods=["POST"])
    @request_schema(LineshapeLoadRequest)
    def api_ls_load():
        try:
            d = parse_json_payload(LineshapeLoadRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        base_dir = d.get("base_dir", "")
        material = d.get("material", "")
        index_k = int_or(d.get("index_k", 1), 1)
        kind = d.get("kind", "photocurrent")
        crop_t0 = float_or(d.get("crop_t0", -0.005), -0.005)
        crop_t1 = float_or(d.get("crop_t1", 0.020), 0.020)

        subdir = "Photocurrent" if kind == "photocurrent" else "Photovoltage"
        root = Path(base_dir) / material / subdir
        if not root.is_dir():
            return err(f"Directory not found: {root}")

        pat = "*_pair_*.csv" if kind == "photocurrent" else "*_pulse_*.csv"
        samples = []
        for dev_dir in sorted(root.iterdir()):
            if not dev_dir.is_dir():
                continue
            m = device_dir_re.match(dev_dir.name)
            if not m:
                continue
            try:
                idx = int(m.group("idx"))
            except Exception:
                continue
            if idx != index_k:
                continue

            for csv_p in sorted(dev_dir.rglob(pat)):
                try:
                    t, y = _ls_read_csv(csv_p)
                    t_rel, y_proc = _ls_center_crop(t, y, kind, crop_t0, crop_t1)
                    if len(t_rel) < 3:
                        continue
                    seg_m = _re.search(r"_(pair|pulse)_(\d+)$", csv_p.stem)
                    if seg_m:
                        label = f"{dev_dir.name} {seg_m.group(1)[0]}{int(seg_m.group(2)):03d}"
                    else:
                        label = dev_dir.name
                    samples.append(
                        {
                            "label": label,
                            "device": dev_dir.name,
                            "file": str(csv_p),
                            "t": t_rel.tolist(),
                            "y": y_proc.tolist(),
                        }
                    )
                except Exception:
                    pass

        if not samples:
            return err(f"No valid segments found under {root}")
        return jsonify({"samples": samples, "n": len(samples)})

    @app.route("/api/echem/lineshape/plot", methods=["POST"])
    @request_schema(LineshapePlotRequest)
    def api_ls_plot():
        try:
            d = parse_json_payload(LineshapePlotRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        samples = d.get("samples", [])
        selected = d.get("selected", [])
        crop_t0 = float_or(d.get("crop_t0", -0.005), -0.005)
        crop_t1 = float_or(d.get("crop_t1", 0.020), 0.020)
        x_offset = float_or(d.get("x_offset", 0.0), 0.0)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        kind = d.get("kind", "photocurrent")
        y_label = "Current (mA)" if kind == "photocurrent" else "|Voltage| (V)"

        if not samples:
            return err("No samples provided")
        if not selected:
            selected = list(range(len(samples)))

        try:
            grid = np.linspace(crop_t0 + x_offset, crop_t1 + x_offset, 500)
            resampled = []
            for i in selected:
                if i >= len(samples):
                    continue
                s = samples[i]
                t_arr = np.array(s["t"]) + x_offset
                y_arr = np.array(s["y"])
                resampled.append(_ls_resample(t_arr, y_arr, grid))

            if not resampled:
                return err("No valid selected samples")

            mat = np.array(resampled)
            avg = np.nanmean(mat, axis=0)
            sem = np.nanstd(mat, axis=0) / max(1, np.sqrt(mat.shape[0]))

            fig_avg, ax_avg = plt.subplots(figsize=(5, 4))
            colors = [
                "#3E6AE1",
                "#e06c00",
                "#2ca02c",
                "#d62728",
                "#9467bd",
                "#8c564b",
                "#e377c2",
                "#17becf",
                "#bcbd22",
                "#7f7f7f",
            ]
            for i, row in enumerate(resampled):
                ax_avg.plot(grid * 1000, row, color=colors[selected[i] % len(colors)], alpha=0.3, lw=0.7)
            ax_avg.plot(grid * 1000, avg, color="#171A20", lw=1.8, zorder=5, label=f"avg n={len(resampled)}")
            ax_avg.fill_between(grid * 1000, avg - sem, avg + sem, color="#171A20", alpha=0.12)
            ax_avg.axvline(0, color="#D0D1D2", lw=0.7, ls="--")
            ax_avg.set_xlabel("Time (ms)")
            ax_avg.set_ylabel(y_label)
            ax_avg.legend(fontsize=9, frameon=False)
            ax_avg.grid(True, alpha=0.3)
            if y_min is not None or y_max is not None:
                cur = ax_avg.get_ylim()
                ax_avg.set_ylim(y_min if y_min is not None else cur[0], y_max if y_max is not None else cur[1])
            fig_avg.tight_layout()
            avg_b64 = fig_to_b64(fig_avg)

            n_show = min(len(samples), 16)
            ncols = 4
            nrows = (n_show + ncols - 1) // ncols
            mini_w, mini_h = 2.8, 2.0
            fig_grid, axes = plt.subplots(nrows, ncols, figsize=(ncols * mini_w, nrows * mini_h))
            if nrows == 1 and ncols == 1:
                axes = np.array([[axes]])
            elif nrows == 1:
                axes = axes[np.newaxis, :]
            elif ncols == 1:
                axes = axes[:, np.newaxis]

            all_y = [samples[i]["y"] for i in range(n_show)]
            global_y_min = min(min(y) for y in all_y if y)
            global_y_max = max(max(y) for y in all_y if y)

            for idx in range(nrows * ncols):
                r, c = divmod(idx, ncols)
                ax = axes[r, c]
                if idx >= n_show:
                    ax.set_visible(False)
                    continue
                s = samples[idx]
                t_arr = np.array(s["t"]) + x_offset
                y_arr = np.array(s["y"])
                is_sel = idx in selected
                col = "#3E6AE1" if is_sel else "#D0D1D2"
                ax.plot(t_arr * 1000, y_arr, color=col, lw=0.9)
                ax.axvline(0, color="#EEEEEE", lw=0.5, ls="--")
                ax.set_title(s["label"], fontsize=7, color="#171A20" if is_sel else "#8E8E8E")
                ax.set_xlim((crop_t0 + x_offset) * 1000, (crop_t1 + x_offset) * 1000)
                ax.set_ylim(global_y_min, global_y_max)
                ax.tick_params(labelsize=6)
                ax.set_xlabel("ms", fontsize=6)
                for sp in ["top", "right"]:
                    ax.spines[sp].set_visible(False)

            fig_grid.tight_layout(pad=0.5)
            grid_b64 = fig_to_b64(fig_grid, dpi=120)

            return jsonify(
                {
                    "avg_img": avg_b64,
                    "grid_img": grid_b64,
                    "n_selected": len(resampled),
                    "n_total": len(samples),
                    "avg_data": {"t_ms": (grid * 1000).tolist(), "y": avg.tolist()},
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/lineshape/export_avg", methods=["POST"])
    @request_schema(LineshapeExportAvgRequest)
    def api_ls_export_avg():
        """Export averaged trace as CSV download."""
        try:
            d = parse_json_payload(LineshapeExportAvgRequest).model_dump()
            mode = d.get("mode", "download")
            export = _lineshape_avg_export(d)
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        if _mode_is_save(mode):
            result = _save_lineshape_avg_export(export)
            return api_ok(result, outputs=result["outputs"])
        return Response(
            export["payload"],
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={export['download_name']}"},
        )

    @app.route("/api/echem/lineshape/export_avg_job", methods=["POST"])
    @request_schema(LineshapeExportAvgRequest)
    def api_ls_export_avg_job():
        try:
            body = parse_json_payload(LineshapeExportAvgRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_lineshape.export_avg",
            "Export echem lineshape average",
            _lineshape_avg_export_task,
            body,
            metadata={"endpoint": "/api/echem/lineshape/export_avg"},
        )
