from __future__ import annotations

from typing import Any

from flask import jsonify
from pydantic import Field

from services import file_profiles as file_profile_service

from .request_validation import RequestModel, api_endpoint


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
    @app.route("/api/file_profiles/get", methods=["POST"])
    @api_endpoint(FileProfileGetRequest)
    def api_file_profiles_get(body):
        return jsonify(file_profile_service.get_file_profile(body))

    @app.route("/api/file_profiles/save", methods=["POST"])
    @api_endpoint(FileProfileSaveRequest)
    def api_file_profiles_save(body):
        return jsonify(file_profile_service.save_file_profile(body))

    @app.route("/api/file_profiles/delete", methods=["POST"])
    @api_endpoint(FileProfileDeleteRequest)
    def api_file_profiles_delete(body):
        return jsonify(file_profile_service.delete_file_profile(body))
