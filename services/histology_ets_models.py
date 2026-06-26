from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

ETS_SUFFIX = ".ets"
CONVERTED_MARKER_SUFFIX = ".dataprocess_ets.json"
CONVERTER_VERSION = 2
DEFAULT_MAX_ETS_TILE_PIXELS = 4096 * 4096
DEFAULT_MAX_ETS_TILES = 2_000_000
DEFAULT_Z_CHANNEL_NAMES = ("Hoechst", "FITC", "Cy5", "Mito")

ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class EtsTile:
    x: int
    y: int
    z: int
    level: int
    offset: int
    byte_count: int


@dataclass
class EtsImageIndex:
    source_path: str
    compression_code: int
    compression_name: str
    tile_width: int
    tile_height: int
    tile_count: int
    level0_tile_count: int
    tiles_across: int
    tiles_down: int
    width: int
    height: int
    z_plane_count: int = 1
    selected_z: int = 0
    z_values: list[int] = field(default_factory=list)
    tiles: list[EtsTile] = field(default_factory=list)


@dataclass
class EtsConversionResult:
    source_path: str
    output_path: str
    case_dir: str
    sample_id: str
    role: str
    status: str
    width: int = 0
    height: int = 0
    tile_width: int = 0
    tile_height: int = 0
    tile_count: int = 0
    compression: str = ""
    z_plane_count: int = 1
    selected_z: int = 0
    warning_messages: list[str] = field(default_factory=list)

__all__ = [
    "CONVERTED_MARKER_SUFFIX",
    "CONVERTER_VERSION",
    "DEFAULT_MAX_ETS_TILE_PIXELS",
    "DEFAULT_MAX_ETS_TILES",
    "DEFAULT_Z_CHANNEL_NAMES",
    "ETS_SUFFIX",
    "EtsConversionResult",
    "EtsImageIndex",
    "EtsTile",
    "ProgressCallback",
]
