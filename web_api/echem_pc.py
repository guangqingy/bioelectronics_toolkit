# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move PC plotting/export payload assembly into echem service helpers and track
# the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import Response, jsonify
from pydantic import Field, ValidationError

from services import echem as echem_service
from services.matplotlib_utils import new_subplots
from web_api.common import as_bool, mode_is_save

from .jobs import submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class EchemPcBrowseRequest(RequestModel):
    folder: str = ""


class EchemPcLoadRequest(RequestModel):
    path: str = Field(min_length=1)


class EchemPcTraceDataRequest(EchemPcLoadRequest):
    x_min: Any = None
    x_max: Any = None
    y_min: Any = None
    y_max: Any = None
    t0: Any = None
    t1: Any = None


class EchemPcDetectRequest(RequestModel):
    path: str = Field(min_length=1)
    t0: Any = None
    t1: Any = None
    pos_min_mA: Any = None
    neg_min_abs_mA: Any = None
    min_delay_ms: Any = 1.0
    max_delay_ms: Any = 15.0
    min_pos_distance_ms: Any = 200.0
    use_all: Any = True
    pos_thresh: Any = None
    neg_thresh: Any = None
    min_dist: Any = None


class EchemPcExportRequest(RequestModel):
    path: str = Field(min_length=1)
    pairs: list[Any] = Field(default_factory=list)
    mode: str = "download"
    window: list[Any] = Field(default_factory=list)
    t0: Any = None
    t1: Any = None
    pos_min_mA: Any = None
    neg_min_abs_mA: Any = None
    pair_window_ms: Any = 50.0


