from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from services.histology_common import sanitize_name

_QUPATH_PROJECT_CACHE: list[Path] | None = None


def _write_server_json(json_path: Path, old_path: Path, new_path: Path, new_name: str) -> bool:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    changed = False
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    if isinstance(metadata, dict):
        if metadata.get("name") != new_name:
            metadata["name"] = new_name
            data["metadata"] = metadata
            changed = True

    uri = str(data.get("uri", "")) if isinstance(data, dict) else ""
    old_uri = old_path.as_posix()
    new_uri = new_path.as_posix()
    if old_uri in uri:
        data["uri"] = uri.replace(old_uri, new_uri)
        changed = True
    elif old_path.name in uri and new_path.name != old_path.name:
        data["uri"] = uri.replace(old_path.name, new_path.name)
        changed = True

    if not changed:
        return False

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _update_qupath_project(qpproj_path: Path, old_path: Path, new_path: Path, new_name: str) -> dict[str, Any]:
    try:
        data = json.loads(qpproj_path.read_text(encoding="utf-8"))
    except Exception:
        return {"project": str(qpproj_path), "updated": False, "error": "failed to read/parse"}

    if not isinstance(data, dict):
        return {"project": str(qpproj_path), "updated": False, "error": "invalid json"}

    images = data.get("images")
    if not isinstance(images, list):
        return {"project": str(qpproj_path), "updated": False, "error": "missing images"}

    changed = False
    updated_entry_ids: list[int] = []

    old_uri = old_path.as_posix()
    new_uri = new_path.as_posix()

    for img in images:
        if not isinstance(img, dict):
            continue
        sb = img.get("serverBuilder")
        if not isinstance(sb, dict):
            continue

        uri = str(sb.get("uri", "") or "")
        uri2 = uri
        if old_uri and old_uri in uri2:
            uri2 = uri2.replace(old_uri, new_uri)
        elif old_path.name and old_path.name in uri2 and new_path.name != old_path.name:
            uri2 = uri2.replace(old_path.name, new_path.name)

        if uri2 == uri:
            continue

        sb["uri"] = uri2
        img["serverBuilder"] = sb
        changed = True

        if str(img.get("imageName", "") or "") != new_name:
            img["imageName"] = new_name
            changed = True

        meta_img = img.get("metadata")
        if isinstance(meta_img, dict) and meta_img.get("name") != new_name:
            meta_img["name"] = new_name
            img["metadata"] = meta_img
            changed = True

        meta_sb = sb.get("metadata")
        if isinstance(meta_sb, dict) and meta_sb.get("name") != new_name:
            meta_sb["name"] = new_name
            sb["metadata"] = meta_sb
            img["serverBuilder"] = sb
            changed = True

        entry_id = img.get("entryID")
        if entry_id is not None:
            try:
                updated_entry_ids.append(int(entry_id))
            except Exception:
                pass

    updated_server_json: list[str] = []
    if changed:
        if "modifyTimestamp" in data:
            try:
                data["modifyTimestamp"] = int(time.time() * 1000)
            except Exception:
                pass
        qpproj_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        proj_dir = qpproj_path.parent
        for eid in sorted(set(updated_entry_ids)):
            sj = proj_dir / "data" / str(eid) / "server.json"
            if not sj.exists():
                continue
            if _write_server_json(sj, old_path, new_path, new_name):
                updated_server_json.append(str(sj))

    return {
        "project": str(qpproj_path),
        "updated": bool(changed),
        "updated_entry_ids": sorted(set(updated_entry_ids)),
        "updated_server_json": updated_server_json,
    }


def _get_qupath_project_candidates(dataset_parent: Path) -> list[Path]:
    global _QUPATH_PROJECT_CACHE
    candidates: list[Path] = []

    if _QUPATH_PROJECT_CACHE is None:
        try:
            workspace_root = Path(__file__).resolve().parents[1]
            _QUPATH_PROJECT_CACHE = list(workspace_root.rglob("project.qpproj"))
        except Exception:
            _QUPATH_PROJECT_CACHE = []
    candidates.extend(_QUPATH_PROJECT_CACHE)

    try:
        candidates.extend(list(dataset_parent.glob("*.qpproj")))
    except Exception:
        pass

    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp in seen or not rp.is_file():
            continue
        uniq.append(rp)
        seen.add(rp)
    return uniq


