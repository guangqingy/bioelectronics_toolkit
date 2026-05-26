from __future__ import annotations

from pathlib import Path
from typing import Any


def candidate_overview_files(case_dir: Path) -> list[Path]:
    results: list[Path] = []
    for pattern in ("*Overview.vsi", "*overview.vsi", "*OVERVIEW.vsi"):
        results.extend(sorted(case_dir.glob(pattern)))
        results.extend(sorted(case_dir.rglob(pattern)))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in results:
        if path not in seen and path.is_file():
            unique.append(path)
            seen.add(path)
    return unique


def find_histology_cases(project_root: str | Path) -> list[dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return []

    cases: list[dict[str, Any]] = []
    for vsi in candidate_overview_files(root):
        case_dir = vsi.parent
        if case_dir.name.startswith("_"):
            case_dir = case_dir.parent
        if not case_dir.is_dir():
            continue
        cases.append(
            {
                "case_dir": str(case_dir),
                "case_name": case_dir.name,
                "overview_path": str(vsi),
                "overview_name": vsi.name,
            }
        )

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        key = str(case.get("overview_path") or "")
        if key in seen:
            continue
        unique.append(case)
        seen.add(key)
    return unique


_candidate_overview_files = candidate_overview_files
