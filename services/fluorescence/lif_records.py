from __future__ import annotations

from pathlib import Path

from services.fluorescence import lif_dimensions, lif_metadata


def record_from_image(lif, image_index: int, image_elements: list[dict]) -> dict:
    image = lif.get_image(image_index)
    info = getattr(image, "info", {}) or {}
    full_name = str(getattr(image, "name", "") or info.get("name") or f"Image {image_index + 1}")
    folder = str(info.get("path", "") or "").strip("/")
    simple_name = full_name.split("/")[-1] if full_name else f"Image {image_index + 1}"

    element_rec = image_elements[image_index] if image_index < len(image_elements) else {}
    element = element_rec.get("element")
    ts = lif_metadata.timestamp_from_element(element)
    acquired = ts["display"] if ts else ""
    sort_value = ts["sort_value"] if ts else None
    sort_source = ts["source"] if ts else "Leica project order"

    dims = {
        "x": lif_dimensions.dim_int(image, "x"),
        "y": lif_dimensions.dim_int(image, "y"),
        "z": lif_dimensions.dim_int(image, "z"),
        "t": lif_dimensions.dim_int(image, "t"),
        "m": lif_dimensions.dim_int(image, "m"),
    }
    channels = max(1, int(getattr(image, "channels", 1) or 1))
    bit_depth = list(getattr(image, "bit_depth", []) or [])
    scale = list(getattr(image, "scale", []) or [])
    scale_n = lif_dimensions.json_safe(getattr(image, "scale_n", {}) or {})
    dims_n = lif_dimensions.json_safe(getattr(image, "dims_n", {}) or {})
    settings = lif_dimensions.json_safe(getattr(image, "settings", {}) or {})
    xml_metadata = lif_dimensions.xml_metadata_summary(element)
    display_dims = list(getattr(image, "display_dims", []) or [])
    calibration = lif_dimensions.calibration_from_scale(scale)
    calibration = lif_dimensions.apply_xml_dimension_calibration(
        calibration,
        xml_metadata.get("dimensions", []),
        settings,
        dims_n,
    )
    record_shell = {"dimensions": dims, "dims_n": dims_n, "display_dims": display_dims}
    plane_dimensions = lif_dimensions.plane_dimensions_from_record(
        record_shell, include_single=True
    )
    orientation = lif_dimensions.orientation_from_settings(settings)

    return {
        "index": int(image_index),
        "original_order": int(image_index + 1),
        "name": simple_name,
        "full_name": full_name,
        "folder": folder,
        "xml_path": element_rec.get("xml_path", full_name),
        "acquired_at": acquired,
        "acquired_iso": ts["iso"] if ts else "",
        "sort_value": sort_value,
        "sort_source": sort_source,
        "timestamp_confidence": ts["confidence"] if ts else 0,
        "timestamp_raw": ts["raw"] if ts else "",
        "dimensions": dims,
        "channels": channels,
        "bit_depth": bit_depth,
        "scale": scale,
        "scale_n": scale_n,
        "dims_n": dims_n,
        "settings": settings,
        "xml_metadata": xml_metadata,
        "channel_lut_names": lif_dimensions.channel_lut_names(xml_metadata, channels),
        "scan_orientation": orientation,
        "calibration": calibration,
        "display_dims": display_dims,
        "plane_dimensions": plane_dimensions,
        "extra_dimensions": [d for d in plane_dimensions if int(d.get("id", 0)) not in {3, 4, 10}],
        "mosaic_position": lif_dimensions.json_safe(getattr(image, "mosaic_position", []) or []),
        "mosaic_tiles": len(getattr(image, "mosaic_position", []) or []),
    }


