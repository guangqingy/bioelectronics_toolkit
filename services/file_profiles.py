from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_profile_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_cache(project_root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(project_root),
        "updated_at": _now_iso(),
        "files": {},
    }


def _read_cache(path: Path, project_root: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_cache(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(path.suffix + ".invalid")
        try:
            path.replace(backup)
        except OSError:
            pass
        return _default_cache(project_root)
    if not isinstance(data, dict):
        return _default_cache(project_root)
    data.setdefault("version", 1)
    data.setdefault("project_root", str(project_root))
    data.setdefault("updated_at", _now_iso())
    data.setdefault("files", {})
    if not isinstance(data["files"], dict):
        data["files"] = {}
    return data


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    data["version"] = int(data.get("version") or 1)
    data["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _as_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _resolve_root(project_root: Any, file_path: Any) -> Path:
    root = _as_path(project_root)
    file_p = _as_path(file_path)
    if root is None:
        if file_p is None:
            raise ValueError("Missing project_root or file_path")
        root = file_p.parent if file_p.suffix else file_p
    if root.suffix and root.exists() and root.is_file():
        root = root.parent
    return root.resolve() if root.exists() else root.absolute()


def _resolve_file(file_path: Any) -> Path:
    file_p = _as_path(file_path)
    if file_p is None:
        raise ValueError("Missing file_path")
    return file_p.resolve() if file_p.exists() else file_p.absolute()


def _cache_path(project_root: Path) -> Path:
    return project_root / ".dataprocess_cache" / "file_profiles.json"


def _file_key(project_root: Path, file_path: Path) -> str:
    try:
        return file_path.relative_to(project_root).as_posix()
    except ValueError:
        return str(file_path)


def _fingerprint(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "suffix": path.suffix.lower(),
    }
    try:
        st = path.stat()
    except OSError:
        out["exists"] = False
        return out
    out.update(
        {
            "exists": True,
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
    )
    return out


def _same_fingerprint(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if not a or not b:
        return False
    return a.get("exists") and b.get("exists") and a.get("size") == b.get("size") and a.get("mtime_ns") == b.get("mtime_ns")


def _profile_response(
    cache_path: Path,
    project_root: Path,
    file_key: str,
    file_entry: dict[str, Any],
    view: str,
    profile_name: str | None,
    current_fp: dict[str, Any],
) -> dict[str, Any]:
    view_entry = (file_entry.get("views") or {}).get(view, {})
    profiles = view_entry.get("profiles") if isinstance(view_entry, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}
    last_profile = str(view_entry.get("last_profile") or "")
    selected = profile_name or last_profile or ("default" if "default" in profiles else "")
    profile = profiles.get(selected) if selected else None
    saved_fp = file_entry.get("fingerprint") if isinstance(file_entry.get("fingerprint"), dict) else {}
    return {
        "ok": True,
        "cache_path": str(cache_path),
        "project_root": str(project_root),
        "file_key": file_key,
        "fingerprint": current_fp,
        "saved_fingerprint": saved_fp,
        "stale": bool(saved_fp and current_fp.get("exists") and not _same_fingerprint(saved_fp, current_fp)),
        "view": view,
        "profiles": profiles,
        "last_profile": last_profile,
        "selected_profile": selected,
        "profile": profile,
    }


def get_file_profile(body: dict[str, Any]) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    view = str(body.get("view", "")).strip()
    if not view:
        raise ValueError("Missing view")
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
    return _profile_response(cache_path, project_root, key, file_entry, view, profile_name, current_fp)


def save_file_profile(body: dict[str, Any]) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    view = str(body.get("view", "")).strip()
    if not view:
        raise ValueError("Missing view")
    profile_name = str(body.get("profile_name", "") or "default").strip() or "default"
    settings = body.get("settings") or {}
    payload = body.get("payload") or {}
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

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
    return _profile_response(cache_path, project_root, key, file_entry, view, profile_name, current_fp)


def delete_file_profile(body: dict[str, Any]) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    view = str(body.get("view", "")).strip()
    profile_name = str(body.get("profile_name", "") or "").strip()
    if not view:
        raise ValueError("Missing view")
    if not profile_name:
        raise ValueError("Missing profile_name")
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
    return _profile_response(cache_path, project_root, key, file_entry or {}, view, None, current_fp)
