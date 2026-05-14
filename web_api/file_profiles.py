from __future__ import annotations

from flask import jsonify, request

from services import file_profiles as file_profile_service


def register_file_profile_routes(app, ctx):
    err = ctx["err"]

    def _json_or_error(func, body):
        try:
            return jsonify(func(body))
        except ValueError as exc:
            return err(str(exc), 400)
        except Exception as exc:
            return err(exc)

    @app.route("/api/file_profiles/get", methods=["POST"])
    def api_file_profiles_get():
        return _json_or_error(file_profile_service.get_file_profile, request.json or {})

    @app.route("/api/file_profiles/save", methods=["POST"])
    def api_file_profiles_save():
        return _json_or_error(file_profile_service.save_file_profile, request.json or {})

    @app.route("/api/file_profiles/delete", methods=["POST"])
    def api_file_profiles_delete():
        return _json_or_error(file_profile_service.delete_file_profile, request.json or {})
