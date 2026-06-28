from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop_apps.web_launcher import main_for_tool


def main() -> int:
    return main_for_tool("emg_peak_selection")


if __name__ == "__main__":
    raise SystemExit(main())
