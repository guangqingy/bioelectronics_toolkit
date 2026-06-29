from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from web_api.common import browse_files, fig_to_b64, request_data


class _FakeFigure:
    def __init__(self) -> None:
        self.savefig_kwargs = None
        self.cleared = False

    def savefig(self, buf, **kwargs) -> None:
        self.savefig_kwargs = kwargs
        buf.write(b"fake-png")

    def clear(self) -> None:
        self.cleared = True


class WebApiCommonTests(unittest.TestCase):
    def test_fig_to_b64_defaults_to_preview_dpi_without_tight_bbox(self) -> None:
        fig = _FakeFigure()
        payload = fig_to_b64(fig)
        self.assertEqual(base64.b64decode(payload), b"fake-png")
        self.assertEqual(fig.savefig_kwargs, {"format": "png", "dpi": 96})
        self.assertTrue(fig.cleared)

    def test_fig_to_b64_can_request_tight_bbox_for_explicit_callers(self) -> None:
        fig = _FakeFigure()
        fig_to_b64(fig, dpi=130, tight=True)
        self.assertEqual(
            fig.savefig_kwargs,
            {"format": "png", "dpi": 130, "bbox_inches": "tight"},
        )

    def test_browse_files_includes_metadata_for_live_refresh(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_browse_meta_") as tmp:
            path = Path(tmp) / "trace.abf"
            path.write_bytes(b"abf")

            files = browse_files(tmp, {".abf"})

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "trace.abf")
        self.assertEqual(files[0]["size"], 3)
        self.assertGreater(files[0]["mtime"], 0)

    def test_request_data_ignores_non_json_post_body(self) -> None:
        app = Flask(__name__)

        with app.test_request_context(
            "/api/test",
            method="POST",
            data="not-json",
            content_type="text/plain",
        ):
            self.assertEqual(request_data(), {})


if __name__ == "__main__":
    unittest.main()
