from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.echem_lineshape_average import _apply_axes
from services.echem_lineshape_common import (
    DEFAULT_CROP_T0,
    DEFAULT_CROP_T1,
    _figure_class,
    _float_or,
    _int_or,
    normalize_kind,
)
from services.output_naming import resolve_output_dir, sanitize_name_part


def average_dataframe(avg_data: dict[str, Any], kind: str) -> pd.DataFrame:
    if not isinstance(avg_data, dict):
        raise ValueError("No averaged data to export")
    if avg_data.get("time_s"):
        time_s = np.asarray(avg_data.get("time_s"), dtype=float)
    elif avg_data.get("t_ms"):
        time_s = np.asarray(avg_data.get("t_ms"), dtype=float) / 1000.0
    else:
        raise ValueError("No averaged time data to export")
    y = np.asarray(avg_data.get("y") or [], dtype=float)
    if len(time_s) == 0 or len(y) == 0 or len(time_s) != len(y):
        raise ValueError("Averaged time and signal data are missing or mismatched")
    y_col = "photovoltage_abs_V" if normalize_kind(kind) == "photovoltage" else "photocurrent_mA"
    return pd.DataFrame({"time_s": time_s, y_col: y})


def csv_bytes(avg_data: dict[str, Any], kind: str) -> bytes:
    return average_dataframe(avg_data, kind).to_csv(index=False).encode("utf-8")


def _as_text_list(value: object) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _export_source_manifest_rows(
    data: dict[str, Any],
    *,
    base_name: str,
    kind: str,
    x_min: float,
    x_max: float,
) -> list[dict[str, Any]]:
    source_paths = _as_text_list(data.get("source_paths") or data.get("source_path"))
    source_set = set(source_paths)
    selected_segments = data.get("selected_segments")
    rows: list[dict[str, Any]] = []
    common = {
        "average_stem": base_name,
        "kind": normalize_kind(kind),
        "crop_t0_s": x_min,
        "crop_t1_s": x_max,
        "x_offset_s": _float_or(data.get("x_offset"), 0.0) or 0.0,
        "selected_count": _int_or(data.get("selected_count"), 0),
    }

    if isinstance(selected_segments, list):
        for item in selected_segments:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            segment_file = str(item.get("file") or "").strip()
            if source:
                source_set.add(source)
            rows.append(
                {
                    **common,
                    "role": "averaged_segment",
                    "selected_order": _int_or(item.get("selected_order"), len(rows) + 1),
                    "sample_index": _int_or(item.get("sample_index"), len(rows)),
                    "label": str(item.get("label") or "").strip(),
                    "device": str(item.get("device") or "").strip(),
                    "source_file": source,
                    "segment_file": segment_file,
                }
            )

    for source in sorted(source_set):
        if any(row.get("source_file") == source for row in rows):
            continue
        rows.append(
            {
                **common,
                "role": "source_file",
                "selected_order": "",
                "sample_index": "",
                "label": Path(source).name,
                "device": "",
                "source_file": source,
                "segment_file": "",
            }
        )
    return rows


def source_manifest_dataframe(
    data: dict[str, Any],
    *,
    base_name: str,
    kind: str,
    x_min: float,
    x_max: float,
) -> pd.DataFrame:
    columns = [
        "average_stem",
        "kind",
        "role",
        "selected_order",
        "sample_index",
        "label",
        "device",
        "source_file",
        "segment_file",
        "crop_t0_s",
        "crop_t1_s",
        "x_offset_s",
        "selected_count",
    ]
    rows = _export_source_manifest_rows(
        data,
        base_name=base_name,
        kind=kind,
        x_min=x_min,
        x_max=x_max,
    )
    return pd.DataFrame(rows, columns=columns)


def _project_root_from_source(source_path: object) -> Path | None:
    paths = source_path if isinstance(source_path, list) else [source_path]
    text = str(next((path for path in paths if str(path or "").strip()), "")).strip()
    if not text:
        return None
    source = Path(text).expanduser()
    anchor = source.parent if source.suffix else source
    if anchor.name in {"Photocurrent", "Photovoltage"} and len(anchor.parents) >= 2:
        return anchor.parents[1]
    if anchor.parent.name in {"Photocurrent", "Photovoltage"} and len(anchor.parents) >= 3:
        return anchor.parents[2]
    return source.parent if source.suffix else source


