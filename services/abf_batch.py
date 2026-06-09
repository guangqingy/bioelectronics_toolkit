from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

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


def _read_sweep(abf: Any, *, i_ch: int, v_ch: int, analog_ch: int) -> tuple[np.ndarray, ...]:
    sweep = list(getattr(abf, "sweepList", [0]))[0]
    abf.setSweep(sweep, channel=i_ch)
    time_s = abf.sweepX.copy()
    current = abf.sweepY.copy()
    abf.setSweep(sweep, channel=v_ch)
    voltage = abf.sweepY.copy()
    try:
        abf.setSweep(sweep, channel=analog_ch)
        analog = abf.sweepY.copy()
    except Exception:
        analog = np.zeros_like(current)
    return time_s, current, voltage, analog


def _find_all_pulses(analog: np.ndarray, voltage: np.ndarray) -> tuple[int, int, float, int, int]:
    pu_a: list[int] = []
    pd_a: list[int] = []
    analog_levels: list[float] = []

    tmp = 0
    direction_up = True
    for idx, _value in enumerate(analog):
        if direction_up and analog[idx] - analog[tmp] > 0.1:
            pu_a.append(idx)
            analog_levels.append(float(analog[idx]))
            tmp = idx
            direction_up = False
        elif not direction_up and analog[idx] - analog[tmp] > 0.1:
            tmp = idx
            if analog_levels:
                analog_levels.pop()
            analog_levels.append(float(analog[idx]))
        elif not direction_up and analog[idx] - analog[tmp] < -0.1:
            pd_a.append(idx)
            tmp = idx
            direction_up = True
        elif direction_up and analog[idx] - analog[tmp] < -0.1:
            tmp = idx

    pu_v: list[int] = []
    pd_v: list[int] = []
    tmp = 0
    direction_up = False
    for idx, _value in enumerate(voltage[:-10]):
        previous = float(np.mean(voltage[tmp : tmp + 10]))
        current = float(np.mean(voltage[idx : idx + 10]))
        if direction_up and current - previous > 0.4:
            pu_v.append(idx)
            tmp = idx
            direction_up = False
        elif not direction_up and current - previous > 0.4:
            tmp = idx
        elif not direction_up and current - previous < -0.4:
            pd_v.append(idx)
            tmp = idx
            direction_up = True
        elif direction_up and current - previous < -0.4:
            tmp = idx

    if len(pu_v) < 1:
        raise RuntimeError("Voltage pulse not found; check V trace.")
    if len(pu_a) < 1:
        return -1, pu_v[0] + 1, -1.0, pu_v[0], pd_v[0] if pd_v else pu_v[0] + 1

    return (
        pu_a[0],
        pd_a[0] if pd_a else pu_a[0] + 1,
        analog_levels[0] if analog_levels else float("nan"),
        pu_v[0],
        pd_v[0] if pd_v else pu_v[0] + 1,
    )


def _resistance_mohm(current: np.ndarray, voltage: np.ndarray, pulse_index: int) -> float:
    start = max(0, pulse_index - 1000)
    end = min(len(voltage) - 1, pulse_index + 1000)
    n_points = 500

    i1 = np.sum(current[start : start + n_points])
    i2 = np.sum(current[end : end + n_points])
    v1 = np.sum(voltage[start : start + n_points])
    v2 = np.sum(voltage[end : end + n_points])

    i_delta = abs(i2 - i1) * 1e-12
    v_delta = abs(v2 - v1) * 1e-3
    if i_delta <= 0:
        return float("nan")
    return float((v_delta / i_delta) / 1e6)


def _current_metrics(current: np.ndarray, pu_a: int, pd_a: int) -> tuple[float, float, float]:
    if pu_a == -1:
        return 0.0, 0.0, 0.0

    samples_per_10_ms = int(10 / 0.01)
    lo = max(0, pu_a - 2 * samples_per_10_ms)
    hi = max(0, pu_a - samples_per_10_ms)
    avg_init = float(np.mean(current[lo:hi]))

    segment = current[pu_a:pd_a]
    max_i = float(np.max(segment))
    min_i = float(np.min(segment))
    pos_peak = max_i - avg_init
    neg_peak = min_i - avg_init
    capacitive = pos_peak if abs(pos_peak) > abs(neg_peak) else neg_peak

    far_raw = float(np.mean(current[pu_a + int(8 / 0.01) : pu_a + int(9 / 0.01)]))
    faradaic = far_raw - avg_init
    integral_pc = float(np.sum(segment - avg_init) * 0.01 / 1000.0)
    return capacitive, faradaic, integral_pc


