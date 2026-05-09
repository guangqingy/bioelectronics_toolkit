from __future__ import annotations

import importlib
import re
import unittest
from pathlib import Path


SCRIPT_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*\"([A-Za-z0-9_.:]+)\"")


def project_scripts() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts: dict[str, str] = {}
    in_scripts = False

    for raw_line in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "[project.scripts]":
            in_scripts = True
            continue
        if in_scripts and line.startswith("[") and line.endswith("]"):
            break
        if not in_scripts or not line or line.startswith("#"):
            continue
        match = SCRIPT_LINE_RE.match(line)
        if match:
            scripts[match.group(1)] = match.group(2)

    return scripts


class ConsoleEntrypointTests(unittest.TestCase):
    def test_all_project_scripts_import_callable_main(self) -> None:
        scripts = project_scripts()
        self.assertGreaterEqual(len(scripts), 1)

        for command, target in scripts.items():
            with self.subTest(command=command):
                module_name, _, attr_name = target.partition(":")
                self.assertTrue(module_name)
                self.assertTrue(attr_name)

                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, attr_name, None)))


if __name__ == "__main__":
    unittest.main()
