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

    def test_desktop_apps_root_stays_small(self) -> None:
        desktop_root = Path(__file__).resolve().parents[1] / "desktop_apps"
        root_modules = sorted(path.name for path in desktop_root.glob("*.py"))

        self.assertEqual(root_modules, ["__init__.py", "web_launcher.py"])
        self.assertTrue((desktop_root / "launchers").is_dir())
        self.assertTrue((desktop_root / "native").is_dir())
        self.assertTrue((desktop_root / "cli").is_dir())

    def test_every_launcher_has_web_route(self) -> None:
        for tool, route in web_launcher.TOOL_ROUTES.items():
            with self.subTest(tool=tool):
                self.assertTrue(route.startswith("/"))

    def test_web_launcher_has_no_legacy_runtime_path(self) -> None:
        self.assertFalse(hasattr(web_launcher, "LEGACY_MODULES"))
        self.assertFalse(hasattr(web_launcher, "_run_legacy"))

    def test_abf_viewer_launchers_preserve_historical_rnorm_defaults(self) -> None:
        self.assertEqual(web_launcher.TOOL_ROUTES["abf_pc_viewer"], "/abf/viewer?rnorm=1")
        self.assertEqual(web_launcher.TOOL_ROUTES["abf_sweep"], "/abf/viewer")

    def test_echem_launchers_use_canonical_workflow_routes(self) -> None:
        self.assertEqual(web_launcher.TOOL_ROUTES["echem_photocurrent"], "/echem/photocurrent")
        self.assertEqual(web_launcher.TOOL_ROUTES["echem_photovoltage"], "/echem/photovoltage")
        self.assertNotIn("echem_pc", web_launcher.TOOL_ROUTES)
        self.assertNotIn("echem_pv", web_launcher.TOOL_ROUTES)

    def test_emg_launchers_use_canonical_workflow_routes(self) -> None:
        self.assertEqual(web_launcher.TOOL_ROUTES["emg_analysis"], "/emg/analysis")
        self.assertEqual(
            web_launcher.TOOL_ROUTES["emg_peak_selection"],
            "/emg/peak-selection",
        )
        self.assertNotIn("emg_rhd", web_launcher.TOOL_ROUTES)
        self.assertNotIn("rhd_viewer", web_launcher.TOOL_ROUTES)
        self.assertNotIn("emg_peaks", web_launcher.TOOL_ROUTES)

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
            "desktop_apps.launchers.emg_analysis_gui",
            "desktop_apps.launchers.emg_peak_selection_gui",
            "desktop_apps.launchers.fluorescence_lut_gui",
            "desktop_apps.launchers.fluorescence_roi_gui",
            "desktop_apps.launchers.histology_analysis_gui",
            "desktop_apps.launchers.histology_naming_gui",
        ]

        for module_name in modules:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, "main", None)))

    def test_histology_analysis_launcher_is_web_launcher(self) -> None:
        module = importlib.import_module("desktop_apps.launchers.histology_analysis_gui")

        self.assertTrue(callable(getattr(module, "main", None)))
        self.assertFalse(hasattr(module, "HistologyRoiTunerApp"))
        self.assertEqual(web_launcher.TOOL_ROUTES["histology_analysis"], "/histology/analysis")


if __name__ == "__main__":
    unittest.main()
