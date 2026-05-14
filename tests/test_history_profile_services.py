from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from services import file_profiles, run_history


class RunHistoryServiceTests(unittest.TestCase):
    def test_record_list_check_report_and_package_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_history_service_") as tmp:
            root = Path(tmp)
            source = root / "input.csv"
            output = root / "plot.png"
            source.write_text("x,y\n1,2\n", encoding="utf-8")
            output.write_bytes(b"png")

            recorded = run_history.record_run(
                {
                    "project_root": str(root),
                    "run_id": "unit_run",
                    "view": "unit",
                    "parameters": {"threshold": 2},
                    "input_files": [{"path": str(source)}],
                    "outputs": [{"path": str(output), "role": "plot"}],
                },
                root,
            )

            self.assertTrue(recorded["ok"])
            manifest_path = Path(recorded["manifest_path"])
            self.assertTrue(manifest_path.exists())

            listed = run_history.list_runs({"project_root": str(root), "view": "unit"}, root)
            self.assertEqual([item["run_id"] for item in listed["runs"]], ["unit_run"])

            loaded = run_history.get_run_manifest({"manifest_path": str(manifest_path)}, root)
            self.assertEqual(loaded["manifest"]["parameters"]["threshold"], 2)

            checked = run_history.check_run_manifest({"manifest_path": str(manifest_path)}, root)
            self.assertEqual(checked["check"]["status"], "ok")

            report = run_history.write_run_report({"manifest_path": str(manifest_path)}, root)
            self.assertTrue(Path(report["report_path"]).exists())
            self.assertIn("unit_run", report["report"])

            packaged = run_history.package_run_manifest({"manifest_path": str(manifest_path)}, root)
            package_path = Path(packaged["package_path"])
            self.assertTrue(package_path.exists())
            with zipfile.ZipFile(package_path) as zf:
                self.assertIn("manifest.json", zf.namelist())
                self.assertIn("report.md", zf.namelist())


class FileProfileServiceTests(unittest.TestCase):
    def test_save_get_and_delete_file_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_profile_service_") as tmp:
            root = Path(tmp)
            data_file = root / "trace.rhd"
            data_file.write_bytes(b"rhd")

            empty = file_profiles.get_file_profile(
                {"project_root": str(root), "file_path": str(data_file), "view": "rhd"}
            )
            self.assertTrue(empty["ok"])
            self.assertEqual(empty["profiles"], {})

            saved = file_profiles.save_file_profile(
                {
                    "project_root": str(root),
                    "file_path": str(data_file),
                    "view": "rhd",
                    "profile_name": "analysis",
                    "settings": {"downsample": 4},
                    "payload": {"channel": "A-000"},
                }
            )
            self.assertEqual(saved["selected_profile"], "analysis")
            self.assertEqual(saved["profile"]["settings"]["downsample"], 4)

            loaded = file_profiles.get_file_profile(
                {
                    "project_root": str(root),
                    "file_path": str(data_file),
                    "view": "rhd",
                    "profile_name": "analysis",
                }
            )
            self.assertEqual(loaded["profile"]["payload"]["channel"], "A-000")

            deleted = file_profiles.delete_file_profile(
                {
                    "project_root": str(root),
                    "file_path": str(data_file),
                    "view": "rhd",
                    "profile_name": "analysis",
                }
            )
            self.assertNotIn("analysis", deleted["profiles"])


if __name__ == "__main__":
    unittest.main()
