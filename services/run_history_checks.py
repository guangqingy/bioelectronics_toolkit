from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.run_history_paths import abs_path, as_path, fingerprint, path_from_record, rel_path


def fingerprint_status(record: dict[str, Any], current: dict[str, Any]) -> str:
    recorded_exists = bool(record.get("exists"))
    current_exists = bool(current.get("exists"))
    if not recorded_exists and not current_exists:
        return "not_recorded"
    if recorded_exists and not current_exists:
        return "missing"
    if not recorded_exists and current_exists:
        return "created_after_manifest"
    if record.get("size") == current.get("size") and record.get("mtime_ns") == current.get(
        "mtime_ns"
    ):
        return "unchanged"
    if record.get("size") == current.get("size"):
        return "timestamp_changed"
    return "changed"


def check_file_records(
    records: Any, kind: str, project_root: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
        p = path_from_record(item)
        if p is None:
            status = "invalid"
            current = {"exists": False}
            path_text = ""
        else:
            p = abs_path(p)
            current = fingerprint(p)
            status = fingerprint_status(item, current)
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
                "rel": str(
                    item.get("rel")
                    or (rel_path(Path(path_text), project_root) if path_text else "")
                ),
                "path": path_text,
            }
        )
    return rows, summary


def combine_check_summary(*parts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in parts:
        for key, value in part.items():
            out[key] = out.get(key, 0) + int(value or 0)
    return out


def check_manifest_files(manifest: dict[str, Any]) -> dict[str, Any]:
    project_root = abs_path(as_path(manifest.get("project_root")) or Path.cwd())
    input_rows, input_summary = check_file_records(
        manifest.get("input_files"), "input", project_root
    )
    output_rows, output_summary = check_file_records(
        manifest.get("outputs"), "output", project_root
    )
    summary = combine_check_summary(input_summary, output_summary)
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


def fmt_size(value: Any) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def manifest_markdown(
    manifest: dict[str, Any], manifest_path: Path | None, check: dict[str, Any] | None
) -> str:
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
            lines.append(
                f"- `{rec.get('rel') or rec.get('path') or ''}` ({fmt_size(rec.get('size'))})"
            )
    if not (manifest.get("input_files") or []):
        lines.append("- None recorded")
    lines.extend(["", "## Outputs", ""])
    for rec in manifest.get("outputs") or []:
        if isinstance(rec, dict):
            lines.append(
                f"- `{rec.get('rel') or rec.get('path') or ''}` ({fmt_size(rec.get('size'))})"
            )
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
    lines.extend(
        [
            "",
            "## Parameters",
            "",
            "```json",
            json.dumps(
                manifest.get("parameters") or {}, indent=2, ensure_ascii=False, sort_keys=True
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)
