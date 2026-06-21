from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.background_jobs import JobContext


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _int_range(value: object, default: int, low: int, high: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _options(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": Path(str(data.get("root", "") or "").strip()).expanduser(),
        "find": str(data.get("find", "") or ""),
        "replace": str(data.get("replace", "") or ""),
        "prefix": str(data.get("prefix", "") or ""),
        "suffix": str(data.get("suffix", "") or ""),
        "recursive": _as_bool(data.get("recursive"), True),
        "include_root": _as_bool(data.get("include_root"), False),
        "include_files": _as_bool(data.get("include_files"), True),
        "include_dirs": _as_bool(data.get("include_dirs"), True),
        "use_regex": _as_bool(data.get("use_regex"), False),
        "case_sensitive": _as_bool(data.get("case_sensitive"), True),
        "preserve_extension": _as_bool(data.get("preserve_extension"), True),
        "skip_hidden": _as_bool(data.get("skip_hidden"), True),
        "max_items": _int_range(data.get("max_items"), 5000, 1, 50000),
        "extensions": _extensions(data.get("extensions")),
    }


def _hidden_path(path: Path, root: Path) -> bool:
    try:
        return any(part.startswith(".") for part in path.relative_to(root).parts)
    except ValueError:
        return False


def _extensions(value: object) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, (list, tuple, set)):
        raw_parts = [str(item) for item in value]
    else:
        raw_parts = re.split(r"[\s,;]+", str(value))
    out = set()
    for raw in raw_parts:
        ext = raw.strip().lower()
        if not ext:
            continue
        out.add(ext if ext.startswith(".") else f".{ext}")
    return out


def _kind(path: Path) -> str | None:
    if path.is_dir() and not path.is_symlink():
        return "folder"
    if path.is_file() or path.is_symlink():
        return "file"
    return None


def _replace_text(text: str, opts: dict[str, Any]) -> str:
    find = opts["find"]
    if not find:
        return text
    flags = 0 if opts["case_sensitive"] else re.IGNORECASE
    if opts["use_regex"]:
        try:
            return re.sub(find, opts["replace"], text, flags=flags)
        except re.error as exc:
            raise ValueError(f"Invalid rename regex: {exc}") from exc
    return re.sub(re.escape(find), lambda _match: opts["replace"], text, flags=flags)


def _rename_component(name: str, *, kind: str, opts: dict[str, Any]) -> str:
    if kind == "file" and opts["preserve_extension"]:
        parsed = Path(name)
        base = parsed.stem
        ext = parsed.suffix
    else:
        base = name
        ext = ""
    renamed = _replace_text(base, opts)
    return f"{opts['prefix']}{renamed}{opts['suffix']}{ext}"


def _invalid_name(name: str) -> str:
    if not name:
        return "New name is empty."
    if name in {".", ".."}:
        return "New name cannot be . or ..."
    if "\x00" in name:
        return "New name contains a NUL byte."
    if "/" in name or "\\" in name:
        return "New name cannot contain path separators."
    return ""


def _iter_candidates(root: Path, opts: dict[str, Any]) -> tuple[list[Path], bool]:
    iterator = root.rglob("*") if opts["recursive"] else root.iterdir()
    items: list[Path] = []
    truncated = False
    if opts["include_root"] and opts["include_dirs"]:
        items.append(root)
    for item in sorted(iterator, key=lambda p: str(p).lower()):
        if opts["skip_hidden"] and _hidden_path(item, root):
            continue
        kind = _kind(item)
        if kind == "file" and not opts["include_files"]:
            continue
        if kind == "file" and opts["extensions"] and item.suffix.lower() not in opts["extensions"]:
            continue
        if kind == "folder" and not opts["include_dirs"]:
            continue
        if kind is None:
            continue
        if len(items) >= opts["max_items"]:
            truncated = True
            break
        items.append(item)
    return items, truncated


def _same_existing_path(a: Path, b: Path) -> bool:
    if a == b:
        return True
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _target_parts(path: Path, root: Path, opts: dict[str, Any]) -> list[str]:
    parts = list(path.relative_to(root).parts)
    out: list[str] = []
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        component_kind = _kind(path) if is_last else "folder"
        if component_kind == "folder" and opts["include_dirs"]:
            out.append(_rename_component(part, kind="folder", opts=opts))
        elif component_kind == "file" and opts["include_files"]:
            out.append(_rename_component(part, kind="file", opts=opts))
        else:
            out.append(part)
    return out


def _target_root(root: Path, opts: dict[str, Any]) -> Path:
    if opts["include_root"] and opts["include_dirs"]:
        return root.with_name(_rename_component(root.name, kind="folder", opts=opts))
    return root


