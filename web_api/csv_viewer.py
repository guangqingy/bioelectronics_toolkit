import io
import traceback
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from flask import Response, jsonify, request


from web_api.common import mode_is_save
from .jobs import submit_flask_route_job


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

    @app.route("/api/csv/browse", methods=["POST"])
    def api_csv_browse():
        d = request.json or {}
        files = browse_files(d.get("folder", ""), {".csv", ".txt", ".tsv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/csv/columns", methods=["POST"])
    def api_csv_columns():
        path = (request.json or {}).get("path", "")
        try:
            df = pd.read_csv(path, nrows=2)
            return jsonify({"columns": list(df.columns)})
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
            df = pd.read_csv(path)
            if x_col not in df.columns or y_col not in df.columns:
                return err(f"Columns {x_col!r} or {y_col!r} not found")
            x = df[x_col].values[::dsf]
            y = df[y_col].values[::dsf]
            if x_min is not None:
                mask = x >= x_min
                x, y = x[mask], y[mask]
            if x_max is not None:
                mask = x <= x_max
                x, y = x[mask], y[mask]
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
        paths = d.get("paths", [])
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        if not isinstance(paths, list) or not paths:
            return err("Merge queue is empty")
        if not x_col or not y_col:
            return err("x_col and y_col are required")
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
        try:
            fig, ax = plt.subplots(figsize=(9, 4))
            plotted = 0
            for i, path in enumerate(paths):
                df = pd.read_csv(path)
                if x_col not in df.columns or y_col not in df.columns:
                    continue
                x = df[x_col].values
                y = df[y_col].values
                if x_min is not None:
                    mask = x >= x_min
                    x, y = x[mask], y[mask]
                if x_max is not None:
                    mask = x <= x_max
                    x, y = x[mask], y[mask]
                ax.plot(x, y, color=colors[i % len(colors)], lw=0.9, label=Path(path).stem)
                plotted += 1
            if plotted == 0:
                return err("No mergeable rows found for selected columns/window")
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
            ax.legend(fontsize=9, frameon=False)
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify({"img": fig_to_b64(fig)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/merge_job", methods=["POST"])
    def api_csv_merge_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/csv/merge",
            "csv.merge_preview",
            "Merge CSV preview",
            api_csv_merge,
            request.json or {},
        )

    @app.route("/api/csv/export_merge", methods=["POST"])
    def api_csv_export_merge():
        d = request.json or {}
        paths = d.get("paths", [])
        x_col = d.get("x_col", "")
        y_col = d.get("y_col", "")
        x_min = float_or(d.get("x_min"), None)
        x_max = float_or(d.get("x_max"), None)
        drop_first_subsequent = bool(d.get("drop_first_subsequent", True))
        mode = d.get("mode", "download")

        if not isinstance(paths, list) or not paths:
            return err("Merge queue is empty")
        if not x_col or not y_col:
            return err("x_col and y_col are required")

        try:
            rows = []
            for k, path in enumerate(paths):
                df = pd.read_csv(path)
                if x_col not in df.columns or y_col not in df.columns:
                    continue

                x = pd.to_numeric(df[x_col], errors="coerce")
                y = pd.to_numeric(df[y_col], errors="coerce")
                m = x.notna() & y.notna()
                if x_min is not None:
                    m = m & (x >= x_min)
                if x_max is not None:
                    m = m & (x <= x_max)
                if not m.any():
                    continue

                sub = pd.DataFrame({x_col: x[m].to_numpy(), y_col: y[m].to_numpy()}).reset_index(drop=True)
                if drop_first_subsequent and k > 0 and len(sub) > 0:
                    sub = sub.iloc[1:].reset_index(drop=True)
                    if sub.empty:
                        continue
                rows.append(sub)

            if not rows:
                return err("No rows available in selected X window")

            out_df = pd.concat(rows, axis=0, ignore_index=True)

            def _tag(v):
                return f"{float(v):.6f}".replace(".", "p")

            name_left = _tag(x_min) if x_min is not None else "auto"
            name_right = _tag(x_max) if x_max is not None else "auto"
            out_name = f"merged_preview_{name_left}-{name_right}.csv"
            out_dir = Path(paths[0]).parent
            out_path = out_dir / out_name

            payload = out_df.to_csv(index=False).encode("utf-8")
            if _mode_is_save(mode):
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path), "rows": int(len(out_df))})

            return Response(
                payload,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={out_name}"},
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/csv/export_merge_job", methods=["POST"])
    def api_csv_export_merge_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/csv/export_merge",
            "csv.export_merge",
            "Export merged CSV preview",
            api_csv_export_merge,
            request.json or {},
        )

    @app.route("/api/csv/export")
    def api_csv_export():
        path = request.args.get("path", "")
        fmt = request.args.get("fmt", "png")
        mode = request.args.get("mode", "download")
        x_col = request.args.get("x_col", "")
        y_col = request.args.get("y_col", "")
        x_min = float_or(request.args.get("x_min"), None)
        x_max = float_or(request.args.get("x_max"), None)
        y_min = float_or(request.args.get("y_min"), None)
        y_max = float_or(request.args.get("y_max"), None)
        try:
            src = Path(path)
            df = pd.read_csv(path)
            x = df[x_col].values
            y = df[y_col].values
            if x_min is not None:
                mask = x >= x_min
                x, y = x[mask], y[mask]
            if x_max is not None:
                mask = x <= x_max
                x, y = x[mask], y[mask]

            if fmt == "csv":
                buf = io.BytesIO()
                pd.DataFrame({x_col: x, y_col: y}).to_csv(buf, index=False)
                buf.seek(0)
                payload = buf.getvalue()
                if _mode_is_save(mode):
                    out_path = src.with_name(f"{src.stem}_plot.csv")
                    out_path.write_bytes(payload)
                    return jsonify({"ok": True, "saved_path": str(out_path)})
                return Response(
                    payload,
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=export.csv"},
                )

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
            payload = buf.getvalue()
            if _mode_is_save(mode):
                out_path = src.with_name(f"{src.stem}_plot.{fmt}")
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            mt = "image/png" if fmt == "png" else "image/svg+xml"
            return Response(
                payload,
                mimetype=mt,
                headers={"Content-Disposition": f"attachment; filename=export.{fmt}"},
            )
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_job", methods=["POST"])
    def api_csv_export_job():
        body = request.json or {}
        query = {key: value for key, value in body.items() if value is not None}
        query["mode"] = "save"
        return submit_flask_route_job(
            app,
            jobs,
            "/api/csv/export",
            "csv.export_plot",
            "Export CSV plot",
            api_csv_export,
            {},
            method="GET",
            query_string=query,
        )

    @app.route("/api/csv/export_csv")
    def api_csv_export_csv_compat():
        path = request.args.get("path", "")
        mode = request.args.get("mode", "download")
        try:
            df = pd.read_csv(path)
            payload = df.to_csv(index=False).encode("utf-8")
            if _mode_is_save(mode):
                src = Path(path)
                out_path = src.with_name(f"{src.stem}_full.csv")
                out_path.write_bytes(payload)
                return jsonify({"ok": True, "saved_path": str(out_path)})
            return Response(
                payload,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={Path(path).stem}.csv"},
            )
        except Exception as e:
            return err(e)

    @app.route("/api/csv/export_csv_job", methods=["POST"])
    def api_csv_export_csv_job():
        body = request.json or {}
        query = {key: value for key, value in body.items() if value is not None}
        query["mode"] = "save"
        return submit_flask_route_job(
            app,
            jobs,
            "/api/csv/export_csv",
            "csv.export_full",
            "Export full CSV",
            api_csv_export_csv_compat,
            {},
            method="GET",
            query_string=query,
        )
