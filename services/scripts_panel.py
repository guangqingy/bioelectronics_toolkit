from __future__ import annotations

import base64
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

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

_running_scripts = {}
_running_script_processes = {}
_PROCESS_LOCK = threading.Lock()


def _register_script_process(job_id, proc):
    with _PROCESS_LOCK:
        _running_script_processes[job_id] = proc


def _forget_script_process(job_id):
    with _PROCESS_LOCK:
        _running_script_processes.pop(job_id, None)


def _terminate_script_process(proc, grace_seconds=1.0):
    if not proc or proc.poll() is not None:
        return False

    def _terminate():
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                return True
            except ProcessLookupError:
                return True
            except Exception:
                pass
        try:
            proc.terminate()
            return True
        except ProcessLookupError:
            return True
        except Exception:
            return False

    def _kill():
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return True
            except ProcessLookupError:
                return True
            except Exception:
                pass
        try:
            proc.kill()
            return True
        except ProcessLookupError:
            return True
        except Exception:
            return False

    sent = _terminate()
    try:
        proc.wait(timeout=max(0.1, float(grace_seconds or 0.1)))
        return sent
    except subprocess.TimeoutExpired:
        killed = _kill()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        return sent or killed


def shutdown_running_scripts(grace_seconds=1.0):
    with _PROCESS_LOCK:
        items = list(_running_script_processes.items())
    terminated = 0
    for job_id, proc in items:
        if _terminate_script_process(proc, grace_seconds=grace_seconds):
            terminated += 1
        _forget_script_process(job_id)
    return {"process_count": len(items), "terminated_count": terminated}


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
    proc = None
    try:
        job_ctx.check_cancelled()
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "env": env,
            "cwd": str(Path(script_path).parent),
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen([sys.executable, script_path], **popen_kwargs)
        _register_script_process(job_id, proc)
        deadline = time.time() + 300
        while proc.poll() is None:
            job_ctx.check_cancelled()
            if time.time() > deadline:
                _terminate_script_process(proc, grace_seconds=0.2)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(
                    [sys.executable, script_path], 300, output=stdout, stderr=stderr
                )
            time.sleep(0.25)
        stdout, stderr = proc.communicate()
        artifacts, resolved_output_dir = _collect_job_artifacts(script_path, output_dir, started_at)
        payload = {
            "job_id": job_id,
            "done": True,
            "ok": proc.returncode == 0,
            "stdout": stdout[-3000:],
            "message": stdout[-500:],
            "stderr": stderr[-2000:],
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
        if e.__class__.__name__ == "JobCancelled":
            _terminate_script_process(proc, grace_seconds=1.0)
            raise
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
    finally:
        _forget_script_process(job_id)


def _script_status_payload(job_id, jobs=None, fallback_output_dir=""):
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


def figures_from_artifacts(artifacts):
    return _figures_from_artifacts(artifacts)


def resolve_output_dir(script_path, params):
    return _resolve_output_dir(script_path, params)


def run_script_job(job_ctx, script_path, env_vars, output_dir):
    return _run_script_job(job_ctx, script_path, env_vars, output_dir)


def script_status_payload(job_id, jobs=None, fallback_output_dir=""):
    return _script_status_payload(job_id, jobs, fallback_output_dir)
