#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "web_app.py", ROOT / "web_api", ROOT / "services"]
FORBIDDEN = (
    "import matplotlib.pyplot",
    "from matplotlib import pyplot",
    "plt.subplots",
    "plt.figure",
    "plt.close",
)


def iter_python_files(path: Path):
    if path.is_file() and path.suffix == ".py":
        yield path
        return
    for item in path.rglob("*.py"):
        if "__pycache__" not in item.parts:
            yield item


def main() -> int:
    failures: list[str] = []
    for target in TARGETS:
        for path in iter_python_files(target):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if any(token in line for token in FORBIDDEN):
                    rel = path.relative_to(ROOT)
                    failures.append(f"{rel}:{lineno}: use services.matplotlib_utils OO helpers")
    if failures:
        print("pyplot guard failed")
        print("\n".join(failures))
        return 1
    print("pyplot guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
