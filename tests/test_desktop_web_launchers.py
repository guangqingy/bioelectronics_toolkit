from __future__ import annotations

import importlib
import unittest

from desktop_apps import web_launcher


class DesktopWebLauncherTests(unittest.TestCase):
    def test_every_launcher_has_route_and_legacy_module(self) -> None:
        self.assertEqual(set(web_launcher.TOOL_ROUTES), set(web_launcher.LEGACY_MODULES))

        for tool, route in web_launcher.TOOL_ROUTES.items():
            with self.subTest(tool=tool):
                self.assertTrue(route.startswith("/"))
                module = importlib.import_module(web_launcher.LEGACY_MODULES[tool])
                self.assertTrue(callable(getattr(module, "main", None)))

    def test_root_compatibility_modules_stay_thin(self) -> None:
        modules = [
            "abf_batch_processor_gui",
            "abf_peak_detection_gui",
            "abf_photocurrent_figure_gui",
            "abf_photocurrent_viewer_gui",
            "abf_sweep_viewer_gui",
            "csv_folder_viewer_gui",
            "echem_photocurrent_gui",
            "echem_photovoltage_gui",
            "emg_peak_selector_gui",
            "emg_rhd_viewer_gui",
            "fluorescence_lut_gui",
            "fluorescence_roi_gui",
            "histology_naming_gui",
        ]

        for module_name in modules:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, "main", None)))


if __name__ == "__main__":
    unittest.main()
