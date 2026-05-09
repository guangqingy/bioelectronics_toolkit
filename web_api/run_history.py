from __future__ import annotations

import json
import os
import getpass
import hashlib
import platform
import re
import socket
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import jsonify, request

from .jobs import submit_flask_route_job


_run_lock = threading.Lock()
_MAX_HISTORY = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).expanduser()


def _abs_path(path: Path) -> Path:
    return path.resolve() if path.exists() else path.absolute()


def _path_from_record(record: Any) -> Path | None:
    if isinstance(record, str):
        return _as_path(record)
    if isinstance(record, dict):
        return _as_path(record.get("path") or record.get("output_path") or record.get("input_path"))
    return None


def _resolve_root(body: dict[str, Any], base_dir: Path) -> Path:
    explicit = _as_path(body.get("project_root"))
    if explicit is not None:
        if explicit.suffix and explicit.exists() and explicit.is_file():
            explicit = explicit.parent
        return _abs_path(explicit)

    for key in ("input_files", "outputs"):
        items = body.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            p = _path_from_record(item)
            if p is None:
                continue
            if p.suffix or (p.exists() and p.is_file()):
                p = p.parent
            return _abs_path(p)

    return _abs_path(base_dir)


def _history_path(project_root: Path) -> Path:
    return project_root / ".dataprocess_cache" / "run_history.json"


def _manifest_dir(project_root: Path) -> Path:
    return project_root / ".dataprocess_cache" / "runs"


def _sanitize_run_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return uuid.uuid4().hex[:12]
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text[:80] or uuid.uuid4().hex[:12]


def _default_history(project_root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(project_root),
        "updated_at": _now_iso(),
        "runs": [],
    }


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _server_context(base_dir: Path) -> dict[str, Any]:
    return {
        "app": "DataProcess Web",
        "cwd": str(base_dir),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
    }


def _rel_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _fingerprint(path: Path) -> dict[str, Any]:
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
            "mtime_iso": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return out


def _normalize_file_records(items: Any, project_root: Path) -> list[dict[str, Any]]:
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

        p = _path_from_record(rec)
        if p is None:
            continue
        p = _abs_path(p)
        rec["path"] = str(p)
        rec["name"] = rec.get("name") or p.name
        rec["rel"] = rec.get("rel") or _rel_path(p, project_root)
        rec["ext"] = str(rec.get("ext") or p.suffix.lower().lstrip(".") or "file")
        rec.update(_fingerprint(p))
        records.append(rec)
    return records


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if str(x).strip()]


