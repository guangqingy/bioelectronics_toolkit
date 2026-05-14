from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np


def dim_int(image, attr: str, default: int = 1) -> int:
    try:
        return max(1, int(getattr(image.dims, attr)))
    except Exception:
        return default


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "_asdict"):
        return json_safe(value._asdict())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def positive_float(value) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not np.isfinite(out) or out <= 0:
        return None
    return out


def nonzero_float(value) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not np.isfinite(out) or abs(out) <= 1e-15:
        return None
    return out


def unit_to_um_factor(unit: str | None) -> float | None:
    u = str(unit or "").strip().lower().replace("µ", "u")
    if u in {"m", "meter", "meters"}:
        return 1e6
    if u in {"um", "micron", "microns", "micrometer", "micrometers"}:
        return 1.0
    if u in {"nm", "nanometer", "nanometers"}:
        return 1e-3
    if u in {"mm", "millimeter", "millimeters"}:
        return 1e3
    if u in {"cm", "centimeter", "centimeters"}:
        return 1e4
    return None


def calibration_from_scale(scale: list | tuple | None) -> dict:
    vals = list(scale or [])
    sx = nonzero_float(vals[0]) if len(vals) > 0 else None
    sy = nonzero_float(vals[1]) if len(vals) > 1 else None
    sz = nonzero_float(vals[2]) if len(vals) > 2 else None
    st = nonzero_float(vals[3]) if len(vals) > 3 else None
    return {
        "unit": "um",
        "pixel_width_um": abs(1.0 / sx) if sx else None,
        "pixel_height_um": abs(1.0 / sy) if sy else None,
        "z_spacing_um": abs(1.0 / sz) if sz else None,
        "z_step_signed_um": (1.0 / sz) if sz else None,
        "frame_interval_s": abs(1.0 / st) if st else None,
        "frame_interval_signed_s": (1.0 / st) if st else None,
        "frame_rate_hz": st,
        "x_pixels_per_um": abs(sx) if sx else None,
        "y_pixels_per_um": abs(sy) if sy else None,
        "z_pixels_per_um": abs(sz) if sz else None,
        "x_pixels_per_um_signed": sx,
        "y_pixels_per_um_signed": sy,
        "z_pixels_per_um_signed": sz,
    }


def int_dict(value) -> dict[int, int]:
    out = {}
    for key, count in (value or {}).items():
        try:
            dim_id = int(key)
            n = max(1, int(count or 1))
        except Exception:
            continue
        out[dim_id] = n
    return out


def display_dims(value) -> list[int]:
    out = []
    for raw in list(value or []):
        try:
            out.append(int(raw))
        except Exception:
            pass
    return out[:2] if len(out) >= 2 else [1, 2]


def dimension_label(dim_id: int) -> str:
    labels = {
        1: "X",
        2: "Y",
        3: "Z",
        4: "T",
        10: "M",
        11: "D11",
    }
    return labels.get(int(dim_id), f"D{int(dim_id)}")


def plane_dimensions_from_record(record: dict, include_single: bool = True) -> list[dict]:
    dims_n = int_dict(record.get("dims_n", {}) or {})
    dims = record.get("dimensions", {}) or {}
    for dim_id, key in ((3, "z"), (4, "t"), (10, "m")):
        try:
            n = max(1, int(dims.get(key, 1) or 1))
        except Exception:
            n = 1
        if n > 1 and dim_id not in dims_n:
            dims_n[dim_id] = n

    display = set(display_dims(record.get("display_dims", []) or []))
    plane_dims = []
    for dim_id, count in dims_n.items():
        if dim_id in display:
            continue
        if not include_single and count <= 1:
            continue
        plane_dims.append(
            {
                "id": int(dim_id),
                "label": dimension_label(int(dim_id)),
                "count": int(count),
            }
        )
    return plane_dims


