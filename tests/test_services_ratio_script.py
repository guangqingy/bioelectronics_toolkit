from __future__ import annotations

import unittest

from dev_scripts.check_private_service_usage import main as private_service_usage_main
from dev_scripts.check_services_ratio import (
    RatioRecord,
    _baseline_payload,
    collect_ratios,
    compare_to_baseline,
)


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
        self.assertEqual(records["web_api/fluorescence_lif.py"].status, "route_over_loc_budget")

    def test_web_api_does_not_call_private_service_helpers(self) -> None:
        self.assertEqual(private_service_usage_main([]), 0)

    def test_baseline_ratchet_detects_service_ratio_regression(self) -> None:
        baseline_record = RatioRecord(
            web_module="web_api/example.py",
            web_lines=100,
            service_target="services/example.py",
            service_lines=200,
            route_to_service_ratio=0.5,
            service_to_route_ratio=2.0,
            status="ok",
        )
        current_record = RatioRecord(
            web_module="web_api/example.py",
            web_lines=150,
            service_target="services/example.py",
            service_lines=200,
            route_to_service_ratio=0.75,
            service_to_route_ratio=1.3333333333333333,
            status="ok",
        )

        failures, improvements = compare_to_baseline(
            [current_record], _baseline_payload([baseline_record])
        )

        self.assertFalse(improvements)
        self.assertEqual([item.kind for item in failures], ["ratio_regression"])

    def test_new_module_must_start_with_service_weight(self) -> None:
        thin_service_record = RatioRecord(
            web_module="web_api/new_tool.py",
            web_lines=120,
            service_target="services/new_tool.py",
            service_lines=80,
            route_to_service_ratio=1.5,
            service_to_route_ratio=0.6666666666666666,
            status="ok",
        )

        failures, improvements = compare_to_baseline([thin_service_record], {"modules": {}})

        self.assertFalse(improvements)
        self.assertEqual([item.kind for item in failures], ["new_module_without_service_weight"])


if __name__ == "__main__":
    unittest.main()
