from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services import abf as abf_service


def scan_filename_tokens(files: list[dict[str, Any] | str]) -> dict[str, Any]:
    """Return suggested main/treatment tokens from ABF batch filenames."""
    mains = set()
    treats = set()
    pat = re.compile(r"^(.+?)_(.+?)_sample_", re.IGNORECASE)
    for file_item in files:
        name = file_item.get("name", "") if isinstance(file_item, dict) else str(file_item)
        match = pat.match(Path(name).stem)
        if match:
            mains.add(match.group(1))
            treats.add(match.group(2))
    main_tokens = sorted(mains)
    treat_tokens = sorted(treats)
    return {
        "mains": main_tokens,
        "treats": treat_tokens,
        "main_token": main_tokens[0] if main_tokens else "",
        "treat_token": treat_tokens[0] if treat_tokens else "",
    }


def browse_payload(
    folder: str,
    browse_files_recursive: Callable[..., list[dict[str, Any]]],
    *,
    limit: int = 300,
) -> dict[str, Any]:
    files = browse_files_recursive(folder, {".abf"}, max_files=limit + 1)
    truncated = len(files) > limit
    return {"files": files[:limit], "truncated": truncated}


def _write_operation_log(root_dir: Path, payload: dict[str, Any]) -> str:
    log_dir = root_dir / ".dataprocess_cache" / "operation_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"abf_batch_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def process_payload(
    data: dict[str, Any],
    *,
    has_abf: bool,
    pyabf_mod: Any,
    float_or: Callable[[Any, float | None], float | None],
    int_or: Callable[[Any, int], int],
    root_dir: Path,
) -> dict[str, Any]:
    """Process a batch of ABF files and return the web response payload."""
    if not has_abf or pyabf_mod is None:
        raise ValueError("pyabf not installed")

    folder = data.get("folder", "")
    main_token = data.get("main", "")
    treat_token = data.get("treat", "")
    try:
        powers = [float(x) for x in data.get("powers", "").split(",") if x.strip()]
    except ValueError as exc:
        raise ValueError("Power list must be comma-separated numbers") from exc
    i_ch = int_or(data.get("i_ch", 0), 0)
    v_ch = int_or(data.get("v_ch", 1), 1)
    bl_pre0 = float_or(data.get("bl_pre0", 0), 0)
    bl_pre1 = float_or(data.get("bl_pre1", 50), 50)
    peak_window = float_or(data.get("peak_window", 200), 200)
    move_files = bool(data.get("move_files", True))
    reindex_seq = bool(data.get("reindex_seq", False))
    dry_run = bool(data.get("dry_run", False))

    source_dir = Path(folder)
    abf_files = sorted(source_dir.rglob("*.abf"))
    pat = re.compile(r"(.+?)_(.+?)_sample_(\d+)_([A-Za-z0-9]+)_(\d+)", re.IGNORECASE)
    records = []
    warnings = []

    parsed = []
    for abf_path in abf_files:
        match = pat.match(abf_path.stem)
        if not match:
            continue
        main, treat, sample, spot, seq = match.groups()
        if main_token and main.lower() != main_token.lower():
            continue
        if treat_token and treat.lower() != treat_token.lower():
            continue
        parsed.append(
            {
                "path": abf_path,
                "main": main,
                "treat": treat,
                "sample": int(sample),
                "spot": str(spot),
                "seq": int(seq),
                "seq_width": len(str(seq)),
            }
        )

    if not parsed:
        return {
            "message": "No matching files processed",
            "n": 0,
            "rows": [],
            "results": [],
        }

    group_min = {}
    if reindex_seq:
        for item in parsed:
            group_key = (
                item["main"].lower(),
                item["treat"].lower(),
                item["sample"],
                item["spot"].lower(),
            )
            cur_min = group_min.get(group_key)
            if cur_min is None or item["seq"] < cur_min:
                group_min[group_key] = item["seq"]

    moved_count = 0
    renamed_count = 0
    operations = []

    for item in parsed:
        abf_path = Path(item["path"])
        main = item["main"]
        treat = item["treat"]
        sample = item["sample"]
        spot = item["spot"]
        seq_i = item["seq"]

        cur_path = abf_path
        if move_files:
            sample_dir = source_dir / f"{main}_{treat}" / f"sample_{sample}"
            dest_path = sample_dir / cur_path.name
            try:
                if cur_path.resolve() != dest_path.resolve():
                    operations.append(
                        {
                            "action": "move",
                            "source": str(cur_path),
                            "destination": str(dest_path),
                        }
                    )
                    if dry_run:
                        cur_path = dest_path
                        moved_count += 1
                    else:
                        sample_dir.mkdir(parents=True, exist_ok=True)
                        if not dest_path.exists():
                            cur_path = cur_path.rename(dest_path)
                            moved_count += 1
                        else:
                            warnings.append(f"Move skipped (exists): {dest_path.name}")
            except Exception as exc:
                warnings.append(f"Move failed for {cur_path.name}: {exc}")

        if reindex_seq:
            group_key = (main.lower(), treat.lower(), sample, spot.lower())
            offset = int(group_min.get(group_key, 0))
            if offset > 0:
                new_seq = seq_i - offset
                if new_seq != seq_i:
                    width = max(4, item["seq_width"])
                    cur_name = cur_path.name
                    repl = f"_{new_seq:0{width}d}"
                    new_stem = re.sub(r"_(\d+)$", repl, cur_path.stem)
                    new_path = cur_path.with_name(new_stem + cur_path.suffix)
                    try:
                        if cur_path.resolve() != new_path.resolve():
                            operations.append(
                                {
                                    "action": "rename",
                                    "source": str(cur_path),
                                    "destination": str(new_path),
                                }
                            )
                            if dry_run:
                                cur_path = new_path
                                renamed_count += 1
                                seq_i = new_seq
                                continue
                            if not new_path.exists():
                                cur_path = cur_path.rename(new_path)
                                renamed_count += 1
                            else:
                                warnings.append(f"Rename skipped (exists): {new_path.name}")
                    except Exception as exc:
                        warnings.append(f"Rename failed for {cur_name}: {exc}")
                    seq_i = new_seq

        if dry_run:
            continue

        try:
            abf = pyabf_mod.ABF(str(cur_path))
            abf.setSweep(0, channel=i_ch)
            y = abf.sweepY.copy()
            t = abf.sweepX
            dt = t[1] - t[0]

            i0 = int(bl_pre0 / 1000 / dt)
            i1 = int(bl_pre1 / 1000 / dt)
            baseline = np.mean(y[i0 : max(i1, i0 + 1)])
            y -= baseline

            win_end = int(peak_window / 1000 / dt)
            segment = y[i1 : i1 + win_end]
            peak_sign = float(segment[np.argmax(np.abs(segment))])
            integral = float(np.trapz(segment, t[i1 : i1 + win_end]))

            abf.setSweep(0, channel=v_ch)
            v_trace = abf.sweepY
            abf.setSweep(0, channel=i_ch)
            resistance = abf_service.estimate_resistance(y + baseline, v_trace, dt)

            power = powers[seq_i] if seq_i < len(powers) else seq_i
            records.append(
                {
                    "file": cur_path.name,
                    "file_path": str(cur_path),
                    "main": main,
                    "treat": treat,
                    "sample": sample,
                    "spot": spot,
                    "seq": str(seq_i),
                    "power_density": power,
                    "capacitance_peak": round(peak_sign, 6),
                    "integral_charge": round(integral, 8),
                    "pipette_resistance_MOhm": round(resistance * 1e3, 2) if resistance else None,
                }
            )
        except Exception:
            continue

    if dry_run:
        log_payload = {
            "mode": "dry_run",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "folder": str(source_dir),
            "main": main_token,
            "treat": treat_token,
            "move_files": move_files,
            "reindex_seq": reindex_seq,
            "planned_count": len(operations),
            "planned_move_count": sum(1 for op in operations if op["action"] == "move"),
            "planned_rename_count": sum(1 for op in operations if op["action"] == "rename"),
            "operations": operations,
            "warnings": warnings,
        }
        log_path = _write_operation_log(root_dir, log_payload)
        return {
            "dry_run": True,
            "message": f"Dry run planned {len(operations)} filesystem operation(s)",
            "n": 0,
            "rows": [],
            "results": [],
            "plan": operations[:200],
            "planned_count": len(operations),
            "moved_count": log_payload["planned_move_count"],
            "renamed_count": log_payload["planned_rename_count"],
            "warnings": warnings[:100],
            "operation_log_path": log_path,
            "outputs": [{"path": log_path, "type": "json", "role": "operation_log"}],
        }

    if not records:
        log_path = ""
        outputs = []
        if operations or warnings:
            log_path = _write_operation_log(
                root_dir,
                {
                    "mode": "apply",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "folder": str(source_dir),
                    "main": main_token,
                    "treat": treat_token,
                    "move_files": move_files,
                    "reindex_seq": reindex_seq,
                    "moved_count": moved_count,
                    "renamed_count": renamed_count,
                    "processed_count": 0,
                    "operations": operations,
                    "warnings": warnings,
                },
            )
            outputs = [{"path": log_path, "type": "json", "role": "operation_log"}]
        return {
            "message": "No matching files processed",
            "n": 0,
            "rows": [],
            "results": [],
            "moved_count": moved_count,
            "renamed_count": renamed_count,
            "operation_log_path": log_path,
            "outputs": outputs,
            "warnings": warnings[:100],
        }

    df = pd.DataFrame(records)
    out_dir = (
        source_dir / f"{main_token}_{treat_token}" if main_token else source_dir / "batch_output"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"summary_{main_token}_{treat_token}.csv"
    df.to_csv(csv_path, index=False)

    results = []
    for record in records:
        results.append(
            {
                "file": record.get("file", ""),
                "status": "ok",
                "main_val": record.get("main", ""),
                "treat_val": record.get("treat", ""),
                "peak_count": 1,
            }
        )

    log_payload = {
        "mode": "apply",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "folder": str(source_dir),
        "main": main_token,
        "treat": treat_token,
        "move_files": move_files,
        "reindex_seq": reindex_seq,
        "moved_count": moved_count,
        "renamed_count": renamed_count,
        "processed_count": len(records),
        "csv_path": str(csv_path),
        "operations": operations,
        "warnings": warnings,
    }
    log_path = _write_operation_log(root_dir, log_payload)

    return {
        "message": f"Processed {len(records)} files. Saved: {csv_path}",
        "n": len(records),
        "rows": df.head(50).to_dict(orient="records"),
        "results": results,
        "csv_path": str(csv_path),
        "moved_count": moved_count,
        "renamed_count": renamed_count,
        "warnings": warnings[:100],
        "operation_log_path": log_path,
        "outputs": [
            {"path": str(csv_path), "type": "csv", "role": "abf_batch_summary"},
            {"path": log_path, "type": "json", "role": "operation_log"},
        ],
    }
