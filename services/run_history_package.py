from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any

from services.run_history_checks import check_manifest_files, manifest_markdown
from services.run_history_paths import (
    abs_path,
    as_path,
    load_manifest_from_request,
    manifest_dir,
    now_iso,
    path_from_record,
    sanitize_run_id,
)


def package_run_manifest(
    body: dict[str, Any], base_dir: Path, job_ctx: Any = None
) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    include_inputs = bool(body.get("include_inputs"))
    include_outputs = body.get("include_outputs") is not False
    if job_ctx is not None:
        job_ctx.check_cancelled()
        job_ctx.set_progress(0.08, "Loading run manifest")
    manifest, manifest_path = load_manifest_from_request(body, base_dir)
    if not manifest:
        return {"ok": False, "error": f"Run manifest not found: {manifest_path or ''}"}

    run_id = sanitize_run_id(manifest.get("run_id") or "run")
    if manifest_path:
        package_path = manifest_path.with_suffix(".zip")
    else:
        project_root = abs_path(as_path(manifest.get("project_root")) or base_dir)
        package_path = manifest_dir(project_root) / f"{run_id}.zip"
    package_path.parent.mkdir(parents=True, exist_ok=True)

    if job_ctx is not None:
        job_ctx.check_cancelled()
        job_ctx.set_progress(0.22, "Checking files")
    check = check_manifest_files(manifest)
    report_text = manifest_markdown(manifest, manifest_path, check)
    index: dict[str, Any] = {
        "run_id": run_id,
        "created_at": now_iso(),
        "include_inputs": include_inputs,
        "include_outputs": include_outputs,
        "manifest_path": str(manifest_path or ""),
        "included": [],
        "missing": [],
    }
    used = {"manifest.json", "report.md", "package_index.json"}
    tmp = package_path.with_suffix(package_path.suffix + ".tmp")
    if job_ctx is not None:
        job_ctx.check_cancelled()
        job_ctx.set_progress(0.4, "Writing package")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        )
        zf.writestr("report.md", report_text)
        if include_outputs:
            included, missing = _add_records_to_zip(zf, manifest.get("outputs"), "outputs", used)
            index["included"].extend(included)
            index["missing"].extend(missing)
        if include_inputs:
            included, missing = _add_records_to_zip(zf, manifest.get("input_files"), "inputs", used)
            index["included"].extend(included)
            index["missing"].extend(missing)
        zf.writestr(
            "package_index.json",
            json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        )
    os.replace(tmp, package_path)

    return {
        "ok": True,
        "manifest_path": str(manifest_path or ""),
        "package_path": str(package_path),
        "included_count": len(index["included"]),
        "missing_count": len(index["missing"]),
        "index": index,
        "check": check,
    }


def _zip_member_name(prefix: str, rec: dict[str, Any], used: set[str]) -> str:
    rel = str(rec.get("rel") or rec.get("name") or "").strip().replace("\\", "/").lstrip("/")
    if not rel or rel.startswith(".."):
        path = path_from_record(rec)
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


def _add_records_to_zip(
    zf: zipfile.ZipFile,
    records: Any,
    prefix: str,
    used: set[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    included: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    if not isinstance(records, list):
        return included, missing
    for rec in records:
        if not isinstance(rec, dict):
            continue
        path = path_from_record(rec)
        if path is None:
            missing.append({"path": "", "reason": "invalid_record"})
            continue
        path = abs_path(path)
        if not path.exists() or not path.is_file():
            missing.append({"path": str(path), "reason": "missing"})
            continue
        arcname = _zip_member_name(prefix, rec, used)
        zf.write(path, arcname)
        included.append({"path": str(path), "archive_name": arcname})
    return included, missing
