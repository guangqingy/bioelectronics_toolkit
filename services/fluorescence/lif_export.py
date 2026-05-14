from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np

from services.fluorescence import lif_dimensions, lif_metadata


def imagej_lut(lut_name: str) -> np.ndarray:
    x = np.arange(256, dtype=np.uint8)
    z = np.zeros(256, dtype=np.uint8)
    name = str(lut_name or "").strip().lower()
    if name == "red":
        return np.stack([x, z, z])
    if name == "green":
        return np.stack([z, x, z])
    if name == "blue":
        return np.stack([z, z, x])
    if name == "cyan":
        return np.stack([z, x, x])
    if name == "magenta":
        return np.stack([x, z, x])
    if name == "yellow":
        return np.stack([x, x, z])
    return np.stack([x, x, x])


def imagej_luts(record: dict, c_count: int) -> list[np.ndarray]:
    names = list(record.get("channel_lut_names", []) or [])
    if not names:
        names = lif_dimensions.channel_lut_names(record.get("xml_metadata", {}) or {}, c_count)
    if not any(str(name or "").strip() for name in names):
        return []
    return [imagej_lut(name) for name in names[: max(1, int(c_count or 1))]]


def tiff_datetime(record: dict) -> str | None:
    iso = str(record.get("acquired_iso", "") or "").strip()
    if not iso or len(iso) < 10:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def resolution_kwargs(calibration: dict) -> dict:
    x_ppum = lif_dimensions.positive_float(calibration.get("x_pixels_per_um"))
    y_ppum = lif_dimensions.positive_float(calibration.get("y_pixels_per_um"))
    if not x_ppum or not y_ppum:
        return {}
    return {
        "resolution": (x_ppum * 10000.0, y_ppum * 10000.0),
        "resolutionunit": "CENTIMETER",
    }


def export_plan(record: dict) -> dict:
    dims = record.get("dimensions", {}) or {}
    plane_dimensions = lif_dimensions.plane_dimensions_from_record(record, include_single=True)
    frame_dimensions = [d for d in plane_dimensions if int(d.get("id", 0)) != 3]
    z_dimension = next((d for d in plane_dimensions if int(d.get("id", 0)) == 3), None)

    frame_count = 1
    for dim in frame_dimensions:
        frame_count *= max(1, int(dim.get("count", 1) or 1))
    z_count = max(1, int((z_dimension or {}).get("count", dims.get("z", 1)) or 1))
    c_count = max(1, int(record.get("channels", 1) or 1))
    y_count = max(1, int(dims.get("y", 1) or 1))
    x_count = max(1, int(dims.get("x", 1) or 1))
    total_planes = frame_count * z_count * c_count

    t_count = next(
        (int(d.get("count", 1) or 1) for d in plane_dimensions if int(d.get("id", 0)) == 4), 1
    )
    m_count = next(
        (int(d.get("count", 1) or 1) for d in plane_dimensions if int(d.get("id", 0)) == 10), 1
    )
    return {
        "plane_dimensions": plane_dimensions,
        "frame_dimensions": frame_dimensions,
        "z_dimension": z_dimension,
        "counts": {
            "frames": frame_count,
            "z": z_count,
            "c": c_count,
            "t": t_count,
            "m": m_count,
            "y": y_count,
            "x": x_count,
            "planes": total_planes,
        },
        "imagej_shape": [frame_count, z_count, c_count, y_count, x_count],
        "imagej_axes": "TZCYX",
        "page_order": "flattened Leica frame dimensions, Z, C, Y, X",
    }


def frame_dimension_combinations(frame_dimensions: list[dict]):
    if not frame_dimensions:
        yield 0, {}
        return

    dim_ids = [int(d.get("id")) for d in frame_dimensions]
    ranges = [range(max(1, int(d.get("count", 1) or 1))) for d in frame_dimensions]
    # readlif stores dimensions in fastest-to-slowest order after X/Y.
    # Reverse twice so the first listed Leica dimension changes fastest.
    for frame_index, reversed_coords in enumerate(product(*reversed(ranges))):
        coords = tuple(reversed(reversed_coords))
        yield frame_index, {dim_id: int(value) for dim_id, value in zip(dim_ids, coords)}