def _parse_qupath_project_paths(value: Any) -> list[Path]:
    if value is None:
        return []
    items: list[str] = []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        s = str(value).strip()
        if not s:
            return []
        items = re.split(r"[\n,;]+", s)

    out: list[Path] = []
    seen: set[Path] = set()
    for raw in items:
        p = str(raw or "").strip()
        if not p:
            continue
        path = Path(p).expanduser()
        try:
            path = path.resolve()
        except Exception:
            pass
        if path.suffix.lower() != ".qpproj":
            continue
        if path in seen:
            continue
        if path.is_file():
            out.append(path)
            seen.add(path)
    return out


def _qupath_uri_to_posix_path(uri: Any) -> str:
    s = str(uri or "").strip()
    if not s:
        return ""
    if s.startswith("file:"):
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(s)
            path = unquote(parsed.path or "")
            if not path:
                path = s[len("file:") :]
            if re.match(r"^/[A-Za-z]:/", path):
                path = path[1:]
            return path
        except Exception:
            return s[len("file:") :]
    return s


def _write_server_json_name_only(json_path: Path, new_name: str) -> bool:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False

    changed = False
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("name") != new_name:
        metadata["name"] = new_name
        data["metadata"] = metadata
        changed = True

    if not changed:
        return False

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _sync_qupath_project_names_from_cases(
    qpproj_path: Path,
    cases: list[dict[str, Any]],
    update_server_json: bool = True,
) -> dict[str, Any]:
    try:
        data = json.loads(qpproj_path.read_text(encoding="utf-8"))
    except Exception:
        return {"project": str(qpproj_path), "updated": False, "error": "failed to read/parse"}

    if not isinstance(data, dict):
        return {"project": str(qpproj_path), "updated": False, "error": "invalid json"}

    images = data.get("images")
    if not isinstance(images, list):
        return {"project": str(qpproj_path), "updated": False, "error": "missing images"}

    prefixes: list[tuple[str, str, str]] = []
    for case in cases or []:
        case_dir_raw = str((case or {}).get("case_dir", "") or "").strip()
        if not case_dir_raw:
            continue
        p = Path(case_dir_raw).expanduser()
        try:
            p = p.resolve()
        except Exception:
            pass
        case_name = str((case or {}).get("case_name", "") or "").strip() or p.name
        prefix = p.as_posix().rstrip("/") + "/"
        prefixes.append((prefix, case_name, p.as_posix()))

    prefixes.sort(key=lambda t: len(t[0]), reverse=True)
    matched_case_dirs: set[str] = set()

    changed = False
    updated_images_count = 0
    updated_entry_ids: list[int] = []
    entry_name_map: dict[int, str] = {}
    updated_details: list[dict[str, Any]] = []
    matched_images = 0
    unmatched_images = 0

    for img in images:
        if not isinstance(img, dict):
            continue
        sb = img.get("serverBuilder")
        if not isinstance(sb, dict):
            continue
        uri = sb.get("uri")
        path_posix = _qupath_uri_to_posix_path(uri)
        if not path_posix:
            continue

        desired_name: str | None = None
        matched_case_dir: str | None = None
        for prefix, case_name, case_dir_posix in prefixes:
            if path_posix.startswith(prefix):
                desired_name = case_name
                matched_case_dir = case_dir_posix
                break

        if desired_name is None:
            unmatched_images += 1
            continue

        matched_images += 1
        if matched_case_dir:
            matched_case_dirs.add(matched_case_dir)

        current_name = str(img.get("imageName", "") or "")
        if current_name == desired_name:
            continue

        img["imageName"] = desired_name
        changed = True
        updated_images_count += 1

        meta_img = img.get("metadata")
        if isinstance(meta_img, dict) and meta_img.get("name") != desired_name:
            meta_img["name"] = desired_name
            img["metadata"] = meta_img

        meta_sb = sb.get("metadata")
        if isinstance(meta_sb, dict) and meta_sb.get("name") != desired_name:
            meta_sb["name"] = desired_name
            sb["metadata"] = meta_sb
            img["serverBuilder"] = sb

        entry_id = img.get("entryID")
        if entry_id is not None:
            try:
                eid_int = int(entry_id)
                updated_entry_ids.append(eid_int)
                entry_name_map[eid_int] = desired_name
            except Exception:
                pass

        if len(updated_details) < 25:
            updated_details.append(
                {
                    "entryID": img.get("entryID"),
                    "old": current_name,
                    "new": desired_name,
                    "uri": str(uri or ""),
                }
            )

    updated_server_json: list[str] = []
    if changed:
        if "modifyTimestamp" in data:
            try:
                data["modifyTimestamp"] = int(time.time() * 1000)
            except Exception:
                pass
        qpproj_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if update_server_json and entry_name_map:
            proj_dir = qpproj_path.parent
            for eid, name in sorted(entry_name_map.items()):
                sj = proj_dir / "data" / str(eid) / "server.json"
                if not sj.exists():
                    continue
                if _write_server_json_name_only(sj, name):
                    updated_server_json.append(str(sj))

    unmatched_case_count = 0
    if prefixes:
        all_case_dirs = {cd for (_prefix, _name, cd) in prefixes}
        unmatched_case_count = len(all_case_dirs - matched_case_dirs)

    return {
        "project": str(qpproj_path),
        "updated": bool(changed),
        "updated_images": int(updated_images_count),
        "updated_entry_ids": sorted(set(updated_entry_ids)),
        "updated_server_json": updated_server_json,
        "matched_images": matched_images,
        "unmatched_images": unmatched_images,
        "unmatched_cases": unmatched_case_count,
        "details": updated_details,
    }


