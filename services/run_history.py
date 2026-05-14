from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from services.run_history_checks import check_manifest_files, manifest_markdown
from services.run_history_package import package_run_manifest
from services.run_history_paths import (
    as_string_list,
    default_history,
    history_path,
    json_hash,
    load_manifest_from_request,
    manifest_dir,
    normalize_file_records,
    now_iso,
    read_json,
    resolve_root,
    sanitize_run_id,
    server_context,
    summary_from_manifest,
    write_json,
)

_run_lock = threading.Lock()
_MAX_HISTORY = 1000


def record_run(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Persist a run manifest and add it to the project history index."""
    if not isinstance(body, dict):
        raise ValueError("Missing run history object")

    project_root = resolve_root(body, base_dir)
    view = str(body.get("view") or "unknown").strip() or "unknown"
    run_id = sanitize_run_id(body.get("run_id") or f"{view}_{uuid.uuid4().hex[:10]}")
    now = now_iso()
    manifest_path = manifest_dir(project_root) / f"{run_id}.json"
    index_path = history_path(project_root)

    parameters = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    manifest = {
        "version": 2,
        "run_id": run_id,
        "view": view,
        "title": str(body.get("title") or view),
        "status": str(body.get("status") or "ok"),
        "project_root": str(project_root),
        "started_at": str(body.get("started_at") or ""),
        "completed_at": str(body.get("completed_at") or now),
        "profile_name": str(body.get("profile_name") or ""),
        "parameters": parameters,
        "input_files": normalize_file_records(body.get("input_files"), project_root),
        "outputs": normalize_file_records(body.get("outputs"), project_root),
        "warnings": as_string_list(body.get("warnings")),
        "errors": as_string_list(body.get("errors")),
        "metadata": metadata,
        "source": body.get("source") if isinstance(body.get("source"), dict) else {},
        "recorded_by": server_context(base_dir),
    }
    manifest["hashes"] = {
        "parameters_sha256": json_hash(manifest["parameters"]),
        "inputs_sha256": json_hash(manifest["input_files"]),
        "outputs_sha256": json_hash(manifest["outputs"]),
    }

    with _run_lock:
        write_json(manifest_path, manifest)
        history = read_json(index_path, default_history(project_root))
        history.setdefault("version", 1)
        history["project_root"] = str(project_root)
        history["updated_at"] = now
        runs = history.get("runs")
        if not isinstance(runs, list):
            runs = []
        summary = summary_from_manifest(manifest, manifest_path)
        runs = [r for r in runs if isinstance(r, dict) and r.get("run_id") != run_id]
        history["runs"] = [summary] + runs[: _MAX_HISTORY - 1]
        write_json(index_path, history)

    return {
        "ok": True,
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "history_path": str(index_path),
    }


def list_runs(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    project_root = resolve_root(body, base_dir)
    view = str(body.get("view") or "").strip()
    limit = int(body.get("limit") or 100)
    index_path = history_path(project_root)
    with _run_lock:
        history = read_json(index_path, default_history(project_root))
    runs = history.get("runs") if isinstance(history.get("runs"), list) else []
    if view:
        runs = [r for r in runs if isinstance(r, dict) and r.get("view") == view]
    return {
        "ok": True,
        "history_path": str(index_path),
        "project_root": str(project_root),
        "runs": runs[: max(1, min(limit, _MAX_HISTORY))],
    }


def get_run_manifest(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    with _run_lock:
        manifest, manifest_path = load_manifest_from_request(body, base_dir)
    if not manifest:
        raise FileNotFoundError(f"Run manifest not found: {manifest_path or ''}")
    return {"ok": True, "manifest_path": str(manifest_path or ""), "manifest": manifest}


def check_run_manifest(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    with _run_lock:
        manifest, manifest_path = load_manifest_from_request(body, base_dir)
    if not manifest:
        raise FileNotFoundError(f"Run manifest not found: {manifest_path or ''}")
    check = check_manifest_files(manifest)
    return {
        "ok": True,
        "manifest_path": str(manifest_path or ""),
        "run_id": manifest.get("run_id", ""),
        "check": check,
    }


def write_run_report(body: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    include_check = body.get("include_check") is not False
    with _run_lock:
        manifest, manifest_path = load_manifest_from_request(body, base_dir)
    if not manifest:
        raise FileNotFoundError(f"Run manifest not found: {manifest_path or ''}")
    check = check_manifest_files(manifest) if include_check else None
    report_text = manifest_markdown(manifest, manifest_path, check)
    report_path = None
    if manifest_path:
        report_path = manifest_path.with_suffix(".md")
        report_path.write_text(report_text, encoding="utf-8")
    return {
        "ok": True,
        "manifest_path": str(manifest_path or ""),
        "report_path": str(report_path or ""),
        "report": report_text,
        "check": check,
    }
