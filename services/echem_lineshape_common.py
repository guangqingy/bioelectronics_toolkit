from __future__ import annotations

import re
from pathlib import Path

import numpy as np

DEFAULT_CHAMBERS = [1, 2, 3]
DEFAULT_CROP_T0 = -0.005
DEFAULT_CROP_T1 = 0.020
DEVICE_DIR_RE = re.compile(r"^(?P<prefix>.*)(?P<ch>\d)_(?P<idx>\d+)$")
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
SOURCE_SUFFIXES = {".csv", ".txt", ".tsv"}


def _figure_class():
    import matplotlib as mpl
    from matplotlib.figure import Figure

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    return Figure


def normalize_kind(kind: object) -> str:
    text = str(kind or "photocurrent").strip().lower()
    return "photovoltage" if text in {"photovoltage", "pv", "voltage"} else "photocurrent"


def parse_chambers(value: object, default: list[int] | None = None) -> list[int]:
    if default is None:
        default = DEFAULT_CHAMBERS
    if value in (None, ""):
        return list(default)
    raw = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value))
    out: list[int] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            out.append(int(float(text)))
        except ValueError:
            continue
    seen: set[int] = set()
    deduped = [x for x in out if not (x in seen or seen.add(x))]
    return deduped or list(default)


def _float_or(value: object, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def _int_or(value: object, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _subfolder(kind: str) -> str:
    return "Photovoltage" if normalize_kind(kind) == "photovoltage" else "Photocurrent"


def _pattern(kind: str) -> str:
    return "*_pulse_*.csv" if normalize_kind(kind) == "photovoltage" else "*_pair_*.csv"


def _natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def infer_kind_from_path(path: str | Path, fallback: object = "photocurrent") -> str:
    text = str(path).lower()
    if "photovoltage" in text or "_pulse_" in text:
        return "photovoltage"
    if "photocurrent" in text or "_pair_" in text:
        return "photocurrent"
    return normalize_kind(fallback)


__all__ = [
    "DEFAULT_CHAMBERS",
    "DEFAULT_CROP_T0",
    "DEFAULT_CROP_T1",
    "DEVICE_DIR_RE",
    "FLOAT_RE",
    "SOURCE_SUFFIXES",
    "_figure_class",
    "_float_or",
    "_int_or",
    "_natural_key",
    "_pattern",
    "_subfolder",
    "infer_kind_from_path",
    "normalize_kind",
    "parse_chambers",
]
