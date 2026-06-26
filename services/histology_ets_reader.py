from __future__ import annotations

import os
import struct
from pathlib import Path

from services.histology_ets_models import (
    DEFAULT_MAX_ETS_TILE_PIXELS,
    DEFAULT_MAX_ETS_TILES,
    EtsImageIndex,
    EtsTile,
)

def _is_hidden(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def _read_exact(handle, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("Unexpected end of ETS file")
    return data


def _compression_name(code: int) -> str:
    return {
        0: "raw",
        1: "tiff",
        2: "jpeg",
        3: "jpeg2000",
        4: "png",
        5: "bmp",
    }.get(int(code), f"unknown_{code}")


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return int(default)
    return max(0, value)


def _max_ets_tile_pixels() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_ETS_TILE_PIXELS", DEFAULT_MAX_ETS_TILE_PIXELS)


def _max_ets_tiles() -> int:
    return _positive_int_env("DP_HISTOLOGY_MAX_ETS_TILES", DEFAULT_MAX_ETS_TILES)


def read_ets_index(source_path: str | Path) -> EtsImageIndex:
    path = Path(source_path).expanduser().resolve()
    with path.open("rb") as handle:
        sis = struct.unpack("<4sIIIQIIQIIIII", _read_exact(handle, 60))
        dummy5 = struct.unpack("<I", _read_exact(handle, 4))[0]
        (
            sis_magic,
            sis_nbytes,
            _sis_version,
            _sis_dim,
            ets_offset,
            ets_nbytes,
            _dummy0,
            tile_table_offset,
            tile_count,
            _dummy1,
            _dummy2,
            _dummy3,
            _dummy4,
        ) = sis
        if not sis_magic.startswith(b"SIS"):
            raise ValueError("Not an Olympus SIS/ETS file")
        if sis_nbytes < 60 or ets_nbytes < 40 or dummy5 != 0:
            raise ValueError("Unsupported ETS header layout")

        handle.seek(int(ets_offset))
        ets = struct.unpack("<4sIIIIIIIII", _read_exact(handle, 40))
        (
            ets_magic,
            _ets_version,
            _ets_dummy1,
            _ets_dummy2,
            _ets_dummy3,
            compression_code,
            _quality,
            tile_width,
            tile_height,
            _tile_depth,
        ) = ets
        if not ets_magic.startswith(b"ETS"):
            raise ValueError("Missing ETS image header")
        if tile_width <= 0 or tile_height <= 0:
            raise ValueError("Invalid ETS tile dimensions")

        max_tiles = _max_ets_tiles()
        if max_tiles and int(tile_count) > max_tiles:
            raise MemoryError(
                f"ETS tile count {tile_count:,} exceeds limit {max_tiles:,}; "
                "set DP_HISTOLOGY_MAX_ETS_TILES to raise the guard."
            )
        tile_pixels = int(tile_width) * int(tile_height)
        max_tile_pixels = _max_ets_tile_pixels()
        if max_tile_pixels and tile_pixels > max_tile_pixels:
            raise MemoryError(
                f"ETS tile size {tile_width}x{tile_height} exceeds "
                f"limit {max_tile_pixels:,} pixels; set "
                "DP_HISTOLOGY_MAX_ETS_TILE_PIXELS to raise the guard."
            )

        handle.seek(int(tile_table_offset))
        tiles: list[EtsTile] = []
        for _ in range(int(tile_count)):
            dummy1, x, y, z, level, offset, byte_count, _dummy2 = struct.unpack(
                "<IIIIIQII", _read_exact(handle, 36)
            )
            if dummy1 != 4:
                raise ValueError("Unsupported ETS tile table layout")
            tiles.append(
                EtsTile(
                    x=int(x),
                    y=int(y),
                    z=int(z),
                    level=int(level),
                    offset=int(offset),
                    byte_count=int(byte_count),
                )
            )

    level0 = [tile for tile in tiles if tile.level == 0 and tile.byte_count > 0]
    if not level0:
        raise ValueError("ETS file has no level-0 tiles")
    tiles_across = max(tile.x for tile in level0) + 1
    tiles_down = max(tile.y for tile in level0) + 1
    z_values = sorted({int(tile.z) for tile in level0})
    return EtsImageIndex(
        source_path=str(path),
        compression_code=int(compression_code),
        compression_name=_compression_name(int(compression_code)),
        tile_width=int(tile_width),
        tile_height=int(tile_height),
        tile_count=int(tile_count),
        level0_tile_count=len(level0),
        tiles_across=int(tiles_across),
        tiles_down=int(tiles_down),
        width=int(tiles_across * tile_width),
        height=int(tiles_down * tile_height),
        z_plane_count=len(z_values),
        selected_z=z_values[0] if z_values else 0,
        z_values=z_values,
        tiles=level0,
    )

__all__ = [
    "_compression_name",
    "_is_hidden",
    "_max_ets_tile_pixels",
    "_max_ets_tiles",
    "_positive_int_env",
    "_read_exact",
    "read_ets_index",
]
