import traceback

from flask import Response, jsonify, request

from services import abf_viewer as abf_viewer_service
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .plot_export import clean_trace_svg, next_numbered_path
from .response import api_ok


def register_abf_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    request_data = ctx["request_data"]
    jobs = ctx.get("jobs")

    service = abf_viewer_service.AbfViewerService(
        has_abf=ctx["HAS_ABF"],
        has_scipy=ctx["HAS_SCIPY"],
        pyabf_mod=ctx.get("pyabf"),
        find_peaks=ctx.get("find_peaks"),
        fig_to_b64=ctx["fig_to_b64"],
        float_or=ctx["float_or"],
        int_or=ctx["int_or"],
        as_bool=as_bool,
        mode_is_save=mode_is_save,
        apply_axes_limits=ctx["apply_axes_limits"],
        clean_trace_svg=clean_trace_svg,
        next_numbered_path=next_numbered_path,
        line_color=ctx["LINE_COLOR"],
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            outputs = data.get("outputs")
            return api_ok(data, outputs=outputs) if outputs else jsonify(data)
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
        )

    def _abf_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_peaks_payload(save_body)["data"]

    def _abf_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting ABF trace")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_payload(save_body)["data"]

    @app.route("/api/abf/browse", methods=["POST"])
    def api_abf_browse():
        d = request.json or {}
        folder = d.get("folder", "")
        files_data = browse_files(folder, {".abf"})
        return jsonify({"files": files_data, "folder": folder})

    @app.route("/api/abf/browse/tree", methods=["POST"])
    def api_abf_browse_tree():
        try:
            return jsonify(service.browse_tree_payload((request.json or {}).get("folder", "")))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/info", methods=["POST"])
    def api_abf_info():
        try:
            return jsonify(service.info_payload((request.json or {}).get("path", "")))
        except ValueError as exc:
            if "pyabf" in str(exc):
                return err("pyabf not installed. Run: pip install pyabf")
            return err(str(exc))
        except Exception as exc:
            return err(exc)

    @app.route("/api/abf/plot", methods=["POST"])
    def api_abf_plot():
        try:
            return jsonify(service.plot_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/detect", methods=["POST"])
    def api_abf_detect_compat():
        try:
            return jsonify(service.detect_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks", methods=["GET", "POST"])
    def api_abf_export_peaks_compat():
        try:
            if request.method == "GET":
                result = service.legacy_trace_export_payload(
                    request.args.get("path", ""),
                    request.args.get("mode", "download"),
                )
            else:
                result = service.export_peaks_payload(request.json or {})
            return _download_or_save(result)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf/export_peaks_job", methods=["POST"])
    def api_abf_export_peaks_job():
        return submit_json_task(
            jobs,
            "abf.export_peaks",
            "Export ABF peaks",
            _abf_export_peaks_task,
            request.json or {},
            metadata={"endpoint": "/api/abf/export_peaks"},
        )

    @app.route("/api/abf/export", methods=["GET", "POST"])
    def api_abf_export():
        try:
            return _download_or_save(service.export_payload(request_data()))
        except ValueError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(exc)

    @app.route("/api/abf/export_job", methods=["POST"])
    def api_abf_export_job():
        return submit_json_task(
            jobs,
            "abf.export",
            "Export ABF trace",
            _abf_export_task,
            request.json or {},
            metadata={"endpoint": "/api/abf/export"},
        )
