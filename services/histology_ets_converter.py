from __future__ import annotations

import json
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from services.histology_ets_models import (
    CONVERTED_MARKER_SUFFIX,
    CONVERTER_VERSION,
    ETS_SUFFIX,
    EtsConversionResult,
    EtsImageIndex,
    ProgressCallback,
)
from services.histology_ets_reader import _read_exact, read_ets_index

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - project dependency, kept defensive
    tifffile = None

try:
    import imagecodecs  # type: ignore
except Exception:  # pragma: no cover - declared dependency, kept defensive
    imagecodecs = None

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


def _gray_tile_stats(arr: np.ndarray) -> tuple[float, float, float, float]:
    gray = np.asarray(arr[..., 0], dtype=np.float32)
    return (
        float(np.mean(gray)),
        float(np.std(gray)),
        float(np.mean(gray >= 250)),
        float(np.mean(gray <= 5)),
    )


def _select_output_z(
    source: Path,
    index: EtsImageIndex,
    *,
    sample_limit: int = 96,
) -> int:
    z_values = sorted(index.z_values or {int(tile.z) for tile in index.tiles})
    if not z_values:
        index.selected_z = 0
        index.z_plane_count = 1
        index.z_values = [0]
        return 0
    index.z_plane_count = len(z_values)
    index.z_values = z_values
    if len(z_values) == 1:
        index.selected_z = int(z_values[0])
        return index.selected_z

    by_z_coord = {(tile.z, tile.x, tile.y): tile for tile in index.tiles}
    coords = sorted({(tile.x, tile.y) for tile in index.tiles})
    if len(coords) > sample_limit:
        step = max(1, len(coords) // sample_limit)
        coords = coords[::step][:sample_limit]

    stats: dict[int, list[tuple[float, float, float, float]]] = {z: [] for z in z_values}
    with source.open("rb") as handle:
        for x, y in coords:
            for z in z_values:
                tile = by_z_coord.get((z, x, y))
                if tile is None:
                    continue
                handle.seek(tile.offset)
                arr = _decode_tile_rgb(
                    _read_exact(handle, tile.byte_count),
                    index.tile_width,
                    index.tile_height,
                )
                stats[z].append(_gray_tile_stats(arr))

    best_z = z_values[0]
    best_score = float("-inf")
    for z in z_values:
        values = stats.get(z) or []
        if not values:
            continue
        data = np.asarray(values, dtype=np.float32)
        std_mean = float(data[:, 1].mean())
        white_frac = float(data[:, 2].mean())
        black_frac = float(data[:, 3].mean())
        texture_score = std_mean * (1.0 - min(0.9, white_frac * 0.85 + black_frac * 0.25))
        if texture_score > best_score:
            best_score = texture_score
            best_z = z
    index.selected_z = int(best_z)
    return index.selected_z


def _tile_iterator(source: Path, index: EtsImageIndex, progress: ProgressCallback | None = None):
    selected_z = int(index.selected_z)
    by_coord = {(tile.x, tile.y): tile for tile in index.tiles if tile.z == selected_z}
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


def _read_conversion_sidecar(output: Path) -> dict[str, object]:
    sidecar = output.with_suffix(output.suffix + CONVERTED_MARKER_SUFFIX)
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _existing_conversion_is_current(output: Path) -> bool:
    data = _read_conversion_sidecar(output)
    try:
        return int(data.get("converter_version") or 0) >= CONVERTER_VERSION
    except Exception:
        return False


def _existing_tiff_is_usable(output: Path) -> bool:
    if output.suffix.lower() not in {".tif", ".tiff"} or not output.is_file():
        return False
    try:
        if tifffile is not None:
            with tifffile.TiffFile(str(output)) as tf:
                source = tf.series[0] if getattr(tf, "series", None) else tf.pages[0]
                shape = tuple(int(x) for x in source.shape)
            return len(shape) >= 2 and min(shape[:2]) > 0
        with Image.open(output) as img:
            return img.size[0] > 0 and img.size[1] > 0
    except Exception:
        return False


def convert_ets_to_tiff(
    source_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
    selected_z: int | None = None,
    progress: ProgressCallback | None = None,
) -> EtsConversionResult:
    if tifffile is None:
        raise RuntimeError("tifffile is required for ETS conversion")
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if source.suffix.lower() != ETS_SUFFIX:
        raise ValueError(f"Expected an .ets file, got {source.suffix}")
    index = read_ets_index(source)
    if selected_z is not None:
        z_values = sorted(index.z_values or {int(tile.z) for tile in index.tiles})
        if int(selected_z) not in z_values:
            raise ValueError(f"ETS z-plane {selected_z} not found in {source.name}")
        index.z_plane_count = len(z_values)
        index.z_values = z_values
        index.selected_z = int(selected_z)
    if output.exists() and not overwrite and _existing_tiff_is_usable(output):
        existing = _read_conversion_sidecar(output)
        existing_index = existing.get("index") if isinstance(existing.get("index"), dict) else {}
        if selected_z is None and isinstance(existing_index, dict):
            index.selected_z = int(existing_index.get("selected_z") or index.selected_z)
            index.z_plane_count = int(existing_index.get("z_plane_count") or index.z_plane_count)
        elif selected_z is not None:
            index.selected_z = int(selected_z)
        is_current = (
            output.stat().st_mtime >= source.stat().st_mtime
            and _existing_conversion_is_current(output)
            and (
                selected_z is None
                or int((existing_index or {}).get("selected_z") or selected_z) == int(selected_z)
            )
        )
        return EtsConversionResult(
            source_path=str(source),
            output_path=str(output),
            case_dir=str(output.parent),
            sample_id=output.parent.name,
            role=_role_for_ets_path(source),
            status="skipped_existing" if is_current else "skipped_existing_tiff",
            width=index.width,
            height=index.height,
            tile_width=index.tile_width,
            tile_height=index.tile_height,
            tile_count=index.level0_tile_count,
            compression=index.compression_name,
            z_plane_count=index.z_plane_count,
            selected_z=index.selected_z,
        )

    if index.tile_width % 16 or index.tile_height % 16:
        raise ValueError("ETS tile dimensions must be multiples of 16 for tiled TIFF output")
    if selected_z is None:
        selected_z = _select_output_z(source, index)
    else:
        index.selected_z = int(selected_z)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(f".{output.name}.tmp")
    if tmp_output.exists():
        tmp_output.unlink()
    if progress:
        z_note = (
            f"; selected z-plane {selected_z} of {index.z_plane_count}"
            if index.z_plane_count > 1
            else ""
        )
        progress(0.0, f"Reading ETS tile index for {source.name}{z_note}")
    with tifffile.TiffWriter(str(tmp_output), bigtiff=True) as writer:
        writer.write(
            data=_tile_iterator(source, index, progress=progress),
            shape=(index.height, index.width, 3),
            dtype=np.uint8,
            photometric="rgb",
            tile=(index.tile_height, index.tile_width),
            compression="deflate",
            metadata={
                "axes": "YXS",
                "source_format": "Olympus ETS",
                "selected_z": int(index.selected_z),
                "z_plane_count": int(index.z_plane_count),
            },
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
        z_plane_count=index.z_plane_count,
        selected_z=index.selected_z,
    )


def _write_conversion_sidecar(output: Path, source: Path, index: EtsImageIndex) -> None:
    sidecar = output.with_suffix(output.suffix + CONVERTED_MARKER_SUFFIX)
    payload = {
        "kind": "dataprocess_ets_conversion",
        "converter_version": CONVERTER_VERSION,
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

__all__ = [
    "_as_uint8_rgb",
    "_decode_tile_rgb",
    "_existing_conversion_is_current",
    "_existing_tiff_is_usable",
    "_gray_tile_stats",
    "_read_conversion_sidecar",
    "_role_for_ets_path",
    "_select_output_z",
    "_tile_iterator",
    "_write_conversion_sidecar",
    "convert_ets_to_tiff",
]
