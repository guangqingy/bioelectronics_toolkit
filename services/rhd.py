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


def folder_recording_files(path: Path) -> list[Path]:
    """Return all RHD files in the selected file's folder as one recording."""
    return sorted(
        (p for p in path.parent.glob("*.rhd") if p.is_file()),
        key=lambda p: p.name.lower(),
    )


def recording_files_for_path(path: Path, do_merge: bool) -> list[Path]:
    path = Path(path)
    if do_merge:
        files = folder_recording_files(path)
        if files:
            return files
    return [path]


def _sample_rate_from_header(header: dict) -> float:
    return float(
        header.get("sample_rate")
        or header.get("frequency_parameters", {}).get("amplifier_sample_rate")
        or 0.0
    )


def _headers_compatible(a: dict, b: dict) -> bool:
    return (
        abs(float(a.get("sample_rate", 0.0)) - float(b.get("sample_rate", 0.0))) <= 1e-9
        and a.get("channels", []) == b.get("channels", [])
        and a.get("native_channels", []) == b.get("native_channels", [])
    )


def read_rhd_metadata(path: Path, rhd_module) -> dict:
    path = Path(path)
    with path.open("rb") as fid:
        header = rhd_module.read_header(fid)
        data_offset = fid.tell()

    bytes_per_block = int(rhd_module.get_bytes_per_data_block(header))
    if bytes_per_block <= 0:
        raise RuntimeError("Invalid RHD data block size.")

    bytes_remaining = path.stat().st_size - data_offset
    if bytes_remaining < 0 or bytes_remaining % bytes_per_block != 0:
        raise RuntimeError("RHD file size does not contain a whole number of data blocks.")

    num_blocks = int(bytes_remaining // bytes_per_block)
    samples_per_block = int(header.get("num_samples_per_data_block", 0))
    n_samples = int(samples_per_block * num_blocks)
    fs = _sample_rate_from_header(header)
    channels = channel_display_names(header)
    native_channels = channel_native_names(header)

    return {
        "path": str(path),
        "header": header,
        "data_offset": data_offset,
        "bytes_per_block": bytes_per_block,
        "num_data_blocks": num_blocks,
        "samples_per_block": samples_per_block,
        "sample_rate": fs,
        "channels": channels,
        "native_channels": native_channels,
        "n_samples": n_samples,
        "duration_s": n_samples / fs if fs > 0 else 0.0,
    }


def recording_metadata_with_merge_option(path: Path, rhd_module, do_merge: bool) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"RHD file not found: {path}")

    files = recording_files_for_path(path, do_merge)
    metas = [read_rhd_metadata(p, rhd_module) for p in files]
    first = metas[0]
    for meta in metas[1:]:
        if not _headers_compatible(first, meta):
            raise RuntimeError(
                "Folder merge found a different channel layout or sample rate. "
                "Turn off auto merge to preview files individually."
            )

    channels = first["channels"]
    native_channels = first["native_channels"]
    n_samples = int(sum(meta["n_samples"] for meta in metas))
    fs = float(first["sample_rate"])
    channels_meta = [
        {
            "idx": i,
            "name": name,
            "native_name": native_channels[i] if i < len(native_channels) else f"ch{i}",
            "label": name,
            "type": "amplifier",
        }
        for i, name in enumerate(channels)
    ]

    return {
        "channels": channels,
        "channels_meta": channels_meta,
        "sample_rate": fs,
        "sampling_rate": fs,
        "n_samples": n_samples,
        "duration_s": n_samples / fs if fs > 0 else 0.0,
        "duration": round(n_samples / fs, 2) if fs > 0 else 0.0,
        "num_amplifiers": len(channels),
        "merged_pair": bool(do_merge and len(files) > 1),
        "merged_folder": bool(do_merge and len(files) > 1),
        "base_stem": files[0].stem,
        "source_path": str(path),
        "source_paths": [str(p) for p in files],
        "segment_count": len(files),
        "merge_mode": "folder" if do_merge and len(files) > 1 else "single",
    }