def _normalized_metrics(
    current: np.ndarray,
    voltage: np.ndarray,
    pulses: tuple[int, int, float, int, int],
) -> tuple[float, float, float, float]:
    pu_a, pd_a, _level, pu_v, _pd_v = pulses
    resistance = _resistance_mohm(current, voltage, pu_v)
    cap, far, integral = _current_metrics(current, pu_a, pd_a)
    if not np.isfinite(resistance):
        return float("nan"), float("nan"), float("nan"), resistance
    return cap * resistance, far * resistance, integral * resistance, resistance


def _segment_bounds(
    time_s: np.ndarray,
    current: np.ndarray,
    voltage: np.ndarray,
    analog: np.ndarray,
    *,
    mode: str,
    manual_t0: float,
    manual_t1: float,
    pulses: tuple[int, int, float, int, int] | None,
) -> tuple[float, float, tuple[int, int, float, int, int] | None]:
    if mode == "manual":
        return (
            max(float(time_s[0]), manual_t0),
            min(float(time_s[-1]), manual_t1),
            pulses,
        )

    if pulses is None:
        pulses = _find_all_pulses(analog, voltage)
    pu_a, pd_a, _level, pu_v, pd_v = pulses
    start_idx = pu_a if pu_a != -1 else pu_v
    end_idx = pd_a if pd_a != -1 else (pd_v if pd_v is not None else start_idx + 1)
    start_idx = max(0, min(start_idx, len(current) - 1))
    end_idx = max(start_idx + 1, min(end_idx, len(current)))

    segment = current[start_idx:end_idx]
    peak_idx = int(np.argmax(np.abs(current))) if segment.size == 0 else start_idx + int(np.argmax(np.abs(segment)))
    peak_time = float(time_s[peak_idx])
    return max(float(time_s[0]), peak_time - 0.1), min(float(time_s[-1]), peak_time + 0.1), pulses


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
    main_tokens = _parse_token_list(main_token)
    treat_tokens = _parse_token_list(treat_token)
    main_filter = {token.lower() for token in main_tokens}
    treat_filter = {token.lower() for token in treat_tokens}
    try:
        powers = [float(x) for x in data.get("powers", "").split(",") if x.strip()]
    except ValueError as exc:
        raise ValueError("Power list must be comma-separated numbers") from exc
    i_ch = int_or(data.get("i_ch", 0), 0)
    v_ch = int_or(data.get("v_ch", 1), 1)
    analog_ch = int_or(data.get("analog_ch", 2), 2)
    move_files = bool(data.get("move_files", True))
    reindex_seq = bool(data.get("reindex_seq", False))
    dry_run = bool(data.get("dry_run", False))
    save_segments = bool(data.get("save_segments", True))
    segment_mode = str(data.get("segment_mode", "auto") or "auto").strip().lower()
    if segment_mode not in {"auto", "manual"}:
        raise ValueError("Segment mode must be 'auto' or 'manual'")
    segment_t0 = float_or(data.get("segment_t0", 0.1), 0.1)
    segment_t1 = float_or(data.get("segment_t1", 0.7), 0.7)
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
                t0, t1, pulses = _segment_bounds(
                    time_s,
                    current,
                    voltage,
                    analog,
                    mode=segment_mode,
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
            "planned_count": len(operations),
            "planned_move_count": sum(1 for op in operations if op["action"] == "move"),
            "planned_rename_count": sum(1 for op in operations if op["action"] == "rename"),
            "operations": operations,
            "warnings": warnings,
            "segment_csv_paths": [],
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
        "moved_count": moved_count,
        "renamed_count": renamed_count,
        "processed_count": len(records),
        "csv_path": str(csv_path),
        "summary_paths": summary_paths,
        "operations": operations,
        "warnings": warnings,
        "segment_csv_paths": segment_paths,
    }
    log_path = _write_operation_log(root_dir, log_payload)

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
        "outputs": [
            *[
                {"path": path, "type": "csv", "role": "abf_batch_summary"}
                for path in summary_paths
            ],
            *[
                {"path": path, "type": "csv", "role": "abf_batch_segment"}
                for path in segment_paths
            ],
            {"path": log_path, "type": "json", "role": "operation_log"},
        ],
    }
