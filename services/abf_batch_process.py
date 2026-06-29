from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.abf_batch_parsing import _parse_abf_batch_stem, _parse_token_list
from services.abf_batch_signals import (
    _find_all_pulses,
    _normalized_metrics,
    _read_sweep,
    _segment_bounds,
)


def _write_operation_log(root_dir: Path, payload: dict[str, Any]) -> str:
    log_dir = root_dir / ".dataprocess_cache" / "operation_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"abf_batch_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _num(value: Any, default: Any) -> Any:
    return default if value is None else value


def process_payload(
    data: dict[str, Any],
    *,
    pyabf_mod: Any,
    root_dir: Path,
) -> dict[str, Any]:
    """Process a batch of ABF files and return the web response payload."""
    if pyabf_mod is None:
        raise ValueError("pyabf not installed")

    folder = data.get("folder", "")
    main_token = data.get("main", "")
    treat_token = data.get("treat", "")
    main_tokens = _parse_token_list(main_token)
    treat_tokens = _parse_token_list(treat_token)
    main_filter = {token.lower() for token in main_tokens}
    treat_filter = {token.lower() for token in treat_tokens}
    pure_csv = bool(data.get("pure_csv", False))
    powers = []
    try:
        if not pure_csv:
            powers = [float(x) for x in data.get("powers", "").split(",") if x.strip()]
    except ValueError as exc:
        raise ValueError("Power list must be comma-separated numbers") from exc
    i_ch = _num(data.get("i_ch"), 0)
    v_ch = _num(data.get("v_ch"), 1)
    analog_ch = _num(data.get("analog_ch"), 2)
    move_files = bool(data.get("move_files", True))
    reindex_seq = bool(data.get("reindex_seq", False))
    dry_run = bool(data.get("dry_run", False))
    save_segments = bool(data.get("save_segments", True)) or pure_csv
    segment_mode = str(data.get("segment_mode", "auto") or "auto").strip().lower()
    if segment_mode not in {"auto", "manual"}:
        raise ValueError("Segment mode must be 'auto' or 'manual'")
    segment_t0 = _num(data.get("segment_t0"), 0.1)
    segment_t1 = _num(data.get("segment_t1"), 0.7)
    if segment_t0 is None or segment_t1 is None or not segment_t1 > segment_t0:
        raise ValueError("Segment window requires t1 > t0")

    source_dir = Path(folder)
    abf_files = sorted(source_dir.rglob("*.abf"))
    records = []
    warnings = []

    parsed = []
    for abf_path in abf_files:
        parsed_name = _parse_abf_batch_stem(abf_path.stem)
        if parsed_name is None:
            continue
        main = parsed_name["main"]
        treat = parsed_name["treat"]
        if main_filter and main.lower() not in main_filter:
            continue
        if treat_filter and treat.lower() not in treat_filter:
            continue
        parsed.append(
            {
                "path": abf_path,
                **parsed_name,
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
    segment_paths = []

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
            time_s, current, voltage, analog = _read_sweep(
                abf,
                i_ch=i_ch,
                v_ch=v_ch,
                analog_ch=analog_ch,
            )
            pulses = None
            segment_csv = ""
            if save_segments:
                # Legacy Pure CSV Conversion uses the manual t0/t1 window even
                # when the normal batch segment mode is set to Auto.
                effective_segment_mode = "manual" if pure_csv else segment_mode
                t0, t1, pulses = _segment_bounds(
                    time_s,
                    current,
                    voltage,
                    analog,
                    mode=effective_segment_mode,
                    manual_t0=float(segment_t0),
                    manual_t1=float(segment_t1),
                    pulses=pulses,
                )
                if t1 <= t0:
                    warnings.append(f"Segment window out of range for {cur_path.name}")
                    continue
                mask = (time_s >= t0) & (time_s <= t1)
                if not np.any(mask):
                    warnings.append(f"Segment window has no points for {cur_path.name}")
                    continue
                seg_df = pd.DataFrame(
                    {
                        "time_s": time_s[mask],
                        "current_pA": current[mask],
                        "voltage_mV": voltage[mask],
                        "analog": analog[mask],
                    }
                )
                out_csv = cur_path.with_name(f"{cur_path.stem}_segment.csv")
                seg_df.to_csv(out_csv, index=False)
                segment_csv = str(out_csv)
                segment_paths.append(segment_csv)

            if pure_csv:
                records.append(
                    {
                        "file": cur_path.name,
                        "file_path": str(cur_path),
                        "main": main,
                        "treat": treat,
                        "sample": sample,
                        "spot": spot,
                        "seq": str(seq_i),
                        "seq_index": seq_i,
                        "segment_csv": segment_csv,
                    }
                )
                continue

            if pulses is None:
                pulses = _find_all_pulses(analog, voltage)
            cap_n, far_n, integral_n, resistance = _normalized_metrics(current, voltage, pulses)

            power = powers[seq_i] if 0 <= seq_i < len(powers) else None
            pulse_level = pulses[2]
            records.append(
                {
                    "file": cur_path.name,
                    "file_path": str(cur_path),
                    "main": main,
                    "treat": treat,
                    "sample": sample,
                    "spot": spot,
                    "seq": str(seq_i),
                    "seq_index": seq_i,
                    "power_density": power,
                    "power_mW": power,
                    "pulse_level": pulse_level,
                    "capacitance_peak": round(cap_n, 6) if np.isfinite(cap_n) else None,
                    "capacitance_peak_norm": round(cap_n, 6) if np.isfinite(cap_n) else None,
                    "faradaic_current_norm": round(far_n, 6) if np.isfinite(far_n) else None,
                    "integral_charge": round(integral_n, 8) if np.isfinite(integral_n) else None,
                    "integral_charge_norm": round(integral_n, 8) if np.isfinite(integral_n) else None,
                    "pipette_resistance_MOhm": round(resistance, 2) if np.isfinite(resistance) else None,
                    "segment_csv": segment_csv,
                }
            )
        except Exception as exc:
            warnings.append(f"Analysis failed for {cur_path.name}: {exc}")
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
            "pure_csv": pure_csv,
            "planned_count": len(operations),
            "planned_move_count": sum(1 for op in operations if op["action"] == "move"),
            "planned_rename_count": sum(1 for op in operations if op["action"] == "rename"),
            "operations": operations,
            "warnings": warnings,
            "segment_csv_paths": [],
        }
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
            "operation_log_path": "",
            "outputs": [],
        }

    if not records:
        log_path = ""
        outputs = []
        if moved_count or renamed_count:
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
                    "pure_csv": pure_csv,
                    "moved_count": moved_count,
                    "renamed_count": renamed_count,
                    "processed_count": 0,
                    "operations": operations,
                    "warnings": warnings,
                    "segment_csv_paths": segment_paths,
                },
            )
            outputs = [
                *[
                    {"path": path, "type": "csv", "role": "abf_batch_segment"}
                    for path in segment_paths
                ],
                {"path": log_path, "type": "json", "role": "operation_log"},
            ]
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

    if pure_csv:
        results = [
            {
                "file": record.get("file", ""),
                "status": "ok",
                "main_val": record.get("main", ""),
                "treat_val": record.get("treat", ""),
                "peak_count": 0,
                "segment_csv": record.get("segment_csv", ""),
            }
            for record in records
        ]
        log_payload = {
            "mode": "pure_csv",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "folder": str(source_dir),
            "main": main_token,
            "treat": treat_token,
            "move_files": move_files,
            "reindex_seq": reindex_seq,
            "pure_csv": True,
            "moved_count": moved_count,
            "renamed_count": renamed_count,
            "processed_count": len(records),
            "operations": operations,
            "warnings": warnings,
            "segment_csv_paths": segment_paths,
        }
        log_path = ""
        outputs = [
            {"path": path, "type": "csv", "role": "abf_batch_segment"}
            for path in segment_paths
        ]
        if moved_count or renamed_count:
            log_path = _write_operation_log(root_dir, log_payload)
            outputs.append({"path": log_path, "type": "json", "role": "operation_log"})
        return {
            "pure_csv": True,
            "message": f"Pure CSV conversion complete: saved {len(segment_paths)} segment CSV file(s).",
            "n": len(records),
            "rows": records[:50],
            "results": results,
            "segment_csv_paths": segment_paths,
            "moved_count": moved_count,
            "renamed_count": renamed_count,
            "warnings": warnings[:100],
            "operation_log_path": log_path,
            "outputs": outputs,
        }

    df = pd.DataFrame(records)
    summary_paths = []
    for (main, treat), group_df in df.groupby(["main", "treat"], sort=True):
        out_dir = source_dir / f"{main}_{treat}"
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / f"summary_{main}_{treat}.csv"
        group_df.sort_values(
            by=["sample", "spot", "seq_index"], inplace=False, kind="mergesort"
        ).to_csv(summary_path, index=False)
        summary_paths.append(str(summary_path))
    csv_path = Path(summary_paths[0]) if summary_paths else source_dir / "batch_output" / "summary.csv"

    results = []
    for record in records:
        results.append(
            {
                "file": record.get("file", ""),
                "status": "ok",
                "main_val": record.get("main", ""),
                "treat_val": record.get("treat", ""),
                "peak_count": 1,
                "segment_csv": record.get("segment_csv", ""),
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
        "pure_csv": pure_csv,
        "moved_count": moved_count,
        "renamed_count": renamed_count,
        "processed_count": len(records),
        "csv_path": str(csv_path),
        "summary_paths": summary_paths,
        "operations": operations,
        "warnings": warnings,
        "segment_csv_paths": segment_paths,
    }
    log_path = ""
    outputs = [
        *[
            {"path": path, "type": "csv", "role": "abf_batch_summary"}
            for path in summary_paths
        ],
        *[
            {"path": path, "type": "csv", "role": "abf_batch_segment"}
            for path in segment_paths
        ],
    ]
    if moved_count or renamed_count:
        log_path = _write_operation_log(root_dir, log_payload)
        outputs.append({"path": log_path, "type": "json", "role": "operation_log"})

    return {
        "message": f"Processed {len(records)} files. Saved {len(summary_paths)} summary CSV file(s).",
        "n": len(records),
        "rows": df.head(50).to_dict(orient="records"),
        "results": results,
        "csv_path": str(csv_path),
        "summary_paths": summary_paths,
        "moved_count": moved_count,
        "renamed_count": renamed_count,
        "warnings": warnings[:100],
        "operation_log_path": log_path,
        "outputs": outputs,
    }

__all__ = [
    "_write_operation_log",
    "process_payload",
]