def plane_sequence(plan: dict, limit: int = 10000) -> list[dict]:
    counts = plan.get("counts", {}) or {}
    total = int(counts.get("planes", 0) or 0)
    if total > limit:
        return []

    z_count = max(1, int(counts.get("z", 1) or 1))
    c_count = max(1, int(counts.get("c", 1) or 1))
    z_dim = plan.get("z_dimension") or {}
    z_dim_id = int(z_dim.get("id", 3) or 3) if z_dim else None
    sequence = []
    page_index = 0
    for frame_index, frame_values in frame_dimension_combinations(
        plan.get("frame_dimensions", []) or []
    ):
        for z in range(z_count):
            dim_values = dict(frame_values)
            if z_dim_id is not None:
                dim_values[z_dim_id] = z
            for c in range(c_count):
                sequence.append(
                    {
                        "page": page_index,
                        "frame": frame_index,
                        "z": z,
                        "c": c,
                        "dimension_indices": {str(k): int(v) for k, v in dim_values.items()},
                    }
                )
                page_index += 1
    return sequence


def build_export_metadata(lif, image, record: dict, output_name: str, plan: dict) -> dict:
    orientation = dict(record.get("scan_orientation", {}) or {})
    calibration = lif_dimensions.oriented_calibration(
        record.get("calibration", {}) or {}, orientation
    )
    raw_counts = plan.get("counts", {}) or {}
    counts = lif_dimensions.oriented_counts(raw_counts, orientation)
    sequence = plane_sequence(plan)
    return {
        "metadata_version": 2,
        "exporter": "DataProcess Leica LIF Browser",
        "source_lif": str(getattr(lif, "filename", "")),
        "source_name": record.get("full_name", ""),
        "source_folder": record.get("folder", ""),
        "export_name": output_name,
        "acquired_at": record.get("acquired_at", ""),
        "acquired_iso": record.get("acquired_iso", ""),
        "plane_order": plan.get("page_order", ""),
        "axes": "FZCYX",
        "dimensions": counts,
        "raw_dimensions": raw_counts,
        "scan_orientation": orientation,
        "plane_dimensions": plan.get("plane_dimensions", []),
        "flattened_frame_dimensions": plan.get("frame_dimensions", []),
        "z_dimension": plan.get("z_dimension"),
        "imagej_mapping": {
            "axes": plan.get("imagej_axes", "TZCYX"),
            "shape": [
                counts.get("frames", 1),
                counts.get("z", 1),
                counts.get("c", 1),
                counts.get("y", 1),
                counts.get("x", 1),
            ],
            "frames": counts.get("frames", 1),
            "slices": counts.get("z", 1),
            "channels": counts.get("c", 1),
            "note": "Non-Z Leica dimensions are flattened into ImageJ frames in Leica dimension order; exact DimID coordinates are in plane_sequence.",
        },
        "plane_sequence": sequence,
        "plane_sequence_truncated": not bool(sequence)
        and int(counts.get("planes", 0) or 0) > 10000,
        "bit_depth": record.get("bit_depth", []),
        "channel_lut_names": record.get("channel_lut_names", []),
        "calibration": calibration,
        "readlif_scale": record.get("scale", []),
        "readlif_scale_n": record.get("scale_n", {}),
        "readlif_dims_n": record.get("dims_n", {}),
        "display_dims": record.get("display_dims", []),
        "mosaic_position": record.get("mosaic_position", []),
        "leica_settings": lif_dimensions.json_safe(
            getattr(image, "settings", {}) or record.get("settings", {}) or {}
        ),
        "leica_xml_metadata": record.get("xml_metadata", {}),
    }


