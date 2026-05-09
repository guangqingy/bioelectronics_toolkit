from __future__ import annotations

import argparse
import importlib
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
    "abf_pc_viewer": "/abf/viewer",
    "abf_sweep": "/abf/viewer",
    "csv_viewer": "/csv",
    "echem_pc": "/echem/photocurrent",
    "echem_pv": "/echem/photovoltage",
    "emg_peaks": "/emg/peaks",
    "emg_rhd": "/emg/rhd",
    "fluorescence_lut": "/fluorescence",
    "fluorescence_roi": "/fluorescence/roi",
    "histology": "/histology",
}

LEGACY_MODULES = {
    "abf_batch": "desktop_apps.legacy.abf_batch_processor_gui",
    "abf_peaks": "desktop_apps.legacy.abf_peak_detection_gui",
    "abf_pc_figure": "desktop_apps.legacy.abf_photocurrent_figure_gui",
    "abf_pc_viewer": "desktop_apps.legacy.abf_photocurrent_viewer_gui",
    "abf_sweep": "desktop_apps.legacy.abf_sweep_viewer_gui",
    "csv_viewer": "desktop_apps.legacy.csv_folder_viewer_gui",
    "echem_pc": "desktop_apps.legacy.echem_photocurrent_gui",
    "echem_pv": "desktop_apps.legacy.echem_photovoltage_gui",
    "emg_peaks": "desktop_apps.legacy.emg_peak_selector_gui",
    "emg_rhd": "desktop_apps.legacy.emg_rhd_viewer_gui",
    "fluorescence_lut": "desktop_apps.legacy.fluorescence_lut_gui",
    "fluorescence_roi": "desktop_apps.legacy.fluorescence_roi_gui",
    "histology": "desktop_apps.legacy.histology_naming_gui",
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


def _run_legacy(tool: str, passthrough_args: list[str]) -> int:
    module_name = LEGACY_MODULES[tool]
    module = importlib.import_module(module_name)
    main_func = getattr(module, "main", None)
    if not callable(main_func):
        raise RuntimeError(f"Legacy module has no callable main(): {module_name}")
    sys.argv = [f"{module_name.rsplit('.', 1)[-1]}.py", *passthrough_args]
    main_func()
    return 0


def main_for_tool(tool: str, argv: list[str] | None = None) -> int:
    if tool not in TOOL_ROUTES:
        raise ValueError(f"Unknown desktop tool: {tool}")

    parser = argparse.ArgumentParser(
        description=(
            "Open the canonical DataProcess WebGUI page for this tool. "
            "Use --legacy to run the old Tkinter window."
        )
    )
    parser.add_argument("--legacy", action="store_true", help="Run the legacy Tkinter GUI.")
    parser.add_argument("--no-start", action="store_true", help="Do not start web_app.py if it is closed.")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser.")
    args, passthrough = parser.parse_known_args(argv)

    if args.legacy:
        return _run_legacy(tool, passthrough)

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
echem_pc_main = _entry("echem_pc")
echem_pv_main = _entry("echem_pv")
emg_rhd_main = _entry("emg_rhd")
emg_peaks_main = _entry("emg_peaks")
fluorescence_lut_main = _entry("fluorescence_lut")
fluorescence_roi_main = _entry("fluorescence_roi")
histology_main = _entry("histology")
