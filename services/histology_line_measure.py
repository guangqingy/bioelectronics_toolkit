from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def distance_px(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def fmt_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def find_image_files(paths: Iterable[str | Path]) -> list[Path]:
    found: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            found.extend(
                sorted(
                    file
                    for file in path.rglob("*")
                    if file.is_file() and file.suffix.lower() in IMAGE_SUFFIXES
                )
            )
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return unique


@dataclass
class Calibration:
    pixel_length: float
    real_length: float
    unit: str = "um"
    x1_px: float | None = None
    y1_px: float | None = None
    x2_px: float | None = None
    y2_px: float | None = None
    source_image: str = ""

    @property
    def real_per_pixel(self) -> float:
        return self.real_length / self.pixel_length

    @property
    def pixels_per_unit(self) -> float:
        return self.pixel_length / self.real_length

    @property
    def has_drawable_line(self) -> bool:
        return None not in (self.x1_px, self.y1_px, self.x2_px, self.y2_px)


@dataclass
class MeasurementLine:
    x1_px: float
    y1_px: float
    x2_px: float
    y2_px: float
    pixel_length: float
    label: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_points(
        cls,
        x1_px: float,
        y1_px: float,
        x2_px: float,
        y2_px: float,
        label: str = "",
    ) -> "MeasurementLine":
        return cls(
            x1_px=x1_px,
            y1_px=y1_px,
            x2_px=x2_px,
            y2_px=y2_px,
            pixel_length=distance_px(x1_px, y1_px, x2_px, y2_px),
            label=label,
        )


@dataclass
class ImageMeasurements:
    path: Path
    lines: list[MeasurementLine] = field(default_factory=list)
    calibration: Calibration | None = None


CSV_COLUMNS = [
    "image_index",
    "image_name",
    "image_path",
    "line_index",
    "label",
    "x1_px",
    "y1_px",
    "x2_px",
    "y2_px",
    "pixel_length",
    "calibrated_length",
    "unit",
    "scale_pixel_length",
    "scale_real_length",
    "scale_pixels_per_unit",
    "scale_unit",
    "scale_source_image",
    "created_at",
]


def measurement_rows(states: Sequence[ImageMeasurements]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for image_index, state in enumerate(states, start=1):
        calibration = state.calibration
        for line_index, line in enumerate(state.lines, start=1):
            calibrated_length = (
                line.pixel_length * calibration.real_per_pixel if calibration is not None else None
            )
            rows.append(
                {
                    "image_index": str(image_index),
                    "image_name": state.path.name,
                    "image_path": str(state.path),
                    "line_index": str(line_index),
                    "label": line.label,
                    "x1_px": fmt_float(line.x1_px),
                    "y1_px": fmt_float(line.y1_px),
                    "x2_px": fmt_float(line.x2_px),
                    "y2_px": fmt_float(line.y2_px),
                    "pixel_length": fmt_float(line.pixel_length),
                    "calibrated_length": fmt_float(calibrated_length),
                    "unit": calibration.unit if calibration is not None else "",
                    "scale_pixel_length": fmt_float(
                        calibration.pixel_length if calibration is not None else None
                    ),
                    "scale_real_length": fmt_float(
                        calibration.real_length if calibration is not None else None
                    ),
                    "scale_pixels_per_unit": fmt_float(
                        calibration.pixels_per_unit if calibration is not None else None
                    ),
                    "scale_unit": calibration.unit if calibration is not None else "",
                    "scale_source_image": calibration.source_image if calibration is not None else "",
                    "created_at": line.created_at,
                }
            )
    return rows


def write_measurements_csv(states: Sequence[ImageMeasurements], output_path: str | Path) -> int:
    rows = measurement_rows(states)
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def default_export_path(state: ImageMeasurements) -> Path:
    return state.path.with_name(f"{state.path.stem}_line_measurements.csv")
