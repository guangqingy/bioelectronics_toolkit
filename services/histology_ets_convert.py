from __future__ import annotations

import json
import os
import struct
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - project dependency, kept defensive
    tifffile = None

try:
    import imagecodecs  # type: ignore
except Exception:  # pragma: no cover - declared dependency, kept defensive
    imagecodecs = None

from services.histology_common import sanitize_name

ETS_SUFFIX = ".ets"
CONVERTED_MARKER_SUFFIX = ".dataprocess_ets.json"
DEFAULT_MAX_ETS_TILE_PIXELS = 4096 * 4096
DEFAULT_MAX_ETS_TILES = 2_000_000

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
    warning_messages: list[str] = field(default_factory=list)


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
        tiles=level0,
    )


def _as_uint8_rgb(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[-1] >= 3:
        arr = arr[..., :3]
    elif arr.ndim == 3 and arr.shape[0] in {3, 4}:
        arr = np.moveaxis(arr[:3], 0, -1)
    else:
        while arr.ndim > 2:
            arr = np.max(arr, axis=0)
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.dtype == np.uint8:
        return np.ascontiguousarray(arr)
    finite = arr[np.isfinite(arr)] if arr.dtype.kind == "f" else arr.reshape(-1)
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo = float(np.min(finite))
    hi = float(np.max(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((arr.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    return np.ascontiguousarray(np.round(scaled * 255.0).astype(np.uint8))


def _decode_tile_rgb(blob: bytes, tile_width: int, tile_height: int) -> np.ndarray:
    try:
        with Image.open(BytesIO(blob)) as img:
            arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    except Exception:
        if imagecodecs is None:
            raise
        arr = _as_uint8_rgb(imagecodecs.imread(BytesIO(blob)))
    if arr.shape[0] == tile_height and arr.shape[1] == tile_width:
        return arr
    padded = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
    h = min(tile_height, int(arr.shape[0]))
    w = min(tile_width, int(arr.shape[1]))
    if h > 0 and w > 0:
        padded[:h, :w, :] = arr[:h, :w, :3]
    return padded


def _tile_iterator(source: Path, index: EtsImageIndex, progress: ProgressCallback | None = None):
    by_coord = {(tile.x, tile.y): tile for tile in index.tiles if tile.z == 0}
    blank = np.zeros((index.tile_height, index.tile_width, 3), dtype=np.uint8)
    total = max(1, index.tiles_across * index.tiles_down)
    done = 0
    with source.open("rb") as handle:
        for y in range(index.tiles_down):
            for x in range(index.tiles_across):
                tile = by_coord.get((x, y))
                if tile is None:
                    arr = blank
                else:
                    handle.seek(tile.offset)
                    arr = _decode_tile_rgb(
                        _read_exact(handle, tile.byte_count),
                        index.tile_width,
                        index.tile_height,
                    )
                done += 1
                if progress and (done == 1 or done == total or done % 25 == 0):
                    progress(done / total, f"Converting ETS tile {done}/{total}")
                yield arr


def convert_ets_to_tiff(
    source_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> EtsConversionResult:
    if tifffile is None:
        raise RuntimeError("tifffile is required for ETS conversion")
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if source.suffix.lower() != ETS_SUFFIX:
        raise ValueError(f"Expected an .ets file, got {source.suffix}")
    if output.exists() and not overwrite and output.stat().st_mtime >= source.stat().st_mtime:
        index = read_ets_index(source)
        return EtsConversionResult(
            source_path=str(source),
            output_path=str(output),
            case_dir=str(output.parent),
            sample_id=output.parent.name,
            role=_role_for_ets_path(source),
            status="skipped_existing",
            width=index.width,
            height=index.height,
            tile_width=index.tile_width,
            tile_height=index.tile_height,
            tile_count=index.level0_tile_count,
            compression=index.compression_name,
        )

    index = read_ets_index(source)
    if index.tile_width % 16 or index.tile_height % 16:
        raise ValueError("ETS tile dimensions must be multiples of 16 for tiled TIFF output")

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(f".{output.name}.tmp")
    if tmp_output.exists():
        tmp_output.unlink()
    if progress:
        progress(0.0, f"Reading ETS tile index for {source.name}")
    with tifffile.TiffWriter(str(tmp_output), bigtiff=True) as writer:
        writer.write(
            data=_tile_iterator(source, index, progress=progress),
            shape=(index.height, index.width, 3),
            dtype=np.uint8,
            photometric="rgb",
            tile=(index.tile_height, index.tile_width),
            compression="deflate",
            metadata={"axes": "YXS", "source_format": "Olympus ETS"},
            software="DataProcess ETS converter (tifffile/Pillow/imagecodecs)",
        )
    tmp_output.replace(output)
    _write_conversion_sidecar(output, source, index)
    if progress:
        progress(1.0, f"Converted {source.name} to {output.name}")
    return EtsConversionResult(
        source_path=str(source),
        output_path=str(output),
        case_dir=str(output.parent),
        sample_id=output.parent.name,
        role=_role_for_ets_path(source),
        status="converted",
        width=index.width,
        height=index.height,
        tile_width=index.tile_width,
        tile_height=index.tile_height,
        tile_count=index.level0_tile_count,
        compression=index.compression_name,
    )


def _write_conversion_sidecar(output: Path, source: Path, index: EtsImageIndex) -> None:
    sidecar = output.with_suffix(output.suffix + CONVERTED_MARKER_SUFFIX)
    payload = {
        "kind": "dataprocess_ets_conversion",
        "source_path": str(source),
        "output_path": str(output),
        "index": asdict(index),
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _role_for_ets_path(path: Path) -> str:
    text = path.as_posix().lower()
    if "overview" in text:
        return "overview"
    if "label" in text or "barcode" in text:
        return "label"
    return "brightfield"


def _case_dir_for_ets(root: Path, ets_path: Path) -> Path:
    root = root.resolve()
    ets_path = ets_path.resolve()
    for parent in [ets_path.parent, *ets_path.parents]:
        if parent == parent.parent:
            break
        if parent.is_dir() and any(item.suffix.lower() == ".vsi" for item in parent.glob("*.vsi")):
            return parent
        if parent == root:
            break
    try:
        rel = ets_path.relative_to(root)
    except ValueError:
        rel = ets_path.name
    if not isinstance(rel, str) and rel.parts:
        first = rel.parts[0]
        if first.startswith("_") or first.lower().startswith("stack"):
            return root
        return root / first
    return ets_path.parent


def _slide_token_for_ets(case_dir: Path, ets_path: Path) -> str:
    try:
        parts = ets_path.relative_to(case_dir).parts
    except ValueError:
        parts = ets_path.parts
    tokens: list[str] = []
    for part in parts:
        if part.startswith("_") and part.endswith("_") and len(part) > 2:
            tokens.append(part.strip("_"))
        elif part.lower().startswith("stack"):
            tokens.append(part)
    return sanitize_name("_".join(tokens), fallback=ets_path.stem)


def _output_path_for_ets(
    root: Path,
    ets_path: Path,
    used_outputs: set[str],
) -> tuple[Path, str, str]:
    case_dir = _case_dir_for_ets(root, ets_path)
    sample_id = sanitize_name(case_dir.name, fallback="sample")
    role = _role_for_ets_path(ets_path)
    role_suffix = {
        "brightfield": "Brightfield",
        "overview": "Overview",
        "label": "Label",
    }.get(role, sanitize_name(role).title())
    candidates = [case_dir / f"{sample_id}_{role_suffix}.tif"]
    token = _slide_token_for_ets(case_dir, ets_path)
    if token:
        candidates.append(case_dir / f"{sample_id}_{token}_{role_suffix}.tif")
    for idx in range(2, 1000):
        candidates.append(case_dir / f"{sample_id}_{role_suffix}_{idx}.tif")
    for candidate in candidates:
        key = str(candidate.resolve())
        if key not in used_outputs:
            used_outputs.add(key)
            return candidate, str(case_dir.resolve()), role
    raise ValueError(f"Could not allocate a converted TIFF name for {ets_path}")


def iter_ets_files(source: str | Path) -> list[Path]:
    root = Path(source).expanduser().resolve()
    if root.is_file():
        return [root] if root.suffix.lower() == ETS_SUFFIX else []
    if not root.is_dir():
        raise FileNotFoundError(f"ETS source folder not found: {root}")
    return [
        path.resolve()
        for path in sorted(root.rglob(f"*{ETS_SUFFIX}"))
        if path.is_file() and not _is_hidden(path, root)
    ]


def convert_ets_folder_to_tiff(
    source: str | Path,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> list[EtsConversionResult]:
    root = Path(source).expanduser().resolve()
    scan_root = root.parent if root.is_file() else root
    ets_files = iter_ets_files(root)
    results: list[EtsConversionResult] = []
    used_outputs: set[str] = set()
    total = max(1, len(ets_files))
    for idx, ets_path in enumerate(ets_files, start=1):
        output, case_dir, role = _output_path_for_ets(scan_root, ets_path, used_outputs)

        def item_progress(fraction: float, message: str, idx: int = idx) -> None:
            if progress:
                overall = (idx - 1 + max(0.0, min(1.0, fraction))) / total
                progress(overall, message)

        try:
            result = convert_ets_to_tiff(
                ets_path,
                output,
                overwrite=overwrite,
                progress=item_progress,
            )
            result.case_dir = case_dir
            result.sample_id = Path(case_dir).name
            result.role = role
            results.append(result)
        except Exception as exc:
            results.append(
                EtsConversionResult(
                    source_path=str(ets_path),
                    output_path=str(output),
                    case_dir=case_dir,
                    sample_id=Path(case_dir).name,
                    role=role,
                    status="error",
                    warning_messages=[str(exc)],
                )
            )
    if progress:
        progress(1.0, f"ETS conversion checked {len(ets_files)} file(s)")
    return results


__all__ = [
    "CONVERTED_MARKER_SUFFIX",
    "ETS_SUFFIX",
    "EtsConversionResult",
    "EtsImageIndex",
    "convert_ets_folder_to_tiff",
    "convert_ets_to_tiff",
    "iter_ets_files",
    "read_ets_index",
]
