from __future__ import annotations

import unittest

from dev_scripts.check_private_service_usage import main as private_service_usage_main
from dev_scripts.check_services_ratio import collect_ratios


class ServicesRatioScriptTests(unittest.TestCase):
    def test_ratio_collection_reports_web_modules(self) -> None:
        records = collect_ratios()
        modules = {record.web_module for record in records}

        self.assertIn("web_api/fluorescence.py", modules)
        self.assertFalse([record for record in records if record.status != "ok"])

    def test_web_api_does_not_call_private_service_helpers(self) -> None:
        self.assertEqual(private_service_usage_main([]), 0)


if __name__ == "__main__":
    unittest.main()
