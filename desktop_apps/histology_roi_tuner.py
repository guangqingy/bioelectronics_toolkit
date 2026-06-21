from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_apps.web_launcher import main_for_tool


def main(argv: list[str] | None = None) -> int:
    return main_for_tool("histology_analysis", argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
