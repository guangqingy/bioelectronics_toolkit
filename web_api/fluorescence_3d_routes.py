from __future__ import annotations

import traceback
from pathlib import Path

from flask import jsonify, request

from services.fluorescence.volume3d_exports import (
    Volume3DExportContext,
    distribution_payload,
    export_volume_payload,
    rotation_gif_payload,
    volume_payload_from_body,
)

from .jobs import submit_json_task
from .response import api_ok


def register_fluorescence_3d_routes(app, fl):
    err = fl["err"]
    int_or = fl["int_or"]
    float_or = fl["float_or"]
    has_tiff = fl["has_tiff"]
    has_pil = fl["has_pil"]
    jobs = fl["jobs"]

    _fl_frame_to_b64 = fl["_fl_frame_to_b64"]
    _fl_tiff_plane_from_array = fl["_fl_tiff_plane_from_array"]
    _fl_tiff_read_array = fl["_fl_tiff_read_array"]
    _fl_tiff_series_info = fl["_fl_tiff_series_info"]

    volume_export_ctx = Volume3DExportContext(
        has_tiff=has_tiff,
        has_pil=has_pil,
        int_or=int_or,
        float_or=float_or,
        denoise_options=list(fl["_FL_DENOISE_OPTIONS"]),
        bool_value=fl["_fl_bool"],
        clean_choice=fl["_fl_clean_choice"],
        apply_optional_denoise=fl["_fl_apply_optional_denoise"],
        sanitize_prefix=fl["_fl_sanitize_prefix"],
        tiff_plane_from_array=_fl_tiff_plane_from_array,
        tiff_read_array=_fl_tiff_read_array,
        tiff_series_info=_fl_tiff_series_info,
        tiff_volume3d_payload=fl["_fl_tiff_volume3d_payload"],
        volume3d_html=fl["_fl_volume3d_html"],
        fig_to_b64=fl["fig_to_b64"],
    )

    def _export_volume_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting fluorescence 3D viewer")
        return export_volume_payload(body, volume_export_ctx)

    def _export_rotation_gif_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.1, "Building 3D rotation GIF")
        result = rotation_gif_payload(body, volume_export_ctx, preview=False)
        job_ctx.set_progress(1.0, "3D rotation GIF exported")
        return result

    @app.route("/api/fluorescence/3d/tiff_info", methods=["POST"])
    def api_fl_3d_tiff_info():
        if not has_tiff:
            return err("tifffile is required")
        path = str((request.json or {}).get("path", "") or "").strip()
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            return jsonify({"ok": True, "info": _fl_tiff_series_info(p)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/tiff_info_batch", methods=["POST"])
    def api_fl_3d_tiff_info_batch():
        if not has_tiff:
            return err("tifffile is required")
        paths = (request.json or {}).get("paths") or []
        info = {}
        for raw in paths:
            p = Path(str(raw or "").strip())
            if not p.exists():
                info[str(raw)] = {"error": "not found"}
                continue
            try:
                info[str(raw)] = _fl_tiff_series_info(p)
            except Exception as exc:
                info[str(raw)] = {"error": str(exc)}
        return jsonify({"ok": True, "info": info})

    @app.route("/api/fluorescence/3d/preview_slice", methods=["POST"])
    def api_fl_3d_preview_slice():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        z = int_or(d.get("z", 0), 0)
        c = int_or(d.get("c", 0), 0)
        t = int_or(d.get("t", 0), 0)
        extra_indices = d.get("extra_indices") if isinstance(d.get("extra_indices"), dict) else {}
        lut = str(d.get("lut", "Gray") or "Gray")
        p_low = max(0.0, min(49.0, float_or(d.get("p_low", 1.0), 1.0)))
        p_high = max(51.0, min(100.0, float_or(d.get("p_high", 99.0), 99.0)))
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            arr, axes, roles = _fl_tiff_read_array(p)
            plane = _fl_tiff_plane_from_array(arr, axes, roles, z=z, c=c, t=t, extra_indices=extra_indices)
            b64 = _fl_frame_to_b64(plane, lut, p_low, p_high)
            return jsonify({"ok": True, "img": b64, "z": z, "c": c, "t": t})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/volume", methods=["POST"])
    def api_fl_3d_volume():
        if not has_tiff:
            return err("tifffile is required")
        try:
            _path, payload = volume_payload_from_body(request.json or {}, volume_export_ctx, for_export=False)
            return jsonify({"ok": True, "volume": payload})
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume", methods=["POST"])
    def api_fl_3d_export_volume():
        try:
            result = export_volume_payload(request.json or {}, volume_export_ctx)
            return api_ok(result, outputs=result["outputs"])
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume_job", methods=["POST"])
    def api_fl_3d_export_volume_job():
        return submit_json_task(
            jobs,
            "fluorescence.export_volume3d",
            "Export fluorescence 3D viewer",
            _export_volume_task,
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/3d/export_volume"},
        )

    @app.route("/api/fluorescence/3d/rotation_gif_preview", methods=["POST"])
    def api_fl_3d_rotation_gif_preview():
        try:
            return api_ok(rotation_gif_payload(request.json or {}, volume_export_ctx, preview=True))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_rotation_gif", methods=["POST"])
    def api_fl_3d_export_rotation_gif():
        try:
            result = rotation_gif_payload(request.json or {}, volume_export_ctx, preview=False)
            return api_ok(result, outputs=result["outputs"])
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_rotation_gif_job", methods=["POST"])
    def api_fl_3d_export_rotation_gif_job():
        return submit_json_task(
            jobs,
            "fluorescence.export_3d_rotation_gif",
            "Export fluorescence 3D rotation GIF",
            _export_rotation_gif_task,
            request.json or {},
            metadata={"endpoint": "/api/fluorescence/3d/export_rotation_gif"},
        )

    @app.route("/api/fluorescence/3d/intensity_distribution", methods=["POST"])
    def api_fl_3d_intensity_distribution():
        try:
            result = distribution_payload(request.json or {}, volume_export_ctx)
            return api_ok(result, outputs=result["outputs"])
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())
