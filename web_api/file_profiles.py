from __future__ import annotations

from typing import Any

from flask import jsonify
from pydantic import Field, ValidationError

from services import file_profiles as file_profile_service

from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)


class FileProfileGetRequest(RequestModel):
    view: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    project_root: str = ""
    profile_name: str = ""


class FileProfileSaveRequest(FileProfileGetRequest):
    profile_name: str = "default"
    settings: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    make_last: bool = True


class FileProfileDeleteRequest(FileProfileGetRequest):
    profile_name: str = Field(min_length=1)


def register_file_profile_routes(app, ctx):
    err = ctx.err

    def _json_or_error(func, body):
        try:
            return jsonify(func(body))
        except ValueError as exc:
            return err(str(exc), 400)
        except Exception as exc:
            return err(exc)

    @app.route("/api/file_profiles/get", methods=["POST"])
    @request_schema(FileProfileGetRequest)
    def api_file_profiles_get():
        try:
            payload = parse_json_payload(FileProfileGetRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(file_profile_service.get_file_profile, payload.model_dump())

    @app.route("/api/file_profiles/save", methods=["POST"])
    @request_schema(FileProfileSaveRequest)
    def api_file_profiles_save():
        try:
            payload = parse_json_payload(FileProfileSaveRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(file_profile_service.save_file_profile, payload.model_dump())

    @app.route("/api/file_profiles/delete", methods=["POST"])
    @request_schema(FileProfileDeleteRequest)
    def api_file_profiles_delete():
        try:
            payload = parse_json_payload(FileProfileDeleteRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        return _json_or_error(file_profile_service.delete_file_profile, payload.model_dump())
