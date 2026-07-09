from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from services.figure_generator import _parse_ranges, browse_payload, preview_payload, run_payload


class FigureGeneratorServiceTest(unittest.TestCase):
    def _write_summary(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "power_density": [1.0, 2.0, 2.0],
                "capacitance_peak": [0.5, 1.0, 1.2],
                "integral_charge": [5.0, 8.0, 9.0],
            }
        ).to_csv(folder / "summary_sample.csv", index=False)

    def _queue_body(self, root: Path, child: Path) -> dict:
        return {
            "main_folder": str(root),
            "output_name": "figures",
            "queue": [{"path": str(child), "label": "sample"}],
            "metrics": ["peak"],
            "x_lin_ranges": "",
            "x_log_ranges": "",
        }

    def test_browse_preview_and_run_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "recording_a"
            self._write_summary(child)

            browse = browse_payload(str(root))
            self.assertEqual(browse["subfolders"], [{"name": "recording_a", "path": str(child)}])

            def fake_fig_to_b64(fig):
                plt.close(fig)
                return "encoded"

            preview = preview_payload(self._queue_body(root, child), fake_fig_to_b64)
            self.assertEqual(preview["queue_count"], 1)
            self.assertEqual(preview["series_count"]["peak"], 1)
            self.assertTrue(preview["images"])

            result = run_payload({**self._queue_body(root, child), "action": "analyze"})
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "analyze")
            self.assertGreaterEqual(result["generated_count"], 1)
            for generated in result["generated_files"]:
                self.assertTrue(Path(generated).exists())

    def test_parse_ranges_accepts_multiple_delimiters(self) -> None:
        self.assertEqual(
            _parse_ranges("0-1, 0-100\n1e-3-1e-1"),
            [(0.0, 1.0), (0.0, 100.0), (0.001, 0.1)],
        )
        self.assertEqual(_parse_ranges([[2, 1], "5 to 7"]), [(1.0, 2.0), (5.0, 7.0)])

    def test_preview_payload_generates_all_requested_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "recording_a"
            self._write_summary(child)
            body = {
                **self._queue_body(root, child),
                "x_lin_ranges": "0-1; 0-100",
                "x_log_ranges": "0.01-1\n0.01-100",
            }

            def fake_fig_to_b64(fig):
                plt.close(fig)
                return "encoded"

            preview = preview_payload(body, fake_fig_to_b64)
            self.assertEqual(
                [item["name"] for item in preview["images"]],
                [
                    "peak_linear_0-1",
                    "peak_linear_0-100",
                    "peak_log_0.01-1",
                    "peak_log_0.01-100",
                ],
            )


if __name__ == "__main__":
    unittest.main()