def build_image_description(tifflib_module: Any, metadata_payload: dict) -> tuple[str, str]:
    dims = metadata_payload.get("dimensions", {}) or {}
    calibration = metadata_payload.get("calibration", {}) or {}
    desc_fn = getattr(tifflib_module, "imagej_description", None)
    if desc_fn is not None:
        imagej_meta = {"unit": "um", "hyperstack": True}
        z_spacing = calibration.get("z_spacing_um")
        frame_interval = calibration.get("frame_interval_s")
        active_frame_dims = [
            d
            for d in (metadata_payload.get("flattened_frame_dimensions", []) or [])
            if int(d.get("count", 1) or 1) > 1
        ]
        if z_spacing is not None:
            imagej_meta["spacing"] = z_spacing
        if frame_interval is not None and len(active_frame_dims) <= 1:
            imagej_meta["finterval"] = frame_interval
        imagej_meta["mode"] = "grayscale"
        return (
            desc_fn(
                (
                    int(dims.get("frames", 1) or 1),
                    int(dims.get("z", 1) or 1),
                    int(dims.get("c", 1) or 1),
                    int(dims.get("y", 1) or 1),
                    int(dims.get("x", 1) or 1),
                ),
                axes="TZCYX",
                **imagej_meta,
            ),
            "ImageJ",
        )
    return json.dumps(metadata_payload, ensure_ascii=False, indent=2), "JSON"


def imagej_extratags(tifflib_module: Any, record: dict, c_count: int):
    tag_fn = getattr(tifflib_module, "imagej_metadata_tag", None)
    if tag_fn is None or c_count <= 1:
        return None
    luts = imagej_luts(record, c_count)
    if not luts:
        return None
    try:
        return tuple(tag_fn({"LUTs": luts}, "<"))
    except Exception:
        return None


def imagej_metadata(metadata_payload: dict, record: dict, c_count: int) -> dict:
    calibration = metadata_payload.get("calibration", {}) or {}
    meta = {
        "axes": "TZCYX",
        "unit": "um",
        "hyperstack": True,
        "mode": "grayscale",
    }
    z_spacing = calibration.get("z_spacing_um")
    if z_spacing is not None:
        meta["spacing"] = z_spacing
    frame_interval = calibration.get("frame_interval_s")
    active_frame_dims = [
        d
        for d in (metadata_payload.get("flattened_frame_dimensions", []) or [])
        if int(d.get("count", 1) or 1) > 1
    ]
    if frame_interval is not None and len(active_frame_dims) <= 1:
        meta["finterval"] = frame_interval
    luts = imagej_luts(record, c_count)
    if luts:
        meta["LUTs"] = luts
    return meta


def sanitize_filename(name: str, fallback: str) -> str:
    raw = str(name or "").strip()
    if raw.lower().endswith((".tif", ".tiff", ".html", ".htm")):
        raw = Path(raw).stem
    if not raw:
        raw = fallback
    out = re.sub(r"[^\w.\- ]+", "_", raw, flags=re.UNICODE).strip(" ._")
    out = re.sub(r"\s+", "_", out)
    return out or fallback


def output_name_for_record(record: dict, rename_map: dict | None = None) -> str:
    rename_map = rename_map if isinstance(rename_map, dict) else {}
    idx = str(record.get("index", ""))
    custom = str(
        rename_map.get(idx, "") or rename_map.get(int(record.get("index", -1)), "") or ""
    ).strip()
    return custom or str(record.get("name", "") or f"image_{int(record.get('index', 0)) + 1}")


def unique_output_path(out_path: Path, overwrite: bool) -> Path:
    if overwrite or not out_path.exists():
        return out_path
    stem = out_path.stem
    suffix = out_path.suffix
    parent = out_path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def manifest_rows(
    records: list[dict], order_indices: list[int] | None = None, rename_map: dict | None = None
) -> list[dict]:
    by_index = {int(r["index"]): r for r in records}
    if order_indices:
        ordered = [by_index[i] for i in order_indices if i in by_index]
        seen = {int(r["index"]) for r in ordered}
        ordered.extend([r for r in records if int(r["index"]) not in seen])
    else:
        ordered = sorted(records, key=lambda r: lif_metadata.record_sort_tuple(r, "time"))

    rows = []
    for display_order, r in enumerate(ordered, start=1):
        dims = r.get("dimensions", {}) or {}
        rows.append(
            {
                "display_order": display_order,
                "original_order": r.get("original_order", ""),
                "image_index": r.get("index", ""),
                "acquired_at": r.get("acquired_at", ""),
                "timestamp_source": r.get("sort_source", ""),
                "folder": r.get("folder", ""),
                "name": r.get("name", ""),
                "display_name": output_name_for_record(r, rename_map),
                "full_name": r.get("full_name", ""),
                "x": dims.get("x", ""),
                "y": dims.get("y", ""),
                "z": dims.get("z", ""),
                "t": dims.get("t", ""),
                "mosaic_tiles": dims.get("m", ""),
                "channels": r.get("channels", ""),
                "bit_depth": ";".join(str(v) for v in (r.get("bit_depth") or [])),
            }
        )
    return rows


