from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from web_api.path_policy import unique_path


class PathPolicyTests(unittest.TestCase):
    def test_unique_path_defaults_to_non_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_path_policy_") as tmp:
            first = Path(tmp) / "result.csv"
            first.write_text("old\n", encoding="utf-8")
            self.assertEqual(unique_path(first).name, "result_2.csv")
            self.assertEqual(unique_path(first, overwrite=True), first)


if __name__ == "__main__":
    unittest.main()
