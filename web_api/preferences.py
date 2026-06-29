from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import jsonify
from pydantic import Field

from .request_validation import RequestModel, api_endpoint

_prefs_lock = threading.Lock()


class PreferencesGetRequest(RequestModel):
    pass


class PreferencesSaveRequest(RequestModel):
    prefs: dict[str, Any]


class ViewPreferencesGetRequest(RequestModel):
    view: str = Field(min_length=1)


class ViewPreferencesSaveRequest(RequestModel):
    view: str = Field(min_length=1)
    data: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_preferences() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now_iso(),
        "global": {},
        "views": {},
    }


def preferences_path(base_dir: Path) -> Path:
    base_dir = Path(base_dir)
    cache_path = base_dir / ".dataprocess_cache" / "web_gui_settings.json"
    legacy_path = base_dir / "web_gui_settings.json"
    if legacy_path.exists() and not cache_path.exists():
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy_path, cache_path)
        except OSError:
            return legacy_path
    return cache_path


def _read_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_preferences()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(path.suffix + ".invalid")
        try:
            path.replace(backup)
        except OSError:
            pass
        return _default_preferences()
    if not isinstance(data, dict):
        return _default_preferences()
    data.setdefault("version", 1)
    data.setdefault("global", {})
    data.setdefault("views", {})
    data.setdefault("updated_at", _now_iso())
    return data


def _write_preferences(path: Path, data: dict[str, Any]) -> None:
    data["version"] = int(data.get("version") or 1)
    data["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def register_preferences_routes(app, ctx):
    prefs_path = preferences_path(Path(ctx.BASE_DIR))

    @app.route("/api/preferences/get", methods=["POST"])
    @api_endpoint(PreferencesGetRequest, dump=False)
    def api_preferences_get(_payload):
        with _prefs_lock:
            prefs = _read_preferences(prefs_path)
        return jsonify({"ok": True, "path": str(prefs_path), "prefs": prefs})

    @app.route("/api/preferences/save", methods=["POST"])
    @api_endpoint(PreferencesSaveRequest, dump=False)
    def api_preferences_save(payload):
        prefs = payload.prefs
        prefs.setdefault("global", {})
        prefs.setdefault("views", {})
        with _prefs_lock:
            _write_preferences(prefs_path, prefs)
        return jsonify({"ok": True, "path": str(prefs_path), "prefs": prefs})

    @app.route("/api/preferences/view_get", methods=["POST"])
    @api_endpoint(ViewPreferencesGetRequest, dump=False)
    def api_preferences_view_get(payload):
        view = payload.view.strip()
        with _prefs_lock:
            prefs = _read_preferences(prefs_path)
        return jsonify(
            {
                "ok": True,
                "path": str(prefs_path),
                "view": view,
                "data": prefs.get("views", {}).get(view, {}),
            }
        )

    @app.route("/api/preferences/view_save", methods=["POST"])
    @api_endpoint(ViewPreferencesSaveRequest, dump=False)
    def api_preferences_view_save(payload):
        view = payload.view.strip()
        data = payload.data
        with _prefs_lock:
            prefs = _read_preferences(prefs_path)
            prefs.setdefault("views", {})[view] = data
            _write_preferences(prefs_path, prefs)
        return jsonify({"ok": True, "path": str(prefs_path), "view": view, "data": data})