def sync_qupath_names_from_histology_cases(
    cases: list[dict[str, Any]],
    qupath_project: Any,
    update_server_json: bool = True,
) -> dict[str, Any]:
    qpprojs = _parse_qupath_project_paths(qupath_project)
    if not qpprojs:
        raise ValueError("QuPath project must be an existing .qpproj file")

    results: list[dict[str, Any]] = []
    total_updated = 0
    total_matched_images = 0
    total_unmatched_images = 0
    total_unmatched_cases = 0
    updated_projects: list[str] = []

    for qpproj in qpprojs:
        info = _sync_qupath_project_names_from_cases(qpproj, cases, update_server_json=update_server_json)
        if info.get("error"):
            raise ValueError(f"{qpproj}: {info.get('error')}")
        results.append(info)
        if info.get("updated"):
            updated_projects.append(str(qpproj))
            total_updated += int(info.get("updated_images") or 0)
        total_matched_images += int(info.get("matched_images") or 0)
        total_unmatched_images += int(info.get("unmatched_images") or 0)
        total_unmatched_cases = max(total_unmatched_cases, int(info.get("unmatched_cases") or 0))

    return {
        "updated_projects": sorted(set(updated_projects)),
        "updated_images": total_updated,
        "matched_images": total_matched_images,
        "unmatched_images": total_unmatched_images,
        "unmatched_cases": total_unmatched_cases,
        "results": results,
    }


def rename_histology_case(
    case_dir: str | Path,
    new_name: str,
    update_server_json: bool = True,
    qupath_project: str | Path | list[str] | None = None,
) -> dict[str, Any]:
    old_path = Path(case_dir).expanduser().resolve()
    if not old_path.exists():
        raise FileNotFoundError(f"Case folder not found: {old_path}")

    clean_name = sanitize_name(new_name, fallback=old_path.name)
    new_path = old_path.with_name(clean_name)
    if new_path.exists() and new_path != old_path:
        raise FileExistsError(f"Target folder already exists: {new_path}")

    qupath_projects: list[Path] = []
    if update_server_json and qupath_project:
        qupath_projects = _parse_qupath_project_paths(qupath_project)
        if not qupath_projects:
            raise ValueError("QuPath project must be an existing .qpproj file")

    old_path.rename(new_path)

    updated_server_json: list[str] = []
    updated_qupath_projects: list[str] = []
    if update_server_json:
        candidates = qupath_projects or _get_qupath_project_candidates(new_path.parent)
        for qpproj in candidates:
            try:
                head = qpproj.read_text(encoding="utf-8", errors="ignore")
                if old_path.as_posix() not in head and old_path.name not in head:
                    continue
            except Exception:
                pass
            info = _update_qupath_project(qpproj, old_path, new_path, clean_name)
            if info.get("updated"):
                updated_qupath_projects.append(str(qpproj))
                updated_server_json.extend(list(info.get("updated_server_json") or []))

    rename_map = new_path.parent / "histology_rename_map.csv"
    rows: list[dict[str, Any]] = []
    if rename_map.exists():
        try:
            import pandas as pd

            raw_rows = pd.read_csv(rename_map).to_dict(orient="records")
            rows = [{str(k): v for k, v in row.items()} for row in (raw_rows or [])]
        except Exception:
            rows = []
    rows = [row for row in rows if str(row.get("old_name", "")) != old_path.name]
    rows.append(
        {
            "old_name": old_path.name,
            "new_name": clean_name,
            "old_path": str(old_path),
            "new_path": str(new_path),
        }
    )
    try:
        import pandas as pd

        pd.DataFrame(rows).to_csv(rename_map, index=False)
    except Exception:
        pass

    return {
        "old_path": str(old_path),
        "new_path": str(new_path),
        "new_name": clean_name,
        "updated_server_json": sorted(set(updated_server_json)),
        "updated_qupath_projects": sorted(set(updated_qupath_projects)),
        "rename_map": str(rename_map),
    }
