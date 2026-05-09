import json
import re
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from flask import jsonify, request

from services import abf as abf_service

from .jobs import submit_json_task
from .response import api_ok

ROOT_DIR = Path(__file__).resolve().parents[1]


def register_abf_batch_routes(app, ctx):
    err = ctx["err"]
    browse_files_recursive = ctx["browse_files_recursive"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    has_abf = ctx["HAS_ABF"]
    pyabf_mod = ctx.get("pyabf")
    jobs = ctx.get("jobs")

    def _abf_estimate_r(i_trace, v_trace, dt):
        """Estimate resistance from V-step edges via dV/dI."""
        return abf_service.estimate_resistance(i_trace, v_trace, dt)

    def _write_abf_batch_operation_log(payload: dict) -> str:
        log_dir = ROOT_DIR / ".dataprocess_cache" / "operation_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = log_dir / f"abf_batch_{stamp}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _abf_batch_process_payload(d: dict) -> dict:
        if not has_abf or pyabf_mod is None:
            raise ValueError("pyabf not installed")

        folder = d.get("folder", "")
        main_token = d.get("main", "")
        treat_token = d.get("treat", "")
        try:
            powers = [float(x) for x in d.get("powers", "").split(",") if x.strip()]
        except ValueError as exc:
            raise ValueError("Power list must be comma-separated numbers") from exc
        i_ch = int_or(d.get("i_ch", 0), 0)
        v_ch = int_or(d.get("v_ch", 1), 1)
        bl_pre0 = float_or(d.get("bl_pre0", 0), 0)
        bl_pre1 = float_or(d.get("bl_pre1", 50), 50)
        peak_window = float_or(d.get("peak_window", 200), 200)
        move_files = bool(d.get("move_files", True))
        reindex_seq = bool(d.get("reindex_seq", False))
        dry_run = bool(d.get("dry_run", False))

        p = Path(folder)
        abf_files = sorted(p.rglob("*.abf"))
        pat = re.compile(r"(.+?)_(.+?)_sample_(\d+)_([A-Za-z0-9]+)_(\d+)", re.IGNORECASE)
        records = []
        warnings = []

        parsed = []
        for af in abf_files:
            m = pat.match(af.stem)
            if not m:
                continue
            mn, tr, samp, spot, seq = m.groups()
            if main_token and mn.lower() != main_token.lower():
                continue
            if treat_token and tr.lower() != treat_token.lower():
                continue
            parsed.append(
                {
                    "path": af,
                    "main": mn,
                    "treat": tr,
                    "sample": int(samp),
                    "spot": str(spot),
                    "seq": int(seq),
                    "seq_width": len(str(seq)),
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
                gk = (item["main"].lower(), item["treat"].lower(), item["sample"], item["spot"].lower())
                cur_min = group_min.get(gk)
                if cur_min is None or item["seq"] < cur_min:
                    group_min[gk] = item["seq"]

        moved_count = 0
        renamed_count = 0
        operations = []

        for item in parsed:
            af = Path(item["path"])
            mn = item["main"]
            tr = item["treat"]
            samp = item["sample"]
            spot = item["spot"]
            seq_i = item["seq"]

            cur_path = af
            if move_files:
                sample_dir = p / f"{mn}_{tr}" / f"sample_{samp}"
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
                except Exception as e:
                    warnings.append(f"Move failed for {cur_path.name}: {e}")

            if reindex_seq:
                gk = (mn.lower(), tr.lower(), samp, spot.lower())
                offset = int(group_min.get(gk, 0))
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
                        except Exception as e:
                            warnings.append(f"Rename failed for {cur_name}: {e}")
                        seq_i = new_seq

            if dry_run:
                continue

            try:
                abf = pyabf_mod.ABF(str(cur_path))
                abf.setSweep(0, channel=i_ch)
                y = abf.sweepY.copy()
                t = abf.sweepX
                dt = t[1] - t[0]

                i0 = int(bl_pre0 / 1000 / dt)
                i1 = int(bl_pre1 / 1000 / dt)
                baseline = np.mean(y[i0 : max(i1, i0 + 1)])
                y -= baseline

                win_end = int(peak_window / 1000 / dt)
                segment = y[i1 : i1 + win_end]
                peak_sign = float(segment[np.argmax(np.abs(segment))])
                integral = float(np.trapz(segment, t[i1 : i1 + win_end]))

                abf.setSweep(0, channel=v_ch)
                v_tr = abf.sweepY
                abf.setSweep(0, channel=i_ch)
                r_val = _abf_estimate_r(y + baseline, v_tr, dt)

                power = powers[seq_i] if seq_i < len(powers) else seq_i
                records.append(
                    {
                        "file": cur_path.name,
                        "file_path": str(cur_path),
                        "main": mn,
                        "treat": tr,
                        "sample": samp,
                        "spot": spot,
                        "seq": str(seq_i),
                        "power_density": power,
                        "capacitance_peak": round(peak_sign, 6),
                        "integral_charge": round(integral, 8),
                        "pipette_resistance_MOhm": round(r_val * 1e3, 2) if r_val else None,
                    }
                )
            except Exception:
                continue

        if dry_run:
            log_payload = {
                "mode": "dry_run",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "folder": str(p),
                "main": main_token,
                "treat": treat_token,
                "move_files": move_files,
                "reindex_seq": reindex_seq,
                "planned_count": len(operations),
                "planned_move_count": sum(1 for op in operations if op["action"] == "move"),
                "planned_rename_count": sum(1 for op in operations if op["action"] == "rename"),
                "operations": operations,
                "warnings": warnings,
            }
            log_path = _write_abf_batch_operation_log(log_payload)
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
                log_path = _write_abf_batch_operation_log(
                    {
                        "mode": "apply",
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "folder": str(p),
                        "main": main_token,
                        "treat": treat_token,
                        "move_files": move_files,
                        "reindex_seq": reindex_seq,
                        "moved_count": moved_count,
                        "renamed_count": renamed_count,
                        "processed_count": 0,
                        "operations": operations,
                        "warnings": warnings,
                    }
                )
                outputs = [{"path": log_path, "type": "json", "role": "operation_log"}]
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
        out_dir = p / f"{main_token}_{treat_token}" if main_token else p / "batch_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / f"summary_{main_token}_{treat_token}.csv"
        df.to_csv(csv_path, index=False)

        results = []
        for r in records:
            results.append(
                {
                    "file": r.get("file", ""),
                    "status": "ok",
                    "main_val": r.get("main", ""),
                    "treat_val": r.get("treat", ""),
                    "peak_count": 1,
                }
            )

        log_payload = {
            "mode": "apply",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "folder": str(p),
            "main": main_token,
            "treat": treat_token,
            "move_files": move_files,
            "reindex_seq": reindex_seq,
            "moved_count": moved_count,
            "renamed_count": renamed_count,
            "processed_count": len(records),
            "csv_path": str(csv_path),
            "operations": operations,
            "warnings": warnings,
        }
        log_path = _write_abf_batch_operation_log(log_payload)

        return {
            "message": f"Processed {len(records)} files. Saved: {csv_path}",
            "n": len(records),
            "rows": df.head(50).to_dict(orient="records"),
            "results": results,
            "csv_path": str(csv_path),
            "moved_count": moved_count,
            "renamed_count": renamed_count,
            "warnings": warnings[:100],
            "operation_log_path": log_path,
            "outputs": [
                {"path": str(csv_path), "type": "csv", "role": "abf_batch_summary"},
                {"path": log_path, "type": "json", "role": "operation_log"},
            ],
        }

    def _abf_batch_process_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Running ABF batch processing")
        return _abf_batch_process_payload(body)

    @app.route("/api/abf_batch/browse", methods=["POST"])
    def api_abf_batch_browse():
        d = request.json or {}
        files = browse_files_recursive(d.get("folder", ""), {".abf"})
        return jsonify({"files": files})

    @app.route("/api/abf_batch/scan_tokens", methods=["POST"])
    def api_abf_batch_scan_tokens():
        """Scan filenames to suggest main/treat tokens."""
        d = request.json or {}
        files = d.get("files", [])
        mains = set()
        treats = set()
        pat = re.compile(r"^(.+?)_(.+?)_sample_", re.IGNORECASE)
        for f in files:
            name = f.get("name", "") if isinstance(f, dict) else str(f)
            m = pat.match(Path(name).stem)
            if m:
                mains.add(m.group(1))
                treats.add(m.group(2))
        mains = sorted(mains)
        treats = sorted(treats)
        return jsonify(
            {
                "mains": mains,
                "treats": treats,
                "main_token": mains[0] if mains else "",
                "treat_token": treats[0] if treats else "",
            }
        )

    @app.route("/api/abf_batch/process", methods=["POST"])
    def api_abf_batch_process():
        """Process a batch of ABF files and extract photocurrent peaks."""
        d = request.json or {}
        try:
            result = _abf_batch_process_payload(d)
            return api_ok(result, outputs=result.get("outputs"), warnings=result.get("warnings"))
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/abf_batch/process_job", methods=["POST"])
    def api_abf_batch_process_job():
        return submit_json_task(
            jobs,
            "abf_batch.process",
            "Run ABF batch processing",
            _abf_batch_process_task,
            request.json or {},
            metadata={"endpoint": "/api/abf_batch/process"},
        )
