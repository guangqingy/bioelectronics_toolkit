from __future__ import annotations

import base64
import unittest

from web_api.common import fig_to_b64


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


if __name__ == "__main__":
    unittest.main()
