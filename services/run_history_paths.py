from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import re
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.provenance import runtime_provenance


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def abs_path(path: Path) -> Path:
    return path.resolve() if path.exists() else path.absolute()


def path_from_record(record: Any) -> Path | None:
    if isinstance(record, str):
        return as_path(record)
    if isinstance(record, dict):
        return as_path(record.get("path") or record.get("output_path") or record.get("input_path"))
    return None


def resolve_root(body: dict[str, Any], base_dir: Path) -> Path:
    explicit_text = str(body.get("project_root") or "").strip()
    if explicit_text in {".", "__DATAPROCESS_BASE_DIR__"}:
        return abs_path(base_dir)

    explicit = as_path(explicit_text)
    if explicit is not None:
        if explicit.suffix and explicit.exists() and explicit.is_file():
            explicit = explicit.parent
        return abs_path(explicit)

    for key in ("input_files", "outputs"):
        items = body.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            p = path_from_record(item)
            if p is None:
                continue
            if p.suffix or (p.exists() and p.is_file()):
                p = p.parent
            return abs_path(p)

    return abs_path(base_dir)


def history_path(project_root: Path) -> Path:
    return project_root / ".dataprocess_cache" / "run_history.json"


def manifest_dir(project_root: Path) -> Path:
    return project_root / ".dataprocess_cache" / "runs"


def sanitize_run_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return uuid.uuid4().hex[:12]
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:80] or uuid.uuid4().hex[:12]


def default_history(project_root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(project_root),
        "updated_at": now_iso(),
        "runs": [],
    }


def read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(path.suffix + ".invalid")
        try:
            path.replace(backup)
        except OSError:
            pass
        return fallback
    return data if isinstance(data, dict) else fallback


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def server_context(base_dir: Path) -> dict[str, Any]:
    return {
        "app": "DataProcess Web",
        "cwd": str(base_dir),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "tool": runtime_provenance(base_dir),
    }


def rel_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def fingerprint(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": False}
    try:
        st = path.stat()
    except OSError:
        return out
    out.update(
        {
            "exists": True,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_ns": st.st_mtime_ns,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
    )
    return out


def normalize_file_records(items: Any, project_root: Path) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    records: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            rec = {k: v for k, v in item.items() if k not in {"children"}}
        elif isinstance(item, str):
            rec = {"path": item}
        else:
            continue

        p = path_from_record(rec)
        if p is None:
            continue
        p = abs_path(p)
        rec["path"] = str(p)
        rec["name"] = rec.get("name") or p.name
        rec["rel"] = rec.get("rel") or rel_path(p, project_root)
        rec["ext"] = str(rec.get("ext") or p.suffix.lower().lstrip(".") or "file")
        rec.update(fingerprint(p))
        records.append(rec)
    return records


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if str(x).strip()]


def summary_from_manifest(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    return {
        "run_id": manifest.get("run_id", ""),
        "view": manifest.get("view", ""),
        "title": manifest.get("title", ""),
        "status": manifest.get("status", ""),
        "completed_at": manifest.get("completed_at", ""),
        "input_count": len(manifest.get("input_files") or []),
        "output_count": len(manifest.get("outputs") or []),
        "profile_name": manifest.get("profile_name", ""),
        "manifest_path": str(manifest_path),
    }


def load_manifest_from_request(
    body: dict[str, Any], base_dir: Path
) -> tuple[dict[str, Any], Path | None]:
    manifest = body.get("manifest")
    if isinstance(manifest, dict):
        return manifest, None

    manifest_path = as_path(body.get("manifest_path"))
    if manifest_path is None:
        project_root = resolve_root(body, base_dir)
        run_id = sanitize_run_id(body.get("run_id"))
        manifest_path = manifest_dir(project_root) / f"{run_id}.json"
    manifest_path = abs_path(manifest_path)
    return read_json(manifest_path, {}), manifest_path
