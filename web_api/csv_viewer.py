import io
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from flask import Response, jsonify, request

from services import csv_tools
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .path_policy import ensure_output_parent
from .response import api_ok


def register_csv_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    apply_axes_limits = ctx["apply_axes_limits"]
    line_color = ctx["LINE_COLOR"]
    jobs = ctx.get("jobs")

    _mode_is_save = mode_is_save

    def _csv_output(path: Path, role: str = "csv") -> dict:
        return {"path": str(path), "type": "csv", "role": role}

    def _merge_preview_payload(d: dict) -> dict:
        paths = d.get("paths", [])
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        if not isinstance(paths, list) or not paths:
            raise ValueError("Merge queue is empty")
        if not x_col or not y_col:
            raise ValueError("x_col and y_col are required")
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
        fig, ax = plt.subplots(figsize=(9, 4))
        plotted = 0
        for i, path in enumerate(paths):
            try:
                x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max)
            except KeyError:
                continue
            ax.plot(x, y, color=colors[i % len(colors)], lw=0.9, label=Path(path).stem)
            plotted += 1
        if plotted == 0:
            plt.close(fig)
            raise ValueError("No mergeable rows found for selected columns/window")
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        ax.legend(fontsize=9, frameon=False)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        return {"img": fig_to_b64(fig)}

    def _merge_export_payload(d: dict) -> dict:
        paths = d.get("paths", [])
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        drop_first_subsequent = bool(d.get("drop_first_subsequent", True))

        if not isinstance(paths, list) or not paths:
            raise ValueError("Merge queue is empty")
        if not x_col or not y_col:
            raise ValueError("x_col and y_col are required")

        out_df = csv_tools.merge_xy_tables(
            paths,
            x_col,
            y_col,
            x_min=x_min,
            x_max=x_max,
            drop_first_subsequent=drop_first_subsequent,
        )
        out_name = csv_tools.default_merge_name(x_min, x_max)
        out_path = Path(paths[0]).parent / out_name
        payload = out_df.to_csv(index=False).encode("utf-8")
        return {
            "payload": payload,
            "out_name": out_name,
            "out_path": out_path,
            "rows": int(len(out_df)),
        }

    def _plot_export_payload(d: dict) -> dict:
        path = d.get("path", "")
        fmt = d.get("fmt", "png")
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        src = Path(path)
        x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max)

        if fmt == "csv":
            buf = io.BytesIO()
            pd.DataFrame({x_col: x, y_col: y}).to_csv(buf, index=False)
            buf.seek(0)
            out_path = src.with_name(f"{src.stem}_plot.csv")
            return {
                "payload": buf.getvalue(),
                "out_path": out_path,
                "mimetype": "text/csv",
                "download_name": "export.csv",
                "output_type": "csv",
                "role": "plot_csv",
            }

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(x, y, color=line_color, lw=0.9)
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
        apply_axes_limits(ax, None, None, y_min, y_max)
        ax.grid(True, alpha=0.4)
        fig.tight_layout()

        buf = io.BytesIO()
        dpi = 300 if fmt == "png" else None
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return {
            "payload": buf.getvalue(),
            "out_path": src.with_name(f"{src.stem}_plot.{fmt}"),
            "mimetype": "image/png" if fmt == "png" else "image/svg+xml",
            "download_name": f"export.{fmt}",
            "output_type": fmt,
            "role": "plot",
        }

    def _full_csv_export_payload(d: dict) -> dict:
        path = d.get("path", "")
        src = Path(path)
        df = pd.read_csv(path)
        return {
            "payload": df.to_csv(index=False).encode("utf-8"),
            "out_path": src.with_name(f"{src.stem}_full.csv"),
            "download_name": f"{src.stem}.csv",
            "rows": int(len(df)),
        }

    def _write_payload_result(export: dict, role: str) -> dict:
        out_path = ensure_output_parent(Path(export["out_path"]))
        out_path.write_bytes(export["payload"])
        result = {"ok": True, "saved_path": str(out_path)}
        if "rows" in export:
            result["rows"] = export["rows"]
        result["outputs"] = [_csv_output(out_path, role)]
        return result

    def _csv_merge_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Rendering CSV merge preview")
        return _merge_preview_payload(body)

    def _csv_export_merge_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Building merged CSV")
        return _write_payload_result(_merge_export_payload(body), "merged_csv")

    def _csv_export_plot_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Rendering CSV export")
        export = _plot_export_payload(body)
        result = _write_payload_result(export, export["role"])
        result["outputs"][0]["type"] = export["output_type"]
        return result

    def _csv_export_full_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting full CSV")
        return _write_payload_result(_full_csv_export_payload(body), "full_csv")

    @app.route("/api/csv/browse", methods=["POST"])
    def api_csv_browse():
        d = request.json or {}
        files = browse_files(d.get("folder", ""), {".csv", ".txt", ".tsv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/csv/columns", methods=["POST"])
    def api_csv_columns():
        path = (request.json or {}).get("path", "")
        try:
            return jsonify({"columns": csv_tools.read_columns(path)})
        except Exception as e:
            return err(e)

    @app.route("/api/csv/plot", methods=["POST"])
    def api_csv_plot():
        d = request.json or {}
        path = d.get("path", "")
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        y_min = float_or(d.get("y_min"), None)
        y_max = float_or(d.get("y_max"), None)
        dsf = int_or(d.get("dsf", 1), 1)
        try:
            x, y = csv_tools.load_xy(path, x_col, y_col, x_min, x_max, downsample=dsf)
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(x, y, color=line_color, lw=0.9)
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
            ax.grid(True, alpha=0.4)
            apply_axes_limits(ax, None, None, y_min, y_max)
            ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/merge", methods=["POST"])
    def api_csv_merge():
        d = request.json or {}
        try:
            return api_ok(_merge_preview_payload(d))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/merge_job", methods=["POST"])
    def api_csv_merge_job():
        return submit_json_task(
            jobs,
            "csv.merge_preview",
            "Merge CSV preview",
            _csv_merge_task,
            request.json or {},
            metadata={"endpoint": "/api/csv/merge"},
        )

    @app.route("/api/csv/export_merge", methods=["POST"])
    def api_csv_export_merge():
        d = request.json or {}
        mode = d.get("mode", "download")

        try:
            export = _merge_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, "merged_csv")
                return api_ok(result, outputs=result["outputs"])

            return Response(
                export["payload"],
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={export['out_name']}"},
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/export_merge_job", methods=["POST"])
    def api_csv_export_merge_job():
        return submit_json_task(
            jobs,
            "csv.export_merge",
            "Export merged CSV",
            _csv_export_merge_task,
            request.json or {},
            metadata={"endpoint": "/api/csv/export_merge"},
        )

    @app.route("/api/csv/export")
    def api_csv_export():
        d = dict(request.args)
        mode = request.args.get("mode", "download")
        try:
            export = _plot_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, export["role"])
                result["outputs"][0]["type"] = export["output_type"]
                return api_ok(result, outputs=result["outputs"])
            return Response(
                export["payload"],
                mimetype=export["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={export['download_name']}"},
            )
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_job", methods=["POST"])
    def api_csv_export_job():
        body = request.json or {}
        return submit_json_task(
            jobs,
            "csv.export_plot",
            "Export CSV plot",
            _csv_export_plot_task,
            body,
            metadata={"endpoint": "/api/csv/export"},
        )

    @app.route("/api/csv/export_csv")
    def api_csv_export_csv_compat():
        d = dict(request.args)
        mode = request.args.get("mode", "download")
        try:
            export = _full_csv_export_payload(d)
            if _mode_is_save(mode):
                result = _write_payload_result(export, "full_csv")
                return api_ok(result, outputs=result["outputs"])
            return Response(
                export["payload"],
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={export['download_name']}"},
            )
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_csv_job", methods=["POST"])
    def api_csv_export_csv_job():
        body = request.json or {}
        return submit_json_task(
            jobs,
            "csv.export_full",
            "Export full CSV",
            _csv_export_full_task,
            body,
            metadata={"endpoint": "/api/csv/export_csv"},
        )
