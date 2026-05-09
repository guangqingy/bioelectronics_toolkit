#!/usr/bin/env python3
"""Audit non-GUI analysis Python scripts for import-safety conventions."""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path


EXCLUDE_DIRS = {
    ".venv",
    ".conda",
    "__pycache__",
    "build",
    "desktop_apps",
    "dist",
    "docs",
    "examples",
    "pipelines",
    "pipeline_readmes",
    "services",
    "tests",
    "vendor",
    "web_api",
    "web_static",
    "web_templates",
}

EXCLUDE_FILES = {
    "config.py",
}

GUI_NAME_TOKENS = (
    "gui",
    "viewer",
    "selector",
    "designer",
    "panel",
    "app",
)

GUI_IMPORT_RE = re.compile(
    r"(^|\n)\s*(import|from)\s+(tkinter|dash|streamlit|flask|PyQt|PySide)\b",
    re.IGNORECASE,
)

TOP_LEVEL_OUTDIR_RE = re.compile(
    r"(?m)^[A-Z_]*OUT[A-Z_]*\.mkdir\(parents=True,\s*exist_ok=True\)"
)

MAIN_RE = re.compile(r"(?m)^def\s+main\s*\(")


@dataclass
class ScriptAudit:
    path: Path
    syntax_error: str = ""
    has_guard: bool = False
    has_main: bool = False
    is_wrapper: bool = False
    has_top_level_output_mkdir: bool = False
    has_gui_import: bool = False
    is_temp_or_test: bool = False

    @property
    def issues(self) -> list[str]:
        out: list[str] = []
        if self.syntax_error:
            out.append(f"syntax:{self.syntax_error}")
        if not self.has_guard:
            out.append("missing-main-guard")
        if self.has_top_level_output_mkdir:
            out.append("top-level-output-mkdir")
        if self.has_gui_import:
            out.append("gui-import")
        if self.is_temp_or_test:
            out.append("temp-or-test-name")
        return out

    @property
    def shape(self) -> str:
        if self.is_wrapper:
            return "wrapper"
        if self.has_main:
            return "main"
        if self.has_guard:
            return "guarded"
        return "helper-or-unstructured"


def is_excluded_path(path: Path) -> bool:
    return path.name in EXCLUDE_FILES or any(part in EXCLUDE_DIRS for part in path.parts)


def is_gui_script(path: Path, text: str) -> bool:
    name = path.name.lower()
    if name == "web_app.py":
        return True
    if "web_api" in path.parts or "web_static" in path.parts:
        return True
    return any(token in name for token in GUI_NAME_TOKENS)


def audit_script(path: Path, root: Path) -> ScriptAudit:
    rel = path.relative_to(root)
    text = path.read_text(errors="replace")
    audit = ScriptAudit(path=rel)

    try:
        ast.parse(text)
    except SyntaxError as exc:
        audit.syntax_error = f"{exc.lineno}:{exc.msg}"

    audit.has_guard = 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text
    audit.has_main = bool(MAIN_RE.search(text))
    audit.is_wrapper = "runpy.run_path" in text
    audit.has_top_level_output_mkdir = bool(TOP_LEVEL_OUTDIR_RE.search(text))
    audit.has_gui_import = bool(GUI_IMPORT_RE.search(text))
    audit.is_temp_or_test = "temp" in path.name.lower() or path.name.lower() == "test.py"
    return audit


def collect_scripts(root: Path) -> list[ScriptAudit]:
    audits: list[ScriptAudit] = []
    for path in sorted(root.rglob("*.py")):
        if is_excluded_path(path):
            continue
        text = path.read_text(errors="replace")
        if is_gui_script(path.relative_to(root), text):
            continue
        audits.append(audit_script(path, root))
    return audits


def print_report(audits: list[ScriptAudit]) -> int:
    counts: dict[str, int] = {}
    for audit in audits:
        counts[audit.shape] = counts.get(audit.shape, 0) + 1

    print(f"Checked scripts: {len(audits)}")
    for shape in sorted(counts):
        print(f"  {shape}: {counts[shape]}")

    blocking = [
        audit for audit in audits
        if audit.syntax_error or not audit.has_guard or audit.has_top_level_output_mkdir or audit.has_gui_import
    ]
    temp_named = [audit for audit in audits if audit.is_temp_or_test]

    print(f"Blocking issues: {len(blocking)}")
    for audit in blocking:
        print(f"  {audit.path}: {', '.join(audit.issues)}")

    print(f"Temp/test named scripts: {len(temp_named)}")
    for audit in temp_named:
        print(f"  {audit.path}")

    return 1 if blocking else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository/workspace root to scan.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.expanduser().resolve()
    raise_code = print_report(collect_scripts(root))
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
