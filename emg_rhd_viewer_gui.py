from __future__ import annotations

from desktop_apps.web_launcher import main_for_tool


def main() -> int:
    return main_for_tool("emg_rhd")


if __name__ == "__main__":
    raise SystemExit(main())