def export_image_as_tiff(
    lif,
    record: dict,
    output_dir: Path,
    output_name: str,
    *,
    tifflib_module: Any,
    get_plane_by_dimensions,
    display_array,
    overwrite: bool = True,
) -> dict:
    if tifflib_module is None:
        raise RuntimeError(
            "tifffile is required for TIFF export. Run: python -m pip install tifffile"
        )

    image_index = int(record.get("index", 0))
    image = lif.get_image(image_index)
    plan = export_plan(record)
    counts = lif_dimensions.oriented_counts(
        plan.get("counts", {}) or {}, record.get("scan_orientation", {}) or {}
    )
    z_count = max(1, int(counts.get("z", 1) or 1))
    c_count = max(1, int(counts.get("c", 1) or 1))
    y_count = max(1, int(counts.get("y", 1) or 1))
    x_count = max(1, int(counts.get("x", 1) or 1))
    total_planes = max(1, int(counts.get("planes", 1) or 1))

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(output_name, f"image_{image_index + 1}")
    out_path = unique_output_path(output_dir / f"{safe_name}.tiff", overwrite)

    bit_depths = record.get("bit_depth", []) or [16]
    max_bit_depth = int(max(bit_depths) if bit_depths else 16)
    bytes_per_pixel = max(1, (max_bit_depth + 7) // 8)
    estimated_bytes = x_count * y_count * total_planes * bytes_per_pixel
    bigtiff = estimated_bytes > 3_500_000_000
    metadata_payload = build_export_metadata(lif, image, record, output_name, plan)
    imagej_meta = imagej_metadata(metadata_payload, record, c_count)
    description_type = "ImageJ"
    calibration = metadata_payload.get("calibration", {}) or {}
    res_kwargs = resolution_kwargs(calibration)
    date_time = tiff_datetime(record)
    sidecar_path = out_path.with_name(f"{out_path.stem}_metadata.json")

    planes_written = 0
    stack = None
    z_dim = plan.get("z_dimension") or {}
    z_dim_id = int(z_dim.get("id", 3) or 3) if z_dim else None
    for frame_index, frame_values in frame_dimension_combinations(
        plan.get("frame_dimensions", []) or []
    ):
        for z in range(z_count):
            dim_values = dict(frame_values)
            if z_dim_id is not None:
                dim_values[z_dim_id] = z
            for c in range(c_count):
                arr = display_array(
                    get_plane_by_dimensions(image, c=c, dimension_values=dim_values), record
                )
                if stack is None:
                    stack = np.empty(
                        (
                            max(1, int(counts.get("frames", 1) or 1)),
                            z_count,
                            c_count,
                            arr.shape[0],
                            arr.shape[1],
                        ),
                        dtype=arr.dtype,
                    )
                stack[frame_index, z, c, :, :] = arr
                planes_written += 1

    if stack is None:
        raise RuntimeError("No image planes found for export.")

    write_kwargs = {
        "imagej": True,
        "bigtiff": bigtiff,
        "byteorder": "<",
        "photometric": "minisblack",
        "metadata": imagej_meta,
        "software": "DataProcess Leica LIF Browser",
        **res_kwargs,
    }
    if date_time:
        write_kwargs["datetime"] = date_time
    tifflib_module.imwrite(str(out_path), stack, **write_kwargs)

    sidecar_path.write_text(
        json.dumps(metadata_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "image_index": image_index,
        "name": record.get("name", ""),
        "output_name": output_name,
        "output_path": str(out_path),
        "metadata_path": str(sidecar_path),
        "outputs": [
            {"path": str(out_path), "type": "tiff", "role": "exported_tiff"},
            {"path": str(sidecar_path), "type": "metadata_json", "role": "metadata"},
        ],
        "planes": planes_written,
        "shape": [planes_written, y_count, x_count] if planes_written > 1 else [y_count, x_count],
        "bigtiff": bigtiff,
        "description_type": description_type,
        "calibration": calibration,
        "plane_dimensions": plan.get("plane_dimensions", []),
    }
