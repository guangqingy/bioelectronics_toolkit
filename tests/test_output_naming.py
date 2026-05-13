from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.output_naming import next_available_path, next_numbered_path, output_dir_for_project


class OutputNamingTests(unittest.TestCase):
    def test_next_numbered_path_uses_short_incrementing_suffixes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_output_naming_") as tmp:
            base = Path(tmp) / "trace.svg"
            (Path(tmp) / "trace_1.svg").write_text("old", encoding="utf-8")

            self.assertEqual(next_numbered_path(base), Path(tmp) / "trace_2.svg")

    def test_next_available_path_sanitizes_stem_and_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_output_naming_") as tmp:
            target_dir = Path(tmp) / "nested"
            path = next_available_path(target_dir, "bad stem/value", "csv")

            self.assertEqual(path.name, "bad_stem_value_1.csv")
            self.assertTrue(target_dir.is_dir())

    def test_output_dir_for_project_points_to_cache_exports(self) -> None:
        root = Path("/tmp/project")

        self.assertEqual(
            output_dir_for_project(root, "RHD Viewer"),
            root / ".dataprocess_cache" / "exports" / "RHD_Viewer",
        )


if __name__ == "__main__":
    unittest.main()
