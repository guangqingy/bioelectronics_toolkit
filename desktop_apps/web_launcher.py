from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = 7433
BASE_URL = f"http://127.0.0.1:{PORT}"

TOOL_ROUTES = {
    "abf_batch": "/abf/batch",
    "abf_peaks": "/abf/peaks",
    "abf_pc_figure": "/abf/figure",
    "abf_pc_viewer": "/abf/viewer?rnorm=1",
    "abf_sweep": "/abf/viewer",
    "csv_viewer": "/csv",
    "echem_photocurrent": "/echem/photocurrent",
    "echem_photovoltage": "/echem/photovoltage",
    "emg_analysis": "/emg/analysis",
    "emg_peak_selection": "/emg/peak-selection",
    "fluorescence_lut": "/fluorescence",
    "fluorescence_roi": "/fluorescence/roi",
    "histology": "/histology/naming",
    "histology_analysis": "/histology/analysis",
}


def _server_alive() -> bool:
    try:
        with urlopen(f"{BASE_URL}/", timeout=0.5) as response:
            return 200 <= int(response.status) < 500
    except URLError:
        return False
    except Exception:
        return False


def _start_webgui() -> subprocess.Popen:
    env = os.environ.copy()
    env["DATAPROCESS_WEB_NO_BROWSER"] = "1"
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "web_app.py")],
        cwd=str(REPO_ROOT),
        env=env,
        start_new_session=True,
    )


def _wait_for_server(timeout_s: float = 12.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _server_alive():
            return True
        time.sleep(0.25)
    return False


def main_for_tool(tool: str, argv: list[str] | None = None) -> int:
    if tool not in TOOL_ROUTES:
        raise ValueError(f"Unknown desktop tool: {tool}")

    parser = argparse.ArgumentParser(
        description="Open the canonical DataProcess WebGUI page for this tool."
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Do not start web_app.py if it is closed.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the URL without opening a browser.",
    )
    args, passthrough = parser.parse_known_args(argv)

    if passthrough:
        parser.error(f"unrecognized arguments: {' '.join(passthrough)}")

    if not args.no_start and not _server_alive():
        _start_webgui()
        if not _wait_for_server():
            raise RuntimeError(f"WebGUI did not start at {BASE_URL}")

    url = f"{BASE_URL}{TOOL_ROUTES[tool]}"
    print(url)
    if not args.no_browser:
        webbrowser.open(url)
    return 0


def _entry(tool: str):
    def _main() -> int:
        return main_for_tool(tool)

    return _main


abf_batch_main = _entry("abf_batch")
abf_peaks_main = _entry("abf_peaks")
abf_sweep_main = _entry("abf_sweep")
abf_pc_viewer_main = _entry("abf_pc_viewer")
abf_pc_figure_main = _entry("abf_pc_figure")
csv_viewer_main = _entry("csv_viewer")
echem_photocurrent_main = _entry("echem_photocurrent")
echem_photovoltage_main = _entry("echem_photovoltage")
emg_analysis_main = _entry("emg_analysis")
emg_peak_selection_main = _entry("emg_peak_selection")
fluorescence_lut_main = _entry("fluorescence_lut")
fluorescence_roi_main = _entry("fluorescence_roi")
histology_main = _entry("histology")
histology_analysis_main = _entry("histology_analysis")