def _summary_from_manifest(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
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


def _load_manifest_from_request(body: dict[str, Any], base_dir: Path) -> tuple[dict[str, Any], Path | None]:
    manifest = body.get("manifest")
    if isinstance(manifest, dict):
        return manifest, None

    manifest_path = _as_path(body.get("manifest_path"))
    if manifest_path is None:
        project_root = _resolve_root(body, base_dir)
        run_id = _sanitize_run_id(body.get("run_id"))
        manifest_path = _manifest_dir(project_root) / f"{run_id}.json"
    manifest_path = _abs_path(manifest_path)
    return _read_json(manifest_path, {}), manifest_path


def _fingerprint_status(record: dict[str, Any], current: dict[str, Any]) -> str:
    recorded_exists = bool(record.get("exists"))
    current_exists = bool(current.get("exists"))
    if not recorded_exists and not current_exists:
        return "not_recorded"
    if recorded_exists and not current_exists:
        return "missing"
    if not recorded_exists and current_exists:
        return "created_after_manifest"
    if record.get("size") == current.get("size") and record.get("mtime_ns") == current.get("mtime_ns"):
        return "unchanged"
    if record.get("size") == current.get("size"):
        return "timestamp_changed"
    return "changed"


def _check_file_records(records: Any, kind: str, project_root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    summary = {
        "total": 0,
        "unchanged": 0,
        "changed": 0,
        "timestamp_changed": 0,
        "missing": 0,
        "created_after_manifest": 0,
        "not_recorded": 0,
        "invalid": 0,
    }
    if not isinstance(records, list):
        return rows, summary

    for item in records:
        if not isinstance(item, dict):
            summary["invalid"] += 1
            continue
        summary["total"] += 1
        p = _path_from_record(item)
        if p is None:
            status = "invalid"
            current = {"exists": False}
            path_text = ""
        else:
            p = _abs_path(p)
            current = _fingerprint(p)
            status = _fingerprint_status(item, current)
            path_text = str(p)
        summary[status] = summary.get(status, 0) + 1
        rows.append(
            {
                "kind": kind,
                "name": str(item.get("name") or (Path(path_text).name if path_text else "")),
                "role": str(item.get("type") or item.get("role") or item.get("ext") or ""),
                "status": status,
                "recorded_size": item.get("size"),
                "current_size": current.get("size"),
                "recorded_mtime": item.get("mtime_iso") or "",
                "current_mtime": current.get("mtime_iso") or "",
                "rel": str(item.get("rel") or (_rel_path(Path(path_text), project_root) if path_text else "")),
                "path": path_text,
            }
        )
    return rows, summary


def _combine_check_summary(*parts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in parts:
        for key, value in part.items():
            out[key] = out.get(key, 0) + int(value or 0)
    return out


def _check_manifest_files(manifest: dict[str, Any]) -> dict[str, Any]:
    project_root = _abs_path(_as_path(manifest.get("project_root")) or Path.cwd())
    input_rows, input_summary = _check_file_records(manifest.get("input_files"), "input", project_root)
    output_rows, output_summary = _check_file_records(manifest.get("outputs"), "output", project_root)
    summary = _combine_check_summary(input_summary, output_summary)
    problem_count = (
        summary.get("changed", 0)
        + summary.get("timestamp_changed", 0)
        + summary.get("missing", 0)
        + summary.get("created_after_manifest", 0)
        + summary.get("invalid", 0)
    )
    if problem_count:
        status = "attention"
    elif summary.get("total", 0):
        status = "ok"
    else:
        status = "empty"
    return {
        "status": status,
        "summary": summary,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "rows": input_rows + output_rows,
    }


def _fmt_size(value: Any) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _manifest_markdown(manifest: dict[str, Any], manifest_path: Path | None, check: dict[str, Any] | None) -> str:
    lines = [
        f"# {manifest.get('title') or manifest.get('view') or 'DataProcess Run'}",
        "",
        f"- Run ID: `{manifest.get('run_id', '')}`",
        f"- View: `{manifest.get('view', '')}`",
        f"- Status: `{manifest.get('status', '')}`",
        f"- Completed: `{manifest.get('completed_at', '')}`",
        f"- Project root: `{manifest.get('project_root', '')}`",
    ]
    if manifest_path:
        lines.append(f"- Manifest: `{manifest_path}`")
    lines.extend(["", "## Inputs", ""])
    for rec in manifest.get("input_files") or []:
        if isinstance(rec, dict):
            lines.append(f"- `{rec.get('rel') or rec.get('path') or ''}` ({_fmt_size(rec.get('size'))})")
    if not (manifest.get("input_files") or []):
        lines.append("- None recorded")
    lines.extend(["", "## Outputs", ""])
    for rec in manifest.get("outputs") or []:
        if isinstance(rec, dict):
            lines.append(f"- `{rec.get('rel') or rec.get('path') or ''}` ({_fmt_size(rec.get('size'))})")
    if not (manifest.get("outputs") or []):
        lines.append("- None recorded")
    if check:
        summary = check.get("summary") or {}
        lines.extend(
            [
                "",
                "## File Check",
                "",
                f"- Status: `{check.get('status', '')}`",
                f"- Total: {summary.get('total', 0)}",
                f"- Unchanged: {summary.get('unchanged', 0)}",
                f"- Changed/timestamp changed: {summary.get('changed', 0) + summary.get('timestamp_changed', 0)}",
                f"- Missing: {summary.get('missing', 0)}",
            ]
        )
    lines.extend(["", "## Parameters", "", "```json", json.dumps(manifest.get("parameters") or {}, indent=2, ensure_ascii=False, sort_keys=True), "```", ""])
    return "\n".join(lines)


def _zip_member_name(prefix: str, rec: dict[str, Any], used: set[str]) -> str:
    rel = str(rec.get("rel") or rec.get("name") or "").strip().replace("\\", "/").lstrip("/")
    if not rel or rel.startswith(".."):
        path = _path_from_record(rec)
        rel = path.name if path else "file"
    candidate = f"{prefix}/{rel}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    stem = Path(rel).stem or "file"
    suffix = Path(rel).suffix
    i = 2
    while True:
        candidate = f"{prefix}/{stem}_{i}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def _add_records_to_zip(zf: zipfile.ZipFile, records: Any, prefix: str, used: set[str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    included: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    if not isinstance(records, list):
        return included, missing
    for rec in records:
        if not isinstance(rec, dict):
            continue
        path = _path_from_record(rec)
        if path is None:
            missing.append({"path": "", "reason": "invalid_record"})
            continue
        path = _abs_path(path)
        if not path.exists() or not path.is_file():
            missing.append({"path": str(path), "reason": "missing"})
            continue
        arcname = _zip_member_name(prefix, rec, used)
        zf.write(path, arcname)
        included.append({"path": str(path), "archive_name": arcname})
    return included, missing


def register_run_history_routes(app, ctx):
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])
    jobs = ctx.get("jobs")

    @app.route("/api/run_history/record", methods=["POST"])
    def api_run_history_record():
        try:
            body = request.json or {}
            if not isinstance(body, dict):
                return err("Missing run history object")

            project_root = _resolve_root(body, base_dir)
            view = str(body.get("view") or "unknown").strip() or "unknown"
            run_id = _sanitize_run_id(body.get("run_id") or f"{view}_{uuid.uuid4().hex[:10]}")
            now = _now_iso()
            manifest_path = _manifest_dir(project_root) / f"{run_id}.json"
            history_path = _history_path(project_root)

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
                "input_files": _normalize_file_records(body.get("input_files"), project_root),
                "outputs": _normalize_file_records(body.get("outputs"), project_root),
                "warnings": _as_string_list(body.get("warnings")),
                "errors": _as_string_list(body.get("errors")),
                "metadata": metadata,
                "source": body.get("source") if isinstance(body.get("source"), dict) else {},
                "recorded_by": _server_context(base_dir),
            }
            manifest["hashes"] = {
                "parameters_sha256": _json_hash(manifest["parameters"]),
                "inputs_sha256": _json_hash(manifest["input_files"]),
                "outputs_sha256": _json_hash(manifest["outputs"]),
            }

            with _run_lock:
                _write_json(manifest_path, manifest)
                history = _read_json(history_path, _default_history(project_root))
                history.setdefault("version", 1)
                history["project_root"] = str(project_root)
                history["updated_at"] = now
                runs = history.get("runs")
                if not isinstance(runs, list):
                    runs = []
                summary = _summary_from_manifest(manifest, manifest_path)
                runs = [r for r in runs if isinstance(r, dict) and r.get("run_id") != run_id]
                history["runs"] = [summary] + runs[: _MAX_HISTORY - 1]
                _write_json(history_path, history)

            return jsonify(
                {
                    "ok": True,
                    "manifest": manifest,
                    "manifest_path": str(manifest_path),
                    "history_path": str(history_path),
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/list", methods=["POST"])
    def api_run_history_list():
        try:
            body = request.json or {}
            project_root = _resolve_root(body, base_dir)
            view = str(body.get("view") or "").strip()
            limit = int(body.get("limit") or 100)
            history_path = _history_path(project_root)
            with _run_lock:
                history = _read_json(history_path, _default_history(project_root))
            runs = history.get("runs") if isinstance(history.get("runs"), list) else []
            if view:
                runs = [r for r in runs if isinstance(r, dict) and r.get("view") == view]
            return jsonify(
                {
                    "ok": True,
                    "history_path": str(history_path),
                    "project_root": str(project_root),
                    "runs": runs[: max(1, min(limit, _MAX_HISTORY))],
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/get", methods=["POST"])
    def api_run_history_get():
        try:
            body = request.json or {}
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            return jsonify({"ok": True, "manifest_path": str(manifest_path or ""), "manifest": manifest})
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/check", methods=["POST"])
    def api_run_history_check():
        try:
            body = request.json or {}
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            check = _check_manifest_files(manifest)
            return jsonify(
                {
                    "ok": True,
                    "manifest_path": str(manifest_path or ""),
                    "run_id": manifest.get("run_id", ""),
                    "check": check,
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/package_job", methods=["POST"])
    def api_run_history_package_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/run_history/package",
            "run_history.package",
            "Package run manifest",
            api_run_history_package,
            request.json or {},
        )

    @app.route("/api/run_history/report", methods=["POST"])
    def api_run_history_report():
        try:
            body = request.json or {}
            include_check = body.get("include_check") is not False
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)
            check = _check_manifest_files(manifest) if include_check else None
            report_text = _manifest_markdown(manifest, manifest_path, check)
            report_path = None
            if manifest_path:
                report_path = manifest_path.with_suffix(".md")
                report_path.write_text(report_text, encoding="utf-8")
            return jsonify(
                {
                    "ok": True,
                    "manifest_path": str(manifest_path or ""),
                    "report_path": str(report_path or ""),
                    "report": report_text,
                    "check": check,
                }
            )
        except Exception as exc:
            return err(exc)

    @app.route("/api/run_history/package", methods=["POST"])
    def api_run_history_package():
        try:
            body = request.json or {}
            include_inputs = bool(body.get("include_inputs"))
            include_outputs = body.get("include_outputs") is not False
            with _run_lock:
                manifest, manifest_path = _load_manifest_from_request(body, base_dir)
            if not manifest:
                return err(f"Run manifest not found: {manifest_path or ''}", 404)

            run_id = _sanitize_run_id(manifest.get("run_id") or "run")
            if manifest_path:
                package_path = manifest_path.with_suffix(".zip")
            else:
                project_root = _abs_path(_as_path(manifest.get("project_root")) or base_dir)
                package_path = _manifest_dir(project_root) / f"{run_id}.zip"
            package_path.parent.mkdir(parents=True, exist_ok=True)

            check = _check_manifest_files(manifest)
            report_text = _manifest_markdown(manifest, manifest_path, check)
            index: dict[str, Any] = {
                "run_id": run_id,
                "created_at": _now_iso(),
                "include_inputs": include_inputs,
                "include_outputs": include_outputs,
                "manifest_path": str(manifest_path or ""),
                "included": [],
                "missing": [],
            }
            used = {"manifest.json", "report.md", "package_index.json"}
            tmp = package_path.with_suffix(package_path.suffix + ".tmp")
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
                zf.writestr("report.md", report_text)
                if include_outputs:
                    included, missing = _add_records_to_zip(zf, manifest.get("outputs"), "outputs", used)
                    index["included"].extend(included)
                    index["missing"].extend(missing)
                if include_inputs:
                    included, missing = _add_records_to_zip(zf, manifest.get("input_files"), "inputs", used)
                    index["included"].extend(included)
                    index["missing"].extend(missing)
                zf.writestr("package_index.json", json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(tmp, package_path)

            return jsonify(
                {
                    "ok": True,
                    "manifest_path": str(manifest_path or ""),
                    "package_path": str(package_path),
                    "included_count": len(index["included"]),
                    "missing_count": len(index["missing"]),
                    "index": index,
                    "check": check,
                }
            )
        except Exception as exc:
            return err(exc)
