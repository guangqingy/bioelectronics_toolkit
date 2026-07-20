"""Technique-aware electrochemistry quantification routes."""

from __future__ import annotations

import math
import traceback
from functools import lru_cache
from pathlib import Path

import numpy as np
from flask import jsonify
from pydantic import ValidationError

from services import echem as echem_service
from services import echem_metrics as metrics
from services import echem_tokens as tokens

from .echem_quant_request_schemas import (
    EchemBatchMetricsRequest,
    EchemCycleMetricsRequest,
    EchemPulseMetricsRequest,
    EchemTokenScanRequest,
)
from .request_validation import parse_json_payload, request_schema, validation_error_response
from .response import api_ok

_AUTO_PRESETS = ("default", "auto_polarity", "waveform", "legacy_day")


def _detection_from(body: dict, preset: str | None = None) -> dict:
    name = str(preset or body.get("preset") or "auto")
    if name == "auto":
        name = "auto_polarity"
    detection = dict(metrics.DETECTION_PRESETS.get(name, metrics.DEFAULT_DETECTION))
    for key in (
        "edge_exclusion_s",
        "threshold_mad",
        "minimum_gap_s",
        "detrend_window_s",
        "expected_period_s",
    ):
        value = body.get(key)
        if value is not None:
            detection[key] = float(value)
    polarity = body.get("polarity")
    if polarity:
        detection["polarity"] = str(polarity)
    return detection


def _measurement_from(body: dict) -> dict:
    measurement = dict(metrics.DEFAULT_MEASUREMENT)
    for key in ("baseline_ms", "post_fraction", "post_cap_ms"):
        value = body.get(key)
        if value is not None:
            measurement[key] = float(value)
    return measurement


def _jsonable(value):
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not np.isfinite(number) else number
    if isinstance(value, (np.integer, int)):
        return int(value)
    return value


def _clean(payload: dict) -> dict:
    return {key: _jsonable(value) for key, value in payload.items()}


def _area(body: dict) -> float:
    area = float(body.get("electrode_area_cm2") or 0.25)
    if not np.isfinite(area) or area <= 0:
        raise ValueError("Electrode area must be greater than zero")
    return area


def _density_fields(summary: dict, area_cm2: float, pairs: tuple[tuple[str, str], ...]) -> dict:
    result: dict = {"electrode_area_cm2": area_cm2}
    for source, target in pairs:
        value = summary.get(source)
        if value is not None and np.isfinite(float(value)):
            result[target] = float(value) / area_cm2
    return result


def _auto_pulse_summary(
    time_s: np.ndarray, current_nA: np.ndarray, body: dict, include_detail: bool = False
) -> tuple[dict, dict, dict | None]:
    requested = str(body.get("preset") or "auto")
    presets = _AUTO_PRESETS if requested == "auto" else (requested,)
    measurement = _measurement_from(body)
    signed = bool(body.get("signed"))
    candidates = []
    for name in presets:
        detection = _detection_from(body, name)
        # Early sessions benefit from the relaxed timing preset but still need
        # polarity inference because cathodic and anodic traces coexist.
        if requested == "auto" and name == "legacy_day":
            detection["polarity"] = "auto"
        summary = metrics.pulse_metrics_summary(
            time_s, current_nA, detection, measurement, signed=signed
        )
        score = (int(summary.get("n_pulses") or 0), int(summary.get("n_detected") or 0))
        candidates.append((score, name, detection, summary))
    _score, name, detection, summary = max(candidates, key=lambda item: item[0])
    summary["detection_preset"] = name
    detail = None
    if include_detail:
        detail = metrics.pulse_metrics(time_s, current_nA, detection, measurement, signed=signed)
    return summary, detection, detail


def _ca_period(path: Path) -> float:
    ca_time, ca_current, _tc, _ic = echem_service.load_photocurrent_nA(path)
    for preset in ("auto_polarity", "legacy_day"):
        detection = _detection_from({"preset": preset})
        detection["polarity"] = "auto"
        summary = metrics.pulse_metrics_summary(ca_time, ca_current, detection)
        period = summary.get("period_s")
        if period is not None and np.isfinite(float(period)):
            return float(period)
    return float("nan")


@lru_cache(maxsize=64)
def _session_ca_period(folder: str, folder_mtime_ns: int) -> float:
    """Representative cycle period for CP files whose paired CA is flat."""
    del folder_mtime_ns  # cache invalidation key when live acquisition adds files
    periods: list[float] = []
    for path in sorted(Path(folder).glob("*_CA.csv")):
        try:
            period = _ca_period(path)
        except ValueError:
            continue
        if np.isfinite(period):
            periods.append(period)
        if len(periods) >= 8:
            break
    return float(np.median(periods)) if periods else float("nan")


