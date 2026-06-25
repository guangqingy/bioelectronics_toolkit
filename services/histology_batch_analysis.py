from __future__ import annotations

import base64
import csv
import json
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np

from services.histology_analysis import ANALYSIS_VERSION, _now_iso, _write_json
from services.histology_data_project import _data_project_dir
from services.matplotlib_utils import close_figure, new_subplots

def _batch_timestamp_slug() -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", _now_iso()) or "now"


def _new_project_batch_dir(project_path: Path) -> Path:
    root = _data_project_dir(project_path) / "project_analysis"
    stem = f"saved_roi_batch_{_batch_timestamp_slug()}"
    out_dir = root / stem
    suffix = 2
    while out_dir.exists():
        out_dir = root / f"{stem}_{suffix}"
        suffix += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _scalar_for_table(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    return out if np.isfinite(out) else float(default)


def _extract_sample_group(entry: dict[str, Any], fallback_index: int) -> tuple[str, float]:
    candidates = [
        entry.get("sample_id"),
        entry.get("case_name"),
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("source_name"),
    ]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        leading = re.match(r"^\s*([0-9]+)(?:\b|[-_\s])", text)
        if leading:
            value = leading.group(1)
            return value, float(int(value))
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.search(r"(?:^|[^0-9])([0-9]+)(?:[^0-9]|$)", text)
        if match:
            value = match.group(1)
            return value, float(int(value))
    value = str(fallback_index + 1)
    return value, float(fallback_index + 1)


def _extract_source_letter(entry: dict[str, Any], row: dict[str, Any], roi_index: int) -> str:
    source_map = {
        "CB": "A",
        "DE": "B",
        "HY": "C",
        "PC": "D",
        "SB": "E",
        "SH": "F",
    }
    for raw in (
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("sample_id"),
        entry.get("case_name"),
    ):
        text = str(raw or "").strip()
        match = re.match(r"^\s*[0-9]+\s*[-_\s]+\s*([A-Za-z]+)", text)
        if match:
            token = match.group(1).upper()
            if token in source_map:
                return source_map[token]
    candidates = [
        row.get("roi_label"),
        row.get("roi_id"),
        entry.get("display_name"),
        entry.get("image_name"),
        entry.get("sample_id"),
        entry.get("case_name"),
    ]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.search(r"(?:^|[^A-Za-z])([A-Z])(?:[^A-Za-z]|$)", text.upper())
        if match:
            return match.group(1)
    return chr(ord("A") + (int(roi_index) % 26))


TREATMENT_SORT_ORDER = {
    "DE": 1.0,
    "PC": 2.0,
    "SB": 3.0,
    "CB": 4.0,
    "HY": 5.0,
    "SH": 6.0,
}


def _canonical_treatment_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    key = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    aliases = {
        "DE": "DE",
        "D": "DE",
        "PC": "PC",
        "SB": "SB",
        "CB": "CB",
        "HY": "HY",
        "H": "HY",
        "SH": "SH",
    }
    return aliases.get(key, key)


def _parse_image_sample_and_treatment(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    match = re.match(r"^\s*([0-9]+)\s*[-_\s]+\s*([A-Za-z]+)", text)
    if not match:
        return "", ""
    return match.group(1), _canonical_treatment_label(match.group(2))


def _apply_batch_grouping(rows: list[dict[str, Any]], group_by: str) -> None:
    if str(group_by or "").strip().lower() not in {"treatment", "material"}:
        return
    for row in rows:
        sample_number = ""
        treatment = ""
        for key in ("image_name", "display_name", "sample_id", "case_name"):
            sample_number, treatment = _parse_image_sample_and_treatment(row.get(key))
            if treatment:
                break
        if not treatment:
            continue
        row["sample_number"] = sample_number
        row["treatment"] = treatment
        row["sample_group"] = treatment
        row["sample_group_sort"] = TREATMENT_SORT_ORDER.get(treatment.upper(), 999.0)
        if sample_number:
            row["letter"] = sample_number
            row["source_label"] = sample_number


def _apply_marker_inclusion(rows: list[dict[str, Any]], params: dict[str, Any]) -> None:
    exclude_zero = _boolish(
        params.get("exclude_zero_observations", params.get("skip_zero_observations", False)),
        default=False,
    )
    for row in rows:
        for marker in ("sma", "macrophage"):
            value = _finite_float(row.get(_metric_column(marker)))
            include = np.isfinite(value) and (not exclude_zero or value > 0)
            row[f"{marker}_include"] = bool(include)


def _group_sort_key(value: Any) -> tuple[int, float, str]:
    text = str(value or "").strip()
    treatment = _canonical_treatment_label(text)
    if treatment in TREATMENT_SORT_ORDER:
        return (0, TREATMENT_SORT_ORDER[treatment], treatment)
    try:
        return (0, float(text), text)
    except Exception:
        return (1, 0.0, text.lower())


def _metric_column(marker: str) -> str:
    return f"{marker}_positive_area_ratio"


def _normalized_metric_column(marker: str) -> str:
    return f"{marker}_positive_area_ratio_normalized"


def _boolish(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _row_metric_value(row: dict[str, Any], marker: str) -> float:
    for key in (f"{marker}_positive_fraction", f"{marker}_positive_fraction_roi"):
        if key in row:
            return _finite_float(row.get(key))
    return 0.0


def _flatten_batch_row(
    project_path: Path,
    entry: dict[str, Any],
    analysis: dict[str, Any],
    result_row: dict[str, Any],
    entry_index: int,
    roi_index: int,
) -> dict[str, Any]:
    group, group_sort = _extract_sample_group(entry, entry_index)
    letter = _extract_source_letter(entry, result_row, roi_index)
    row: dict[str, Any] = {
        "project_path": str(project_path),
        "entry_id": str(entry.get("entry_id") or analysis.get("entry_id") or ""),
        "entry_index": entry_index + 1,
        "image_name": str(entry.get("image_name") or analysis.get("image_name") or ""),
        "display_name": str(entry.get("display_name") or analysis.get("display_name") or ""),
        "sample_id": str(entry.get("sample_id") or analysis.get("sample_id") or ""),
        "case_name": str(entry.get("case_name") or analysis.get("case_name") or ""),
        "sample_group": group,
        "sample_group_sort": group_sort,
        "letter": letter,
        "source_label": letter,
        "roi_index": roi_index + 1,
        "created_at": str(analysis.get("created_at") or ""),
        "backend": str(analysis.get("backend") or ""),
        "image_width": int(analysis.get("width") or 0),
        "image_height": int(analysis.get("height") or 0),
        "analysis_path": str(entry.get("analysis_path") or ""),
        "geojson_path": str(entry.get("geojson_path") or ""),
    }
    for key, value in result_row.items():
        row[key] = _scalar_for_table(value)
    for marker in ("sma", "macrophage"):
        row[_metric_column(marker)] = _row_metric_value(result_row, marker)
    return row


def _roi_parameter_overrides(params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("roi_parameter_overrides") or params.get("roi_parameters_by_roi")
    return raw if isinstance(raw, dict) else {}


def _roi_parameter_override_candidates(entry_id: str, roi: dict[str, Any], roi_index: int) -> list[str]:
    roi_id = str(roi.get("id") or "").strip()
    roi_label = str(roi.get("label") or "").strip()
    one_based_index = str(int(roi_index) + 1)
    candidates = [
        f"{entry_id}::roi_index::{one_based_index}",
        f"{entry_id}::{one_based_index}",
    ]
    if roi_id:
        candidates.insert(0, f"{entry_id}::roi_id::{roi_id}")
        candidates.insert(1, f"{entry_id}::{roi_id}")
    if roi_label:
        candidates.append(f"{entry_id}::roi_label::{roi_label}")
        candidates.append(f"{entry_id}::{roi_label}")
    return candidates


def _params_for_roi_parameter_override(
    params: dict[str, Any],
    entry_id: str,
    roi: dict[str, Any],
    roi_index: int,
    normalize_params: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    overrides = _roi_parameter_overrides(params)
    for key in _roi_parameter_override_candidates(entry_id, roi, roi_index):
        raw = overrides.get(key)
        if not isinstance(raw, dict):
            continue
        merged = dict(params)
        merged.pop("roi_parameter_overrides", None)
        merged.pop("roi_parameters_by_roi", None)
        clean_override = dict(raw)
        clean_override.pop("roi_parameter_overrides", None)
        clean_override.pop("roi_parameters_by_roi", None)
        merged.update(clean_override)
        return normalize_params(merged) if normalize_params else merged, key
    return params, ""


def _numeric_mean(values: list[Any]) -> float | None:
    finite: list[float] = []
    for value in values:
        if value in ("", None):
            continue
        try:
            number = float(value)
        except Exception:
            return None
        if np.isfinite(number):
            finite.append(number)
    if not finite:
        return None
    return float(np.mean(finite))


def _aggregate_roi_rows_by_entry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.get("entry_id") or row.get("image_name") or len(order))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)
    out: list[dict[str, Any]] = []
    metadata_keys = {
        "project_path",
        "entry_id",
        "entry_index",
        "image_name",
        "display_name",
        "sample_id",
        "case_name",
        "sample_group",
        "sample_group_sort",
        "letter",
        "source_label",
        "created_at",
        "backend",
        "image_width",
        "image_height",
        "analysis_path",
        "geojson_path",
    }
    for key in order:
        group_rows = grouped[key]
        first = group_rows[0]
        record = {field: first.get(field, "") for field in metadata_keys}
        record["observation_level"] = "image"
        record["roi_count"] = len(group_rows)
        record["n_roi"] = len(group_rows)
        record["roi_id"] = ";".join(str(row.get("roi_id") or "") for row in group_rows)
        record["roi_label"] = f"{len(group_rows)} ROI mean"
        record["roi_labels"] = ";".join(str(row.get("roi_label") or "") for row in group_rows)
        keys = sorted({key for row in group_rows for key in row.keys() if key not in metadata_keys})
        for field in keys:
            if field in {"roi_id", "roi_label", "roi_labels", "roi_index", "observation_level", "source_label"}:
                continue
            mean = _numeric_mean([row.get(field) for row in group_rows])
            if mean is not None:
                record[field] = mean
        for marker in ("sma", "macrophage"):
            metric = _metric_column(marker)
            record[metric] = float(np.mean([_finite_float(row.get(metric)) for row in group_rows]))
        out.append(record)
    return out


def _normalize_batch_rows(
    rows: list[dict[str, Any]],
    normalize_to_group: str,
) -> dict[str, Any]:
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    baseline_group = str(normalize_to_group or "").strip() or "1"
    if baseline_group not in groups and groups:
        baseline_group = groups[0]
    baselines: dict[str, float] = {}
    warnings: list[str] = []
    for marker in ("sma", "macrophage"):
        values = [
            _finite_float(row.get(_metric_column(marker)))
            for row in rows
            if str(row.get("sample_group") or "") == baseline_group
        ]
        finite = [value for value in values if np.isfinite(value)]
        baseline = float(np.mean(finite)) if finite else 0.0
        if baseline <= 0:
            warnings.append(
                f"{marker.upper()} baseline group {baseline_group} has no positive area; normalized values use 1.0 as denominator."
            )
            baseline = 1.0
        baselines[marker] = baseline
        for row in rows:
            row[_normalized_metric_column(marker)] = _finite_float(row.get(_metric_column(marker))) / baseline
    return {
        "normalize_to_group": baseline_group,
        "baseline_values": baselines,
        "warnings": warnings,
    }


def _apply_normalization_to_rows(rows: list[dict[str, Any]], normalization: dict[str, Any]) -> None:
    baselines = normalization.get("baseline_values") if isinstance(normalization, dict) else {}
    if not isinstance(baselines, dict):
        baselines = {}
    for marker in ("sma", "macrophage"):
        baseline = _finite_float(baselines.get(marker), default=1.0)
        if baseline <= 0:
            baseline = 1.0
        for row in rows:
            row[_normalized_metric_column(marker)] = _finite_float(row.get(_metric_column(marker))) / baseline


def _mean_sd_sem(values: list[float]) -> tuple[float, float, float]:
    finite = np.asarray([value for value in values if np.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return 0.0, 0.0, 0.0
    mean = float(np.mean(finite))
    if finite.size <= 1:
        return mean, 0.0, 0.0
    sd = float(np.std(finite, ddof=1))
    sem = float(sd / np.sqrt(finite.size))
    return mean, sd, sem


def _batch_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    summary: list[dict[str, Any]] = []
    for group in groups:
        group_rows = [row for row in rows if str(row.get("sample_group") or "") == group]
        record: dict[str, Any] = {
            "sample_group": group,
            "sample_group_sort": _group_sort_key(group)[1],
            "n_observations": len(group_rows),
            "n_roi": int(sum(max(1, int(_finite_float(row.get("n_roi") or row.get("roi_count"), 1))) for row in group_rows)),
            "n_entries": len({str(row.get("entry_id") or "") for row in group_rows}),
        }
        for marker in ("sma", "macrophage"):
            marker_rows = [
                row for row in group_rows if _boolish(row.get(f"{marker}_include", True), default=True)
            ]
            raw_values = [_finite_float(row.get(_metric_column(marker))) for row in marker_rows]
            norm_values = [_finite_float(row.get(_normalized_metric_column(marker))) for row in marker_rows]
            mean_raw, sd_raw, sem_raw = _mean_sd_sem(raw_values)
            mean_norm, sd_norm, sem_norm = _mean_sd_sem(norm_values)
            record[f"{marker}_n_observations"] = len(marker_rows)
            record[f"{marker}_mean"] = mean_raw
            record[f"{marker}_sd"] = sd_raw
            record[f"{marker}_sem"] = sem_raw
            record[f"{marker}_normalized_mean"] = mean_norm
            record[f"{marker}_normalized_sd"] = sd_norm
            record[f"{marker}_normalized_sem"] = sem_norm
        summary.append(record)
    return summary


def _batch_anova(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats_out: dict[str, Any] = {}
    groups = sorted({str(row.get("sample_group") or "") for row in rows}, key=_group_sort_key)
    for marker in ("sma", "macrophage"):
        grouped = []
        labels = []
        for group in groups:
            values = [
                _finite_float(row.get(_normalized_metric_column(marker)), default=np.nan)
                for row in rows
                if str(row.get("sample_group") or "") == group
                and _boolish(row.get(f"{marker}_include", True), default=True)
            ]
            finite = np.asarray([value for value in values if np.isfinite(value)], dtype=np.float64)
            if finite.size:
                grouped.append(finite)
                labels.append(group)
        result: dict[str, Any] = {
            "marker": marker,
            "group_labels": labels,
            "group_count": len(grouped),
            "n": int(sum(len(values) for values in grouped)),
            "f": None,
            "p": None,
            "reason": "",
        }
        if len(grouped) < 2 or result["n"] <= len(grouped):
            result["reason"] = "Need at least two groups with residual degrees of freedom."
            stats_out[marker] = result
            continue
        try:
            import warnings as py_warnings

            from scipy import stats as scipy_stats  # type: ignore

            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore")
                f_value, p_value = scipy_stats.f_oneway(*grouped)
            if np.isfinite(f_value) and np.isfinite(p_value):
                result["f"] = float(f_value)
                result["p"] = float(p_value)
            else:
                result["reason"] = "ANOVA returned a non-finite statistic."
        except Exception as exc:  # pragma: no cover - scipy is a declared dependency
            result["reason"] = str(exc) or "ANOVA failed."
        stats_out[marker] = result
    return stats_out


def _write_csv_records(path: Path, rows: list[dict[str, Any]], preferred_fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(preferred_fields)
    seen = set(fieldnames)
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _scalar_for_table(row.get(key, "")) for key in fieldnames})


def _anova_label(stats: dict[str, Any], marker: str) -> str:
    anova = stats.get(marker) if isinstance(stats, dict) else None
    if not isinstance(anova, dict) or anova.get("f") is None or anova.get("p") is None:
        return "ANOVA: n/a"
    p_value = float(anova["p"])
    p_text = f"{p_value:.3g}" if p_value < 0.001 else f"{p_value:.5f}".rstrip("0").rstrip(".")
    return f"ANOVA: F = {float(anova['f']):.3f}, P = {p_text}"


def _plot_color(marker: str, index: int, total: int) -> tuple[float, float, float, float]:
    import matplotlib as mpl

    cmap = mpl.colormaps["Oranges" if marker == "sma" else "Greens"]
    if total <= 1:
        return cmap(0.72)
    return cmap(0.82 - 0.42 * (index / max(1, total - 1)))


def _save_batch_plot(
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    stats: dict[str, Any],
    marker: str,
    out_dir: Path,
    *,
    per_source: bool = False,
) -> dict[str, Any]:
    metric = _normalized_metric_column(marker)
    groups = [str(item["sample_group"]) for item in summary]
    x = np.arange(len(groups), dtype=np.float64)
    means = np.asarray([_finite_float(item.get(f"{marker}_normalized_mean")) for item in summary], dtype=np.float64)
    sems = np.asarray([_finite_float(item.get(f"{marker}_normalized_sem")) for item in summary], dtype=np.float64)
    fig, ax = new_subplots(figsize=(12.8, 6.8), dpi=150)
    colors = [_plot_color(marker, idx, len(groups)) for idx in range(len(groups))]
    ax.bar(x, means, color=colors, edgecolor="black", linewidth=1.25, width=0.6, zorder=2)
    ax.errorbar(x, means, yerr=sems, fmt="none", ecolor="black", elinewidth=1.4, capsize=4, zorder=3)
    letter_colors = {
        "A": "#1f77b4",
        "B": "#ff7f0e",
        "C": "#2ca02c",
        "D": "#d62728",
        "E": "#9467bd",
        "F": "#8c564b",
        "1": "#1f77b4",
        "2": "#ff7f0e",
        "3": "#2ca02c",
        "4": "#d62728",
        "5": "#9467bd",
        "6": "#8c564b",
    }
    plotted_letters: set[str] = set()
    for idx, group in enumerate(groups):
        group_rows = [
            row
            for row in rows
            if str(row.get("sample_group") or "") == group
            and _boolish(row.get(f"{marker}_include", True), default=True)
        ]
        for row_idx, row in enumerate(group_rows):
            y_value = _finite_float(row.get(metric))
            jitter = ((row_idx % 9) - 4) * 0.018
            letter = str(row.get("letter") or "").upper()[:1] or "A"
            if per_source:
                color = letter_colors.get(letter, "#666666")
                label = letter if letter not in plotted_letters else None
                plotted_letters.add(letter)
            else:
                color = "#D9D9D9"
                label = None
            ax.scatter(
                idx + jitter,
                y_value,
                s=38,
                facecolor=color,
                edgecolor="black",
                linewidth=0.45,
                label=label,
                zorder=4,
            )
            if per_source:
                ax.text(idx + jitter, y_value + 0.012, letter, ha="center", va="bottom", fontsize=8)
    marker_label = "SMA" if marker == "sma" else "Macrophage"
    if per_source:
        title = f"{marker_label} positive area ratio (per-image source labeled; normalized)"
    else:
        numeric_groups = all(str(group).strip().isdigit() for group in groups)
        sample_label = f"{groups[0]}-{groups[-1]}" if groups and numeric_groups else ""
        if sample_label:
            title = f"{marker_label} positive area ratio across samples {sample_label} (normalized)"
        else:
            title = f"{marker_label} positive area ratio across treatments (normalized)"
    ax.set_title(title, fontsize=17)
    ax.set_ylabel(f"{marker_label} positive area ratio (normalized to group 1)", fontsize=12)
    ax.set_xticks(x, groups)
    ax.grid(axis="y", linestyle="--", alpha=0.38, zorder=1)
    ax.set_axisbelow(True)
    ymax = 1.0
    values = [*_finite_float_list(means), *[max(0.0, a + b) for a, b in zip(means, sems, strict=False)]]
    for row in rows:
        if _boolish(row.get(f"{marker}_include", True), default=True):
            values.append(_finite_float(row.get(metric)))
    if values:
        ymax = max(1.0, float(np.nanmax(values)))
    ax.set_ylim(0, ymax * 1.18 + 0.05)
    ax.text(
        0.01,
        0.98,
        _anova_label(stats, marker),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=12,
        bbox={"facecolor": "white", "edgecolor": "black", "linewidth": 1.0, "pad": 5},
    )
    if per_source and plotted_letters:
        ax.legend(title="Letter", frameon=False, ncol=min(5, len(plotted_letters)), loc="upper right")
    safe_kind = "per_source" if per_source else "summary"
    png_path = out_dir / f"{marker}_positive_area_ratio_{safe_kind}_normalized.png"
    svg_path = out_dir / f"{marker}_positive_area_ratio_{safe_kind}_normalized.svg"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(svg_path, format="svg", bbox_inches="tight")
    close_figure(fig)
    return {
        "marker": marker,
        "kind": safe_kind,
        "path": str(png_path),
        "svg_path": str(svg_path),
        "img": base64.b64encode(png_path.read_bytes()).decode("ascii"),
    }


def _finite_float_list(values: Any) -> list[float]:
    return [_finite_float(value, default=np.nan) for value in list(values)]


def _write_batch_outputs(
    out_dir: Path,
    roi_rows: list[dict[str, Any]],
    observation_rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    stats: dict[str, Any],
    normalization: dict[str, Any],
    params: dict[str, Any],
    skipped: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    observation_level: str = "image",
    roi_parameter_override_keys: list[str] | None = None,
) -> dict[str, Any]:
    roi_table_path = out_dir / "roi_measurements_normalized.csv"
    image_table_path = out_dir / "image_measurements_normalized.csv"
    summary_table_path = out_dir / "sample_summary_normalized.csv"
    statistics_path = out_dir / "statistics.json"
    manifest_path = out_dir / "manifest.json"
    _write_csv_records(
        roi_table_path,
        roi_rows,
        [
            "sample_group",
            "treatment",
            "sample_number",
            "letter",
            "roi_label",
            "roi_id",
            "image_name",
            "entry_id",
            "roi_parameter_override_key",
            "sma_positive_area_ratio",
            "sma_positive_area_ratio_normalized",
            "sma_include",
            "macrophage_positive_area_ratio",
            "macrophage_positive_area_ratio_normalized",
            "macrophage_include",
            "area_px",
            "analysis_area_px",
        ],
    )
    _write_csv_records(
        image_table_path,
        observation_rows,
        [
            "sample_group",
            "treatment",
            "sample_number",
            "letter",
            "image_name",
            "entry_id",
            "roi_count",
            "sma_positive_area_ratio",
            "sma_positive_area_ratio_normalized",
            "sma_include",
            "macrophage_positive_area_ratio",
            "macrophage_positive_area_ratio_normalized",
            "macrophage_include",
            "roi_labels",
        ],
    )
    _write_csv_records(
        summary_table_path,
        summary,
        [
            "sample_group",
            "n_observations",
            "n_roi",
            "n_entries",
            "sma_n_observations",
            "sma_normalized_mean",
            "sma_normalized_sem",
            "macrophage_n_observations",
            "macrophage_normalized_mean",
            "macrophage_normalized_sem",
        ],
    )
    plots = [
        _save_batch_plot(observation_rows, summary, stats, "sma", out_dir, per_source=False),
        _save_batch_plot(observation_rows, summary, stats, "macrophage", out_dir, per_source=False),
        _save_batch_plot(observation_rows, summary, stats, "sma", out_dir, per_source=True),
        _save_batch_plot(observation_rows, summary, stats, "macrophage", out_dir, per_source=True),
    ]
    stats_payload = {
        "statistics": stats,
        "normalization": normalization,
        "parameters": params,
        "observation_level": observation_level,
    }
    _write_json(statistics_path, stats_payload)
    output_records = [
        {"path": str(roi_table_path), "type": "csv", "role": "histology_roi_measurements_normalized"},
        {"path": str(image_table_path), "type": "csv", "role": "histology_image_measurements_normalized"},
        {"path": str(summary_table_path), "type": "csv", "role": "histology_sample_summary_normalized"},
        {"path": str(statistics_path), "type": "json", "role": "histology_statistics"},
        {"path": str(manifest_path), "type": "json", "role": "histology_batch_manifest"},
    ]
    for plot in plots:
        output_records.append({"path": plot["path"], "type": "png", "role": f"histology_{plot['marker']}_{plot['kind']}_plot"})
        output_records.append({"path": plot["svg_path"], "type": "svg", "role": f"histology_{plot['marker']}_{plot['kind']}_plot"})
    manifest = {
        "version": ANALYSIS_VERSION,
        "kind": "histology_saved_roi_batch_analysis",
        "created_at": _now_iso(),
        "run_dir": str(out_dir),
        "observation_level": observation_level,
        "observation_count": len(observation_rows),
        "roi_count": len(roi_rows),
        "sample_count": len(summary),
        "outputs": output_records,
        "normalization": normalization,
        "statistics": stats,
        "parameters": params,
        "roi_parameter_override_count": len(roi_parameter_override_keys or []),
        "roi_parameter_override_keys": list(roi_parameter_override_keys or []),
        "skipped_entries": skipped,
        "failed_entries": failures,
    }
    _write_json(manifest_path, manifest)
    return {
        "run_dir": str(out_dir),
        "roi_table_path": str(roi_table_path),
        "image_table_path": str(image_table_path),
        "summary_table_path": str(summary_table_path),
        "statistics_path": str(statistics_path),
        "manifest_path": str(manifest_path),
        "plots": plots,
        "outputs": output_records,
    }

__all__ = [
    "_aggregate_roi_rows_by_entry",
    "_apply_batch_grouping",
    "_apply_marker_inclusion",
    "_apply_normalization_to_rows",
    "_batch_anova",
    "_batch_group_summary",
    "_boolish",
    "_finite_float",
    "_flatten_batch_row",
    "_new_project_batch_dir",
    "_normalize_batch_rows",
    "_params_for_roi_parameter_override",
    "_parse_image_sample_and_treatment",
    "_write_batch_outputs",
]
