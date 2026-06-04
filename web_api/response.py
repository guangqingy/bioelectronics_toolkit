from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import current_app, has_app_context, jsonify, request

from services.output_records import as_list, infer_outputs, is_envelope

LOG = logging.getLogger(__name__)


def make_envelope(payload: Any = None, *, ok: bool = True, error: Any = None) -> dict[str, Any]:
    if is_envelope(payload):
        if payload.get("ok") is not False and not payload.get("outputs"):
            enriched = dict(payload)
            enriched["outputs"] = infer_outputs(payload)
            return enriched
        return payload

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    legacy_error = payload.get("error")
    is_ok = bool(ok) and legacy_error in (None, "")
    data = {} if not is_ok else dict(payload)
    outputs = infer_outputs(payload) if is_ok else as_list(payload.get("outputs"))
    warnings = as_list(payload.get("warnings"))
    envelope = {
        "ok": is_ok,
        "data": data,
        "outputs": outputs,
        "warnings": warnings,
        "error": None if is_ok else str(error or legacy_error or "Request failed"),
    }

    # Temporary compatibility layer: existing pages still read fields such as
    # saved_path/files/img directly from the response object.
    for key, value in payload.items():
        if key not in envelope:
            envelope[key] = value
    return envelope


def api_ok(
    data: dict[str, Any] | None = None,
    *,
    outputs: list[Any] | None = None,
    warnings: list[str] | None = None,
    **extra,
):
    payload = dict(data or {})
    payload.update(extra)
    if outputs is not None:
        payload["outputs"] = outputs
    if warnings is not None:
        payload["warnings"] = warnings
    return jsonify(make_envelope(payload, ok=True))


def _debug_errors_enabled() -> bool:
    if not has_app_context():
        return False
    return bool(current_app.config.get("DEBUG"))


def _looks_like_traceback(message: Any) -> bool:
    text = str(message or "")
    return "Traceback (most recent call last)" in text or "\n  File " in text


def _friendly_traceback_message(correlation_id: str) -> str:
    return (
        "The operation failed before it could finish. Check that the selected file "
        "exists, is not corrupted, and matches this tool, then try again. "
        f"Error ID: {correlation_id}"
    )


def _public_error(message: Any, code: int) -> tuple[str, int, str | None, str | None]:
    if not _looks_like_traceback(message):
        return str(message), code, None, None
    correlation_id = uuid.uuid4().hex[:8]
    technical_details = str(message)
    LOG.error("[%s] Unhandled API exception\n%s", correlation_id, technical_details)
    if _debug_errors_enabled():
        return technical_details, max(500, code), correlation_id, technical_details
    return _friendly_traceback_message(correlation_id), max(500, code), correlation_id, technical_details


def api_error(
    message: Any,
    code: int = 400,
    *,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
):
    message, code, correlation_id, technical_details = _public_error(message, code)
    payload = dict(data or {})
    payload["error"] = message
    if correlation_id:
        payload["id"] = correlation_id
        payload["error_id"] = correlation_id
    if technical_details:
        payload["technical_details"] = technical_details
    if warnings is not None:
        payload["warnings"] = warnings
    return jsonify(make_envelope(payload, ok=False, error=message)), code


def register_api_envelope(app) -> None:
    @app.after_request
    def _wrap_api_json_response(response):
        if not request.path.startswith("/api/") or not response.is_json:
            return response
        if response.headers.get("X-DP-Envelope") == "1":
            return response
        payload = response.get_json(silent=True)
        if payload is None:
            return response
        ok = 200 <= response.status_code < 400
        envelope = make_envelope(payload, ok=ok)
        wrapped = jsonify(envelope)
        wrapped.status_code = response.status_code
        wrapped.headers["X-DP-Envelope"] = "1"
        return wrapped
