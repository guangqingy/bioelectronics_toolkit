"""Tokenized parsing of electrochemistry recording filenames.

Recording filenames in the EChem datasets already encode the full experimental
condition, for example::

    mb_5mM_oil_1pct_distance_5cm_parallel_group_01_CA.csv
    mb_5mM_oil_1pct_light_on_0p25s_off_0p75s_nominal_bias_m0p2V_..._COR.csv
    mb_solution_50uM_scan_200mVps_parallel_group_01_CV.csv

This module turns that flat string into a stable, machine-readable token set so
the WebGUI can group, filter and label recordings without every page
re-implementing its own ad-hoc string matching.

Two representations are produced for every file:

``fields``
    Typed values keyed by a stable token name (``analyte``, ``concentration_mM``,
    ``bias_V`` ...). Numeric where a number is meaningful, so the GUI can sort
    and plot against them directly.

``tokens``
    A flat ``key=value`` string list (``["technique=CA", "oil_pct=1"]``) intended
    for filter chips, faceted browsing and free-text search in the GUI.

Number encoding follows the dataset convention: ``p`` is the decimal point and a
leading ``m``/``p`` on a voltage is the sign (``m0p2V`` -> ``-0.2`` V).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

__all__ = [
    "TOKEN_ORDER",
    "TOKEN_LABELS",
    "decode_number",
    "discover_recording_paths",
    "parse_recording_name",
    "parse_recording_names",
    "recording_matches_techniques",
    "token_facets",
]

# Stable ordering used for GUI columns, chips and CSV headers. Adding a token
# means appending here so existing column positions never shift.
TOKEN_ORDER: tuple[str, ...] = (
    "technique",
    "analyte",
    "concentration_mM",
    "oil_pct",
    "distance_cm",
    "wavelength_nm",
    "bias_V",
    "scan_rate_mVps",
    "ph",
    "light_on_s",
    "light_off_s",
    "light_period_s",
    "light_duty",
    "age_min",
    "placement_min",
    "surfactant_pct",
    "electrode_area_mm2",
    "au_thickness_nm",
    "current_range_uA",
    "substrate",
    "sweep_mode",
    "replicate",
    "replicate_kind",
    "session",
    "qualifiers",
)

# Human-readable labels for GUI headers and chip tooltips.
TOKEN_LABELS: dict[str, str] = {
    "technique": "Technique",
    "analyte": "Analyte",
    "concentration_mM": "Concentration (mM)",
    "oil_pct": "Oil fraction (%)",
    "distance_cm": "Distance (cm)",
    "wavelength_nm": "Wavelength (nm)",
    "bias_V": "Nominal bias (V)",
    "scan_rate_mVps": "Scan rate (mV/s)",
    "ph": "pH",
    "light_on_s": "Light ON (s)",
    "light_off_s": "Light OFF (s)",
    "light_period_s": "Light period (s)",
    "light_duty": "Light duty cycle",
    "age_min": "Emulsion age (min)",
    "placement_min": "Placement time (min)",
    "surfactant_pct": "Surfactant (%)",
    "electrode_area_mm2": "Electrode area (mm^2)",
    "au_thickness_nm": "Au thickness (nm)",
    "current_range_uA": "Current range (uA)",
    "substrate": "Substrate",
    "sweep_mode": "Sweep mode",
    "replicate": "Replicate",
    "replicate_kind": "Replicate kind",
    "session": "Session",
    "qualifiers": "Qualifiers",
}

# Technique is carried by the filename suffix for standardized exports; .txt
# recordings from the pH/placement sessions are CorrTest square-wave captures.
_TECHNIQUE_SUFFIXES: dict[str, str] = {
    "CA": "CA",
    "CP": "CP",
    "CV": "CV",
    "COR": "COR",
}

# Multi-word analytes must be matched before the single-word fallbacks.
_ANALYTE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^beta_carotene", "beta_carotene"),
    (r"^rose_bengal", "rose_bengal"),
    (r"^vitamin_b2", "vitamin_b2"),
    (r"^adsorbed_au", "adsorbed_au"),
    (r"^water_control", "water_control"),
    (r"^blank_control", "blank"),
    (r"^blank", "blank"),
    (r"^curcumin", "curcumin"),
    (r"^me(?:_|$)", "me"),
    (r"^mb", "mb"),
)

_DISCOVERY_EXCLUDED_DIRS = frozenset(
    {
        ".dataprocess_cache",
        "__pycache__",
        "archive",
        "output",
        "outputs",
        "references",
        "reports",
        "results",
        "slide_ready",
    }
)

# Bare flags that describe the preparation rather than a measured quantity.
_QUALIFIER_TOKENS: tuple[str, ...] = (
    "piranha_cleaned_container",
    "no_ultrasound",
    "ultrasound",
    "split_cell",
    "new_device",
    "no_response",
    "current_decay",
    "manual_light_switch",
    "plasma_au",
    "pt_on_au",
    "saline",
    "retry",
    "fresh",
    "control",
)

_NUMBER = r"\d+(?:p\d+)?"


def decode_number(text: str) -> float | None:
    """Decode a dataset-encoded number: ``0p375`` -> 0.375, ``m0p2`` -> -0.2."""
    raw = str(text or "").strip()
    if not raw:
        return None
    sign = 1.0
    if raw[0] in "mM" and len(raw) > 1 and (raw[1].isdigit() or raw[1] == "p"):
        sign, raw = -1.0, raw[1:]
    elif raw[0] in "pP" and len(raw) > 1 and raw[1].isdigit():
        sign, raw = 1.0, raw[1:]
    try:
        return sign * float(raw.replace("p", "."))
    except ValueError:
        return None


def _search(pattern: str, stem: str) -> re.Match[str] | None:
    return re.search(pattern, stem, re.IGNORECASE)


def _technique(path: Path, stem: str) -> str | None:
    tail = stem.rsplit("_", 1)[-1].upper()
    if tail in _TECHNIQUE_SUFFIXES:
        return _TECHNIQUE_SUFFIXES[tail]
    if path.suffix.lower() == ".txt":
        return "COR"
    return None


def _analyte(stem: str) -> str | None:
    for pattern, name in _ANALYTE_PATTERNS:
        if _search(pattern, stem):
            return name
    return None


def _concentration_mM(stem: str) -> float | None:
    """Concentration normalized to mM regardless of the unit in the name.

    Molar units are matched case-sensitively: ``nM`` is nanomolar while ``nm``
    is a wavelength or a film thickness, and the two appear in sibling files
    (``mb_50uM_...`` next to ``au_thickness_200nm_...``).
    """
    match = re.search(rf"_({_NUMBER})(mM|uM|nM)(?:_|$)", f"_{stem}")
    if not match:
        return None
    value = decode_number(match.group(1))
    if value is None:
        return None
    scale = {"mM": 1.0, "uM": 1e-3, "nM": 1e-6}[match.group(2)]
    return value * scale


def _light_timing(stem: str) -> dict[str, float]:
    match = _search(rf"light_on_({_NUMBER})s_off_({_NUMBER})s", stem)
    if not match:
        return {}
    on_s = decode_number(match.group(1))
    off_s = decode_number(match.group(2))
    if on_s is None or off_s is None:
        return {}
    period = on_s + off_s
    fields: dict[str, float] = {
        "light_on_s": on_s,
        "light_off_s": off_s,
        "light_period_s": period,
    }
    if period > 0:
        fields["light_duty"] = round(on_s / period, 6)
    return fields


def _substrate(stem: str) -> str | None:
    """Where the dye sits: adsorbed on gold, dissolved, or a bare control."""
    if _search(r"(^|_)on_au(_|$)", stem) or _search(r"(^|_)adsorbed_au(_|$)", stem):
        return "on_au"
    if _search(r"(^|_)solution(_|$)", stem):
        return "solution"
    if _search(r"(^|_)blank(_|$)", stem):
        return "blank"
    return None


def _replicate(stem: str) -> tuple[int | None, str | None]:
    """Replicate index plus which naming convention produced it."""
    for pattern, kind in (
        (r"parallel_group_(\d+)", "parallel_group"),
        (r"(?:^|_)pass_(\d+)", "pass"),
        (r"(?:ph|_)scan_(\d+)(?:_|$)", "scan"),
        (r"(?:^|_)manual_light_switch_(\d+)", "manual_switch"),
    ):
        match = _search(pattern, stem)
        if match:
            return int(match.group(1)), kind
    return None, None


def _qualifiers(stem: str) -> list[str]:
    found: list[str] = []
    for flag in _QUALIFIER_TOKENS:
        if not _search(rf"(^|_){flag}(_|$)", stem):
            continue
        # ``ultrasound`` is a substring of ``no_ultrasound``; keep the specific one.
        if flag == "ultrasound" and "no_ultrasound" in found:
            continue
        found.append(flag)
    for placement in ("far", "close", "middle"):
        if _search(rf"(^|_)au_{placement}(_|$)", stem):
            found.append(f"electrode_{placement}")
    return found


def _session(path: Path) -> str | None:
    """The dated experiment folder a recording belongs to, when available."""
    for parent in path.parents:
        if re.match(r"^\d{8}", parent.name):
            return parent.name
    return None


def recording_matches_techniques(path: str | Path, techniques: set[str] | frozenset[str]) -> bool:
    """Whether a recording belongs on a technique-specific legacy page."""
    technique = _technique(Path(path), Path(path).stem)
    return technique in {str(value).upper() for value in techniques}


def discover_recording_paths(folder: str | Path, max_files: int = 5000) -> list[str]:
    """Recursively discover source recordings while excluding generated data.

    This supports both the current date-folder workspace and the earlier
    characterization/data layout.  Archives, result tables and exported pulse
    windows are excluded so one physical acquisition is not quantified twice.
    """
    root = Path(str(folder or "").strip()).expanduser()
    if not root.is_dir():
        return []
    result: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".") and name.lower() not in _DISCOVERY_EXCLUDED_DIRS
        )
        for name in sorted(files):
            if Path(name).suffix.lower() not in {".csv", ".txt"}:
                continue
            path = Path(current) / name
            if _technique(path, path.stem) is None:
                continue
            result.append(str(path))
            if len(result) >= max(1, int(max_files)):
                return result
    return result


def parse_recording_name(path: str | Path) -> dict[str, Any]:
    """Parse one recording filename into typed fields and ``key=value`` tokens.

    Unrecognized tokens are never dropped silently: anything the parser did not
    consume is returned under ``unparsed`` so a new naming convention shows up
    in the GUI instead of disappearing.
    """
    source = Path(str(path or ""))
    stem = source.stem
    fields: dict[str, Any] = {}

    technique = _technique(source, stem)
    if technique:
        fields["technique"] = technique

    analyte = _analyte(stem)
    if analyte:
        fields["analyte"] = analyte

    concentration = _concentration_mM(stem)
    if concentration is not None:
        fields["concentration_mM"] = concentration

    scalars: tuple[tuple[str, str, int], ...] = (
        ("oil_pct", rf"oil_({_NUMBER})pct", 1),
        ("distance_cm", rf"distance_({_NUMBER})cm", 1),
        ("wavelength_nm", rf"(?:^|_)({_NUMBER})nm(?:_|$)", 1),
        ("bias_V", rf"nominal_bias_([mp]?{_NUMBER})V", 1),
        ("scan_rate_mVps", rf"scan_({_NUMBER})mVps", 1),
        ("ph", rf"(?:^|_)ph_({_NUMBER})(?:_|$)", 1),
        ("age_min", rf"age_({_NUMBER})min", 1),
        ("placement_min", rf"placement_({_NUMBER})min", 1),
        ("surfactant_pct", rf"triton_({_NUMBER})pct", 1),
        ("electrode_area_mm2", rf"electrode_({_NUMBER})mm2", 1),
        ("au_thickness_nm", rf"au_thickness_({_NUMBER})nm", 1),
        ("current_range_uA", rf"range_({_NUMBER})uA", 1),
    )
    for name, pattern, group in scalars:
        match = _search(pattern, stem)
        if not match:
            continue
        value = decode_number(match.group(group))
        if value is not None:
            fields[name] = value

    # au_thickness_200nm also matches the bare wavelength pattern; the explicit
    # thickness token wins and the spurious wavelength is dropped.
    if "au_thickness_nm" in fields and fields.get("wavelength_nm") == fields["au_thickness_nm"]:
        fields.pop("wavelength_nm", None)

    fields.update(_light_timing(stem))

    substrate = _substrate(stem)
    if substrate:
        fields["substrate"] = substrate

    if _search(r"bias_staircase", stem):
        fields["sweep_mode"] = "staircase"
    elif "bias_V" in fields:
        fields["sweep_mode"] = "discrete_bias"

    replicate, replicate_kind = _replicate(stem)
    if replicate is not None:
        fields["replicate"] = replicate
        fields["replicate_kind"] = replicate_kind

    session = _session(source)
    if session:
        fields["session"] = session

    qualifiers = _qualifiers(stem)
    if qualifiers:
        fields["qualifiers"] = qualifiers

    return {
        "file": source.name,
        "path": str(source),
        "stem": stem,
        "fields": fields,
        "tokens": _as_tokens(fields),
        "label": build_label(fields),
        "unparsed": _unparsed(stem, fields),
    }


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _as_tokens(fields: dict[str, Any]) -> list[str]:
    """Flatten typed fields into ``key=value`` strings in stable token order."""
    tokens: list[str] = []
    for name in TOKEN_ORDER:
        if name not in fields:
            continue
        value = fields[name]
        if isinstance(value, list):
            tokens.extend(f"{name}={_format_value(item)}" for item in value)
        else:
            tokens.append(f"{name}={_format_value(value)}")
    return tokens


# Short prefixes keep GUI labels compact while staying unambiguous.
_LABEL_PARTS: tuple[tuple[str, str, str], ...] = (
    ("analyte", "", ""),
    ("concentration_mM", "", " mM"),
    ("oil_pct", "oil ", "%"),
    ("distance_cm", "d=", " cm"),
    ("wavelength_nm", "", " nm"),
    ("bias_V", "E=", " V"),
    ("scan_rate_mVps", "v=", " mV/s"),
    ("ph", "pH ", ""),
    ("light_on_s", "on ", " s"),
    ("age_min", "age ", " min"),
    ("placement_min", "t=", " min"),
)


def build_label(fields: dict[str, Any]) -> str:
    """Compact human label for plot legends and table rows."""
    parts = [
        f"{prefix}{_format_value(fields[name])}{suffix}"
        for name, prefix, suffix in _LABEL_PARTS
        if name in fields
    ]
    replicate = fields.get("replicate")
    if replicate is not None:
        parts.append(f"#{int(replicate)}")
    return " ".join(parts)


# Structural words that carry no condition information on their own.
_STRUCTURAL_WORDS = frozenset(
    {
        "parallel",
        "group",
        "nominal",
        "bias",
        "light",
        "on",
        "off",
        "scan",
        "ph",
        "oil",
        "distance",
        "age",
        "placement",
        "electrode",
        "range",
        "triton",
        "au",
        "thickness",
        "solution",
        "pass",
        "staircase",
        "beta",
        "carotene",
        "rose",
        "bengal",
        "vitamin",
        "b2",
        "water",
        "adsorbed",
        "pt",
        "cleaned",
        "container",
        "piranha",
        "split",
        "cell",
        "new",
        "device",
        "manual",
        "switch",
        "current",
        "decay",
        "no",
        "response",
    }
)


def _unparsed(stem: str, fields: dict[str, Any]) -> list[str]:
    """Name fragments no rule claimed, so unknown conventions stay visible."""
    consumed = {
        str(fields.get("analyte", "")),
        str(fields.get("technique", "")).upper(),
        str(fields.get("substrate", "")),
        str(fields.get("session", "")),
    }
    consumed.update(_STRUCTURAL_WORDS)
    for qualifier in fields.get("qualifiers", []):
        consumed.update(str(qualifier).split("_"))
    for analyte in dict(_ANALYTE_PATTERNS).values():
        if fields.get("analyte") == analyte:
            consumed.update(analyte.split("_"))

    leftovers: list[str] = []
    for word in stem.split("_"):
        lowered = word.lower()
        if not lowered or lowered in consumed:
            continue
        # Any word carrying digits was either consumed by a scalar rule above or
        # is a replicate index; either way it is not an unknown convention.
        if any(character.isdigit() for character in lowered):
            continue
        if lowered.upper() in _TECHNIQUE_SUFFIXES:
            continue
        leftovers.append(word)
    return leftovers


def parse_recording_names(paths: list[str | Path]) -> list[dict[str, Any]]:
    """Parse many filenames, preserving input order."""
    return [parse_recording_name(path) for path in paths]


def token_facets(parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Distinct values per token across a set of recordings, for GUI filters.

    Numeric facets are sorted numerically (so 0.1 mM precedes 10 mM rather than
    sorting as text) and each value carries the number of matching recordings.
    """
    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    for record in parsed:
        for name, value in record.get("fields", {}).items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                key = _format_value(item)
                slot = buckets.setdefault(name, {}).setdefault(
                    key, {"value": key, "raw": item, "count": 0}
                )
                slot["count"] += 1

    facets: list[dict[str, Any]] = []
    for name in TOKEN_ORDER:
        if name not in buckets:
            continue
        values = list(buckets[name].values())
        numeric = all(isinstance(entry["raw"], (int, float)) for entry in values)
        values.sort(key=(lambda e: e["raw"]) if numeric else (lambda e: e["value"]))
        facets.append(
            {
                "token": name,
                "label": TOKEN_LABELS.get(name, name),
                "numeric": numeric,
                "values": values,
            }
        )
    return facets
