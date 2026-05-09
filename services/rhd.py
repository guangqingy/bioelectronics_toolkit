from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def channel_display_names(result: dict) -> list[str]:
    names: list[str] = []
    for i, ch in enumerate(result.get("amplifier_channels", [])):
        names.append(str(ch.get("custom_channel_name") or f"ch{i}"))
    return names


def channel_native_names(result: dict) -> list[str]:
    names: list[str] = []
    for i, ch in enumerate(result.get("amplifier_channels", [])):
        names.append(str(ch.get("native_channel_name") or f"ch{i}"))
    return names


def resolve_channel_index(result: dict, ch_in, default: int = 0) -> int:
    if isinstance(ch_in, str) and not ch_in.isdigit():
        display_names = channel_display_names(result)
        if ch_in in display_names:
            return display_names.index(ch_in)
        native_names = channel_native_names(result)
        if ch_in in native_names:
            return native_names.index(ch_in)
        return default
    try:
        return int(ch_in)
    except (TypeError, ValueError):
        return default


def find_split_partner(path: Path) -> tuple[Path | None, Path | None]:
    stem = path.stem
    if len(stem) < 4:
        return None, None
    last4 = stem[-4:]
    if not last4.isdigit():
        return None, None
    cur_val = int(last4)

    candidates = []
    for delta in (-100, 100):
        target = cur_val + delta
        if 0 <= target <= 9999:
            cand_path = path.with_name(stem[:-4] + f"{target:04d}" + path.suffix)
            if cand_path.exists():
                candidates.append(cand_path)

    filtered = [q for q in candidates if len(q.stem) == len(stem) and q.stem[:-4] == stem[:-4]]
    if not filtered:
        return None, None

    partner = filtered[0]
    cur_last4 = int(stem[-4:])
    partner_last4 = int(partner.stem[-4:])
    if abs(partner_last4 - cur_last4) != 100:
        return None, None

    earlier = path if cur_last4 < partner_last4 else partner
    later = partner if cur_last4 < partner_last4 else path
    return earlier, later


def load_rhd_arrays(path: Path, rhd_module):
    result, _ = rhd_module.load_file(str(path))
    fs = float(result.get("frequency_parameters", {}).get("amplifier_sample_rate", 0.0) or 0.0)
    amp = np.asarray(result.get("amplifier_data", np.empty((0, 0))), dtype=float)
    if amp.ndim != 2:
        raise RuntimeError("Amplifier data shape mismatch.")

    t_raw = result.get("t_amplifier", None)
    if t_raw is not None:
        t = np.asarray(t_raw, dtype=float)
        if t.ndim != 1 or t.size != amp.shape[1]:
            t = np.arange(amp.shape[1], dtype=float) / (fs if fs > 0 else 1.0)
    else:
        t = np.arange(amp.shape[1], dtype=float) / (fs if fs > 0 else 1.0)

    ch_names = channel_display_names(result)
    if len(ch_names) != amp.shape[0]:
        ch_names = [f"ch{i}" for i in range(amp.shape[0])]

    return t, fs, ch_names, amp, result


def load_merged_if_pair(path: Path, rhd_module):
    earlier, later = find_split_partner(path)
    if earlier is None or later is None:
        t, fs, ch, amp, _ = load_rhd_arrays(path, rhd_module)
        return t, fs, ch, amp, path.stem, False

    t1, fs1, ch1, amp1, _ = load_rhd_arrays(earlier, rhd_module)
    t2, fs2, ch2, amp2, _ = load_rhd_arrays(later, rhd_module)
    if abs(fs1 - fs2) > 1e-9 or len(ch1) != len(ch2) or any(x != y for x, y in zip(ch1, ch2)):
        t, fs, ch, amp, _ = load_rhd_arrays(path, rhd_module)
        return t, fs, ch, amp, path.stem, False

    if fs1 > 0:
        dt = 1.0 / fs1
    elif t1.size > 1:
        dt = float(t1[1] - t1[0])
    else:
        dt = 0.0

    offset = float(t1[-1]) + dt - float(t2[0]) if t1.size > 0 and t2.size > 0 else 0.0
    return (
        np.concatenate([t1, t2 + offset], axis=0),
        fs1,
        ch1,
        np.concatenate([amp1, amp2], axis=1),
        earlier.stem,
        True,
    )


def load_with_merge_option(path: Path, rhd_module, do_merge: bool):
    if do_merge:
        return load_merged_if_pair(path, rhd_module)
    t, fs, ch, amp, _ = load_rhd_arrays(path, rhd_module)
    return t, fs, ch, amp, path.stem, False


def all_channels_wide_frame(
    time_s: np.ndarray,
    ch_names: list[str],
    amp: np.ndarray,
) -> pd.DataFrame:
    out = {"time": np.asarray(time_s, dtype=float)}
    for i, name in enumerate(ch_names):
        out[str(name)] = np.asarray(amp[i, :], dtype=float)
    return pd.DataFrame(out)