def clone_records(records: list[dict]) -> list[dict]:
    out = []
    for record in records:
        r = dict(record)
        r["dimensions"] = dict(record.get("dimensions", {}) or {})
        r["bit_depth"] = list(record.get("bit_depth", []) or [])
        r["scale"] = list(record.get("scale", []) or [])
        r["scale_n"] = dict(record.get("scale_n", {}) or {})
        r["dims_n"] = dict(record.get("dims_n", {}) or {})
        r["settings"] = dict(record.get("settings", {}) or {})
        r["xml_metadata"] = dict(record.get("xml_metadata", {}) or {})
        r["channel_lut_names"] = list(record.get("channel_lut_names", []) or [])
        r["scan_orientation"] = dict(record.get("scan_orientation", {}) or {})
        r["calibration"] = dict(record.get("calibration", {}) or {})
        r["display_dims"] = list(record.get("display_dims", []) or [])
        r["plane_dimensions"] = [dict(d) for d in (record.get("plane_dimensions", []) or [])]
        r["extra_dimensions"] = [dict(d) for d in (record.get("extra_dimensions", []) or [])]
        r["mosaic_position"] = list(record.get("mosaic_position", []) or [])
        out.append(r)
    return out


def load_records(path: str, *, lif_file_cls, cache: dict, reader_error: str = ""):
    if reader_error:
        raise RuntimeError(reader_error)
    path_text = str(path or "").strip()
    if not path_text:
        raise ValueError("Missing LIF file path")
    p = Path(path_text).expanduser()
    if not p.is_file():
        raise ValueError(f"LIF file not found: {path}")

    cache_key = str(p.resolve())
    stat = p.stat()
    cache_stamp = (stat.st_mtime_ns, stat.st_size)
    cached = cache.get(cache_key)
    if cached and cached.get("stamp") == cache_stamp:
        return cached["lif"], clone_records(cached["records"])

    lif = lif_file_cls(str(p))
    image_elements = lif_metadata.collect_image_elements(lif.xml_root)
    records = [record_from_image(lif, i, image_elements) for i in range(int(lif.num_images))]
    cache.clear()
    cache[cache_key] = {"stamp": cache_stamp, "lif": lif, "records": clone_records(records)}
    return lif, records


def get_plane_by_dimensions(image, c: int, dimension_values: dict[int, int]):
    c = max(0, min(int(c), max(1, int(getattr(image, "channels", 1) or 1)) - 1))
    dims_n = lif_dimensions.int_dict(getattr(image, "dims_n", {}) or {})
    display_dims = lif_dimensions.display_dims(getattr(image, "display_dims", []) or [])
    display = set(display_dims)
    non_display_dim_ids = [dim_id for dim_id in dims_n if dim_id not in display]
    has_extra_dim = any(dim_id not in {3, 4, 10} for dim_id in non_display_dim_ids)

    if not has_extra_dim and tuple(display_dims) == (1, 2):
        try:
            return image.get_frame(
                z=max(
                    0,
                    min(
                        int(dimension_values.get(3, 0) or 0), lif_dimensions.dim_int(image, "z") - 1
                    ),
                ),
                t=max(
                    0,
                    min(
                        int(dimension_values.get(4, 0) or 0), lif_dimensions.dim_int(image, "t") - 1
                    ),
                ),
                c=c,
                m=max(
                    0,
                    min(
                        int(dimension_values.get(10, 0) or 0),
                        lif_dimensions.dim_int(image, "m") - 1,
                    ),
                ),
            )
        except Exception:
            if not dims_n:
                raise

    if not dims_n:
        return image.get_frame(
            z=int(dimension_values.get(3, 0) or 0),
            t=int(dimension_values.get(4, 0) or 0),
            c=c,
            m=int(dimension_values.get(10, 0) or 0),
        )

    requested = {}
    for dim_id, count in dims_n.items():
        if dim_id in display:
            continue
        try:
            raw = int(dimension_values.get(dim_id, 0) or 0)
        except Exception:
            raw = 0
        requested[dim_id] = max(0, min(raw, count - 1))
    return image.get_plane(c=c, requested_dims=requested)


def get_plane(image, z: int, t: int, c: int, m: int, requested_dims: dict | None = None):
    dims = {3: z, 4: t, 10: m}
    for key, value in (requested_dims or {}).items():
        try:
            dims[int(key)] = int(value)
        except Exception:
            continue
    return get_plane_by_dimensions(image, c=c, dimension_values=dims)


def plane_count(record: dict) -> int:
    total = max(1, int(record.get("channels", 1) or 1))
    plane_dims = lif_dimensions.plane_dimensions_from_record(record, include_single=False)
    for dim in plane_dims:
        total *= max(1, int(dim.get("count", 1) or 1))
    return total
