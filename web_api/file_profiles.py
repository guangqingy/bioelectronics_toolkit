from __future__ import annotations

from flask import jsonify, request

from services import file_profiles as file_profile_service

_profile_lock = file_profile_service._profile_lock
_now_iso = file_profile_service._now_iso
_read_cache = file_profile_service._read_cache
_write_cache = file_profile_service._write_cache
_resolve_root = file_profile_service._resolve_root
_resolve_file = file_profile_service._resolve_file
_cache_path = file_profile_service._cache_path
_file_key = file_profile_service._file_key
_fingerprint = file_profile_service._fingerprint
_profile_response = file_profile_service._profile_response


def register_file_profile_routes(app, ctx):
    err = ctx["err"]

    @app.route("/api/file_profiles/get", methods=["POST"])
    def api_file_profiles_get():
        try:
            body = request.json or {}
            view = str(body.get("view", "")).strip()
            if not view:
                return err("Missing view")
            file_path = _resolve_file(body.get("file_path"))
            project_root = _resolve_root(body.get("project_root"), file_path)
            cache_path = _cache_path(project_root)
            key = _file_key(project_root, file_path)
            profile_name = str(body.get("profile_name", "") or "").strip() or None
            current_fp = _fingerprint(file_path)
            with _profile_lock:
                cache = _read_cache(cache_path, project_root)
                file_entry = cache.get("files", {}).get(key, {})
                if not isinstance(file_entry, dict):
                    file_entry = {}
            return jsonify(_profile_response(cache_path, project_root, key, file_entry, view, profile_name, current_fp))
        except Exception as exc:
            return err(exc)

    @app.route("/api/file_profiles/save", methods=["POST"])
    def api_file_profiles_save():
        try:
            body = request.json or {}
            view = str(body.get("view", "")).strip()
            if not view:
                return err("Missing view")
            profile_name = str(body.get("profile_name", "") or "default").strip() or "default"
            settings = body.get("settings") or {}
            payload = body.get("payload") or {}
            if not isinstance(settings, dict):
                return err("settings must be an object")
            if not isinstance(payload, dict):
                return err("payload must be an object")

            file_path = _resolve_file(body.get("file_path"))
            project_root = _resolve_root(body.get("project_root"), file_path)
            cache_path = _cache_path(project_root)
            key = _file_key(project_root, file_path)
            current_fp = _fingerprint(file_path)
            with _profile_lock:
                cache = _read_cache(cache_path, project_root)
                files = cache.setdefault("files", {})
                file_entry = files.setdefault(key, {})
                file_entry["path"] = str(file_path)
                file_entry["fingerprint"] = current_fp
                file_entry["updated_at"] = _now_iso()
                views = file_entry.setdefault("views", {})
                view_entry = views.setdefault(view, {})
                profiles = view_entry.setdefault("profiles", {})
                profiles[profile_name] = {
                    "settings": settings,
                    "payload": payload,
                    "updated_at": _now_iso(),
                }
                if body.get("make_last", True):
                    view_entry["last_profile"] = profile_name
                view_entry["updated_at"] = _now_iso()
                _write_cache(cache_path, cache)
                file_entry = files.get(key, {})
            return jsonify(_profile_response(cache_path, project_root, key, file_entry, view, profile_name, current_fp))
        except Exception as exc:
            return err(exc)

    @app.route("/api/file_profiles/delete", methods=["POST"])
    def api_file_profiles_delete():
        try:
            body = request.json or {}
            view = str(body.get("view", "")).strip()
            profile_name = str(body.get("profile_name", "") or "").strip()
            if not view:
                return err("Missing view")
            if not profile_name:
                return err("Missing profile_name")
            file_path = _resolve_file(body.get("file_path"))
            project_root = _resolve_root(body.get("project_root"), file_path)
            cache_path = _cache_path(project_root)
            key = _file_key(project_root, file_path)
            current_fp = _fingerprint(file_path)
            with _profile_lock:
                cache = _read_cache(cache_path, project_root)
                file_entry = cache.get("files", {}).get(key, {})
                view_entry = (file_entry.get("views") or {}).get(view, {}) if isinstance(file_entry, dict) else {}
                profiles = view_entry.get("profiles") if isinstance(view_entry, dict) else {}
                if isinstance(profiles, dict):
                    profiles.pop(profile_name, None)
                    if view_entry.get("last_profile") == profile_name:
                        view_entry["last_profile"] = next(iter(profiles), "")
                    view_entry["updated_at"] = _now_iso()
                    file_entry["updated_at"] = _now_iso()
                    _write_cache(cache_path, cache)
            return jsonify(_profile_response(cache_path, project_root, key, file_entry or {}, view, None, current_fp))
        except Exception as exc:
            return err(exc)
