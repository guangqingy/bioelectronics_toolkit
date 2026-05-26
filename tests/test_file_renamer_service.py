from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services import file_renamer


class FileRenamerServiceTests(unittest.TestCase):
    def test_emg_session_rename_updates_folder_and_nested_file_preview_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_file_renamer_") as tmp:
            root = Path(tmp) / "rough_session"
            nested = root / "rough_session_exports"
            nested.mkdir(parents=True)
            source = nested / "rough_session_A-000.csv"
            source.write_text("time,value\n0,1\n", encoding="utf-8")

            payload = {
                "root": str(root),
                "find": "rough_session",
                "replace": "clean_session",
                "include_root": True,
                "include_dirs": True,
                "include_files": True,
                "extensions": ".csv,.rhd",
            }
            preview = file_renamer.preview_payload(payload)

            self.assertEqual(preview["conflict_count"], 0)
            self.assertEqual(preview["ready_count"], 3)
            target_paths = {Path(change["target_path"]) for change in preview["changes"]}
            self.assertIn(
                Path(tmp) / "clean_session" / "clean_session_exports" / "clean_session_A-000.csv",
                target_paths,
            )

            applied = file_renamer.apply_payload(payload)

            self.assertEqual(applied["renamed_count"], 3)
            self.assertFalse(root.exists())
            self.assertTrue(
                (
                    Path(tmp)
                    / "clean_session"
                    / "clean_session_exports"
                    / "clean_session_A-000.csv"
                ).exists()
            )

    def test_existing_target_is_reported_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_file_renamer_conflict_") as tmp:
            root = Path(tmp)
            (root / "rough_session_A-000.rhd").write_bytes(b"source")
            (root / "clean_session_A-000.rhd").write_bytes(b"target")

            preview = file_renamer.preview_payload(
                {
                    "root": str(root),
                    "find": "rough_session",
                    "replace": "clean_session",
                    "include_root": False,
                    "include_files": True,
                    "include_dirs": False,
                    "extensions": ".rhd",
                }
            )

            self.assertEqual(preview["conflict_count"], 1)
            self.assertEqual(preview["changes"][0]["status"], "target_exists")


if __name__ == "__main__":
    unittest.main()
