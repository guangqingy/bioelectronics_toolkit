import traceback

from flask import Response, jsonify, request

from services import emg_peaks as emg_peaks_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .response import api_ok


def register_emg_peaks_routes(app, ctx):
    err = ctx["err"]
    jobs = ctx.get("jobs")
    service = emg_peaks_service.EmgPeaksService(
        has_scipy=ctx["HAS_SCIPY"],
        find_peaks=ctx.get("find_peaks"),
        peak_widths=ctx.get("peak_widths"),
        fig_to_b64=ctx["fig_to_b64"],
        float_or=ctx["float_or"],
        line_color=ctx["LINE_COLOR"],
        mode_is_save=mode_is_save,
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            return api_ok(data, outputs=data["outputs"])
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
        )

    def _emg_grouped_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG grouped peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.grouped_export_payload(save_body)["data"]

    def _emg_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG peaks CSV")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_peaks_payload(save_body)["data"]

    @app.route("/api/emg/browse", methods=["POST"])
    def api_emg_browse():
        return jsonify(service.browse_payload((request.json or {}).get("folder", "")))

    @app.route("/api/emg/load_channels", methods=["POST"])
    def api_emg_load_channels_compat():
        d = request.json or {}
        return jsonify(service.channel_payload(d.get("folder", ""), d.get("subfolder", "")))

    @app.route("/api/emg/load", methods=["POST"])
    def api_emg_load_compat():
        d = request.json or {}
        try:
            return jsonify(service.load_duration_payload(d.get("folder", ""), d.get("subfolder", ""), d.get("channel", "")))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/plot", methods=["POST"])
    def api_emg_plot_compat():
        try:
            return jsonify(service.plot_payload(request.json or {}))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect", methods=["POST"])
    def api_emg_detect_compat():
        try:
            return jsonify(service.detect_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export", methods=["POST"])
    def api_emg_export_compat():
        try:
            return _download_or_save(service.grouped_export_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_job", methods=["POST"])
    def api_emg_export_job():
        return submit_json_task(
            jobs,
            "emg.export",
            "Export EMG grouped peaks",
            _emg_grouped_export_task,
            request.json or {},
            metadata={"endpoint": "/api/emg/export"},
        )

    @app.route("/api/emg/load_csv", methods=["POST"])
    def api_emg_load_csv():
        try:
            return jsonify(service.load_csv_payload((request.json or {}).get("path", "")))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/detect_peaks", methods=["POST"])
    def api_emg_detect_peaks():
        try:
            return jsonify(service.detect_peaks_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_peaks", methods=["POST"])
    def api_emg_export_peaks():
        try:
            return _download_or_save(service.export_peaks_payload(request.json or {}))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/emg/export_peaks_job", methods=["POST"])
    def api_emg_export_peaks_job():
        return submit_json_task(
            jobs,
            "emg.export_peaks",
            "Export EMG peaks CSV",
            _emg_export_peaks_task,
            request.json or {},
            metadata={"endpoint": "/api/emg/export_peaks"},
        )
