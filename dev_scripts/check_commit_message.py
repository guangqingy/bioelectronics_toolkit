#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CONVENTIONAL_RE = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(\([a-z0-9_.-]+\))?!?: .+"
)
ALLOWED_PREFIXES = (
    "Merge ",
    "Revert ",
    "fixup! ",
    "squash! ",
)


def is_valid_subject(subject: str) -> bool:
    text = str(subject or "").strip()
    if not text:
        return False
    if text.startswith(ALLOWED_PREFIXES):
        return True
    return bool(CONVENTIONAL_RE.match(text))


def _subject_from_file(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return ""


def _subjects_from_range(commit_range: str) -> list[str]:
    proc = subprocess.run(
        ["git", "log", "--format=%s", commit_range],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _check_subjects(subjects: list[str]) -> int:
    invalid = [subject for subject in subjects if not is_valid_subject(subject)]
    if not invalid:
        return 0
    print("Commit message(s) must follow Conventional Commits:", file=sys.stderr)
    for subject in invalid:
        print(f"  - {subject}", file=sys.stderr)
    print("Example: feat(web): add LIF export preview", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Conventional Commit subjects.")
    parser.add_argument("message_file", nargs="?", help="Commit message file from commit-msg hook.")
    parser.add_argument("--range", dest="commit_range", help="Git commit range to check.")
    args = parser.parse_args(argv)

    if args.commit_range:
        return _check_subjects(_subjects_from_range(args.commit_range))
    if not args.message_file:
        parser.error("provide a commit message file or --range")
    return _check_subjects([_subject_from_file(Path(args.message_file))])


if __name__ == "__main__":
    raise SystemExit(main())
