from __future__ import annotations

import unittest

from dev_scripts import check_no_pyplot


class PyplotGuardTests(unittest.TestCase):
    def test_web_and_service_layers_use_oo_matplotlib_helpers(self) -> None:
        self.assertEqual(check_no_pyplot.main(), 0)


if __name__ == "__main__":
    unittest.main()
