from __future__ import annotations

import base64
import unittest

import numpy as np

from services.fluorescence import route_helpers


class FluorescenceRouteHelperTests(unittest.TestCase):
    def test_parse_bool_and_unit_scale(self) -> None:
        self.assertTrue(route_helpers.parse_bool("yes"))
        self.assertFalse(route_helpers.parse_bool("off", True))
        self.assertEqual(route_helpers.unit_to_um_scale("nm"), 1e-3)
        self.assertEqual(route_helpers.sanitize_prefix(" bad/name "), "bad_name")
        self.assertEqual(route_helpers.normalize_hex_color("3af"), "#33aaff")

    def test_select_display_frame_handles_projection_modes(self) -> None:
        stack = np.arange(12, dtype=float).reshape(3, 2, 2)

        single, meta = route_helpers.select_display_frame(stack, 1, "single", None, None)
        self.assertTrue(np.array_equal(single, stack[1]))
        self.assertEqual(meta["mode"], "single")

        maximum, meta = route_helpers.select_display_frame(stack, 0, "max", 0, 2)
        self.assertTrue(np.array_equal(maximum, np.nanmax(stack, axis=0)))
        self.assertEqual(meta["z_end"], 2)

    def test_decode_data_url_payload(self) -> None:
        raw = b"hello"
        payload = "data:image/png;base64," + base64.b64encode(raw).decode()

        self.assertEqual(route_helpers.decode_base64_payload(payload), raw)


if __name__ == "__main__":
    unittest.main()
