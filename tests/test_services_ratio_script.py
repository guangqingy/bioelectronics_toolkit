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

    def test_loc_budget_check_honors_schema_exception(self) -> None:
        records = {record.web_module: record for record in collect_ratios(check_loc_budget=True)}

        schema_record = records["web_api/fluorescence_request_schemas.py"]
        self.assertTrue(schema_record.loc_budget_exception)
        self.assertEqual(schema_record.status, "ok")
        self.assertEqual(records["web_api/lif_viewer.py"].status, "route_over_loc_budget")

    def test_web_api_does_not_call_private_service_helpers(self) -> None:
        self.assertEqual(private_service_usage_main([]), 0)


if __name__ == "__main__":
    unittest.main()
