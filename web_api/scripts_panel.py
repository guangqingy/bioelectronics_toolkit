import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from flask import jsonify, request


_running_scripts = {}

ARTIFACT_EXTS = {
    ".png",
    ".svg",
    ".pdf",
    ".csv",
    ".xlsx",
    ".xls",
    ".json",
    ".txt",
    ".npz",
    ".pt",
}
PREVIEW_IMAGE_LIMIT = 6
ARTIFACT_LIMIT = 240


def register_scripts_panel_routes(app, ctx):
    err = ctx["err"]
    base_dir = ctx["BASE_DIR"]
    jobs = ctx.get("jobs")

    def _env_value(value):
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(v) for v in value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _artifact_record(path, root, started_at):
        try:
            stat = path.stat()
        except OSError:
            return None
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        return {
            "name": path.name,
            "path": str(path),
            "rel": rel,
            "ext": path.suffix.lower().lstrip(".") or "file",
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "updated": bool(started_at and stat.st_mtime >= started_at - 0.05),
        }

    def _collect_artifacts(root, started_at=0.0):
        if not root:
            return []
        root = Path(root).expanduser()
        if not root.is_dir():
            return []

        out = []
        for path in sorted(root.rglob("*"), key=lambda p: p.as_posix().lower()):
            if not path.is_file() or path.suffix.lower() not in ARTIFACT_EXTS:
                continue
            rec = _artifact_record(path, root, started_at)
            if rec:
                out.append(rec)

        out.sort(key=lambda r: (not r["updated"], r["rel"].lower()))
        return out[:ARTIFACT_LIMIT]

    def _figures_from_artifacts(artifacts):
        figures = []
        for rec in artifacts:
            if rec.get("ext") != "png":
                continue
            try:
                with open(rec["path"], "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                figures.append({"name": Path(rec["name"]).stem, "img": b64, "path": rec["path"]})
            except OSError:
                continue
            if len(figures) >= PREVIEW_IMAGE_LIMIT:
                break
        return figures

    def _resolve_output_dir(script_path, params):
        def _as_base_dir(raw_base):
            p = Path(raw_base).expanduser()
            if p.is_file() or (not p.exists() and p.suffix):
                return p.parent
            return p

        raw_out = str(params.get("output_dir", "")).strip()
        if not raw_out:
            raw_output_path = str(params.get("output_path", "")).strip()
            if not raw_output_path:
                return ""

            output_path = Path(raw_output_path).expanduser()
            if output_path.is_absolute():
                return str(output_path.parent)

            for key in ["base_dir", "peaks_dir", "data_dir", "model_dir", "csv_path", "input_path"]:
                raw_base = str(params.get(key, "")).strip()
                if not raw_base:
                    continue
                base_path = _as_base_dir(raw_base)
                return str((base_path / output_path).resolve().parent)

            return str((Path(script_path).parent / output_path).resolve().parent)

        out_path = Path(raw_out).expanduser()
        if out_path.is_absolute():
            return str(out_path)

        for key in ["base_dir", "peaks_dir", "data_dir", "model_dir", "csv_path", "input_path"]:
            raw_base = str(params.get(key, "")).strip()
            if not raw_base:
                continue
            base_path = _as_base_dir(raw_base)
            return str((base_path / out_path).resolve())

        return str((Path(script_path).parent / out_path).resolve())

    def _artifact_root(script_path, output_dir):
        out = Path(output_dir).expanduser() if output_dir else None
        if out and out.is_dir():
            return out
        if output_dir:
            return out
        return Path(script_path).parent

    def _prefer_run_artifacts(artifacts):
        updated = [rec for rec in artifacts if rec.get("updated")]
        return updated if updated else artifacts

    def _common_artifact_dir(artifacts, fallback_root):
        parents = [str(Path(rec["path"]).parent) for rec in artifacts if rec.get("path")]
        if not parents:
            return str(fallback_root or "")
        try:
            return os.path.commonpath(parents)
        except ValueError:
            return str(fallback_root or "")

    def _collect_job_artifacts(script_path, output_dir, started_at):
        root = _artifact_root(script_path, output_dir)
        artifacts = _collect_artifacts(root, started_at)
        if output_dir and not any(rec.get("updated") for rec in artifacts):
            fallback_root = Path(script_path).parent
            fallback_artifacts = _collect_artifacts(fallback_root, started_at)
            if any(rec.get("updated") for rec in fallback_artifacts):
                current = _prefer_run_artifacts(fallback_artifacts)
                return current, _common_artifact_dir(current, fallback_root)
        current = _prefer_run_artifacts(artifacts)
        return current, _common_artifact_dir(current, root)

    def _run_script_job(job_ctx, script_path, env_vars, output_dir):
        """Background worker that runs a Python script as a subprocess."""
        job_id = job_ctx.job_id
        env = os.environ.copy()
        env.update({f"DP_{k.upper()}": _env_value(v) for k, v in env_vars.items()})
        started_at = time.time()
        _running_scripts[job_id] = {"done": False, "ok": True, "output_dir": str(output_dir or "")}
        job_ctx.set_progress(0.05, "Starting script")
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
                cwd=str(Path(script_path).parent),
            )
            artifacts, resolved_output_dir = _collect_job_artifacts(script_path, output_dir, started_at)
            payload = {
                "job_id": job_id,
                "done": True,
                "ok": result.returncode == 0,
                "stdout": result.stdout[-3000:],
                "message": result.stdout[-500:],
                "stderr": result.stderr[-2000:],
                "artifacts": artifacts,
                "output_dir": resolved_output_dir,
                "figures": _figures_from_artifacts(artifacts),
            }
            _running_scripts[job_id] = payload
            return payload
        except subprocess.TimeoutExpired:
            artifacts, resolved_output_dir = _collect_job_artifacts(script_path, output_dir, started_at)
            payload = {
                "job_id": job_id,
                "done": True,
                "ok": False,
                "message": "",
                "stderr": "Timeout (5 min)",
                "artifacts": artifacts,
                "output_dir": resolved_output_dir,
                "figures": _figures_from_artifacts(artifacts),
            }
            _running_scripts[job_id] = payload
            return payload
        except Exception as e:
            artifacts, resolved_output_dir = _collect_job_artifacts(script_path, output_dir, started_at)
            payload = {
                "job_id": job_id,
                "done": True,
                "ok": False,
                "message": "",
                "stderr": str(e),
                "artifacts": artifacts,
                "output_dir": resolved_output_dir,
                "figures": _figures_from_artifacts(artifacts),
            }
            _running_scripts[job_id] = payload
            return payload

    def _script_status_payload(job_id, fallback_output_dir=""):
        job = jobs.get(job_id) if jobs else None
        if job:
            data = job.get("data") if isinstance(job.get("data"), dict) else {}
            if data.get("done"):
                payload = dict(data)
            else:
                payload = {
                    "job_id": job_id,
                    "done": job.get("status") in {"succeeded", "failed", "cancelled"},
                    "ok": job.get("status") != "failed",
                    "message": job.get("message", ""),
                    "stderr": job.get("error") or "",
                    "output_dir": (job.get("metadata") or {}).get("output_dir", fallback_output_dir),
                    "artifacts": [],
                    "figures": [],
                }
            payload["job_status"] = job.get("status")
            payload["progress"] = job.get("progress")
            return payload
        return _running_scripts.get(job_id)

    @app.route("/api/scripts/run", methods=["POST"])
    def api_scripts_run():
        """
        For analysis scripts: attempt to run the script with modified parameters
        injected via environment variables (DP_BASE_DIR, DP_OUTPUT_DIR, etc.).
        """
        d = request.json or {}
        script_id = d.get("script_id", "")
        params = d.get("params", {})

        script_map = {
            # Photocurrent
            "pc_line_chart":    "2025_Subcutaneous/Photocurrent/model_line_chart.py",
            "pc_peaks_overlay": "2025_Subcutaneous/Photocurrent/model_peaks_overlay.py",
            "pc_heatmap":       "2025_Subcutaneous/Photocurrent/model_heatmap.py",
            "pc_decay":         "2025_Subcutaneous/Photocurrent/model_decay_curve.py",
            "pc_longterm":      "2025_Subcutaneous/Photocurrent/model_longterm_bar.py",
            # EMG
            "emg_demo":         "2025_Subcutaneous/EMG/model_demo_single_peak.py",
            "emg_overlay":      "2025_Subcutaneous/EMG/model_overlay_mean.py",
            "emg_bar":          "2025_Subcutaneous/EMG/model_bar_diagram.py",
            "emg_heatmap":      "2025_Subcutaneous/EMG/model_heatmap.py",
            # Electrochemistry curves
            "echem_pc_curve":      "2025_Subcutaneous/Electrochemistry/group_1_photocurrent_curve.py",
            "echem_pv_curve":      "2025_Subcutaneous/Electrochemistry/group_1_photovoltage_curve.py",
            # Cell viability
            "viab_watershed_area": "2025_Subcutaneous/Cell_Number_Counting/model_watershed_area_ratio.py",
            "viab_stardist_auto":  "2025_Subcutaneous/Cell_Number_Counting/model_stardist_auto_ratio.py",
        }

        script_rel = script_map.get(script_id)
        if script_rel:
            script_path = base_dir / script_rel
            if not script_path.exists():
                script_path = None
        else:
            script_path = None

        if script_path and script_path.exists():
            out_dir = _resolve_output_dir(script_path, params)
            if not jobs:
                return err("Job manager is not available")
            job = jobs.submit(
                "script",
                f"Pipeline script: {script_id}",
                _run_script_job,
                str(script_path),
                params,
                out_dir,
                metadata={
                    "script_id": script_id,
                    "category": d.get("cat", ""),
                    "script_path": str(script_path),
                    "output_dir": str(out_dir or ""),
                },
            )
            job_id = job["job_id"]
            deadline = time.time() + 15
            result = _script_status_payload(job_id, str(out_dir or "")) or {}
            while time.time() < deadline and not result.get("done"):
                time.sleep(0.15)
                result = _script_status_payload(job_id, str(out_dir or "")) or {}

            if result.get("done"):
                artifacts = result.get("artifacts", [])
                figures = _figures_from_artifacts(artifacts)
                return jsonify(
                    {
                        "message": result.get("stdout", "Done")[:500],
                        "ok": result.get("ok", False),
                        "stderr": result.get("stderr", "")[:1000],
                        "figures": figures,
                        "output_dir": result.get("output_dir", str(out_dir) if out_dir else ""),
                        "artifacts": artifacts,
                        "job_id": job_id,
                    }
                )

            return jsonify(
                {
                    "message": "Script running in background (>15s). Check output folder.",
                    "ok": True,
                    "figures": [],
                    "output_dir": str(out_dir) if out_dir else "",
                    "artifacts": [],
                    "job_id": job_id,
                    "running": True,
                }
            )

        return jsonify(
            {
                "message": (
                    f"Script '{script_id}' is not directly runnable via web panel yet. "
                    "Copy the parameters below into the corresponding group_*.py PANEL block and run manually."
                ),
                "ok": True,
                "config_preview": json.dumps(params, indent=2),
                "figures": [],
                "artifacts": [],
            }
        )

    @app.route("/api/scripts/status", methods=["POST"])
    def api_scripts_status():
        job_id = (request.json or {}).get("job_id", "")
        result = _script_status_payload(job_id)
        if not result:
            return err(f"Unknown script job: {job_id}", 404)
        artifacts = result.get("artifacts", [])
        return jsonify(
            {
                "job_id": job_id,
                "done": bool(result.get("done")),
                "ok": bool(result.get("ok", False)) if result.get("done") else True,
                "message": (result.get("message") or result.get("stdout", ""))[:500],
                "stderr": result.get("stderr", "")[:1000],
                "output_dir": result.get("output_dir", ""),
                "artifacts": artifacts,
                "figures": _figures_from_artifacts(artifacts) if result.get("done") else [],
                "job_status": result.get("job_status", ""),
                "progress": result.get("progress"),
            }
        )

    @app.route("/api/scripts/open_folder", methods=["POST"])
    def api_scripts_open_folder():
        path = (request.json or {}).get("path", "")
        p = Path(path)
        if p.is_dir():
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(p)])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(p)])
                else:
                    subprocess.Popen(["explorer", str(p)])
                return jsonify({"ok": True})
            except Exception as e:
                return err(e)
        return err(f"Not a directory: {path}")
