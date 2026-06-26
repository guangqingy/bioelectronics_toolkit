from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

TOKEN_KEYS = ["sample", "electrode", "freestanding", "thermal", "decay"]
FILENAME_PATTERNS = [
    re.compile(
        rf"^(.+?)_(.+?)_(?:{'|'.join(map(re.escape, TOKEN_KEYS))})_"
        r"(\d+)_([A-Za-z0-9]+)_(\d+)$",
        re.IGNORECASE,
    ),
    re.compile(r"^(.+?)_(.+?)_(\d+)_([A-Za-z0-9]+)_(\d+)$", re.IGNORECASE),
]


def _seq_index(seq_raw: str) -> int:
    return int(seq_raw[-3:]) if len(seq_raw) >= 3 else int(seq_raw)


def _parse_abf_batch_stem(stem: str) -> dict[str, Any] | None:
    for pattern in FILENAME_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        main, treat, sample, spot, seq = match.groups()
        return {
            "main": main,
            "treat": treat,
            "sample": int(sample),
            "spot": str(spot),
            "seq": _seq_index(seq),
            "seq_width": len(str(seq)),
        }
    return None

def scan_filename_tokens(files: list[dict[str, Any] | str]) -> dict[str, Any]:
    """Return suggested main/treatment tokens from ABF batch filenames."""
    mains: Counter[str] = Counter()
    treats: Counter[str] = Counter()
    pat = re.compile(r"^(.+?)_(.+?)_sample_", re.IGNORECASE)
    token_set = {token.lower() for token in TOKEN_KEYS}
    for file_item in files:
        name = file_item.get("name", "") if isinstance(file_item, dict) else str(file_item)
        stem = Path(name).stem
        parsed_name = _parse_abf_batch_stem(stem)
        if parsed_name:
            mains[str(parsed_name["main"])] += 1
            treats[str(parsed_name["treat"])] += 1
            continue
        parts = stem.split("_")
        if len(parts) >= 2:
            token_index = next(
                (
                    idx
                    for idx, part in enumerate(parts)
                    if any(part.lower().startswith(token) for token in token_set)
                ),
                None,
            )
            if token_index is None or token_index >= 2:
                mains[parts[0]] += 1
                treats[parts[1]] += 1
                continue
        match = pat.match(stem)
        if match:
            mains[match.group(1)] += 1
            treats[match.group(2)] += 1
    main_tokens = sorted(mains)
    treat_tokens = sorted(treats)
    main_text = ", ".join(main_tokens)
    treat_text = ", ".join(treat_tokens)
    return {
        "mains": main_tokens,
        "treats": treat_tokens,
        "main_counts": {key: mains[key] for key in main_tokens},
        "treat_counts": {key: treats[key] for key in treat_tokens},
        "main_token": main_text,
        "treat_token": treat_text,
        "multiple_main_tokens": len(main_tokens) > 1,
        "multiple_treat_tokens": len(treat_tokens) > 1,
    }


def _parse_token_list(raw: Any) -> list[str]:
    return [token.strip() for token in str(raw or "").split(",") if token.strip()]


def browse_payload(
    folder: str,
    browse_files_recursive: Callable[..., list[dict[str, Any]]],
    *,
    limit: int = 300,
) -> dict[str, Any]:
    files = browse_files_recursive(folder, {".abf"}, max_files=limit + 1)
    truncated = len(files) > limit
    return {"files": files[:limit], "truncated": truncated}

__all__ = [
    "FILENAME_PATTERNS",
    "TOKEN_KEYS",
    "_parse_abf_batch_stem",
    "_parse_token_list",
    "_seq_index",
    "browse_payload",
    "scan_filename_tokens",
]
