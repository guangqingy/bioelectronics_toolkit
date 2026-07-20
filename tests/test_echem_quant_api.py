"""Route-level tests for the tokenized echem quantification endpoints."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np


def _write_ca(path: Path, amplitude: float = 40.0, period_s: float = 1.0) -> None:
    """A standardized CA export with a clean periodic pulse train."""
    t = np.arange(0.0, 12.0, 5e-4)
    current = np.zeros_like(t)
    for onset in np.arange(1.0, 11.0, period_s):
        current[(t >= onset) & (t < onset + 0.2)] = amplitude
    current = current + np.random.default_rng(0).normal(0.0, 0.4, t.size)
    frame = np.column_stack([t, current, np.full_like(t, -0.2)])
    path.write_text(
        "time_s,current_nA,potential_V\n"
        + "\n".join(f"{row[0]:.6f},{row[1]:.6f},{row[2]:.3f}" for row in frame),
        encoding="utf-8",
    )


def _write_cp(path: Path, amplitude: float = 6.0, period_s: float = 1.0) -> None:
    t = np.arange(0.0, 12.0, 1e-3)
    potential = np.where((t % period_s) < 0.25 * period_s, amplitude, 0.0)
    path.write_text(
        "time_s,potential_mV\n" + "\n".join(f"{a:.6f},{b:.6f}" for a, b in zip(t, potential)),
        encoding="utf-8",
    )


def _payload(response):
    body = response.get_json()
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"], body
    return body, body


class EchemQuantApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import web_app

        cls.app = web_app.create_app()
        cls.client = cls.app.test_client()

    def test_page_renders(self) -> None:
        self.assertEqual(self.client.get("/echem/quant").status_code, 200)

    def test_token_scan_returns_records_and_facets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_tokens_") as tmp:
            folder = Path(tmp)
            _write_ca(folder / "mb_5mM_oil_1pct_parallel_group_01_CA.csv")
            _write_ca(folder / "mb_5mM_oil_2pct_parallel_group_01_CA.csv")

            response = self.client.post("/api/echem/tokens/scan", json={"folder": str(folder)})
            data, _envelope = _payload(response)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["n_files"], 2)
            self.assertEqual(data["n_unparsed"], 0)

            facets = {facet["token"]: facet for facet in data["facets"]}
            self.assertIn("oil_pct", facets)
            self.assertEqual([v["value"] for v in facets["oil_pct"]["values"]], ["1", "2"])

    def test_pulse_metrics_quantifies_a_recording(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_pulse_") as tmp:
            path = Path(tmp) / "mb_5mM_oil_1pct_parallel_group_01_CA.csv"
            _write_ca(path, amplitude=40.0)

            response = self.client.post("/api/echem/metrics/pulse", json={"path": str(path)})
            data, _envelope = _payload(response)
            summary = data["summary"]

            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(summary["n_pulses"], 8)
            self.assertAlmostEqual(summary["amplitude_nA"], 40.0, delta=3.0)
            self.assertAlmostEqual(summary["period_s"], 1.0, places=2)
            self.assertIn("technique=CA", data["tokens"]["tokens"])

    def test_cycle_metrics_auto_detects_full_period_and_allows_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_cycle_") as tmp:
            path = Path(tmp) / "mb_5mM_oil_1pct_parallel_group_01_CP.csv"
            _write_cp(path)

            inferred, envelope = _payload(
                self.client.post("/api/echem/metrics/cycle", json={"path": str(path)})
            )
            self.assertEqual(inferred["summary"]["period_source"], "auto")
            self.assertAlmostEqual(inferred["summary"]["period_ms"], 1000.0, delta=20.0)
            self.assertAlmostEqual(inferred["summary"]["amplitude_mV"], 6.0, delta=0.5)
            self.assertFalse(envelope.get("warnings"))

            supplied, _envelope = _payload(
                self.client.post(
                    "/api/echem/metrics/cycle",
                    json={"path": str(path), "expected_period_s": 1.0},
                )
            )
            self.assertEqual(supplied["summary"]["period_source"], "manual")
            self.assertAlmostEqual(supplied["summary"]["amplitude_mV"], 6.0, delta=0.5)

    def test_token_scan_is_recursive_and_excludes_generated_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_recursive_") as tmp:
            root = Path(tmp)
            session = root / "20260702_session"
            output = session / "output"
            session.mkdir()
            output.mkdir()
            source = session / "mb_5mM_parallel_group_01_CA.csv"
            _write_ca(source)
            _write_ca(output / "mb_5mM_parallel_group_01_CA.csv")
            (root / "figure_captions.csv").write_text("caption\n", encoding="utf-8")

            data, _envelope = _payload(
                self.client.post("/api/echem/tokens/scan", json={"folder": str(root)})
            )

            self.assertEqual(data["n_files"], 1)
            self.assertEqual(data["technique_counts"], {"CA": 1})
            self.assertEqual(data["records"][0]["fields"]["session"], "20260702_session")

    def test_batch_reports_missing_detections_rather_than_ok(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_batch_") as tmp:
            folder = Path(tmp)
            good = folder / "mb_5mM_oil_1pct_parallel_group_01_CA.csv"
            flat = folder / "mb_5mM_oil_2pct_parallel_group_01_CA.csv"
            _write_ca(good, amplitude=40.0)
            _write_ca(flat, amplitude=0.0)  # no pulse train at all

            response = self.client.post(
                "/api/echem/metrics/batch", json={"paths": [str(good), str(flat)]}
            )
            data, _envelope = _payload(response)
            rows = {Path(row["path"]).name: row for row in data["rows"]}

            self.assertEqual(rows[good.name]["status"], "ok")
            self.assertEqual(rows[flat.name]["status"], "no pulses detected")
            self.assertEqual(data["n_ok"], 1)

    def test_batch_rows_carry_condition_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_batch_tokens_") as tmp:
            path = Path(tmp) / "mb_5mM_oil_1pct_distance_5cm_parallel_group_02_CA.csv"
            _write_ca(path)

            data, _envelope = _payload(
                self.client.post("/api/echem/metrics/batch", json={"paths": [str(path)]})
            )
            row = data["rows"][0]

            self.assertAlmostEqual(row["token_concentration_mM"], 5.0)
            self.assertAlmostEqual(row["token_distance_cm"], 5.0)
            self.assertEqual(row["token_replicate"], 2)
            self.assertEqual(row["label"], "mb 5 mM oil 1% d=5 cm #2")

    def test_batch_survives_an_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="echem_batch_bad_") as tmp:
            folder = Path(tmp)
            good = folder / "mb_5mM_oil_1pct_parallel_group_01_CA.csv"
            bad = folder / "mb_5mM_oil_9pct_parallel_group_01_CA.csv"
            _write_ca(good)
            bad.write_text("not a recording\n", encoding="utf-8")

            data, _envelope = _payload(
                self.client.post("/api/echem/metrics/batch", json={"paths": [str(good), str(bad)]})
            )
            statuses = {Path(row["path"]).name: row["status"] for row in data["rows"]}

            self.assertEqual(statuses[good.name], "ok")
            self.assertTrue(statuses[bad.name].startswith("error"))

    def test_missing_path_is_rejected(self) -> None:
        response = self.client.post("/api/echem/metrics/pulse", json={"path": ""})
        self.assertGreaterEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
