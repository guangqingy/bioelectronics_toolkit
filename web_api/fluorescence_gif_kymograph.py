from __future__ import annotations

import traceback
from typing import Any

from flask import jsonify
from pydantic import ValidationError

from services.fluorescence.gif_kymograph import build_gif_roi_kymograph_payload
from services.fluorescence.gif_kymograph_export import save_gif_roi_kymograph_outputs

from .fluorescence_request_schemas import (
    FluorescenceGifRoiKymographExportRequest,
    FluorescenceGifRoiKymographRequest,
)
from .jobs import route_response_to_payload, submit_json_task
from .path_policy import resolve_output_dir
from .request_validation import (
    api_endpoint,
    parse_json_payload,
    request_schema,
    validation_error_response,
)


def _validated_payload(model, payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return parse_json_payload(model).model_dump()
    return model.model_validate(payload).model_dump()


def register_fluorescence_gif_kymograph_routes(app, fl):
    err = fl["err"]
    jobs = fl["jobs"]

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    @app.route("/api/fluorescence/gif_roi/kymograph", methods=["POST"])
    @request_schema(FluorescenceGifRoiKymographRequest)
    def api_fl_gif_roi_kymograph(payload=None):
        """Build a time-vs-intensity distribution kymograph for one polygon ROI."""
        try:
            body = _validated_payload(FluorescenceGifRoiKymographRequest, payload)
        except ValidationError as exc:
            return validation_error_response(exc)

        try:
            return jsonify(
                build_gif_roi_kymograph_payload(
                    body,
                    helpers=fl,
                    resolve_output_dir=resolve_output_dir,
                )
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/kymograph_job", methods=["POST"])
    @api_endpoint(FluorescenceGifRoiKymographRequest)
    def api_fl_gif_roi_kymograph_job(body):
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_kymograph",
            "Build GIF ROI kymograph",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_fl_gif_roi_kymograph, "Building GIF ROI kymograph"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/kymograph"},
        )

    @app.route("/api/fluorescence/gif_roi/kymograph_export", methods=["POST"])
    @request_schema(FluorescenceGifRoiKymographExportRequest)
    def api_fl_gif_roi_kymograph_export(payload=None):
        """Save selected-ROI kymograph plot and data to disk."""
        try:
            body = _validated_payload(FluorescenceGifRoiKymographExportRequest, payload)
        except ValidationError as exc:
            return validation_error_response(exc)

        try:
            return jsonify(
                save_gif_roi_kymograph_outputs(
                    body,
                    bool_value=fl["_fl_bool"],
                    sanitize_prefix=fl["_fl_sanitize_prefix"],
                    decode_base64_payload=fl["_fl_decode_base64_payload"],
                    resolve_output_dir=resolve_output_dir,
                )
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/gif_roi/kymograph_export_job", methods=["POST"])
    @api_endpoint(FluorescenceGifRoiKymographExportRequest)
    def api_fl_gif_roi_kymograph_export_job(body):
        return submit_json_task(
            jobs,
            "fluorescence.gif_roi_kymograph_export",
            "Export GIF ROI kymograph outputs",
            lambda job_ctx, body: _response_task(
                job_ctx,
                body,
                api_fl_gif_roi_kymograph_export,
                "Exporting GIF ROI kymograph outputs",
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/gif_roi/kymograph_export"},
        )
