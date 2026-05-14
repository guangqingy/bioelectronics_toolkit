#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_API = ROOT / "web_api"

PRIVATE_ALIAS_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*_service\._[A-Za-z][A-Za-z0-9_]*")
PRIVATE_IMPORT_RE = re.compile(
    r"^\s*from\s+services(?:\.[A-Za-z0-9_]+)+\s+import\s+.*\b_[A-Za-z][A-Za-z0-9_]*", re.MULTILINE
)


def _scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[str] = []
    for match in PRIVATE_ALIAS_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        findings.append(f"{path.relative_to(ROOT)}:{line}: private service member {match.group(0)}")
    for match in PRIVATE_IMPORT_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        findings.append(f"{path.relative_to(ROOT)}:{line}: private import from services")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Block web_api from calling private service helpers."
    )
    parser.add_argument("--warn-only", action="store_true", help="Always exit 0.")
    args = parser.parse_args(argv)

    findings: list[str] = []
    for path in sorted(WEB_API.glob("*.py")):
        findings.extend(_scan_file(path))

    if findings:
        print("private service usage report")
        for finding in findings:
            print(f"- {finding}")
    else:
        print("private service usage report")
        print("- ok: no web_api module calls private service helpers")
    return 0 if args.warn_only or not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