def _build_change(path: Path, root: Path, opts: dict[str, Any]) -> dict[str, Any] | None:
    kind = _kind(path)
    if kind is None:
        return None

    new_name = _rename_component(path.name, kind=kind, opts=opts)
    if new_name == path.name:
        return None

    direct_target = path.with_name(new_name)
    if path == root:
        final_target = direct_target
        depth = 0
    else:
        final_target = _target_root(root, opts).joinpath(*_target_parts(path, root, opts))
        depth = len(path.relative_to(root).parts)
    status = "ready"
    reason = ""
    invalid_reason = _invalid_name(new_name)
    if invalid_reason:
        status = "invalid"
        reason = invalid_reason
    elif direct_target.exists() and not _same_existing_path(direct_target, path):
        status = "target_exists"
        reason = "Target already exists."

    return {
        "kind": kind,
        "status": status,
        "reason": reason,
        "source_path": str(path),
        "target_path": str(final_target),
        "direct_target_path": str(direct_target),
        "old_name": path.name,
        "new_name": new_name,
        "depth": depth,
    }


def preview_payload(data: dict[str, Any]) -> dict[str, Any]:
    opts = _options(data)
    root = opts["root"]
    if not root.is_dir():
        raise ValueError(f"Root folder not found: {root}")
    if not opts["include_files"] and not opts["include_dirs"]:
        raise ValueError("Choose files, folders, or both.")
    if not (opts["find"] or opts["prefix"] or opts["suffix"]):
        raise ValueError("Enter find text, a prefix, or a suffix.")

    candidates, truncated = _iter_candidates(root, opts)
    changes = [change for item in candidates if (change := _build_change(item, root, opts))]

    direct_targets: dict[str, int] = {}
    final_targets: dict[str, int] = {}
    for index, change in enumerate(changes):
        direct_targets.setdefault(change["direct_target_path"], index)
        final_targets.setdefault(change["target_path"], index)

    for index, change in enumerate(changes):
        if change["status"] != "ready":
            continue
        if direct_targets.get(change["direct_target_path"]) != index:
            change["status"] = "duplicate_target"
            change["reason"] = "Another item would receive the same direct target."
        elif final_targets.get(change["target_path"]) != index:
            change["status"] = "duplicate_target"
            change["reason"] = "Another item would receive the same final path."

    conflict_count = sum(1 for change in changes if change["status"] != "ready")
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"Preview stopped at {opts['max_items']} item(s); narrow the folder or scope."
        )

    return {
        "ok": True,
        "root": str(root),
        "scanned_count": len(candidates),
        "changed_count": len(changes),
        "ready_count": len(changes) - conflict_count,
        "conflict_count": conflict_count,
        "changes": changes,
        "warnings": warnings,
        "truncated": truncated,
    }


def _preflight(changes: list[dict[str, Any]]) -> None:
    blockers = [change for change in changes if change["status"] != "ready"]
    if blockers:
        raise ValueError(f"Resolve {len(blockers)} rename conflict(s) before applying.")
    for change in changes:
        source = Path(change["source_path"])
        target = Path(change["direct_target_path"])
        if not source.exists():
            raise ValueError(f"Source no longer exists: {source}")
        if target.exists() and not _same_existing_path(target, source):
            raise ValueError(f"Target already exists: {target}")


def _updated_root(root: str, changes: list[dict[str, Any]]) -> str:
    for change in changes:
        if change.get("source_path") == root:
            return str(change.get("target_path") or root)
    return root


def apply_payload(data: dict[str, Any], job_ctx: JobContext | None = None) -> dict[str, Any]:
    plan = preview_payload(data)
    changes = list(plan["changes"])
    _preflight(changes)
    operations = sorted(changes, key=lambda item: item["depth"], reverse=True)
    renamed: list[dict[str, Any]] = []

    total = max(1, len(operations))
    for index, change in enumerate(operations, start=1):
        if job_ctx is not None:
            job_ctx.check_cancelled()
            job_ctx.set_progress(index / total, f"Renaming {index}/{total}")
        source = Path(change["source_path"])
        direct_target = Path(change["direct_target_path"])
        if source != direct_target:
            source.rename(direct_target)
        renamed.append(
            {
                "kind": change["kind"],
                "source_path": change["source_path"],
                "target_path": change["target_path"],
                "old_name": change["old_name"],
                "new_name": change["new_name"],
            }
        )

    outputs = [
        {
            "path": item["target_path"],
            "type": (
                "directory"
                if item["kind"] == "folder"
                else Path(item["target_path"]).suffix.lstrip(".") or "file"
            ),
            "role": "renamed_path",
        }
        for item in renamed
    ]
    return {
        "ok": True,
        "root": plan["root"],
        "updated_root": _updated_root(plan["root"], changes),
        "renamed_count": len(renamed),
        "changes": renamed,
        "outputs": outputs,
        "warnings": plan.get("warnings", []),
    }