def _read_amplifier_channel(path: Path, rhd_module, ch_in):
    path = Path(path)
    with path.open("rb") as fid:
        header = rhd_module.read_header(fid)
        data_offset = fid.tell()

        n_ch = int(header.get("num_amplifier_channels", 0))
        if n_ch <= 0:
            raise RuntimeError("No amplifier channels found in this RHD file.")

        channels = channel_display_names(header)
        native_channels = channel_native_names(header)
        if len(channels) != n_ch:
            channels = [f"ch{i}" for i in range(n_ch)]
        if len(native_channels) != n_ch:
            native_channels = [f"ch{i}" for i in range(n_ch)]

        ch = max(0, min(resolve_channel_index(header, ch_in), n_ch - 1))
        samples_per_block = int(header.get("num_samples_per_data_block", 0))
        bytes_per_block = int(rhd_module.get_bytes_per_data_block(header))
        if samples_per_block <= 0 or bytes_per_block <= 0:
            raise RuntimeError("Invalid RHD data block layout.")

        bytes_remaining = path.stat().st_size - data_offset
        if bytes_remaining < 0 or bytes_remaining % bytes_per_block != 0:
            raise RuntimeError("RHD file size does not contain a whole number of data blocks.")
        n_blocks = int(bytes_remaining // bytes_per_block)
        n_samples = int(n_blocks * samples_per_block)

        y = np.empty(n_samples, dtype=np.float32)
        timestamp_bytes = samples_per_block * 4
        channel_bytes = samples_per_block * 2
        amp_bytes = samples_per_block * n_ch * 2
        rest_bytes = int(bytes_per_block - timestamp_bytes - amp_bytes)
        if rest_bytes < 0:
            raise RuntimeError("RHD data block layout is inconsistent.")

        offset = 0
        for _ in range(n_blocks):
            fid.seek(timestamp_bytes + ch * channel_bytes, 1)
            raw = np.fromfile(fid, dtype="<u2", count=samples_per_block)
            if raw.size != samples_per_block:
                raise RuntimeError("Unexpected end of RHD amplifier data.")
            y[offset : offset + samples_per_block] = (raw.astype(np.int32) - 32768) * 0.195
            offset += samples_per_block
            fid.seek((n_ch - ch - 1) * channel_bytes + rest_bytes, 1)

    fs = _sample_rate_from_header(header)
    return {
        "sample_rate": fs,
        "channels": channels,
        "native_channels": native_channels,
        "channel_index": ch,
        "channel_name": channels[ch] if ch < len(channels) else f"ch{ch}",
        "data": y.astype(float, copy=False),
    }


def load_channel_with_merge_option(path: Path, rhd_module, ch_in, do_merge: bool):
    files = recording_files_for_path(Path(path), do_merge)
    chunks = []
    first = None
    ch_index = 0
    ch_name = "ch0"
    for p in files:
        chunk = _read_amplifier_channel(p, rhd_module, ch_in)
        if first is None:
            first = chunk
            ch_index = int(chunk["channel_index"])
            ch_name = str(chunk["channel_name"])
        elif not _headers_compatible(first, chunk):
            raise RuntimeError(
                "Folder merge found a different channel layout or sample rate. "
                "Turn off auto merge to preview files individually."
            )
        chunks.append(np.asarray(chunk["data"], dtype=float))

    if first is None:
        raise RuntimeError("No RHD files found.")

    y = np.concatenate(chunks) if chunks else np.empty(0, dtype=float)
    fs = float(first["sample_rate"])
    t = np.arange(y.size, dtype=float) / (fs if fs > 0 else 1.0)
    return (
        t,
        fs,
        list(first["channels"]),
        y,
        ch_index,
        ch_name,
        files[0].stem,
        bool(do_merge and len(files) > 1),
        len(files),
    )


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
