from __future__ import annotations

import unittest
from pathlib import Path

from services.self_check import format_self_check_report, run_self_check


class SelfCheckTests(unittest.TestCase):
    def test_bundled_examples_pass_self_check(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = run_self_check(root)

        self.assertTrue(report["ok"], format_self_check_report(report))
        names = {item["name"] for item in report["checks"]}
        self.assertIn("example:sample_patch_clamp.abf", names)
        self.assertIn("example:sample_echem_photocurrent.csv", names)
        self.assertIn("example:sample_fluorescence_stack.tif", names)
        self.assertIn("dependencies", report["provenance"])


if __name__ == "__main__":
    unittest.main()