def register_echem_pc_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    fig_to_b64 = ctx["fig_to_b64"]
    float_or = ctx["float_or"]
    line_color = ctx["LINE_COLOR"]
    jobs = ctx.get("jobs")
    has_scipy = ctx["HAS_SCIPY"]
    find_peaks = ctx.get("find_peaks")

    _mode_is_save = mode_is_save
    _as_bool = as_bool

    def _load_echem(path):
        """Load time/current data from a .txt or .csv echem file."""
        return echem_service.load_photocurrent(path)

    def _pc_outputs(output_folder: Path, summary_path: Path, saved_paths: list[str]) -> list[dict]:
        outputs = [
            {"path": str(output_folder), "type": "directory", "role": "photocurrent_pair_folder"}
        ]
        outputs.append(
            {"path": str(summary_path), "type": "csv", "role": "photocurrent_pair_summary"}
        )
        outputs.extend(
            {"path": path, "type": "csv", "role": "photocurrent_pair_window"}
            for path in saved_paths
            if path != str(summary_path)
        )
        return outputs

    def _echem_pc_export_payload(d: dict) -> dict:
        pairs = d.get("pairs", [])
        path = d.get("path", "")
        mode = d.get("mode", "download")
        if not pairs:
            raise ValueError("No pairs to export")

        stem = Path(path).stem if path else "pairs"
        if _mode_is_save(mode):
            src = Path(path)
            if not path:
                raise ValueError("Missing source file path")

            t, i_raw, _t_col, _i_col = _load_echem(path)
            if len(t) == 0:
                raise ValueError("No data points found in file")

            output_folder = src.with_name(src.stem)
            output_folder.mkdir(parents=True, exist_ok=True)

            window = d.get("window", [])
            if isinstance(window, list) and len(window) >= 2:
                win_t0 = float_or(window[0], np.nan)
                win_t1 = float_or(window[1], np.nan)
            else:
                win_t0 = float_or(d.get("t0"), np.nan)
                win_t1 = float_or(d.get("t1"), np.nan)

            pos_min_mA = float_or(d.get("pos_min_mA"), np.nan)
            neg_min_abs_mA = float_or(d.get("neg_min_abs_mA"), np.nan)

            summary_path = output_folder / f"{src.stem}_pairs_summary.csv"
            rows = []
            pair_indices = []
            saved_paths = [str(summary_path)]
            export_idx = 1
            for p in pairs:
                pi = int(p.get("pi", -1)) if p.get("pi", None) is not None else -1
                ni = int(p.get("ni", -1)) if p.get("ni", None) is not None else -1

                if pi < 0 or pi >= len(t):
                    tp = float_or(p.get("t_pos"), None)
                    if tp is None:
                        continue
                    pi = int(np.argmin(np.abs(t - tp)))
                if ni < 0 or ni >= len(t):
                    tn = float_or(p.get("t_neg"), None)
                    if tn is None:
                        continue
                    ni = int(np.argmin(np.abs(t - tn)))

                tp = float(t[pi])
                ip = float(i_raw[pi])
                tn = float(t[ni])
                ineg = float(i_raw[ni])

                rows.append(
                    [
                        export_idx,
                        int(p.get("original_index", export_idx)),
                        tp,
                        ip,
                        tn,
                        ineg,
                        (ip + abs(ineg)),
                        (tn - tp),
                        win_t0,
                        win_t1,
                        pos_min_mA,
                        neg_min_abs_mA,
                    ]
                )
                pair_indices.append((export_idx, pi))
                export_idx += 1

            header = (
                "export_index,original_index,POS_t_s,POS_I_mA,NEG_t_s,NEG_I_mA,Delta_I_mA,Delta_t_s,"
                "window_start_s,window_end_s,pos_min_mA,neg_min_abs_mA"
            )
            with summary_path.open("w", encoding="utf-8") as f:
                f.write(header + "\n")
                for r in rows:
                    f.write(
                        ",".join(f"{v:.9g}" if isinstance(v, float) else str(v) for v in r) + "\n"
                    )

            window_ms = float_or(d.get("pair_window_ms"), 50.0)
            if window_ms is None or window_ms <= 0:
                window_ms = 50.0

            saved_count = 0
            for export_idx, pi in pair_indices:
                tp = float(t[pi])
                t_start = tp - (window_ms / 1000.0)
                t_end = tp + (window_ms / 1000.0)
                mask = (t >= t_start) & (t <= t_end)
                if not np.any(mask):
                    continue

                pair_path = output_folder / f"{src.stem}_pair_{export_idx:03d}.csv"
                with pair_path.open("w", encoding="utf-8") as f:
                    f.write("time_s,current_mA\n")
                    for t_val, i_val in zip(t[mask], i_raw[mask], strict=True):
                        f.write(f"{float(t_val):.9g},{float(i_val):.9g}\n")
                saved_count += 1
                saved_paths.append(str(pair_path))

            return {
                "kind": "save",
                "data": {
                    "ok": True,
                    "saved_path": str(output_folder),
                    "summary_path": str(summary_path),
                    "saved_count": saved_count,
                    "saved_paths": saved_paths,
                    "outputs": _pc_outputs(output_folder, summary_path, saved_paths),
                },
            }

        df = pd.DataFrame(pairs)
        return {
            "kind": "download",
            "payload": df.to_csv(index=False).encode("utf-8"),
            "mimetype": "text/csv",
            "download_name": f"{stem}_pairs.csv",
        }

    def _echem_pc_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting photocurrent pairs")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return _echem_pc_export_payload(save_body)["data"]

    @app.route("/api/echem/browse", methods=["POST"])
    @request_schema(EchemPcBrowseRequest)
    def api_echem_browse():
        try:
            payload = parse_json_payload(EchemPcBrowseRequest)
        except ValidationError as exc:
            return validation_error_response(exc)
        files = browse_files(payload.folder, {".txt", ".csv"})
        return jsonify({"files": [f["path"] for f in files], "file_meta": files})

    @app.route("/api/echem/load", methods=["POST"])
    @request_schema(EchemPcLoadRequest)
    def api_echem_load():
        try:
            path = parse_json_payload(EchemPcLoadRequest).path
            t, i, t_col, i_col = _load_echem(path)
            if len(t) == 0:
                return err("No data points found in file")

            fig, ax = new_subplots(figsize=(9, 3.5))
            ax.plot(t, i, color=line_color, lw=0.7)
            ax.set_xlabel(t_col)
            ax.set_ylabel(i_col)
            ax.set_title(Path(path).name, fontsize=10, color="#5C5E62")
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "t_range": [float(t[0]), float(t[-1])],
                    "duration": round(float(t[-1] - t[0]), 3) if len(t) else 0,
                    "n_points": len(t),
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/trace_data", methods=["POST"])
    @request_schema(EchemPcTraceDataRequest)
    def api_echem_trace_data():
        try:
            d = parse_json_payload(EchemPcTraceDataRequest).model_dump()
            return jsonify(echem_service.photocurrent_trace_data_payload(d))
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/detect", methods=["POST"])
    @request_schema(EchemPcDetectRequest)
    def api_echem_detect():
        if not has_scipy or find_peaks is None:
            return err("scipy not installed")

        try:
            d = parse_json_payload(EchemPcDetectRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        path = d.get("path", "")
        t0 = float_or(d.get("t0"), None)
        t1 = float_or(d.get("t1"), None)
        pos_min_mA = float_or(d.get("pos_min_mA"), None)
        neg_min_abs_mA = float_or(d.get("neg_min_abs_mA"), None)
        min_delay_ms = float_or(d.get("min_delay_ms", 1.0), 1.0)
        max_delay_ms = float_or(d.get("max_delay_ms", 15.0), 15.0)
        min_pos_distance_ms = float_or(d.get("min_pos_distance_ms", 200.0), 200.0)
        use_all = _as_bool(d.get("use_all"), True)

        # Backward-compatible aliases from earlier web payload naming.
        if pos_min_mA is None:
            pos_min_mA = float_or(d.get("pos_thresh"), 0.01)
        if neg_min_abs_mA is None:
            neg_old = float_or(d.get("neg_thresh"), None)
            neg_min_abs_mA = abs(neg_old) if neg_old is not None else 0.01
        if d.get("min_dist") is not None and d.get("min_pos_distance_ms") is None:
            min_pos_distance_ms = float_or(d.get("min_dist"), min_pos_distance_ms)

        try:
            t, i_raw, t_col, i_col = _load_echem(path)
            if len(t) < 2:
                return err("Not enough data points for detection")

            if use_all:
                t0 = float(t[0])
                t1 = float(t[-1])
            else:
                if t0 is None or t1 is None:
                    return err("Set an analysis window first (t0/t1).")
                if t1 < t0:
                    t0, t1 = t1, t0

            pairs_idx = echem_service.detect_photocurrent_pairs(
                t,
                i_raw,
                float(t0),
                float(t1),
                float(pos_min_mA),
                float(neg_min_abs_mA),
                float(min_delay_ms),
                float(max_delay_ms),
                float(min_pos_distance_ms),
                find_peaks,
            )

            pairs = []
            for orig_idx, (pi, ni) in enumerate(pairs_idx, start=1):
                t_pos = float(t[pi])
                i_pos = float(i_raw[pi])
                t_neg = float(t[ni])
                i_neg = float(i_raw[ni])
                dt_ms = float((t_neg - t_pos) * 1000.0)
                pairs.append(
                    {
                        "idx": orig_idx - 1,
                        "original_index": orig_idx,
                        "pi": int(pi),
                        "ni": int(ni),
                        "t_pos": round(t_pos, 6),
                        "i_pos": round(i_pos, 6),
                        "t_neg": round(t_neg, 6),
                        "i_neg": round(i_neg, 6),
                        "delta_t": round(dt_ms, 3),
                        "delta_i": round(i_pos + abs(i_neg), 6),
                        "time": round(t_pos, 6),
                        "pos_peak": round(i_pos, 6),
                        "neg_peak": round(i_neg, 6),
                        "duration": round(dt_ms, 3),
                    }
                )

            mask_w = (t >= float(t0)) & (t <= float(t1))
            t_w = t[mask_w]
            i_w = i_raw[mask_w]

            fig, ax = new_subplots(figsize=(10, 3.5))
            ax.plot(t, i_raw, color="#D0D1D2", lw=0.6, zorder=1)
            ax.plot(t_w, i_w, color=line_color, lw=0.8, zorder=2)
            ax.axvspan(float(t0), float(t1), alpha=0.12, color="gray")
            ax.axvline(float(t0), ls="--", lw=0.8, color="gray")
            ax.axvline(float(t1), ls="--", lw=0.8, color="gray")

            if pairs:
                pos_t = [float(p["t_pos"]) for p in pairs]
                pos_i = [float(p["i_pos"]) for p in pairs]
                ax.scatter(pos_t, pos_i, s=28, marker="^", color="red", zorder=5)
                for p in pairs:
                    ax.annotate(
                        str(p["original_index"]),
                        xy=(p["t_pos"], p["i_pos"]),
                        xytext=(3, 3),
                        textcoords="offset points",
                        fontsize=8,
                        color="red",
                    )

            ax.axhline(float(pos_min_mA), color="#22c55e", lw=0.6, ls="--", alpha=0.7)
            ax.axhline(-float(neg_min_abs_mA), color="#ef4444", lw=0.6, ls="--", alpha=0.7)
            ax.set_xlabel(t_col)
            ax.set_ylabel(i_col)
            ax.set_title(
                f"{Path(path).name} - {len(pairs)} pairs detected", fontsize=10, color="#5C5E62"
            )
            ax.grid(True, alpha=0.4)
            fig.tight_layout()
            return jsonify(
                {
                    "img": fig_to_b64(fig),
                    "pairs": pairs,
                    "window": [float(t0), float(t1)],
                    "params": {
                        "pos_min_mA": float(pos_min_mA),
                        "neg_min_abs_mA": float(neg_min_abs_mA),
                        "min_delay_ms": float(min_delay_ms),
                        "max_delay_ms": float(max_delay_ms),
                        "min_pos_distance_ms": float(min_pos_distance_ms),
                    },
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/export", methods=["POST"])
    @request_schema(EchemPcExportRequest)
    def api_echem_export():
        try:
            d = parse_json_payload(EchemPcExportRequest).model_dump()
            result = _echem_pc_export_payload(d)
            if result["kind"] == "save":
                data = result["data"]
                return api_ok(data, outputs=data["outputs"])
            return Response(
                result["payload"],
                mimetype=result["mimetype"],
                headers={"Content-Disposition": f"attachment; filename={result['download_name']}"},
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/echem/export_job", methods=["POST"])
    @request_schema(EchemPcExportRequest)
    def api_echem_export_job():
        try:
            body = parse_json_payload(EchemPcExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "echem_pc.export",
            "Export echem photocurrent pairs",
            _echem_pc_export_task,
            body,
            metadata={"endpoint": "/api/echem/export"},
        )
