from __future__ import annotations

import unittest

from web_app import app


class EmgPeakSelectionApiValidationTests(unittest.TestCase):
    def test_load_requires_complete_selection(self) -> None:
        client = app.test_client()

        response = client.post("/api/emg/peak-selection/load", json={})

        self.assertEqual(response.status_code, 422)

    def test_plot_rejects_directory_path_without_server_error(self) -> None:
        client = app.test_client()

        response = client.post("/api/emg/peak-selection/plot", json={"path": "."})
        payload = response.get_json()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("EMG CSV file not found", payload["error"])


if __name__ == "__main__":
    unittest.main()