def float_from_settings(settings: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = positive_float((settings or {}).get(key))
        if value is not None:
            return value
    return None


def apply_xml_dimension_calibration(calibration: dict, xml_dims: list[dict], settings: dict, dims_n: dict) -> dict:
    out = dict(calibration or {})
    dim_counts = {}

    for dim in xml_dims or []:
        try:
            dim_id = int(dim.get("DimID"))
            n = int(dim.get("NumberOfElements", 1))
        except Exception:
            continue
        dim_counts[dim_id] = n
        length = nonzero_float(dim.get("Length"))
        unit = str(dim.get("Unit", "") or "")
        if n <= 1 or length is None:
            continue

        if dim_id in {1, 2, 3}:
            factor = unit_to_um_factor(unit)
            if factor is None:
                continue
            signed_step_um = length * factor / float(n - 1)
            if dim_id == 1:
                out["pixel_width_um"] = abs(signed_step_um)
                out["x_step_signed_um"] = signed_step_um
                out["x_pixels_per_um"] = 1.0 / abs(signed_step_um)
                out["x_pixels_per_um_signed"] = 1.0 / signed_step_um
            elif dim_id == 2:
                out["pixel_height_um"] = abs(signed_step_um)
                out["y_step_signed_um"] = signed_step_um
                out["y_pixels_per_um"] = 1.0 / abs(signed_step_um)
                out["y_pixels_per_um_signed"] = 1.0 / signed_step_um
            else:
                out["z_spacing_um"] = abs(signed_step_um)
                out["z_step_signed_um"] = signed_step_um
                out["z_pixels_per_um"] = 1.0 / abs(signed_step_um)
                out["z_pixels_per_um_signed"] = 1.0 / signed_step_um

        elif dim_id == 4 and unit.lower() in {"s", "sec", "second", "seconds"}:
            signed_step_s = length / float(n - 1)
            out["frame_interval_s"] = abs(signed_step_s)
            out["frame_interval_signed_s"] = signed_step_s
            out["frame_rate_hz"] = 1.0 / signed_step_s if signed_step_s else None

    has_time_like_extra = False
    for key, value in (dims_n or {}).items():
        try:
            dim_id = int(key)
            n = int(value)
        except Exception:
            continue
        if dim_id not in {1, 2, 3, 10} and n > 1:
            has_time_like_extra = True
            break

    if out.get("frame_interval_s") is None and has_time_like_extra:
        frame_time = float_from_settings(settings, ("FrameTime", "CycleTime", "CompleteTime"))
        if frame_time is not None:
            out["frame_interval_s"] = frame_time
            out["frame_interval_from_settings_s"] = frame_time
            out["frame_interval_source"] = "ATLConfocalSettingDefinition.FrameTime"

    return out


def xml_metadata_summary(element: ET.Element | None) -> dict:
    if element is None:
        return {}

    dims = []
    channels = []
    settings = []
    for node in element.iter():
        tag = node.tag.split("}")[-1]
        attrs = json_safe(node.attrib)
        if tag == "DimensionDescription":
            dims.append(attrs)
        elif tag == "ChannelDescription":
            channels.append(attrs)
        elif "setting" in tag.lower() or any(
            "setting" in str(v).lower() for k, v in node.attrib.items() if k.lower() in {"identifier", "name", "key"}
        ):
            if len(settings) < 300:
                settings.append({"tag": tag, "attributes": attrs})

    return {
        "dimensions": dims,
        "channels": channels,
        "settings": settings,
    }


def channel_lut_names(xml_metadata: dict, channel_count: int) -> list[str]:
    names = []
    for ch in (xml_metadata or {}).get("channels", []) or []:
        name = str(ch.get("LUTName") or ch.get("Name") or ch.get("ChannelName") or "").strip()
        names.append(name)
    while len(names) < max(1, int(channel_count or 1)):
        names.append("")
    return names[: max(1, int(channel_count or 1))]


def bool_setting(settings: dict, key: str) -> bool:
    value = (settings or {}).get(key)
    if isinstance(value, bool):
        return value
    try:
        return bool(int(float(value)))
    except Exception:
        return str(value or "").strip().lower() in {"true", "yes", "on"}


def orientation_from_settings(settings: dict) -> dict:
    return {
        "swap_xy": bool_setting(settings, "SwapXY"),
        "flip_x": bool_setting(settings, "FlipX"),
        "flip_y": bool_setting(settings, "FlipY"),
        "source": "ATLConfocalSettingDefinition",
    }


def apply_orientation(arr: np.ndarray, orientation: dict | None) -> np.ndarray:
    out = np.asarray(arr)
    orient = orientation or {}
    if orient.get("swap_xy"):
        out = out.T
    if orient.get("flip_x"):
        out = np.fliplr(out)
    if orient.get("flip_y"):
        out = np.flipud(out)
    return np.ascontiguousarray(out)


def oriented_counts(counts: dict, orientation: dict | None) -> dict:
    out = dict(counts or {})
    if (orientation or {}).get("swap_xy"):
        out["x"], out["y"] = out.get("y", 1), out.get("x", 1)
    return out


def oriented_calibration(calibration: dict, orientation: dict | None) -> dict:
    out = dict(calibration or {})
    orient = orientation or {}
    if orient.get("swap_xy"):
        for a, b in (
            ("pixel_width_um", "pixel_height_um"),
            ("x_step_signed_um", "y_step_signed_um"),
            ("x_pixels_per_um", "y_pixels_per_um"),
            ("x_pixels_per_um_signed", "y_pixels_per_um_signed"),
        ):
            out[a], out[b] = out.get(b), out.get(a)
    if orient.get("flip_x") and out.get("x_step_signed_um") is not None:
        out["x_step_signed_um"] = -float(out["x_step_signed_um"])
        if out.get("x_pixels_per_um_signed") is not None:
            out["x_pixels_per_um_signed"] = -float(out["x_pixels_per_um_signed"])
    if orient.get("flip_y") and out.get("y_step_signed_um") is not None:
        out["y_step_signed_um"] = -float(out["y_step_signed_um"])
        if out.get("y_pixels_per_um_signed") is not None:
            out["y_pixels_per_um_signed"] = -float(out["y_pixels_per_um_signed"])
    return out
