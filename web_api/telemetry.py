from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from .request_validation import RequestModel, parse_json_payload, request_schema, validation_error_response
from .response import api_ok


_telemetry_lock = threading.Lock()
_ALLOWED_EVENTS = {"page_open", "export_click", "startup"}


class TelemetryEventRequest(RequestModel):
    event: str = Field(default="", max_length=80)
    view: str = Field(default="unknown", max_length=80)
    label: str = Field(default="", max_length=80)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)
    return data if isinstance(data, dict) else dict(fallback)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _telemetry_enabled(prefs_path: Path) -> bool:
    prefs = _read_json(prefs_path, {})
    global_prefs = prefs.get("global") if isinstance(prefs.get("global"), dict) else {}
    return bool(global_prefs.get("telemetry_enabled") is True)


def register_telemetry_routes(app, ctx) -> None:
    base_dir = Path(ctx["BASE_DIR"])
    err = ctx["err"]
    prefs_path = base_dir / "web_gui_settings.json"
    telemetry_path = base_dir / ".dataprocess_cache" / "telemetry.json"

    @app.route("/api/telemetry/event", methods=["POST"])
    @request_schema(TelemetryEventRequest)
    def api_telemetry_event():
        try:
            payload = parse_json_payload(TelemetryEventRequest)
            event = payload.event.strip()
            if event not in _ALLOWED_EVENTS:
                return api_ok({"enabled": False, "recorded": False})
            if not _telemetry_enabled(prefs_path):
                return api_ok({"enabled": False, "recorded": False})

            view = payload.view.strip()[:80] or "unknown"
            label = payload.label.strip()[:80]
            key = f"{event}:{view}" + (f":{label}" if label else "")

            with _telemetry_lock:
                data = _read_json(telemetry_path, {"version": 1, "events": {}, "updated_at": ""})
                events = data.setdefault("events", {})
                events[key] = int(events.get(key) or 0) + 1
                data["updated_at"] = _now_iso()
                data["webgui_version"] = str(app.config.get("APP_VERSION") or "")
                data["remote_url_configured"] = bool(os.environ.get("DATAPROCESS_TELEMETRY_URL"))
                _write_json(telemetry_path, data)
            return api_ok({"enabled": True, "recorded": True})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception as exc:
            return err(exc)
