import base64
import csv
import html
import io
import json
import math
import re
import traceback
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from flask import jsonify, request

from .jobs import submit_flask_route_job


def register_lif_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    has_tiff = ctx["HAS_TIFF"]
    has_pil = ctx["HAS_PIL"]
    tifflib = ctx.get("tifflib")
    image_mod = ctx.get("Image")
    jobs = ctx.get("jobs")

    has_readlif = ctx.get("HAS_READLIF", False)
    LifFile = ctx.get("LifFile")
    _lif_cache = {}

    time_key_re = re.compile(r"(acq|acquis|date|time|stamp|creat|start|end|modif)", re.IGNORECASE)
    skip_key_re = re.compile(
        r"(bytes|inc|dim|length|resolution|wavelength|laser|pinhole|zoom|objective|"
        r"exposure|dwell|cycle|interval|delay|duration|voxel|pixel|physical|numberofelements|bit)",
        re.IGNORECASE,
    )
    iso_re = re.compile(
        r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"
        r"(?:[T\s_]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?"
        r"(?:\s*(?P<ampm>AM|PM|am|pm))?)?"
    )
    us_eu_re = re.compile(
        r"(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>\d{2,4})"
        r"(?:[T\s_]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?"
        r"(?:\s*(?P<ampm>AM|PM|am|pm))?)?"
    )
    time_only_re = re.compile(r"\b(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?\s*(?P<ampm>AM|PM|am|pm)?\b")

    def _lif_require_reader():
        if not has_readlif or LifFile is None:
            return "readlif is not installed. Run: python -m pip install readlif"
        return ""

    def _lif_apply_lut(gray8: np.ndarray, lut: str) -> np.ndarray:
        lut_name = (lut or "Gray").strip().lower()
        z = np.zeros_like(gray8)
        if lut_name == "red":
            return np.stack([gray8, z, z], axis=-1)
        if lut_name == "green":
            return np.stack([z, gray8, z], axis=-1)
        if lut_name == "blue":
            return np.stack([z, z, gray8], axis=-1)
        if lut_name == "magenta":
            return np.stack([gray8, z, gray8], axis=-1)
        if lut_name == "cyan":
            return np.stack([z, gray8, gray8], axis=-1)
        if lut_name == "yellow":
            return np.stack([gray8, gray8, z], axis=-1)
        return np.stack([gray8, gray8, gray8], axis=-1)

    def _lif_plane_to_b64(frame, lut: str, p_low: float, p_high: float) -> str:
        arr = np.asarray(frame)
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            arr = arr.reshape(arr.shape[-2], arr.shape[-1])

        arr = arr.astype(np.float32)
        lo, hi = np.percentile(arr, [p_low, p_high])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.min(arr)), float(np.max(arr))
        if hi <= lo:
            hi = lo + 1.0

        gray8 = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
        rgb = _lif_apply_lut(gray8, lut)
        img = image_mod.fromarray(rgb, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    def _lif_clean_str(value) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _lif_parse_second(sec_raw: str | None) -> tuple[int, int]:
        if not sec_raw:
            return 0, 0
        sec_float = float(sec_raw)
        sec = int(sec_float)
        micros = int(round((sec_float - sec) * 1_000_000))
        return sec, micros

    def _lif_apply_ampm(hour: int, ampm: str | None) -> int:
        if not ampm:
            return hour
        ap = ampm.lower()
        if ap == "pm" and hour < 12:
            return hour + 12
        if ap == "am" and hour == 12:
            return 0
        return hour

    def _lif_candidate_score(key: str, value: str, parsed_kind: str) -> int:
        k = key.lower()
        score = 0
        if "acquis" in k or "acq" in k:
            score += 120
        if "creation" in k or "created" in k or "create" in k:
            score += 110
        if "date" in k:
            score += 90
        if "timestamp" in k or "stamp" in k:
            score += 80
        if "start" in k:
            score += 45
        if "time" in k:
            score += 35
        if "end" in k or "modified" in k or "modif" in k:
            score -= 25
        if "list" in k:
            score -= 35
        if parsed_kind == "datetime":
            score += 140
        elif parsed_kind == "date":
            score += 80
        elif parsed_kind == "time":
            score += 30
        if len(value) > 180:
            score -= 40
        return score

    def _lif_parse_datetime_text(value: str):
        raw = _lif_clean_str(value)
        if not raw or len(raw) > 300:
            return None

        compact = raw.replace("Z", "+00:00")
        for candidate in (compact, compact.replace("/", "-")):
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return {
                    "sort_value": dt.timestamp(),
                    "iso": dt.isoformat(sep=" ", timespec="seconds"),
                    "display": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": "datetime" if (dt.hour or dt.minute or dt.second or dt.microsecond) else "date",
                }
            except Exception:
                pass

        m = iso_re.search(raw)
        if m:
            try:
                sec, micros = _lif_parse_second(m.group("s"))
                h = int(m.group("h") or 0)
                h = _lif_apply_ampm(h, m.group("ampm"))
                dt = datetime(
                    int(m.group("y")),
                    int(m.group("m")),
                    int(m.group("d")),
                    h,
                    int(m.group("mi") or 0),
                    sec,
                    micros,
                )
                has_time = m.group("h") is not None
                return {
                    "sort_value": dt.timestamp(),
                    "iso": dt.isoformat(sep=" ", timespec="seconds"),
                    "display": dt.strftime("%Y-%m-%d %H:%M:%S") if has_time else dt.strftime("%Y-%m-%d"),
                    "kind": "datetime" if has_time else "date",
                }
            except Exception:
                pass

        m = us_eu_re.search(raw)
        if m:
            try:
                a = int(m.group("a"))
                b = int(m.group("b"))
                y = int(m.group("y"))
                if y < 100:
                    y += 2000 if y < 70 else 1900
                # Leica exports seen in labs are often month/day/year, but if the
                # first field is impossible as a month, treat it as day/month/year.
                month, day = (b, a) if a > 12 else (a, b)
                sec, micros = _lif_parse_second(m.group("s"))
                h = int(m.group("h") or 0)
                h = _lif_apply_ampm(h, m.group("ampm"))
                dt = datetime(y, month, day, h, int(m.group("mi") or 0), sec, micros)
                has_time = m.group("h") is not None
                return {
                    "sort_value": dt.timestamp(),
                    "iso": dt.isoformat(sep=" ", timespec="seconds"),
                    "display": dt.strftime("%Y-%m-%d %H:%M:%S") if has_time else dt.strftime("%Y-%m-%d"),
                    "kind": "datetime" if has_time else "date",
                }
            except Exception:
                pass

        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", raw)
        if len(nums) == 1:
            try:
                n = float(nums[0])
                if n > 1e12:
                    dt = datetime.fromtimestamp(n / 1000.0, tz=timezone.utc).replace(tzinfo=None)
                    return {
                        "sort_value": dt.timestamp(),
                        "iso": dt.isoformat(sep=" ", timespec="seconds"),
                        "display": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "kind": "datetime",
                    }
                if n > 1e9:
                    dt = datetime.fromtimestamp(n, tz=timezone.utc).replace(tzinfo=None)
                    return {
                        "sort_value": dt.timestamp(),
                        "iso": dt.isoformat(sep=" ", timespec="seconds"),
                        "display": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "kind": "datetime",
                    }
                if 20000 < n < 80000:
                    dt = datetime(1899, 12, 30) + timedelta(days=n)
                    return {
                        "sort_value": dt.timestamp(),
                        "iso": dt.isoformat(sep=" ", timespec="seconds"),
                        "display": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "kind": "datetime",
                    }
            except Exception:
                pass

        m = time_only_re.search(raw)
        if m:
            try:
                sec, micros = _lif_parse_second(m.group("s"))
                hour = _lif_apply_ampm(int(m.group("h")), m.group("ampm"))
                minute = int(m.group("mi"))
                sort_value = hour * 3600 + minute * 60 + sec + micros / 1_000_000.0
                return {
                    "sort_value": sort_value,
                    "iso": "",
                    "display": f"{hour:02d}:{minute:02d}:{sec:02d}",
                    "kind": "time",
                }
            except Exception:
                pass

        return None

    def _lif_timestamp_from_element(element: ET.Element | None):
        if element is None:
            return None

        candidates = []
        for node in element.iter():
            tag = node.tag.split("}")[-1]
            for logical_key in ("Identifier", "Name", "Key", "Description"):
                logical_name = _lif_clean_str(node.attrib.get(logical_key, ""))
                if not logical_name:
                    continue
                if not time_key_re.search(logical_name) or skip_key_re.search(logical_name):
                    continue
                for value_key in ("Variant", "Value", "Text", "Data"):
                    if value_key not in node.attrib:
                        continue
                    text = _lif_clean_str(node.attrib.get(value_key, ""))
                    parsed = _lif_parse_datetime_text(text)
                    if parsed is None:
                        continue
                    key_path = f"{tag}.{logical_name}"
                    score = _lif_candidate_score(key_path, text, parsed["kind"])
                    candidates.append((score, key_path, text, parsed))

            for key, value in node.attrib.items():
                key_path = f"{tag}.{key}"
                if not time_key_re.search(key_path) or skip_key_re.search(key_path):
                    continue
                text = _lif_clean_str(value)
                parsed = _lif_parse_datetime_text(text)
                if parsed is None:
                    continue
                score = _lif_candidate_score(key_path, text, parsed["kind"])
                candidates.append((score, key_path, text, parsed))

            text = _lif_clean_str(node.text)
            if text and time_key_re.search(tag) and not skip_key_re.search(tag):
                parsed = _lif_parse_datetime_text(text)
                if parsed is not None:
                    score = _lif_candidate_score(tag, text, parsed["kind"])
                    candidates.append((score, tag, text, parsed))

        if not candidates:
            return None

        score, key_path, raw_text, parsed = max(candidates, key=lambda item: item[0])
        if score < 70:
            return None
        return {
            "sort_value": float(parsed["sort_value"]),
            "display": parsed["display"],
            "iso": parsed["iso"],
            "source": key_path,
            "raw": raw_text,
            "kind": parsed["kind"],
            "confidence": score,
        }

    def _lif_collect_image_elements(root: ET.Element):
        records = []

        def walk(tree: ET.Element, path: str = ""):
            children = tree.findall("./Children/Element")
            if len(children) < 1:
                children = tree.findall("./Element")

            for item in children:
                name = str(item.attrib.get("Name", ""))
                appended = name if not path else f"{path}/{name}"
                is_image = len(item.findall("./Data/Image")) > 0
                if is_image:
                    records.append({"element": item, "xml_path": appended})
                if len(item.findall("./Children/Element")) > 0:
                    walk(item, appended)

        walk(root)
        return records

    def _lif_dim_int(image, attr: str, default: int = 1) -> int:
        try:
            return max(1, int(getattr(image.dims, attr)))
        except Exception:
            return default

    def _lif_json_safe(value):
        if isinstance(value, dict):
            return {str(k): _lif_json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_lif_json_safe(v) for v in value]
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, Path):
            return str(value)
        if hasattr(value, "_asdict"):
            return _lif_json_safe(value._asdict())
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _lif_positive_float(value) -> float | None:
        try:
            out = float(value)
        except Exception:
            return None
        if not np.isfinite(out) or out <= 0:
            return None
        return out

    def _lif_nonzero_float(value) -> float | None:
        try:
            out = float(value)
        except Exception:
            return None
        if not np.isfinite(out) or abs(out) <= 1e-15:
            return None
        return out

    def _lif_unit_to_um_factor(unit: str | None) -> float | None:
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

    def _lif_calibration_from_scale(scale: list | tuple | None) -> dict:
        vals = list(scale or [])
        sx = _lif_nonzero_float(vals[0]) if len(vals) > 0 else None
        sy = _lif_nonzero_float(vals[1]) if len(vals) > 1 else None
        sz = _lif_nonzero_float(vals[2]) if len(vals) > 2 else None
        st = _lif_nonzero_float(vals[3]) if len(vals) > 3 else None
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

    def _lif_int_dict(value) -> dict[int, int]:
        out = {}
        for key, count in (value or {}).items():
            try:
                dim_id = int(key)
                n = max(1, int(count or 1))
            except Exception:
                continue
            out[dim_id] = n
        return out

    def _lif_display_dims(value) -> list[int]:
        out = []
        for raw in list(value or []):
            try:
                out.append(int(raw))
            except Exception:
                pass
        return out[:2] if len(out) >= 2 else [1, 2]

    def _lif_dimension_label(dim_id: int) -> str:
        labels = {
            1: "X",
            2: "Y",
            3: "Z",
            4: "T",
            10: "M",
            11: "D11",
        }
        return labels.get(int(dim_id), f"D{int(dim_id)}")

    def _lif_plane_dimensions_from_record(record: dict, include_single: bool = True) -> list[dict]:
        dims_n = _lif_int_dict(record.get("dims_n", {}) or {})
        dims = record.get("dimensions", {}) or {}
        for dim_id, key in ((3, "z"), (4, "t"), (10, "m")):
            try:
                n = max(1, int(dims.get(key, 1) or 1))
            except Exception:
                n = 1
            if n > 1 and dim_id not in dims_n:
                dims_n[dim_id] = n

        display = set(_lif_display_dims(record.get("display_dims", []) or []))
        plane_dims = []
        for dim_id, count in dims_n.items():
            if dim_id in display:
                continue
            if not include_single and count <= 1:
                continue
            plane_dims.append(
                {
                    "id": int(dim_id),
                    "label": _lif_dimension_label(int(dim_id)),
                    "count": int(count),
                }
            )
        return plane_dims

    def _lif_float_from_settings(settings: dict, keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = _lif_positive_float((settings or {}).get(key))
            if value is not None:
                return value
        return None

    def _lif_apply_xml_dimension_calibration(calibration: dict, xml_dims: list[dict], settings: dict, dims_n: dict) -> dict:
        out = dict(calibration or {})
        dim_counts = {}

        for dim in xml_dims or []:
            try:
                dim_id = int(dim.get("DimID"))
                n = int(dim.get("NumberOfElements", 1))
            except Exception:
                continue
            dim_counts[dim_id] = n
            length = _lif_nonzero_float(dim.get("Length"))
            unit = str(dim.get("Unit", "") or "")
            if n <= 1 or length is None:
                continue

            if dim_id in {1, 2, 3}:
                factor = _lif_unit_to_um_factor(unit)
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
            frame_time = _lif_float_from_settings(settings, ("FrameTime", "CycleTime", "CompleteTime"))
            if frame_time is not None:
                out["frame_interval_s"] = frame_time
                out["frame_interval_from_settings_s"] = frame_time
                out["frame_interval_source"] = "ATLConfocalSettingDefinition.FrameTime"

        return out

    def _lif_xml_metadata_summary(element: ET.Element | None) -> dict:
        if element is None:
            return {}

        dims = []
        channels = []
        settings = []
        for node in element.iter():
            tag = node.tag.split("}")[-1]
            attrs = _lif_json_safe(node.attrib)
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

    def _lif_channel_lut_names(xml_metadata: dict, channel_count: int) -> list[str]:
        names = []
        for ch in (xml_metadata or {}).get("channels", []) or []:
            name = str(ch.get("LUTName") or ch.get("Name") or ch.get("ChannelName") or "").strip()
            names.append(name)
        while len(names) < max(1, int(channel_count or 1)):
            names.append("")
        return names[: max(1, int(channel_count or 1))]

    def _lif_imagej_lut(lut_name: str) -> np.ndarray:
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

    def _lif_imagej_luts(record: dict, c_count: int) -> list[np.ndarray]:
        names = list(record.get("channel_lut_names", []) or [])
        if not names:
            names = _lif_channel_lut_names(record.get("xml_metadata", {}) or {}, c_count)
        if not any(str(name or "").strip() for name in names):
            return []
        return [_lif_imagej_lut(name) for name in names[: max(1, int(c_count or 1))]]

    def _lif_bool_setting(settings: dict, key: str) -> bool:
        value = (settings or {}).get(key)
        if isinstance(value, bool):
            return value
        try:
            return bool(int(float(value)))
        except Exception:
            return str(value or "").strip().lower() in {"true", "yes", "on"}

    def _lif_orientation_from_settings(settings: dict) -> dict:
        return {
            "swap_xy": _lif_bool_setting(settings, "SwapXY"),
            "flip_x": _lif_bool_setting(settings, "FlipX"),
            "flip_y": _lif_bool_setting(settings, "FlipY"),
            "source": "ATLConfocalSettingDefinition",
        }

    def _lif_apply_orientation(arr: np.ndarray, orientation: dict | None) -> np.ndarray:
        out = np.asarray(arr)
        orient = orientation or {}
        if orient.get("swap_xy"):
            out = out.T
        if orient.get("flip_x"):
            out = np.fliplr(out)
        if orient.get("flip_y"):
            out = np.flipud(out)
        return np.ascontiguousarray(out)

    def _lif_oriented_counts(counts: dict, orientation: dict | None) -> dict:
        out = dict(counts or {})
        if (orientation or {}).get("swap_xy"):
            out["x"], out["y"] = out.get("y", 1), out.get("x", 1)
        return out

    def _lif_oriented_calibration(calibration: dict, orientation: dict | None) -> dict:
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

    def _lif_record_from_image(lif, image_index: int, image_elements: list[dict]):
        image = lif.get_image(image_index)
        info = getattr(image, "info", {}) or {}
        full_name = str(getattr(image, "name", "") or info.get("name") or f"Image {image_index + 1}")
        folder = str(info.get("path", "") or "").strip("/")
        simple_name = full_name.split("/")[-1] if full_name else f"Image {image_index + 1}"

        element_rec = image_elements[image_index] if image_index < len(image_elements) else {}
        element = element_rec.get("element")
        ts = _lif_timestamp_from_element(element)
        acquired = ts["display"] if ts else ""
        sort_value = ts["sort_value"] if ts else None
        sort_source = ts["source"] if ts else "Leica project order"

        dims = {
            "x": _lif_dim_int(image, "x"),
            "y": _lif_dim_int(image, "y"),
            "z": _lif_dim_int(image, "z"),
            "t": _lif_dim_int(image, "t"),
            "m": _lif_dim_int(image, "m"),
        }
        channels = max(1, int(getattr(image, "channels", 1) or 1))
        bit_depth = list(getattr(image, "bit_depth", []) or [])
        scale = list(getattr(image, "scale", []) or [])
        scale_n = _lif_json_safe(getattr(image, "scale_n", {}) or {})
        dims_n = _lif_json_safe(getattr(image, "dims_n", {}) or {})
        settings = _lif_json_safe(getattr(image, "settings", {}) or {})
        xml_metadata = _lif_xml_metadata_summary(element)
        display_dims = list(getattr(image, "display_dims", []) or [])
        calibration = _lif_calibration_from_scale(scale)
        calibration = _lif_apply_xml_dimension_calibration(
            calibration,
            xml_metadata.get("dimensions", []),
            settings,
            dims_n,
        )
        record_shell = {"dimensions": dims, "dims_n": dims_n, "display_dims": display_dims}
        plane_dimensions = _lif_plane_dimensions_from_record(record_shell, include_single=True)
        orientation = _lif_orientation_from_settings(settings)

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
            "channel_lut_names": _lif_channel_lut_names(xml_metadata, channels),
            "scan_orientation": orientation,
            "calibration": calibration,
            "display_dims": display_dims,
            "plane_dimensions": plane_dimensions,
            "extra_dimensions": [d for d in plane_dimensions if int(d.get("id", 0)) not in {3, 4, 10}],
            "mosaic_position": _lif_json_safe(getattr(image, "mosaic_position", []) or []),
            "mosaic_tiles": len(getattr(image, "mosaic_position", []) or []),
        }

    def _lif_clone_records(records: list[dict]) -> list[dict]:
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

    def _lif_load_records(path: str):
        reader_error = _lif_require_reader()
        if reader_error:
            raise RuntimeError(reader_error)
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"LIF file not found: {path}")

        cache_key = str(p.resolve())
        stat = p.stat()
        cache_stamp = (stat.st_mtime_ns, stat.st_size)
        cached = _lif_cache.get(cache_key)
        if cached and cached.get("stamp") == cache_stamp:
            return cached["lif"], _lif_clone_records(cached["records"])

        lif = LifFile(str(p))
        image_elements = _lif_collect_image_elements(lif.xml_root)
        records = [_lif_record_from_image(lif, i, image_elements) for i in range(int(lif.num_images))]
        _lif_cache.clear()
        _lif_cache[cache_key] = {"stamp": cache_stamp, "lif": lif, "records": _lif_clone_records(records)}
        return lif, records

    def _lif_record_sort_tuple(record: dict, mode: str):
        if mode == "name":
            return (str(record.get("full_name", "")).lower(), int(record.get("original_order", 0)))
        if mode == "original":
            return (int(record.get("original_order", 0)),)
        val = record.get("sort_value")
        if val is None or (isinstance(val, float) and not math.isfinite(val)):
            return (1, int(record.get("original_order", 0)))
        return (0, float(val), int(record.get("original_order", 0)))

    def _lif_get_plane_by_dimensions(image, c: int, dimension_values: dict[int, int]):
        c = max(0, min(int(c), max(1, int(getattr(image, "channels", 1) or 1)) - 1))
        dims_n = _lif_int_dict(getattr(image, "dims_n", {}) or {})
        display_dims = _lif_display_dims(getattr(image, "display_dims", []) or [])
        display = set(display_dims)
        non_display_dim_ids = [dim_id for dim_id in dims_n if dim_id not in display]
        has_extra_dim = any(dim_id not in {3, 4, 10} for dim_id in non_display_dim_ids)

        if not has_extra_dim and tuple(display_dims) == (1, 2):
            try:
                return image.get_frame(
                    z=max(0, min(int(dimension_values.get(3, 0) or 0), _lif_dim_int(image, "z") - 1)),
                    t=max(0, min(int(dimension_values.get(4, 0) or 0), _lif_dim_int(image, "t") - 1)),
                    c=c,
                    m=max(0, min(int(dimension_values.get(10, 0) or 0), _lif_dim_int(image, "m") - 1)),
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

    def _lif_get_plane(image, z: int, t: int, c: int, m: int, requested_dims: dict | None = None):
        dims = {3: z, 4: t, 10: m}
        for key, value in (requested_dims or {}).items():
            try:
                dims[int(key)] = int(value)
            except Exception:
                continue
        return _lif_get_plane_by_dimensions(image, c=c, dimension_values=dims)

    def _lif_sanitize_filename(name: str, fallback: str) -> str:
        raw = str(name or "").strip()
        if raw.lower().endswith((".tif", ".tiff", ".html", ".htm")):
            raw = Path(raw).stem
        if not raw:
            raw = fallback
        out = re.sub(r"[^\w.\- ]+", "_", raw, flags=re.UNICODE).strip(" ._")
        out = re.sub(r"\s+", "_", out)
        return out or fallback

    def _lif_output_name_for_record(record: dict, rename_map: dict | None = None) -> str:
        rename_map = rename_map if isinstance(rename_map, dict) else {}
        idx = str(record.get("index", ""))
        custom = str(rename_map.get(idx, "") or rename_map.get(int(record.get("index", -1)), "") or "").strip()
        return custom or str(record.get("name", "") or f"image_{int(record.get('index', 0)) + 1}")

    def _lif_normalize_2d_array(frame) -> np.ndarray:
        arr = np.asarray(frame)
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            arr = arr.reshape(arr.shape[-2], arr.shape[-1])
        return arr

    def _lif_display_array(frame, record: dict) -> np.ndarray:
        return _lif_apply_orientation(_lif_normalize_2d_array(frame), record.get("scan_orientation", {}) or {})

    def _lif_plane_count(record: dict) -> int:
        total = max(1, int(record.get("channels", 1) or 1))
        plane_dims = _lif_plane_dimensions_from_record(record, include_single=False)
        for dim in plane_dims:
            total *= max(1, int(dim.get("count", 1) or 1))
        return total

    def _lif_unique_output_path(out_path: Path, overwrite: bool) -> Path:
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

    def _lif_lut_rgb(lut_name: str) -> tuple[float, float, float]:
        name = str(lut_name or "").strip().lower()
        if name == "red":
            return 1.0, 0.05, 0.05
        if name == "green":
            return 0.05, 1.0, 0.12
        if name == "blue":
            return 0.12, 0.25, 1.0
        if name == "cyan":
            return 0.05, 1.0, 1.0
        if name == "magenta":
            return 1.0, 0.08, 1.0
        if name == "yellow":
            return 1.0, 0.86, 0.05
        return 0.95, 0.95, 0.95

    def _lif_volume_indices(count: int, max_count: int) -> list[int]:
        count = max(1, int(count or 1))
        max_count = max(1, int(max_count or count))
        if count <= max_count:
            return list(range(count))
        return sorted({int(x) for x in np.linspace(0, count - 1, max_count)})

    def _lif_plane_points(
        arr: np.ndarray,
        z_index: int,
        c_index: int,
        z_count: int,
        c_count: int,
        xy_step: int,
        per_plane_quota: int,
        threshold_percentile: float,
        calibration: dict,
        lut_rgb: tuple[float, float, float],
    ) -> tuple[list[float], list[float]]:
        data = np.asarray(arr, dtype=np.float32)
        if data.size == 0:
            return [], []

        view = data[::xy_step, ::xy_step]
        if view.size == 0:
            return [], []

        finite = view[np.isfinite(view)]
        if finite.size == 0:
            return [], []

        lo = float(np.percentile(finite, 1.0))
        hi = float(np.percentile(finite, 99.7))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.min(finite)), float(np.max(finite))
        if hi <= lo:
            return [], []

        norm = np.clip((view - lo) / (hi - lo), 0.0, 1.0)
        threshold_percentile = max(0.0, min(99.95, float(threshold_percentile)))
        thr = float(np.percentile(finite, threshold_percentile))
        ys, xs = np.where(view >= thr)
        if ys.size == 0:
            flat = view.reshape(-1)
            k = min(max(1, per_plane_quota), flat.size)
            selected = np.argpartition(flat, -k)[-k:]
            ys, xs = np.unravel_index(selected, view.shape)

        if ys.size > per_plane_quota:
            intensities = norm[ys, xs]
            k = max(1, int(per_plane_quota))
            keep = np.argpartition(intensities, -k)[-k:]
            ys = ys[keep]
            xs = xs[keep]

        pixel_w = _lif_positive_float(calibration.get("pixel_width_um")) or 1.0
        pixel_h = _lif_positive_float(calibration.get("pixel_height_um")) or pixel_w
        z_spacing = _lif_positive_float(calibration.get("z_spacing_um")) or pixel_w
        h, w = data.shape
        cx = (w - 1) * pixel_w / 2.0
        cy = (h - 1) * pixel_h / 2.0
        cz = (max(1, z_count) - 1) * z_spacing / 2.0
        channel_offset = 0.0
        if c_count > 1:
            channel_offset = (c_index - (c_count - 1) / 2.0) * z_spacing * 0.08

        positions: list[float] = []
        colors: list[float] = []
        for y_s, x_s in zip(ys, xs):
            brightness = float(norm[y_s, x_s])
            if brightness <= 0:
                continue
            x_px = int(x_s) * xy_step
            y_px = int(y_s) * xy_step
            positions.extend(
                [
                    round(x_px * pixel_w - cx, 4),
                    round(cy - y_px * pixel_h, 4),
                    round(z_index * z_spacing - cz + channel_offset, 4),
                ]
            )
            colors.extend(
                [
                    round(min(1.0, max(0.0, lut_rgb[0] * brightness)), 4),
                    round(min(1.0, max(0.0, lut_rgb[1] * brightness)), 4),
                    round(min(1.0, max(0.0, lut_rgb[2] * brightness)), 4),
                ]
            )
        return positions, colors

    def _lif_build_volume3d_payload(
        lif,
        record: dict,
        requested_dims: dict | None = None,
        t: int = 0,
        m: int = 0,
        c: int = 0,
        channel_mode: str = "composite",
        max_points: int = 70000,
        max_xy: int = 180,
        max_z: int = 80,
        threshold_percentile: float = 98.8,
    ) -> dict:
        image_index = int(record.get("index", 0))
        image = lif.get_image(image_index)
        plan = _lif_export_plan(record)
        counts = plan.get("counts", {}) or {}
        z_count = max(1, int(counts.get("z", 1) or 1))
        c_count = max(1, int(counts.get("c", record.get("channels", 1)) or 1))
        dims = record.get("dimensions", {}) or {}
        x_count = max(1, int(dims.get("x", counts.get("x", 1)) or 1))
        y_count = max(1, int(dims.get("y", counts.get("y", 1)) or 1))

        if z_count < 2:
            raise ValueError("This subfile has only one Z slice; 3D z-stack viewing needs Z > 1.")

        max_points = max(1000, min(250000, int(max_points or 70000)))
        max_xy = max(48, min(512, int(max_xy or 180)))
        max_z = max(2, min(200, int(max_z or 80)))
        xy_step = max(1, int(math.ceil(max(x_count, y_count) / float(max_xy))))
        z_indices = _lif_volume_indices(z_count, max_z)

        if str(channel_mode or "composite").lower() == "current":
            channels = [max(0, min(int(c), c_count - 1))]
        else:
            channels = list(range(c_count))

        channel_luts = record.get("channel_lut_names", []) or []
        positions: list[float] = []
        colors: list[float] = []
        plane_quota = max(12, int(math.ceil(max_points / max(1, len(z_indices) * len(channels)))))
        z_dim = plan.get("z_dimension") or {}
        z_dim_id = int(z_dim.get("id", 3) or 3) if z_dim else 3
        base_dims = {3: 0, 4: int(t or 0), 10: int(m or 0)}
        for key, value in (requested_dims or {}).items():
            try:
                base_dims[int(key)] = int(value)
            except Exception:
                continue

        calibration = _lif_oriented_calibration(record.get("calibration", {}) or {}, record.get("scan_orientation", {}) or {})
        fallback_channel_luts = ["Red", "Green", "Blue", "Magenta", "Cyan", "Yellow"]
        for z in z_indices:
            dim_values = dict(base_dims)
            dim_values[z_dim_id] = int(z)
            for channel in channels:
                arr = _lif_display_array(_lif_get_plane_by_dimensions(image, c=channel, dimension_values=dim_values), record)
                lut_name = channel_luts[channel] if channel < len(channel_luts) else "Gray"
                if channel_mode != "current" and str(lut_name or "").strip().lower() in {"", "gray"} and c_count > 1:
                    lut_name = fallback_channel_luts[channel % len(fallback_channel_luts)]
                p, col = _lif_plane_points(
                    arr=arr,
                    z_index=int(z),
                    c_index=int(channel),
                    z_count=z_count,
                    c_count=c_count,
                    xy_step=xy_step,
                    per_plane_quota=plane_quota,
                    threshold_percentile=threshold_percentile,
                    calibration=calibration,
                    lut_rgb=_lif_lut_rgb(lut_name),
                )
                positions.extend(p)
                colors.extend(col)

        n_points = len(positions) // 3
        if n_points > max_points:
            idx = np.linspace(0, n_points - 1, max_points, dtype=np.int64)
            pos_arr = np.asarray(positions, dtype=np.float32).reshape(-1, 3)[idx]
            col_arr = np.asarray(colors, dtype=np.float32).reshape(-1, 3)[idx]
            positions = np.round(pos_arr.reshape(-1), 4).tolist()
            colors = np.round(col_arr.reshape(-1), 4).tolist()
            n_points = len(positions) // 3

        if n_points <= 0:
            raise ValueError("No bright voxels were found for 3D rendering. Try lowering the threshold.")

        pixel_w = _lif_positive_float(calibration.get("pixel_width_um")) or 1.0
        pixel_h = _lif_positive_float(calibration.get("pixel_height_um")) or pixel_w
        z_spacing = _lif_positive_float(calibration.get("z_spacing_um")) or pixel_w
        return {
            "title": record.get("full_name") or record.get("name") or f"Image {image_index + 1}",
            "image_index": image_index,
            "source_name": record.get("full_name", ""),
            "dimensions": {
                "x": x_count,
                "y": y_count,
                "z": z_count,
                "c": c_count,
                "z_sampled": len(z_indices),
                "channels_rendered": channels,
            },
            "calibration": {
                "pixel_width_um": pixel_w,
                "pixel_height_um": pixel_h,
                "z_spacing_um": z_spacing,
            },
            "render": {
                "type": "point_cloud",
                "positions": positions,
                "colors": colors,
                "n_points": n_points,
                "xy_step": xy_step,
                "z_indices": z_indices,
                "channel_mode": channel_mode,
                "threshold_percentile": threshold_percentile,
                "point_size": max(0.35, min(4.0, pixel_w * xy_step * 0.9)),
            },
        }

    def _lif_volume3d_html(volume_payload: dict) -> str:
        payload_json = json.dumps(volume_payload, ensure_ascii=False).replace("</", "<\\/")
        title = html.escape(str(volume_payload.get("title", "Leica LIF 3D Viewer") or "Leica LIF 3D Viewer"))
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title} - 3D Volume</title>
<script type="importmap">
{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}}}
</script>
<style>
html,body{{margin:0;height:100%;background:#08090c;color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;overflow:hidden}}
#viewer{{position:fixed;inset:0}}
#hud{{position:fixed;left:14px;top:14px;max-width:min(460px,calc(100vw - 28px));background:rgba(8,9,12,.72);border:1px solid rgba(255,255,255,.16);backdrop-filter:blur(12px);border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.5}}
#title{{font-weight:700;font-size:13px;margin-bottom:3px}}
#hint{{color:#b7beca}}
</style>
</head>
<body>
<div id="viewer"></div>
<div id="hud">
  <div id="title"></div>
  <div id="meta"></div>
  <div id="hint">Mouse drag: rotate · Wheel: zoom · Right drag: pan</div>
</div>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';
const data = {payload_json};
const el = document.getElementById('viewer');
document.getElementById('title').textContent = data.title || 'Leica LIF 3D Viewer';
document.getElementById('meta').textContent = `${{data.render.n_points}} points · Z ${{data.dimensions.z}} · C ${{data.dimensions.c}} · ${{data.calibration.pixel_width_um.toFixed(4)}} um/px · Z step ${{data.calibration.z_spacing_um.toFixed(4)}} um`;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x08090c);
const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 100000);
const renderer = new THREE.WebGLRenderer({{antialias:true}});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
el.appendChild(renderer.domElement);
const geom = new THREE.BufferGeometry();
geom.setAttribute('position', new THREE.Float32BufferAttribute(data.render.positions, 3));
geom.setAttribute('color', new THREE.Float32BufferAttribute(data.render.colors, 3));
geom.computeBoundingSphere();
const mat = new THREE.PointsMaterial({{size:data.render.point_size || 1, vertexColors:true, transparent:true, opacity:0.92, sizeAttenuation:true}});
const points = new THREE.Points(geom, mat);
scene.add(points);
const sphere = geom.boundingSphere || new THREE.Sphere(new THREE.Vector3(), 100);
const axes = new THREE.AxesHelper(Math.max(20, sphere.radius * 0.65));
scene.add(axes);
const grid = new THREE.GridHelper(Math.max(20, sphere.radius * 2.2), 10, 0x2c3445, 0x151923);
grid.rotation.x = Math.PI / 2;
scene.add(grid);
camera.position.set(sphere.center.x + sphere.radius * 1.6, sphere.center.y + sphere.radius * 1.25, sphere.center.z + sphere.radius * 1.8);
camera.near = Math.max(0.01, sphere.radius / 1000);
camera.far = Math.max(1000, sphere.radius * 12);
camera.updateProjectionMatrix();
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(sphere.center);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.update();
scene.add(new THREE.AmbientLight(0xffffff, 1.0));
function resize(){{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}}
window.addEventListener('resize', resize);
function animate(){{
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}}
animate();
</script>
</body>
</html>"""

    def _lif_tiff_datetime(record: dict) -> str | None:
        iso = str(record.get("acquired_iso", "") or "").strip()
        if not iso or len(iso) < 10:
            return None
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%Y:%m:%d %H:%M:%S")
        except Exception:
            return None

    def _lif_resolution_kwargs(calibration: dict) -> dict:
        x_ppum = _lif_positive_float(calibration.get("x_pixels_per_um"))
        y_ppum = _lif_positive_float(calibration.get("y_pixels_per_um"))
        if not x_ppum or not y_ppum:
            return {}
        return {
            "resolution": (x_ppum * 10000.0, y_ppum * 10000.0),
            "resolutionunit": "CENTIMETER",
        }

    def _lif_export_plan(record: dict) -> dict:
        dims = record.get("dimensions", {}) or {}
        plane_dimensions = _lif_plane_dimensions_from_record(record, include_single=True)
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

        t_count = next((int(d.get("count", 1) or 1) for d in plane_dimensions if int(d.get("id", 0)) == 4), 1)
        m_count = next((int(d.get("count", 1) or 1) for d in plane_dimensions if int(d.get("id", 0)) == 10), 1)
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

    def _lif_frame_dimension_combinations(frame_dimensions: list[dict]):
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

    def _lif_plane_sequence(plan: dict, limit: int = 10000) -> list[dict]:
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
        for frame_index, frame_values in _lif_frame_dimension_combinations(plan.get("frame_dimensions", []) or []):
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

    def _lif_build_export_metadata(lif, image, record: dict, output_name: str, plan: dict) -> dict:
        orientation = dict(record.get("scan_orientation", {}) or {})
        calibration = _lif_oriented_calibration(record.get("calibration", {}) or {}, orientation)
        raw_counts = plan.get("counts", {}) or {}
        counts = _lif_oriented_counts(raw_counts, orientation)
        sequence = _lif_plane_sequence(plan)
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
            "plane_sequence_truncated": not bool(sequence) and int(counts.get("planes", 0) or 0) > 10000,
            "bit_depth": record.get("bit_depth", []),
            "channel_lut_names": record.get("channel_lut_names", []),
            "calibration": calibration,
            "readlif_scale": record.get("scale", []),
            "readlif_scale_n": record.get("scale_n", {}),
            "readlif_dims_n": record.get("dims_n", {}),
            "display_dims": record.get("display_dims", []),
            "mosaic_position": record.get("mosaic_position", []),
            "leica_settings": _lif_json_safe(getattr(image, "settings", {}) or record.get("settings", {}) or {}),
            "leica_xml_metadata": record.get("xml_metadata", {}),
        }

    def _lif_build_image_description(metadata_payload: dict) -> tuple[str, str]:
        dims = metadata_payload.get("dimensions", {}) or {}
        calibration = metadata_payload.get("calibration", {}) or {}
        desc_fn = getattr(tifflib, "imagej_description", None)
        if desc_fn is not None:
            imagej_meta = {"unit": "um", "hyperstack": True}
            z_spacing = calibration.get("z_spacing_um")
            frame_interval = calibration.get("frame_interval_s")
            active_frame_dims = [
                d for d in (metadata_payload.get("flattened_frame_dimensions", []) or [])
                if int(d.get("count", 1) or 1) > 1
            ]
            if z_spacing is not None:
                imagej_meta["spacing"] = z_spacing
            if frame_interval is not None and len(active_frame_dims) <= 1:
                imagej_meta["finterval"] = frame_interval
            # Open multi-channel stacks as independent channels in Fiji/ImageJ.
            # Composite mode overlays all channels even when the C slider is on
            # one channel, which makes the exported channels look inseparable.
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

    def _lif_imagej_extratags(record: dict, c_count: int):
        tag_fn = getattr(tifflib, "imagej_metadata_tag", None)
        if tag_fn is None or c_count <= 1:
            return None
        luts = _lif_imagej_luts(record, c_count)
        if not luts:
            return None
        try:
            return tuple(tag_fn({"LUTs": luts}, "<"))
        except Exception:
            return None

    def _lif_imagej_metadata(metadata_payload: dict, record: dict, c_count: int) -> dict:
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
            d for d in (metadata_payload.get("flattened_frame_dimensions", []) or [])
            if int(d.get("count", 1) or 1) > 1
        ]
        if frame_interval is not None and len(active_frame_dims) <= 1:
            meta["finterval"] = frame_interval
        luts = _lif_imagej_luts(record, c_count)
        if luts:
            meta["LUTs"] = luts
        return meta

    def _lif_export_image_as_tiff(lif, record: dict, output_dir: Path, output_name: str, overwrite: bool = True) -> dict:
        if not has_tiff or tifflib is None:
            raise RuntimeError("tifffile is required for TIFF export. Run: python -m pip install tifffile")

        image_index = int(record.get("index", 0))
        image = lif.get_image(image_index)
        plan = _lif_export_plan(record)
        counts = _lif_oriented_counts(plan.get("counts", {}) or {}, record.get("scan_orientation", {}) or {})
        z_count = max(1, int(counts.get("z", 1) or 1))
        c_count = max(1, int(counts.get("c", 1) or 1))
        y_count = max(1, int(counts.get("y", 1) or 1))
        x_count = max(1, int(counts.get("x", 1) or 1))
        total_planes = max(1, int(counts.get("planes", 1) or 1))

        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _lif_sanitize_filename(output_name, f"image_{image_index + 1}")
        out_path = _lif_unique_output_path(output_dir / f"{safe_name}.tiff", overwrite)

        bit_depths = record.get("bit_depth", []) or [16]
        max_bit_depth = int(max(bit_depths) if bit_depths else 16)
        bytes_per_pixel = max(1, (max_bit_depth + 7) // 8)
        estimated_bytes = x_count * y_count * total_planes * bytes_per_pixel
        bigtiff = estimated_bytes > 3_500_000_000
        metadata_payload = _lif_build_export_metadata(lif, image, record, output_name, plan)
        imagej_metadata = _lif_imagej_metadata(metadata_payload, record, c_count)
        description_type = "ImageJ"
        calibration = metadata_payload.get("calibration", {}) or {}
        resolution_kwargs = _lif_resolution_kwargs(calibration)
        tiff_datetime = _lif_tiff_datetime(record)
        sidecar_path = out_path.with_name(f"{out_path.stem}_metadata.json")

        planes_written = 0
        stack = None
        z_dim = plan.get("z_dimension") or {}
        z_dim_id = int(z_dim.get("id", 3) or 3) if z_dim else None
        for frame_index, frame_values in _lif_frame_dimension_combinations(plan.get("frame_dimensions", []) or []):
            for z in range(z_count):
                dim_values = dict(frame_values)
                if z_dim_id is not None:
                    dim_values[z_dim_id] = z
                for c in range(c_count):
                    arr = _lif_display_array(_lif_get_plane_by_dimensions(image, c=c, dimension_values=dim_values), record)
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
            "metadata": imagej_metadata,
            "software": "DataProcess Leica LIF Browser",
            **resolution_kwargs,
        }
        if tiff_datetime:
            write_kwargs["datetime"] = tiff_datetime
        tifflib.imwrite(str(out_path), stack, **write_kwargs)

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

    def _lif_manifest_rows(records: list[dict], order_indices: list[int] | None = None, rename_map: dict | None = None):
        by_index = {int(r["index"]): r for r in records}
        if order_indices:
            ordered = [by_index[i] for i in order_indices if i in by_index]
            seen = {int(r["index"]) for r in ordered}
            ordered.extend([r for r in records if int(r["index"]) not in seen])
        else:
            ordered = sorted(records, key=lambda r: _lif_record_sort_tuple(r, "time"))

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
                    "display_name": _lif_output_name_for_record(r, rename_map),
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

    @app.route("/api/fluorescence/lif/browse", methods=["POST"])
    def api_lif_browse():
        d = request.json or {}
        folder = d.get("folder", "")
        files = browse_files(folder, {".lif"})
        return jsonify({"files": files})

    @app.route("/api/fluorescence/lif/info", methods=["POST"])
    def api_lif_info():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        sort_mode = str(d.get("sort", "time") or "time").strip().lower()
        if sort_mode not in {"time", "original", "name"}:
            sort_mode = "time"

        try:
            _, records = _lif_load_records(path)
            sorted_records = sorted(records, key=lambda r: _lif_record_sort_tuple(r, sort_mode))
            for i, r in enumerate(sorted_records, start=1):
                r["display_order"] = i
                r["has_timestamp"] = r.get("sort_value") is not None
            timestamp_count = sum(1 for r in records if r.get("sort_value") is not None)
            return jsonify(
                {
                    "ok": True,
                    "path": path,
                    "name": Path(path).name,
                    "n_images": len(records),
                    "timestamp_count": timestamp_count,
                    "records": sorted_records,
                    "sort": sort_mode,
                    "readlif": has_readlif,
                }
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/preview", methods=["POST"])
    def api_lif_preview():
        if not has_pil or image_mod is None:
            return err("Pillow is required for LIF previews. Run: python -m pip install Pillow")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        image_index = int_or(d.get("image_index", 0), 0)
        z = int_or(d.get("z", 0), 0)
        t = int_or(d.get("t", 0), 0)
        c = int_or(d.get("c", 0), 0)
        m = int_or(d.get("m", 0), 0)
        requested_dims = d.get("requested_dims") if isinstance(d.get("requested_dims"), dict) else {}
        lut = str(d.get("lut", "Gray") or "Gray")
        p_low = float_or(d.get("p_low", 1.0), 1.0)
        p_high = float_or(d.get("p_high", 99.0), 99.0)
        p_low = max(0.0, min(49.0, p_low))
        p_high = max(51.0, min(100.0, p_high))

        try:
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            image = lif.get_image(image_index)
            plane = _lif_display_array(_lif_get_plane(image, z=z, t=t, c=c, m=m, requested_dims=requested_dims), records[image_index])
            b64 = _lif_plane_to_b64(plane, lut, p_low, p_high)
            return jsonify(
                {
                    "ok": True,
                    "img": b64,
                    "record": records[image_index],
                    "z": z,
                    "t": t,
                    "c": c,
                    "m": m,
                    "requested_dims": requested_dims,
                }
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/volume3d", methods=["POST"])
    def api_lif_volume3d():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        image_index = int_or(d.get("image_index", 0), 0)
        t = int_or(d.get("t", 0), 0)
        c = int_or(d.get("c", 0), 0)
        m = int_or(d.get("m", 0), 0)
        requested_dims = d.get("requested_dims") if isinstance(d.get("requested_dims"), dict) else {}
        channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
        if channel_mode not in {"composite", "current"}:
            channel_mode = "composite"
        max_points = int_or(d.get("max_points", 70000), 70000)
        max_xy = int_or(d.get("max_xy", 180), 180)
        max_z = int_or(d.get("max_z", 80), 80)
        threshold_percentile = float_or(d.get("threshold_percentile", 98.8), 98.8)

        try:
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            payload = _lif_build_volume3d_payload(
                lif,
                records[image_index],
                requested_dims=requested_dims,
                t=t,
                m=m,
                c=c,
                channel_mode=channel_mode,
                max_points=max_points,
                max_xy=max_xy,
                max_z=max_z,
                threshold_percentile=threshold_percentile,
            )
            return jsonify({"ok": True, "volume": payload})
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_volume3d", methods=["POST"])
    def api_lif_export_volume3d():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        image_index = int_or(d.get("image_index", 0), 0)
        output_name = str(d.get("output_name", "") or "").strip()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        overwrite = bool(d.get("overwrite", True))
        t = int_or(d.get("t", 0), 0)
        c = int_or(d.get("c", 0), 0)
        m = int_or(d.get("m", 0), 0)
        requested_dims = d.get("requested_dims") if isinstance(d.get("requested_dims"), dict) else {}
        channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
        if channel_mode not in {"composite", "current"}:
            channel_mode = "composite"
        max_points = int_or(d.get("max_points", 110000), 110000)
        max_xy = int_or(d.get("max_xy", 220), 220)
        max_z = int_or(d.get("max_z", 120), 120)
        threshold_percentile = float_or(d.get("threshold_percentile", 98.6), 98.6)

        try:
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            p = Path(path).expanduser()
            output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else p.with_name(f"{p.stem}_lif_exports")
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            record = records[image_index]
            output_name = output_name or _lif_output_name_for_record(record)
            safe_name = _lif_sanitize_filename(output_name, f"image_{image_index + 1}")
            out_path = _lif_unique_output_path(output_dir / f"{safe_name}_3d_viewer.html", overwrite)
            payload = _lif_build_volume3d_payload(
                lif,
                record,
                requested_dims=requested_dims,
                t=t,
                m=m,
                c=c,
                channel_mode=channel_mode,
                max_points=max_points,
                max_xy=max_xy,
                max_z=max_z,
                threshold_percentile=threshold_percentile,
            )
            out_path.write_text(_lif_volume3d_html(payload), encoding="utf-8")
            return jsonify(
                {
                    "ok": True,
                    "output_path": str(out_path),
                    "image_index": image_index,
                    "name": record.get("name", ""),
                    "output_name": output_name,
                    "n_points": payload.get("render", {}).get("n_points", 0),
                    "z_sampled": payload.get("dimensions", {}).get("z_sampled", 0),
                    "channels_rendered": payload.get("dimensions", {}).get("channels_rendered", []),
                    "calibration": payload.get("calibration", {}),
                }
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_volume3d_job", methods=["POST"])
    def api_lif_export_volume3d_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/lif/export_volume3d",
            "fluorescence.lif_export_volume3d",
            "Export LIF 3D viewer",
            api_lif_export_volume3d,
            request.json or {},
        )

    @app.route("/api/fluorescence/lif/export_manifest", methods=["POST"])
    def api_lif_export_manifest():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        order_indices_raw = d.get("order_indices") or []
        rename_map = d.get("rename_map") if isinstance(d.get("rename_map"), dict) else {}
        order_indices = []
        if isinstance(order_indices_raw, list):
            for raw in order_indices_raw:
                try:
                    order_indices.append(int(raw))
                except Exception:
                    pass

        try:
            _, records = _lif_load_records(path)
            rows = _lif_manifest_rows(records, order_indices, rename_map)
            p = Path(path).expanduser()
            out_path = p.with_name(f"{p.stem}_lif_time_order.csv")
            fields = [
                "display_order",
                "original_order",
                "image_index",
                "acquired_at",
                "timestamp_source",
                "folder",
                "name",
                "display_name",
                "full_name",
                "x",
                "y",
                "z",
                "t",
                "mosaic_tiles",
                "channels",
                "bit_depth",
            ]
            with out_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            return jsonify({"ok": True, "output_path": str(out_path), "rows": len(rows)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff", methods=["POST"])
    def api_lif_export_tiff():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        image_index = int_or(d.get("image_index", 0), 0)
        output_name = str(d.get("output_name", "") or "").strip()
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        overwrite = bool(d.get("overwrite", True))

        try:
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            p = Path(path).expanduser()
            output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else p.with_name(f"{p.stem}_lif_exports")
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir
            record = records[image_index]
            output_name = output_name or _lif_output_name_for_record(record)
            result = _lif_export_image_as_tiff(lif, record, output_dir, output_name, overwrite=overwrite)
            return jsonify({"ok": True, **result})
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff_job", methods=["POST"])
    def api_lif_export_tiff_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/lif/export_tiff",
            "fluorescence.lif_export_tiff",
            "Export selected LIF TIFF",
            api_lif_export_tiff,
            request.json or {},
        )

    @app.route("/api/fluorescence/lif/export_tiff_batch", methods=["POST"])
    def api_lif_export_tiff_batch():
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        order_indices_raw = d.get("order_indices") or []
        rename_map = d.get("rename_map") if isinstance(d.get("rename_map"), dict) else {}
        output_dir_raw = str(d.get("output_dir", "") or "").strip()
        overwrite = bool(d.get("overwrite", True))

        order_indices = []
        if isinstance(order_indices_raw, list):
            for raw in order_indices_raw:
                try:
                    order_indices.append(int(raw))
                except Exception:
                    pass

        try:
            lif, records = _lif_load_records(path)
            by_index = {int(r["index"]): r for r in records}
            ordered = [by_index[i] for i in order_indices if i in by_index] if order_indices else sorted(records, key=lambda r: _lif_record_sort_tuple(r, "time"))
            p = Path(path).expanduser()
            output_dir = Path(output_dir_raw).expanduser() if output_dir_raw else p.with_name(f"{p.stem}_lif_exports")
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir

            outputs = []
            failed = []
            for display_order, record in enumerate(ordered, start=1):
                try:
                    output_name = f"{display_order:03d}_{_lif_output_name_for_record(record, rename_map)}"
                    outputs.append(_lif_export_image_as_tiff(lif, record, output_dir, output_name, overwrite=overwrite))
                except Exception as exc:
                    failed.append({"image_index": record.get("index"), "name": record.get("name", ""), "error": str(exc)})

            return jsonify(
                {
                    "ok": True,
                    "output_dir": str(output_dir),
                    "success": len(outputs),
                    "failed": len(failed),
                    "outputs": outputs,
                    "failed_files": failed,
                }
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff_batch_job", methods=["POST"])
    def api_lif_export_tiff_batch_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/lif/export_tiff_batch",
            "fluorescence.lif_export_tiff_batch",
            "Export all LIF TIFFs",
            api_lif_export_tiff_batch,
            request.json or {},
        )
