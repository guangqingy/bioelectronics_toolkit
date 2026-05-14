from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

TIME_KEY_RE = re.compile(r"(acq|acquis|date|time|stamp|creat|start|end|modif)", re.IGNORECASE)
SKIP_KEY_RE = re.compile(
    r"(bytes|inc|dim|length|resolution|wavelength|laser|pinhole|zoom|objective|"
    r"exposure|dwell|cycle|interval|delay|duration|voxel|pixel|physical|numberofelements|bit)",
    re.IGNORECASE,
)
ISO_RE = re.compile(
    r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"
    r"(?:[T\s_]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?"
    r"(?:\s*(?P<ampm>AM|PM|am|pm))?)?"
)
US_EU_RE = re.compile(
    r"(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>\d{2,4})"
    r"(?:[T\s_]+(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?"
    r"(?:\s*(?P<ampm>AM|PM|am|pm))?)?"
)
TIME_ONLY_RE = re.compile(
    r"\b(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}(?:\.\d+)?))?\s*(?P<ampm>AM|PM|am|pm)?\b"
)


def clean_str(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_second(sec_raw: str | None) -> tuple[int, int]:
    if not sec_raw:
        return 0, 0
    sec_float = float(sec_raw)
    sec = int(sec_float)
    micros = int(round((sec_float - sec) * 1_000_000))
    return sec, micros


def apply_ampm(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    ap = ampm.lower()
    if ap == "pm" and hour < 12:
        return hour + 12
    if ap == "am" and hour == 12:
        return 0
    return hour


def candidate_score(key: str, value: str, parsed_kind: str) -> int:
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


def parse_datetime_text(value: str):
    raw = clean_str(value)
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
                "kind": "datetime"
                if (dt.hour or dt.minute or dt.second or dt.microsecond)
                else "date",
            }
        except Exception:
            pass

    m = ISO_RE.search(raw)
    if m:
        try:
            sec, micros = parse_second(m.group("s"))
            h = int(m.group("h") or 0)
            h = apply_ampm(h, m.group("ampm"))
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
                "display": dt.strftime("%Y-%m-%d %H:%M:%S")
                if has_time
                else dt.strftime("%Y-%m-%d"),
                "kind": "datetime" if has_time else "date",
            }
        except Exception:
            pass

    m = US_EU_RE.search(raw)
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
            sec, micros = parse_second(m.group("s"))
            h = int(m.group("h") or 0)
            h = apply_ampm(h, m.group("ampm"))
            dt = datetime(y, month, day, h, int(m.group("mi") or 0), sec, micros)
            has_time = m.group("h") is not None
            return {
                "sort_value": dt.timestamp(),
                "iso": dt.isoformat(sep=" ", timespec="seconds"),
                "display": dt.strftime("%Y-%m-%d %H:%M:%S")
                if has_time
                else dt.strftime("%Y-%m-%d"),
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

    m = TIME_ONLY_RE.search(raw)
    if m:
        try:
            sec, micros = parse_second(m.group("s"))
            hour = apply_ampm(int(m.group("h")), m.group("ampm"))
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


def timestamp_from_element(element: ET.Element | None):
    if element is None:
        return None

    candidates = []
    for node in element.iter():
        tag = node.tag.split("}")[-1]
        for logical_key in ("Identifier", "Name", "Key", "Description"):
            logical_name = clean_str(node.attrib.get(logical_key, ""))
            if not logical_name:
                continue
            if not TIME_KEY_RE.search(logical_name) or SKIP_KEY_RE.search(logical_name):
                continue
            for value_key in ("Variant", "Value", "Text", "Data"):
                if value_key not in node.attrib:
                    continue
                text = clean_str(node.attrib.get(value_key, ""))
                parsed = parse_datetime_text(text)
                if parsed is None:
                    continue
                key_path = f"{tag}.{logical_name}"
                score = candidate_score(key_path, text, parsed["kind"])
                candidates.append((score, key_path, text, parsed))

        for key, value in node.attrib.items():
            key_path = f"{tag}.{key}"
            if not TIME_KEY_RE.search(key_path) or SKIP_KEY_RE.search(key_path):
                continue
            text = clean_str(value)
            parsed = parse_datetime_text(text)
            if parsed is None:
                continue
            score = candidate_score(key_path, text, parsed["kind"])
            candidates.append((score, key_path, text, parsed))

        text = clean_str(node.text)
        if text and TIME_KEY_RE.search(tag) and not SKIP_KEY_RE.search(tag):
            parsed = parse_datetime_text(text)
            if parsed is not None:
                score = candidate_score(tag, text, parsed["kind"])
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


def collect_image_elements(root: ET.Element):
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


def record_sort_tuple(record: dict, mode: str):
    if mode == "name":
        return (str(record.get("full_name", "")).lower(), int(record.get("original_order", 0)))
    if mode == "original":
        return (int(record.get("original_order", 0)),)
    val = record.get("sort_value")
    if val is None or (isinstance(val, float) and not math.isfinite(val)):
        return (1, int(record.get("original_order", 0)))
    return (0, float(val), int(record.get("original_order", 0)))