def _infer_cp_period(
    time_s: np.ndarray, potential_mV: np.ndarray, source_path: str | None = None
) -> float:
    """Infer a full CP cycle from signal folding, with an edge fallback."""
    if source_path:
        source = Path(source_path)
        if source.stem.upper().endswith("_CP"):
            try:
                folder_period = _session_ca_period(
                    str(source.parent), source.parent.stat().st_mtime_ns
                )
                if np.isfinite(folder_period):
                    return folder_period
            except OSError:
                pass
            ca_path = source.with_name(f"{source.stem[:-3]}_CA{source.suffix}")
            if ca_path.is_file():
                try:
                    ca_period = _ca_period(ca_path)
                    if np.isfinite(ca_period):
                        return ca_period
                except ValueError:
                    pass
    if len(time_s) < 16:
        return float("nan")
    stride = max(1, int(math.ceil(len(time_s) / 50_000)))
    t = np.asarray(time_s[::stride], dtype=float)
    y = np.asarray(potential_mV[::stride], dtype=float)
    finite = np.isfinite(t) & np.isfinite(y)
    t, y = t[finite], y[finite]
    dt = metrics.robust_dt(t)
    if len(t) >= 16 and np.isfinite(dt) and dt > 0:
        slope, intercept = np.polyfit(t - t[0], y, 1)
        work = y - (slope * (t - t[0]) + intercept)
        period = metrics.find_period_s(work, dt)
        if np.isfinite(period):
            return float(period)

    detection = _detection_from({"preset": "legacy_day"})
    _onsets, _dt, edge_period = metrics.detect_steps(time_s, potential_mV, detection)
    return float(2.0 * edge_period) if np.isfinite(edge_period) else float("nan")


def _cycle_summary(
    time_s: np.ndarray, potential_mV: np.ndarray, body: dict, parsed: dict
) -> tuple[dict, dict]:
    detection = _detection_from(body)
    explicit = body.get("expected_period_s")
    token_period = parsed.get("fields", {}).get("light_period_s")
    if explicit is not None:
        detection["expected_period_s"] = float(explicit)
        detection["period_source"] = "manual"
    elif token_period is not None:
        detection["expected_period_s"] = float(token_period)
        detection["period_source"] = "filename"
    else:
        inferred = _infer_cp_period(time_s, potential_mV, parsed.get("path"))
        if np.isfinite(inferred):
            detection["expected_period_s"] = float(inferred)
            detection["period_source"] = "auto"
    return metrics.cycle_amplitudes_summary(time_s, potential_mV, detection), detection


def _base_row(path: str, parsed: dict) -> dict:
    return {
        "file": Path(path).name,
        "path": path,
        "label": parsed["label"],
        "tokens": parsed["tokens"],
        **{f"token_{key}": _jsonable(value) for key, value in parsed["fields"].items()},
    }


def _quantify_one(path: str, body: dict) -> dict:
    parsed = tokens.parse_recording_name(path)
    technique = parsed["fields"].get("technique")
    row = _base_row(path, parsed)
    area_cm2 = _area(body)

    if technique == "CA":
        time_s, current_nA, _tc, _ic = echem_service.load_photocurrent_nA(path)
        summary, _detection, _detail = _auto_pulse_summary(time_s, current_nA, body)
        row.update(_clean(summary))
        row.update(
            _density_fields(
                summary,
                area_cm2,
                (
                    ("amplitude_nA", "amplitude_nA_cm2"),
                    ("amplitude_sd_nA", "amplitude_sd_nA_cm2"),
                    ("p2p_nA", "p2p_nA_cm2"),
                    ("charge_nC", "charge_nC_cm2"),
                ),
            )
        )
        row["status"] = "ok" if row.get("n_pulses") else "no pulses detected"
    elif technique == "CP":
        time_s, potential_mV, _tc, _vc = echem_service.load_photovoltage_mV(path)
        summary, _detection = _cycle_summary(time_s, potential_mV, body, parsed)
        row.update(_clean(summary))
        row["status"] = "ok" if row.get("n_cycles") else "no cycles detected"
    elif technique == "COR":
        time_s, current_nA, potential_v, ocp_v = echem_service.load_corrtest(path)
        hint = parsed["fields"].get("light_period_s")
        summary = metrics.square_wave_metrics(time_s, current_nA, hint)
        summary.update(
            {
                "applied_potential_V": float(np.nanmedian(potential_v)),
                "ocp_V": ocp_v,
                "period_source": "filename" if hint is not None else "auto",
            }
        )
        row.update(_clean(summary))
        row.update(
            _density_fields(
                summary,
                area_cm2,
                (
                    ("spike_nA", "spike_nA_cm2"),
                    ("spike_sd_nA", "spike_sd_nA_cm2"),
                    ("plateau_nA", "plateau_nA_cm2"),
                    ("i_median_nA", "i_median_nA_cm2"),
                ),
            )
        )
        row["status"] = "ok" if summary.get("flag") == "ok" else str(summary.get("flag"))
    elif technique == "CV":
        potential_v, current_uA, _ec, _ic = echem_service.load_cv(path)
        summary = metrics.cv_anodic_peak_summary(
            potential_v,
            current_uA,
            (
                float(body.get("cv_window_low_V", -0.25)),
                float(body.get("cv_window_high_V", -0.12)),
            ),
            float(body.get("cv_edge_guard_V", 0.02)),
        )
        row.update(_clean(summary))
        row.update(_density_fields(summary, area_cm2, (("Ipa_uA", "Ipa_uA_cm2"),)))
        row["status"] = "ok" if summary["anodic_valid"] else summary["anodic_status"]
    else:
        row["status"] = f"skipped: no quantifier for technique {technique}"
    return row


