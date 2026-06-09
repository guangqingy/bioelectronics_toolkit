from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from desktop_apps import web_launcher


class DesktopWebLauncherTests(unittest.TestCase):
    def test_root_has_no_desktop_gui_launchers(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        root_launchers = sorted(project_root.glob("*_gui.py"))

        self.assertEqual(root_launchers, [])
        self.assertFalse((project_root / "fluorescence_tiff_to_gif.py").exists())

    def test_every_launcher_has_route_and_legacy_module(self) -> None:
        self.assertEqual(set(web_launcher.TOOL_ROUTES), set(web_launcher.LEGACY_MODULES))

        for tool, route in web_launcher.TOOL_ROUTES.items():
            with self.subTest(tool=tool):
                self.assertTrue(route.startswith("/"))
                module = importlib.import_module(web_launcher.LEGACY_MODULES[tool])
                self.assertTrue(callable(getattr(module, "main", None)))

    def test_abf_viewer_launchers_preserve_legacy_rnorm_defaults(self) -> None:
        self.assertEqual(web_launcher.TOOL_ROUTES["abf_pc_viewer"], "/abf/viewer?rnorm=1")
        self.assertEqual(web_launcher.TOOL_ROUTES["abf_sweep"], "/abf/viewer")

    def test_launcher_modules_stay_thin(self) -> None:
        modules = [
            "desktop_apps.launchers.abf_batch_processor_gui",
            "desktop_apps.launchers.abf_peak_detection_gui",
            "desktop_apps.launchers.abf_photocurrent_figure_gui",
            "desktop_apps.launchers.abf_photocurrent_viewer_gui",
            "desktop_apps.launchers.abf_sweep_viewer_gui",
            "desktop_apps.launchers.csv_folder_viewer_gui",
            "desktop_apps.launchers.echem_photocurrent_gui",
            "desktop_apps.launchers.echem_photovoltage_gui",
            "desktop_apps.launchers.emg_peak_selector_gui",
            "desktop_apps.launchers.emg_rhd_viewer_gui",
            "desktop_apps.launchers.fluorescence_lut_gui",
            "desktop_apps.launchers.fluorescence_roi_gui",
            "desktop_apps.launchers.histology_naming_gui",
        ]

        for module_name in modules:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, "main", None)))


if __name__ == "__main__":
    unittest.main()
