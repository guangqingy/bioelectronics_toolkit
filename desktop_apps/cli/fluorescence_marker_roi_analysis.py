from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.fluorescence.marker_roi import (  # noqa: F401 - compatibility re-export
    DEFAULT_OUTPUT_DIRNAME,
    DEFAULT_ROI_JSON,
    MarkerParams,
    analyze,
    build_arg_parser,
    run_parameter_tuning,
)
from services.fluorescence.marker_roi import (
    main as _service_main,
)


def main(argv: Sequence[str] | None = None) -> int:
    return _service_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
