from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipelines.registry import (
    default_category_id,
    find_pipeline_script,
    pipeline_catalog,
    pipeline_category_ids,
    validate_registry,
)


class PipelineRegistryTests(unittest.TestCase):
    def test_registry_is_valid_and_ids_are_unique(self) -> None:
        catalog = pipeline_catalog()
        self.assertEqual([], validate_registry(catalog))
        self.assertIn(default_category_id(), pipeline_category_ids())

        script_ids: list[str] = []
        for category in catalog["categories"]:
            self.assertTrue(category["scripts"], category["id"])
            script_ids.extend(script["id"] for script in category["scripts"])

        self.assertEqual(len(script_ids), len(set(script_ids)))
        self.assertIn("pc_line_chart", script_ids)

    def test_catalog_availability_is_visitor_friendly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_pipeline_registry_") as tmp:
            catalog = pipeline_catalog(tmp, include_availability=True)

        scripts = [
            script
            for category in catalog["categories"]
            for script in category.get("scripts", [])
        ]
        self.assertTrue(scripts)
        self.assertTrue(all("available" in script for script in scripts))
        self.assertTrue(all("resolved_script_path" not in script for script in scripts))
        self.assertTrue(any(script["available"] is False for script in scripts))

    def test_backend_lookup_keeps_resolved_path_internal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_pipeline_lookup_") as tmp:
            fake_script = (
                Path(tmp)
                / "2025_Subcutaneous"
                / "Photocurrent"
                / "model_line_chart.py"
            )
            fake_script.parent.mkdir(parents=True)
            fake_script.write_text("print('ok')\n", encoding="utf-8")
            script = find_pipeline_script("pc_line_chart", tmp)

        self.assertIsNotNone(script)
        assert script is not None
        self.assertEqual(script["category"], "photocurrent")
        self.assertTrue(script["available"])
        self.assertIn("resolved_script_path", script)
        self.assertTrue(script["resolved_script_path"].endswith("model_line_chart.py"))

    def test_webgui_pipeline_catalog_api(self) -> None:
        from web_app import app

        response = app.test_client().get("/api/pipelines/catalog")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual([], payload["errors"])
        self.assertGreaterEqual(len(payload["categories"]), 4)
        self.assertIn("available", payload["categories"][0]["scripts"][0])
        self.assertNotIn("resolved_script_path", payload["categories"][0]["scripts"][0])

    def test_scripts_page_uses_registry_categories(self) -> None:
        from web_app import app

        client = app.test_client()
        for category in pipeline_category_ids():
            with self.subTest(category=category):
                response = client.get(f"/scripts/{category}")
                html = response.data.decode("utf-8")
                self.assertEqual(response.status_code, 200)
                self.assertIn("PIPELINE_CATALOG", html)
                self.assertIn(category, html)

    def test_old_hardcoded_pipeline_maps_are_removed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        backend = (root / "web_api" / "scripts_panel.py").read_text(encoding="utf-8")
        template = (root / "web_templates" / "scripts.html").read_text(encoding="utf-8")

        self.assertNotIn("script_map = {", backend)
        self.assertNotIn("const SCRIPTS = {", template)


if __name__ == "__main__":
    unittest.main()
