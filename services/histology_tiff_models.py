from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RAW_OLYMPUS_SUFFIXES = {".vsi", ".ets"}
PROJECT_KIND = "dataprocess_histology_project"
PROJECT_PROTOCOL = "dataprocess-tiff-histology"
PROJECT_FILE_NAME = "histology_project.dphistology"


@dataclass
class ImageRecord:
    file_path: str
    channel_name: str
    detected_channel: str
    confidence: float
    original_filename: str
    dtype: str = ""
    shape: tuple[int, ...] = field(default_factory=tuple)
    bit_depth: int = 0
    warning_messages: list[str] = field(default_factory=list)


@dataclass
class SampleRecord:
    sample_id: str
    image_files: dict[str, str] = field(default_factory=dict)
    images: list[ImageRecord] = field(default_factory=list)
    raw_olympus_reference: str = ""
    analysis_folder: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

__all__ = [
    "ImageRecord",
    "PROJECT_FILE_NAME",
    "PROJECT_KIND",
    "PROJECT_PROTOCOL",
    "RAW_OLYMPUS_SUFFIXES",
    "SampleRecord",
]
