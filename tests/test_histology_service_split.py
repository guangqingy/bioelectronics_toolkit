from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services import histology
from services import histology_discovery, histology_qupath


class HistologyServiceSplitTests(unittest.TestCase):
    def test_facade_exports_core_histology_functions(self) -> None:
        self.assertEqual(histology.sanitize_name(" Case 01 / A "), "Case_01_A")
        self.assertTrue(histology.parse_bool("yes"))
        self.assertEqual(histology.normalize_rotate_deg("90"), 90)
        self.assertEqual(histology.normalize_rotate_deg("45"), 0)

    def test_discovery_finds_overview_and_qupath_display_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_discovery_") as tmp:
            root = Path(tmp)
            case = root / "Case_A"
            case.mkdir()
            overview = case / "Sample_Overview.vsi"
            overview.write_bytes(b"")
            server_dir = case / "qupath" / "data" / "1"
            server_dir.mkdir(parents=True)
            (server_dir / "server.json").write_text(
                json.dumps({"metadata": {"name": "QuPath Case A"}}),
                encoding="utf-8",
            )

            cases = histology_discovery.find_histology_cases(root)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["case_name"], "Case_A")
            self.assertEqual(cases[0]["overview_path"], str(overview.resolve()))
            self.assertEqual(cases[0]["qupath_name"], "QuPath Case A")

    def test_qupath_sync_updates_image_names_from_case_dirs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_qupath_") as tmp:
            root = Path(tmp)
            case = (root / "Case_B").resolve()
            case.mkdir()
            project = root / "project.qpproj"
            project.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "entryID": 1,
                                "imageName": "old",
                                "serverBuilder": {"uri": f"file:{case.as_posix()}/scan.vsi"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = histology_qupath.sync_qupath_names_from_histology_cases(
                [{"case_dir": str(case), "case_name": "Case_B"}],
                str(project),
                update_server_json=False,
            )

            self.assertEqual(result["updated_images"], 1)
            updated = json.loads(project.read_text(encoding="utf-8"))
            self.assertEqual(updated["images"][0]["imageName"], "Case_B")


if __name__ == "__main__":
    unittest.main()