def register_echem_quant_routes(app, ctx):
    err = ctx.err

    def _resolve_paths(body: dict) -> list[str]:
        paths = [str(path) for path in (body.get("paths") or []) if str(path).strip()]
        if paths:
            return paths
        return tokens.discover_recording_paths(body.get("folder", ""), body.get("limit", 5000))

    @app.route("/api/echem/tokens/scan", methods=["POST"])
    @request_schema(EchemTokenScanRequest)
    def api_echem_tokens_scan():
        try:
            body = parse_json_payload(EchemTokenScanRequest).model_dump()
            paths = _resolve_paths(body)
            parsed = tokens.parse_recording_names(paths)
            technique_counts: dict[str, int] = {}
            for record in parsed:
                technique = str(record["fields"].get("technique") or "unknown")
                technique_counts[technique] = technique_counts.get(technique, 0) + 1
            return jsonify(
                {
                    "records": parsed,
                    "facets": tokens.token_facets(parsed),
                    "token_order": list(tokens.TOKEN_ORDER),
                    "token_labels": tokens.TOKEN_LABELS,
                    "technique_counts": technique_counts,
                    "n_files": len(parsed),
                    "n_unparsed": sum(1 for record in parsed if record["unparsed"]),
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/metrics/pulse", methods=["POST"])
    @request_schema(EchemPulseMetricsRequest)
    def api_echem_metrics_pulse():
        try:
            body = parse_json_payload(EchemPulseMetricsRequest).model_dump()
            path = body["path"]
            time_s, current_nA, _tc, _ic = echem_service.load_photocurrent_nA(path)
            summary, detection, detail = _auto_pulse_summary(
                time_s, current_nA, body, include_detail=True
            )
            area_cm2 = _area(body)
            summary.update(
                _density_fields(
                    summary,
                    area_cm2,
                    (("amplitude_nA", "amplitude_nA_cm2"), ("charge_nC", "charge_nC_cm2")),
                )
            )
            return jsonify(
                {
                    "summary": _clean(summary),
                    "per_pulse": {
                        "amplitude_nA": [_jsonable(value) for value in detail["amplitudes_nA"]],
                        "p2p_nA": [_jsonable(value) for value in detail["p2p_nA"]],
                        "charge_nC": [_jsonable(value) for value in detail["charge_nC"]],
                    },
                    "detection": _clean(detection),
                    "measurement": _clean(_measurement_from(body)),
                    "tokens": tokens.parse_recording_name(path),
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/metrics/cycle", methods=["POST"])
    @request_schema(EchemCycleMetricsRequest)
    def api_echem_metrics_cycle():
        try:
            body = parse_json_payload(EchemCycleMetricsRequest).model_dump()
            path = body["path"]
            time_s, potential_mV, _tc, _vc = echem_service.load_photovoltage_mV(path)
            parsed = tokens.parse_recording_name(path)
            summary, detection = _cycle_summary(time_s, potential_mV, body, parsed)
            warnings = []
            if summary["period_source"] == "inferred":
                warnings.append("The full light cycle could not be inferred; enter it manually.")
            return api_ok(
                {
                    "summary": _clean(summary),
                    "detection": _clean(detection),
                    "tokens": parsed,
                },
                warnings=warnings,
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/metrics/batch", methods=["POST"])
    @request_schema(EchemBatchMetricsRequest)
    def api_echem_metrics_batch():
        try:
            body = parse_json_payload(EchemBatchMetricsRequest).model_dump()
            paths = _resolve_paths(body)[: max(1, int(body.get("limit") or 5000))]
            rows = []
            for path in paths:
                try:
                    rows.append(_quantify_one(path, body))
                except Exception as exc:
                    parsed = tokens.parse_recording_name(path)
                    rows.append({**_base_row(path, parsed), "status": f"error: {exc}"})
            technique_counts: dict[str, int] = {}
            for row in rows:
                technique = str(row.get("token_technique") or "unknown")
                technique_counts[technique] = technique_counts.get(technique, 0) + 1
            return jsonify(
                {
                    "rows": rows,
                    "n_files": len(rows),
                    "n_ok": sum(1 for row in rows if row.get("status") == "ok"),
                    "technique_counts": technique_counts,
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())
