from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def sanitize_name(value: object) -> str:
    text = str(value or "")
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in text)


def source_path(payload: dict) -> Path | None:
    path = payload.get("path")
    if path:
        return Path(path)
    folder = payload.get("folder", "")
    subfolder = payload.get("subfolder", "")
    channel = payload.get("channel", "")
    if folder and subfolder and channel:
        return Path(folder) / subfolder / channel
    return None


def channel_label_from_source(src: Path) -> str:
    parent = src.parent.name
    stem = src.stem
    if stem.startswith(parent + "_"):
        label = stem[len(parent) + 1 :]
    else:
        label = stem
    return sanitize_name(label) or "channel"


def pick_columns(df: pd.DataFrame) -> tuple[str, str]:
    t_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
    v_col = next(
        (
            c
            for c in df.columns
            if any(k in c.lower() for k in ["value", "uv", "\u00b5v", "amp"])
        ),
        df.columns[1],
    )
    return t_col, v_col


def numeric_signal(df: pd.DataFrame, t_col: str, v_col: str):
    t_raw = pd.to_numeric(df[t_col], errors="coerce").to_numpy()
    v_raw = pd.to_numeric(df[v_col], errors="coerce").to_numpy()
    valid = np.isfinite(t_raw) & np.isfinite(v_raw)
    if np.count_nonzero(valid) < 3:
        raise ValueError("Not enough numeric rows in source CSV")
    return t_raw, v_raw, valid


def ms_to_samples(ms: float, fs: float) -> int:
    return int(round(float(ms) * 1e-3 * float(fs)))


def robust_noise_std(x: np.ndarray) -> float:
    median = np.median(x)
    mad = np.median(np.abs(x - median))
    return float(1.4826 * mad)


def infer_sampling_rate(t: np.ndarray) -> float:
    dt = np.diff(t)
    dt = dt[np.isfinite(dt) & (dt > 0)]
    if dt.size == 0:
        raise ValueError("Cannot infer sampling rate from time column")
    return float(1.0 / np.median(dt))


def build_peak_kwargs(
    sig: np.ndarray,
    fs: float,
    min_peak_distance_ms: float,
    min_width_ms: float | None,
    wlen_ms: float | None,
    min_prominence_uV: float | None,
    min_height_uV: float | None,
    use_adaptive_sigma: bool,
    sigma_for_prom: float | None,
    sigma_for_height: float | None,
) -> dict:
    kwargs = {"distance": max(1, ms_to_samples(min_peak_distance_ms, fs))}

    if min_width_ms is not None and min_width_ms > 0:
        kwargs["width"] = max(1, ms_to_samples(min_width_ms, fs))

    if wlen_ms is not None and wlen_ms > 0:
        wlen = ms_to_samples(wlen_ms, fs)
        if 3 <= wlen < sig.size:
            kwargs["wlen"] = wlen

    prom_thr = (
        None
        if (min_prominence_uV is None or min_prominence_uV <= 0)
        else float(min_prominence_uV)
    )
    height_thr = None if min_height_uV is None else float(min_height_uV)

    if use_adaptive_sigma:
        sigma = robust_noise_std(sig)
        median = float(np.median(sig))
        prom_adapt = (
            (sigma_for_prom or 0) * sigma
            if (sigma_for_prom and sigma_for_prom > 0)
            else None
        )
        height_adapt = (
            median + (sigma_for_height or 0) * sigma
            if (sigma_for_height and sigma_for_height > 0)
            else None
        )
        if prom_adapt is not None:
            prom_thr = prom_adapt if prom_thr is None else max(prom_thr, prom_adapt)
        if height_adapt is not None:
            height_thr = height_adapt if height_thr is None else max(height_thr, height_adapt)

    if prom_thr is not None:
        kwargs["prominence"] = prom_thr
    if height_thr is not None:
        kwargs["height"] = height_thr
    return kwargs


def detect_with_polarity(sig, fs, params, polarity, find_peaks_func, peak_widths_func):
    pos_idx = np.array([], dtype=int)
    pos_w_ms = np.array([], dtype=float)
    neg_idx = np.array([], dtype=int)
    neg_w_ms = np.array([], dtype=float)

    if polarity in ("positive", "both"):
        kw_pos = build_peak_kwargs(sig, fs, **params)
        pos_idx, _ = find_peaks_func(sig, **kw_pos)
        if pos_idx.size:
            widths, _, _, _ = peak_widths_func(sig, pos_idx, rel_height=0.5)
            pos_w_ms = (widths / fs) * 1e3

    if polarity in ("negative", "both"):
        inv = -sig
        kw_neg = build_peak_kwargs(inv, fs, **params)
        neg_idx, _ = find_peaks_func(inv, **kw_neg)
        if neg_idx.size:
            widths, _, _, _ = peak_widths_func(inv, neg_idx, rel_height=0.5)
            neg_w_ms = (widths / fs) * 1e3

    if polarity != "both":
        idx = pos_idx if polarity == "positive" else neg_idx
        widths = pos_w_ms if polarity == "positive" else neg_w_ms
        signs = (
            np.ones_like(idx, dtype=int)
            if polarity == "positive"
            else -np.ones_like(idx, dtype=int)
        )
        return idx, widths, signs

    all_idx = np.concatenate([pos_idx, neg_idx])
    all_signs = np.concatenate(
        [np.ones_like(pos_idx, dtype=int), -np.ones_like(neg_idx, dtype=int)]
    )
    all_widths = np.concatenate([pos_w_ms, neg_w_ms])
    if all_idx.size == 0:
        return all_idx, all_widths, all_signs

    order = np.argsort(all_idx)
    all_idx = all_idx[order]
    all_signs = all_signs[order]
    all_widths = all_widths[order]

    keep = np.ones(all_idx.size, dtype=bool)
    min_dist = ms_to_samples(params["min_peak_distance_ms"], fs)
    for i in range(all_idx.size):
        if not keep[i]:
            continue
        j = i + 1
        while j < all_idx.size and (all_idx[j] - all_idx[i]) < min_dist:
            if abs(sig[all_idx[j]]) > abs(sig[all_idx[i]]):
                keep[i] = False
                break
            keep[j] = False
            j += 1

    return all_idx[keep], all_widths[keep], all_signs[keep]
