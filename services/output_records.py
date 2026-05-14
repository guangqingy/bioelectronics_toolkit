from __future__ import annotations

from pathlib import Path
from typing import Any

ENVELOPE_KEYS = {"ok", "data", "outputs", "warnings", "error"}

OUTPUT_SCALAR_KEYS = (
    "saved_path",
    "output_path",
    "csv_path",
    "plot_path",
    "summary_path",
    "summary_csv_path",
    "heatmap_path",
    "heatmap_csv_path",
    "preview_path",
    "radial_csv_path",
    "radial_plot_path",
    "manifest_path",
    "package_path",
    "report_path",
    "combined_tiff",
    "macro",
    "json",
    "gif_path",
    "saved_folder",
    "output_dir",
)

OUTPUT_LIST_KEYS = (
    "saved_paths",
    "generated_files",
    "stack_files",
    "segment_paths",
)

OUTPUT_RECORD_LIST_KEYS = (
    "outputs",
    "artifacts",
)

OUTPUT_TYPE_BY_KEY = {
    "output_dir": "directory",
    "saved_folder": "directory",
    "manifest_path": "run_manifest",
    "package_path": "run_package",
    "report_path": "report",
    "combined_tiff": "combined_tiff",
    "stack_files": "stack_tiff",
    "macro": "fiji_macro",
}

OUTPUT_TYPE_BY_SUFFIX = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".txt": "text",
    ".png": "png",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "svg",
    ".gif": "gif",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".json": "json",
    ".zip": "zip",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".ijm": "fiji_macro",
    ".pdf": "pdf",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".npz": "numpy_archive",
    ".pt": "model",
}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def is_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and ENVELOPE_KEYS.issubset(payload.keys())


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 2048 or "\n" in text or "\r" in text:
        return False
    if text.startswith(("data:", "http://", "https://", "{", "[")):
        return False
    path = Path(text)
    return "/" in text or "\\" in text or text.startswith("~") or bool(path.suffix)


def infer_output_type(path: str, key: str = "") -> str:
    if key in OUTPUT_TYPE_BY_KEY:
        return OUTPUT_TYPE_BY_KEY[key]
    suffix = Path(path).suffix.lower()
    if suffix in OUTPUT_TYPE_BY_SUFFIX:
        return OUTPUT_TYPE_BY_SUFFIX[suffix]
    if "dir" in key or "folder" in key:
        return "directory"
    return "file"


def normalize_output_record(value: Any, key: str = "") -> dict[str, Any] | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not _looks_like_path(text) and "dir" not in key and "folder" not in key:
            return None
        return {"path": text, "type": infer_output_type(text, key)}
    if not isinstance(value, dict):
        return None
    path = value.get("path") or value.get("output_path") or value.get("saved_path")
    if not _looks_like_path(path):
        return None
    rec = dict(value)
    rec["path"] = str(path).strip()
    rec.setdefault("type", infer_output_type(rec["path"], key or str(rec.get("role") or "")))
    rec.pop("img", None)
    rec.pop("preview", None)
    rec.pop("gif_preview", None)
    rec.pop("outputs", None)
    return rec


def _direct_output_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        rec = normalize_output_record(item, "outputs")
        if not rec:
            return []
        records.append(rec)
    return records


def infer_outputs(payload: Any) -> list[dict[str, Any]]:
    """Extract stable output records from legacy route payload shapes."""
    if not isinstance(payload, dict):
        return []

    direct_outputs = _direct_output_records(payload.get("outputs"))
    if direct_outputs:
        return direct_outputs

    source = payload
    if is_envelope(payload) and isinstance(payload.get("data"), dict):
        source = dict(payload["data"])
        for key, value in payload.items():
            if key != "data":
                source.setdefault(key, value)

    outputs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(value: Any, key: str = "") -> None:
        rec = normalize_output_record(value, key)
        if not rec:
            return
        dedupe_key = (rec.get("path", ""), rec.get("role") or rec.get("type") or key)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        outputs.append(rec)

    def scan_record(record: Any, allow_direct_record: bool = False) -> None:
        if isinstance(record, str):
            add(record)
            return
        if not isinstance(record, dict):
            return
        if allow_direct_record:
            add(record)
        for key in OUTPUT_SCALAR_KEYS:
            if key in record:
                add(record.get(key), key)
        for key in OUTPUT_LIST_KEYS:
            values = record.get(key)
            if isinstance(values, list):
                for item in values:
                    add(item, key)
        for key in OUTPUT_RECORD_LIST_KEYS:
            values = record.get(key)
            if isinstance(values, list):
                for item in values:
                    scan_record(item, True)

    scan_record(source)
    return outputs
