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

    def test_emg_token_rename_updates_split_recording_folder_rhds_and_xml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_file_renamer_rhd_token_") as tmp:
            root = Path(tmp) / "spinalcord_20260610"
            session = root / "02ms2_260610_194735"
            session.mkdir(parents=True)
            (session / "02ms2_260610_194735.rhd").write_bytes(b"rhd-1")
            (session / "02ms2_260610_194835.rhd").write_bytes(b"rhd-2")
            (session / "02ms2_260610_194735_settings.xml").write_text("<settings />", encoding="utf-8")
            (session / "settings.xml").write_text("<settings />", encoding="utf-8")

            payload = {
                "root": str(root),
                "find": "02ms2",
                "replace": "2ms_2",
                "include_root": True,
                "include_dirs": True,
                "include_files": True,
                "recursive": True,
                "extensions": ".rhd,.xml,.csv,.txt,.tsv,.json,.png,.svg",
            }
            preview = file_renamer.preview_payload(payload)

            self.assertEqual(preview["conflict_count"], 0)
            self.assertEqual(preview["ready_count"], 4)
            target_names = {Path(change["target_path"]).name for change in preview["changes"]}
            self.assertIn("2ms_2_260610_194735", target_names)
            self.assertIn("2ms_2_260610_194735.rhd", target_names)
            self.assertIn("2ms_2_260610_194835.rhd", target_names)
            self.assertIn("2ms_2_260610_194735_settings.xml", target_names)

            applied = file_renamer.apply_payload(payload)

            renamed_session = root / "2ms_2_260610_194735"
            self.assertEqual(applied["renamed_count"], 4)
            self.assertFalse(session.exists())
            self.assertTrue((renamed_session / "2ms_2_260610_194735.rhd").exists())
            self.assertTrue((renamed_session / "2ms_2_260610_194835.rhd").exists())
            self.assertTrue((renamed_session / "2ms_2_260610_194735_settings.xml").exists())
            self.assertTrue((renamed_session / "settings.xml").exists())


if __name__ == "__main__":
    unittest.main()
