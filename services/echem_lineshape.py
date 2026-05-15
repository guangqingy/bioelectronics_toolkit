from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from services.output_naming import sanitize_name_part

DEFAULT_CHAMBERS = [1, 2, 3]
DEFAULT_CROP_T0 = -0.005
DEFAULT_CROP_T1 = 0.020
DEVICE_DIR_RE = re.compile(r"^(?P<prefix>.*)(?P<ch>\d)_(?P<idx>\d+)$")
FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


def _figure_class():
    import matplotlib as mpl
    from matplotlib.figure import Figure

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    return Figure


def normalize_kind(kind: object) -> str:
    text = str(kind or "photocurrent").strip().lower()
    return "photovoltage" if text in {"photovoltage", "pv", "voltage"} else "photocurrent"


def parse_chambers(value: object, default: list[int] | None = None) -> list[int]:
    if default is None:
        default = DEFAULT_CHAMBERS
    if value in (None, ""):
        return list(default)
    raw = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value))
    out: list[int] = []
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        try:
            out.append(int(float(text)))
        except ValueError:
            continue
    seen: set[int] = set()
    deduped = [x for x in out if not (x in seen or seen.add(x))]
    return deduped or list(default)


def _float_or(value: object, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def _int_or(value: object, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _subfolder(kind: str) -> str:
    return "Photovoltage" if normalize_kind(kind) == "photovoltage" else "Photocurrent"


def _pattern(kind: str) -> str:
    return "*_pulse_*.csv" if normalize_kind(kind) == "photovoltage" else "*_pair_*.csv"


def infer_kind_from_path(path: str | Path, fallback: object = "photocurrent") -> str:
    text = str(path).lower()
    if "photovoltage" in text or "_pulse_" in text:
        return "photovoltage"
    if "photocurrent" in text or "_pair_" in text:
        return "photocurrent"
    return normalize_kind(fallback)


def list_materials(base_dir: str | Path) -> list[str]:
    root = Path(base_dir).expanduser()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {base_dir}")
    materials = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "Photocurrent").is_dir() or (child / "Photovoltage").is_dir():
            materials.append(child.name)
    return materials


def list_device_dirs(
    base_dir: str | Path,
    material: str,
    index_k: object,
    kind: str,
    chambers: object = None,
) -> list[Path]:
    root = Path(base_dir).expanduser() / str(material).strip() / _subfolder(kind)
    if not root.is_dir():
        raise ValueError(f"Directory not found: {root}")
    idx_target = _int_or(index_k, 1)
    chamber_set = set(parse_chambers(chambers))
    out: list[Path] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        match = DEVICE_DIR_RE.match(path.name)
        if not match:
            continue
        ch = _int_or(match.group("ch"), -1)
        idx = _int_or(match.group("idx"), -1)
        if idx == idx_target and ch in chamber_set:
            out.append(path)
    def _device_key(path: Path) -> tuple[int, str]:
        match = DEVICE_DIR_RE.match(path.name)
        return (_int_or(match.group("ch"), 999) if match else 999, path.name)

    return sorted(out, key=_device_key)


def segment_paths_from_source(source_path: str | Path, kind: str) -> tuple[list[Path], Path, Path]:
    source = Path(source_path).expanduser()
    if not source.exists():
        raise ValueError(f"Path not found: {source_path}")
    pattern = _pattern(kind)
    if source.is_dir():
        segment_dir = source
    elif re.search(r"_(pair|pulse)_\d+", source.stem):
        segment_dir = source.parent
    else:
        segment_dir = source.with_suffix("")

    if source.is_file() and re.search(r"_(pair|pulse)_\d+", source.stem):
        csv_paths = sorted(segment_dir.glob(pattern))
    else:
        csv_paths = sorted(segment_dir.rglob(pattern)) if segment_dir.is_dir() else []
    if source.is_file() and source.match(pattern) and source not in csv_paths:
        csv_paths = [source]
    return csv_paths, source, segment_dir


def read_two_column_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    path = Path(path)
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read CSV: {path.name}: {exc}") from exc

    col_time = next(
        (c for c in df.columns if str(c).strip().lower() in {"time_s", "time", "t", "t_s"}),
        None,
    )
    col_value = next(
        (
            c
            for c in df.columns
            if str(c).strip().lower()
            in {"current_ma", "current", "i_ma", "i", "voltage_v", "voltage", "v"}
        ),
        None,
    )
    if col_time is not None and col_value is not None:
        t = pd.to_numeric(df[col_time], errors="coerce").to_numpy()
        y = pd.to_numeric(df[col_value], errors="coerce").to_numpy()
    elif df.shape[1] >= 2:
        t = pd.to_numeric(df.iloc[:, 0], errors="coerce").to_numpy()
        y = pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy()
    else:
        t_values: list[float] = []
        y_values: list[float] = []
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                nums = FLOAT_RE.findall(raw.replace(",", " "))
                if len(nums) >= 2:
                    t_values.append(float(nums[0]))
                    y_values.append(float(nums[1]))
        if not t_values:
            raise RuntimeError(f"No numeric columns detected in {path.name}")
        t = np.asarray(t_values, dtype=float)
        y = np.asarray(y_values, dtype=float)

    mask = np.isfinite(t) & np.isfinite(y)
    t = t[mask]
    y = y[mask]
    if len(t) < 3:
        raise RuntimeError(f"Too few points in {path.name}")
    if np.any(np.diff(t) <= 0):
        order = np.argsort(t)
        t = t[order]
        y = y[order]
    return t, y


def center_trace(t_abs: np.ndarray, y: np.ndarray, kind: str) -> tuple[np.ndarray, np.ndarray]:
    if normalize_kind(kind) == "photovoltage":
        center_idx = int(np.argmin(y))
        y_proc = -np.asarray(y, dtype=float)
    else:
        center_idx = int(np.argmax(y))
        y_proc = np.asarray(y, dtype=float).copy()
    return np.asarray(t_abs, dtype=float) - float(t_abs[center_idx]), y_proc


def short_label(device_dir: Path, csv_path: Path) -> str:
    match = re.search(r"_(pair|pulse)_(\d+)$", csv_path.stem)
    suffix = f"{match.group(1)[0]}{int(match.group(2)):03d}" if match else "seg"
    return f"{device_dir.name} {suffix}"


def y_limits_for_samples(samples: list[dict[str, Any]]) -> list[float] | None:
    values: list[float] = []
    for sample in samples:
        y = np.asarray(sample.get("y") or [], dtype=float)
        if y.size:
            finite = y[np.isfinite(y)]
            if finite.size:
                values.extend([float(np.min(finite)), float(np.max(finite))])
    if not values:
        return None
    ymin = min(values)
    ymax = max(values)
    span = max(1e-12, ymax - ymin)
    pad = 0.05 * span
    return [ymin - pad, ymax + pad]


def samples_payload_from_csvs(
    csv_paths: list[Path],
    *,
    kind: str,
    source_path: Path | None = None,
    segment_dir: Path | None = None,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    for csv_path in csv_paths:
        device_dir = csv_path.parent
        try:
            t, y = read_two_column_csv(csv_path)
            t_rel, y_proc = center_trace(t, y, kind)
        except Exception as exc:
            warnings.append(f"Skipped {csv_path.name}: {exc}")
            continue
        samples.append(
            {
                "label": short_label(device_dir, csv_path),
                "device": device_dir.name,
                "file": str(csv_path),
                "kind": kind,
                "t": t_rel.tolist(),
                "y": y_proc.tolist(),
            }
        )
    if not samples:
        root = segment_dir or (csv_paths[0].parent if csv_paths else None)
        target = root if root is not None else source_path
        raise ValueError(f"No valid segments found under {target}")
    return {
        "samples": samples,
        "n": len(samples),
        "warnings": warnings,
        "y_limits": y_limits_for_samples(samples),
        "x_limits": [DEFAULT_CROP_T0, DEFAULT_CROP_T1],
        "kind": kind,
        "source_path": str(source_path) if source_path else "",
        "segment_dir": str(segment_dir) if segment_dir else "",
    }


def load_samples_from_source(data: dict[str, Any]) -> dict[str, Any]:
    source_text = str(data.get("source_path") or "").strip()
    if not source_text:
        raise ValueError("source_path is required")
    kind = infer_kind_from_path(source_text, data.get("kind"))
    csv_paths, source, segment_dir = segment_paths_from_source(source_text, kind)
    if not csv_paths:
        raise ValueError(f"No {_pattern(kind)} files found under {segment_dir}")
    return samples_payload_from_csvs(
        csv_paths,
        kind=kind,
        source_path=source,
        segment_dir=segment_dir,
    )


def load_samples_payload(data: dict[str, Any]) -> dict[str, Any]:
    if str(data.get("source_path") or "").strip():
        return load_samples_from_source(data)

    base_dir = data.get("base_dir", "")
    material = str(data.get("material") or "").strip()
    if not material:
        raise ValueError("material is required")
    kind = normalize_kind(data.get("kind"))
    device_dirs = list_device_dirs(
        base_dir,
        material,
        data.get("index_k", 1),
        kind,
        data.get("chambers"),
    )
    csv_paths: list[Path] = []
    for device_dir in device_dirs:
        csv_paths.extend(sorted(device_dir.rglob(_pattern(kind))))
    if not csv_paths:
        root = Path(base_dir).expanduser() / material / _subfolder(kind)
        raise ValueError(f"No {_pattern(kind)} files found under {root}")
    payload = samples_payload_from_csvs(csv_paths, kind=kind)
    payload["chambers"] = parse_chambers(data.get("chambers"))
    payload["material"] = material
    payload["index_k"] = _int_or(data.get("index_k", 1), 1)
    return payload


def resample_to_grid(t_rel: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    if len(t_rel) < 2:
        return np.full_like(grid, np.nan, dtype=float)
    return np.interp(grid, t_rel, y, left=np.nan, right=np.nan)


def selected_indexes(selected: object, total: int) -> list[int]:
    if not isinstance(selected, list):
        return []
    out: list[int] = []
    for item in selected:
        idx = _int_or(item, -1)
        if 0 <= idx < total:
            out.append(idx)
    seen: set[int] = set()
    return [idx for idx in out if not (idx in seen or seen.add(idx))]


def compute_average(
    samples: list[dict[str, Any]],
    selected: list[int],
    *,
    x_min: float = DEFAULT_CROP_T0,
    x_max: float = DEFAULT_CROP_T1,
    x_offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    if not selected:
        raise ValueError("No samples selected")
    first = samples[selected[0]]
    t_first = np.asarray(first.get("t") or [], dtype=float)
    dt_est = float(np.median(np.diff(t_first))) if len(t_first) > 2 else 2e-4
    dt = min(max(dt_est, 5e-5), 5e-4)
    grid = np.arange(x_min, x_max + dt / 2, dt, dtype=float)
    rows: list[np.ndarray] = []
    for idx in selected:
        sample = samples[idx]
        t = np.asarray(sample.get("t") or [], dtype=float) + x_offset
        y = np.asarray(sample.get("y") or [], dtype=float)
        if len(t) < 2 or len(y) < 2:
            continue
        rows.append(resample_to_grid(t, y, grid))
    if not rows:
        raise ValueError("No valid selected samples")
    matrix = np.vstack(rows)
    valid = np.any(np.isfinite(matrix), axis=0)
    if not np.any(valid):
        raise ValueError("Selected samples do not overlap the current x range")
    return grid[valid], np.nanmean(matrix[:, valid], axis=0)


def y_label(kind: str) -> str:
    return "Photovoltage |V| (V)" if normalize_kind(kind) == "photovoltage" else "Photocurrent (mA)"


def _apply_axes(
    ax,
    *,
    x_min: float,
    x_max: float,
    y_min: float | None,
    y_max: float | None,
    kind: str,
) -> None:
    ax.set_xlim(x_min, x_max)
    if y_min is not None and y_max is not None and y_max > y_min:
        ax.set_ylim(y_min, y_max)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(y_label(kind))
    ax.grid(True, alpha=0.3, linewidth=0.5)


def average_plot_b64(
    samples: list[dict[str, Any]],
    selected: list[int],
    fig_to_b64: Callable[..., str],
    *,
    kind: str,
    x_min: float,
    x_max: float,
    x_offset: float,
    y_min: float | None,
    y_max: float | None,
) -> tuple[str, dict[str, Any]]:
    Figure = _figure_class()
    grid, avg = compute_average(samples, selected, x_min=x_min, x_max=x_max, x_offset=x_offset)
    fig = Figure(figsize=(4.2, 3.0), dpi=130)
    ax = fig.add_subplot(111)
    ax.plot(grid, avg, lw=1.8, color="black")
    ax.set_title(f"Average (n={len(selected)})", fontsize=10)
    _apply_axes(ax, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, kind=kind)
    fig.tight_layout()
    avg_data = {
        "time_s": grid.tolist(),
        "t_ms": (grid * 1000.0).tolist(),
        "y": avg.tolist(),
        "y_column": (
            "photovoltage_abs_V"
            if normalize_kind(kind) == "photovoltage"
            else "photocurrent_mA"
        ),
    }
    return fig_to_b64(fig), avg_data


def plot_payload(data: dict[str, Any], fig_to_b64: Callable[..., str]) -> dict[str, Any]:
    samples = data.get("samples") if isinstance(data.get("samples"), list) else []
    if not samples:
        raise ValueError("No samples provided")
    kind = normalize_kind(data.get("kind"))
    x_min = _float_or(data.get("crop_t0"), DEFAULT_CROP_T0)
    x_max = _float_or(data.get("crop_t1"), DEFAULT_CROP_T1)
    if x_min is None or x_max is None or x_max <= x_min:
        raise ValueError("X max must be greater than X min")
    selected = selected_indexes(data.get("selected"), len(samples))
    x_offset = _float_or(data.get("x_offset"), 0.0) or 0.0
    y_min = _float_or(data.get("y_min"), None)
    y_max = _float_or(data.get("y_max"), None)
    avg_img, avg_data = average_plot_b64(
        samples,
        selected,
        fig_to_b64,
        kind=kind,
        x_min=x_min,
        x_max=x_max,
        x_offset=x_offset,
        y_min=y_min,
        y_max=y_max,
    )
    return {
        "avg_img": avg_img,
        "avg_data": avg_data,
        "n_selected": len(selected),
        "n_total": len(samples),
        "x_limits": [x_min, x_max],
        "y_limits": (
            [y_min, y_max]
            if y_min is not None and y_max is not None
            else y_limits_for_samples(samples)
        ),
    }


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


def _project_root_from_source(source_path: object) -> Path | None:
    text = str(source_path or "").strip()
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
    return Path.cwd() / "plots_shape_average"


def export_base_name(
    material: object,
    index_k: object,
    kind: str,
    source_path: object = "",
) -> str:
    source_text = str(source_path or "").strip()
    if source_text:
        source = Path(source_text).expanduser()
        return f"shape_{sanitize_name_part(source.stem or source.name, 'source')}_avg"
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
    out_dir = _resolve_output_dir(data.get("base_dir"), data.get("output_dir"), data.get("source_path"))
    out_dir.mkdir(parents=True, exist_ok=True)
    base = export_base_name(data.get("material"), data.get("index_k"), kind, data.get("source_path"))
    csv_path = out_dir / f"{base}.csv"
    png_path = out_dir / f"{base}.png"
    svg_path = out_dir / f"{base}.svg"
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
    outputs = [
        {"path": str(csv_path), "type": "csv", "role": "lineshape_average_csv"},
        {"path": str(png_path), "type": "png", "role": "lineshape_average_plot"},
        {"path": str(svg_path), "type": "svg", "role": "lineshape_average_signal_svg"},
    ]
    return {
        "ok": True,
        "output_dir": str(out_dir),
        "csv_path": str(csv_path),
        "png_path": str(png_path),
        "svg_path": str(svg_path),
        "outputs": outputs,
    }
