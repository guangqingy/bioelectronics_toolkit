# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move remaining 3D response assembly into volume3d service helpers and track
# the GitHub issue draft in docs/loc_budget_issue_drafts.md.
from __future__ import annotations

import traceback
from pathlib import Path

from flask import jsonify
from pydantic import ValidationError

from services.fluorescence.volume3d_exports import (
    Volume3DExportContext,
    distribution_payload,
    export_volume_payload,
    rotation_gif_job_payload,
    rotation_gif_payload,
    volume_payload_from_body,
)

from .fluorescence_request_schemas import (
    Fluorescence3dDistributionRequest,
    Fluorescence3dPreviewSliceRequest,
    Fluorescence3dRotationGifRequest,
    Fluorescence3dVolumeRequest,
    FluorescencePathRequest,
    FluorescenceTiffInfoBatchRequest,
)
from .jobs import submit_json_task
from .request_validation import parse_json_payload, request_schema, validation_error_response
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
        return rotation_gif_job_payload(body, volume_export_ctx, job_ctx)

    @app.route("/api/fluorescence/3d/tiff_info", methods=["POST"])
    @request_schema(FluorescencePathRequest)
    def api_fl_3d_tiff_info():
        if not has_tiff:
            return err("tifffile is required")
        try:
            path = parse_json_payload(FluorescencePathRequest).path.strip()
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            return jsonify({"ok": True, "info": _fl_tiff_series_info(p)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/tiff_info_batch", methods=["POST"])
    @request_schema(FluorescenceTiffInfoBatchRequest)
    def api_fl_3d_tiff_info_batch():
        if not has_tiff:
            return err("tifffile is required")
        try:
            paths = parse_json_payload(FluorescenceTiffInfoBatchRequest).paths
        except ValidationError as exc:
            return validation_error_response(exc)
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
    @request_schema(Fluorescence3dPreviewSliceRequest)
    def api_fl_3d_preview_slice():
        if not has_tiff or not has_pil:
            return err("tifffile and Pillow are required")
        try:
            d = parse_json_payload(Fluorescence3dPreviewSliceRequest)
            path = d.path.strip()
            z = int_or(d.z, 0)
            c = int_or(d.c, 0)
            t = int_or(d.t, 0)
            extra_indices = d.extra_indices if isinstance(d.extra_indices, dict) else {}
            lut = str(d.lut or "Gray")
            p_low = max(0.0, min(49.0, float_or(d.p_low, 1.0)))
            p_high = max(51.0, min(100.0, float_or(d.p_high, 99.0)))
            p = Path(path)
            if not p.exists():
                return err(f"Input TIFF not found: {path}")
            arr, axes, roles = _fl_tiff_read_array(p)
            plane = _fl_tiff_plane_from_array(
                arr, axes, roles, z=z, c=c, t=t, extra_indices=extra_indices
            )
            b64 = _fl_frame_to_b64(plane, lut, p_low, p_high)
            return jsonify({"ok": True, "img": b64, "z": z, "c": c, "t": t})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/volume", methods=["POST"])
    @request_schema(Fluorescence3dVolumeRequest)
    def api_fl_3d_volume():
        if not has_tiff:
            return err("tifffile is required")
        try:
            body = parse_json_payload(Fluorescence3dVolumeRequest).model_dump()
            _path, payload = volume_payload_from_body(body, volume_export_ctx, for_export=False)
            return jsonify({"ok": True, "volume": payload})
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume", methods=["POST"])
    @request_schema(Fluorescence3dVolumeRequest)
    def api_fl_3d_export_volume():
        try:
            body = parse_json_payload(Fluorescence3dVolumeRequest).model_dump()
            result = export_volume_payload(body, volume_export_ctx)
            return api_ok(result, outputs=result["outputs"])
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_volume_job", methods=["POST"])
    @request_schema(Fluorescence3dVolumeRequest)
    def api_fl_3d_export_volume_job():
        try:
            body = parse_json_payload(Fluorescence3dVolumeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.export_volume3d",
            "Export fluorescence 3D viewer",
            _export_volume_task,
            body,
            metadata={"endpoint": "/api/fluorescence/3d/export_volume"},
        )

    @app.route("/api/fluorescence/3d/rotation_gif_preview", methods=["POST"])
    @request_schema(Fluorescence3dRotationGifRequest)
    def api_fl_3d_rotation_gif_preview():
        try:
            body = parse_json_payload(Fluorescence3dRotationGifRequest).model_dump()
            return api_ok(rotation_gif_payload(body, volume_export_ctx, preview=True))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_rotation_gif", methods=["POST"])
    @request_schema(Fluorescence3dRotationGifRequest)
    def api_fl_3d_export_rotation_gif():
        try:
            body = parse_json_payload(Fluorescence3dRotationGifRequest).model_dump()
            result = rotation_gif_payload(body, volume_export_ctx, preview=False)
            return api_ok(result, outputs=result["outputs"])
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/3d/export_rotation_gif_job", methods=["POST"])
    @request_schema(Fluorescence3dRotationGifRequest)
    def api_fl_3d_export_rotation_gif_job():
        try:
            body = parse_json_payload(Fluorescence3dRotationGifRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.export_3d_rotation_gif",
            "Export fluorescence 3D rotation GIF",
            _export_rotation_gif_task,
            body,
            metadata={"endpoint": "/api/fluorescence/3d/export_rotation_gif"},
        )

    @app.route("/api/fluorescence/3d/intensity_distribution", methods=["POST"])
    @request_schema(Fluorescence3dDistributionRequest)
    def api_fl_3d_intensity_distribution():
        try:
            body = parse_json_payload(Fluorescence3dDistributionRequest).model_dump()
            result = distribution_payload(body, volume_export_ctx)
            return api_ok(result, outputs=result["outputs"])
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())