def _resolve_output_dir(base_dir: object, output_dir: object, source_path: object = "") -> Path:
    text = str(output_dir or "").strip()
    root = Path(str(base_dir)).expanduser() if str(base_dir or "").strip() else None
    if root is None:
        root = _project_root_from_source(source_path)
    if text:
        out = Path(text).expanduser()
        if not out.is_absolute() and root is not None:
            out = root / out
        return out
    if root is not None:
        return root / "plots_shape_average"
    return resolve_output_dir(default_suffix="plots_shape_average")


def export_base_name(
    material: object,
    index_k: object,
    kind: str,
    source_path: object = "",
) -> str:
    source_paths = source_path if isinstance(source_path, list) else [source_path]
    sources = [Path(str(path)).expanduser() for path in source_paths if str(path or "").strip()]
    if len(sources) == 1:
        return f"shape_{sanitize_name_part(sources[0].stem or sources[0].name, 'source')}_avg"
    if len(sources) > 1:
        first = sanitize_name_part(sources[0].stem or sources[0].name, "source")
        return f"shape_{first}_plus{len(sources) - 1}_avg"
    mat = sanitize_name_part(material, "material")
    idx = _int_or(index_k, 1)
    return f"shape_{mat}_idx{idx}_{normalize_kind(kind)}_avg"


def export_average_files(data: dict[str, Any]) -> dict[str, Any]:
    Figure = _figure_class()
    kind = normalize_kind(data.get("kind"))
    avg_data = data.get("avg_data") if isinstance(data.get("avg_data"), dict) else {}
    frame = average_dataframe(avg_data, kind)
    x_min = _float_or(data.get("crop_t0"), DEFAULT_CROP_T0) or DEFAULT_CROP_T0
    x_max = _float_or(data.get("crop_t1"), DEFAULT_CROP_T1) or DEFAULT_CROP_T1
    y_min = _float_or(data.get("y_min"), None)
    y_max = _float_or(data.get("y_max"), None)
    dpi = max(72, min(600, _int_or(data.get("dpi"), 300)))
    source_paths = data.get("source_paths") or data.get("source_path")
    out_dir = _resolve_output_dir(data.get("base_dir"), data.get("output_dir"), source_paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = export_base_name(data.get("material"), data.get("index_k"), kind, source_paths)
    csv_path = out_dir / f"{base}.csv"
    png_path = out_dir / f"{base}.png"
    svg_path = out_dir / f"{base}.svg"
    manifest_path = out_dir / f"{base}_source_manifest.csv"
    x = frame["time_s"].to_numpy(dtype=float)
    y = frame.iloc[:, 1].to_numpy(dtype=float)

    fig_png = Figure(figsize=(4.2, 3.0), dpi=dpi)
    ax_png = fig_png.add_subplot(111)
    ax_png.plot(x, y, lw=1.8, color="black")
    _apply_axes(ax_png, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, kind=kind)
    fig_png.tight_layout()
    fig_png.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")

    fig_svg = Figure(figsize=(4.2, 3.0), dpi=dpi)
    ax_svg = fig_svg.add_subplot(111)
    ax_svg.plot(x, y, lw=1.8, color="black")
    ax_svg.set_xlim(x_min, x_max)
    if y_min is not None and y_max is not None and y_max > y_min:
        ax_svg.set_ylim(y_min, y_max)
    ax_svg.set_xticks([])
    ax_svg.set_yticks([])
    for spine in ax_svg.spines.values():
        spine.set_visible(False)
    ax_svg.set_frame_on(False)
    ax_svg.axis("off")
    ax_svg.set_position([0, 0, 1, 1])
    fig_svg.savefig(svg_path, format="svg", bbox_inches="tight", pad_inches=0, transparent=True)

    frame.to_csv(csv_path, index=False)
    source_manifest_dataframe(
        data,
        base_name=base,
        kind=kind,
        x_min=x_min,
        x_max=x_max,
    ).to_csv(manifest_path, index=False)
    outputs = [
        {"path": str(csv_path), "type": "csv", "role": "lineshape_average_csv"},
        {"path": str(png_path), "type": "png", "role": "lineshape_average_plot"},
        {"path": str(svg_path), "type": "svg", "role": "lineshape_average_signal_svg"},
        {
            "path": str(manifest_path),
            "type": "csv",
            "role": "lineshape_average_source_manifest",
        },
    ]
    return {
        "ok": True,
        "output_dir": str(out_dir),
        "csv_path": str(csv_path),
        "png_path": str(png_path),
        "svg_path": str(svg_path),
        "source_manifest_path": str(manifest_path),
        "outputs": outputs,
    }


__all__ = [
    "average_dataframe",
    "csv_bytes",
    "export_average_files",
    "export_base_name",
    "source_manifest_dataframe",
]
