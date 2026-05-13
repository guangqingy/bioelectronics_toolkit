from __future__ import annotations

import unittest

from dev_scripts.check_services_ratio import collect_ratios


class ServicesRatioScriptTests(unittest.TestCase):
    def test_ratio_collection_reports_web_modules(self) -> None:
        records = collect_ratios()
        modules = {record.web_module for record in records}

        self.assertIn("web_api/fluorescence.py", modules)
        self.assertTrue(any(record.status != "ok" for record in records))


if __name__ == "__main__":
    unittest.main()
