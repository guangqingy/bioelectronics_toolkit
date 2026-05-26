from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path

from pydantic import ValidationError

from services import scripts_panel as script_service
from services import system_picker
from services.system_picker import (
    PickerUnavailableError,
    _applescript_string,
    _default_picker_dir,
)

from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)
from .response import api_ok


class PickerRequest(RequestModel):
    start: str = ""


_windows_picker_error = system_picker._windows_picker_error
_choose_windows_folder = system_picker._choose_windows_folder
_choose_windows_file = system_picker._choose_windows_file
_choose_tk_folder = system_picker._choose_tk_folder
_choose_tk_file = system_picker._choose_tk_file


def _cancel_running_jobs(jobs) -> int:
    if not jobs:
        return 0
    cancelled = 0
    try:
        candidates = jobs.list(limit=getattr(jobs, "max_jobs", 200), include_finished=False)
    except Exception:
        return 0
    for job in candidates:
        if job.get("status") not in {"pending", "running"}:
            continue
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        try:
            if jobs.request_cancel(job_id):
                cancelled += 1
        except Exception:
            continue
    return cancelled


def _shutdown_current_process(jobs, delay_seconds: float = 0.35) -> None:
    time.sleep(max(0.0, float(delay_seconds)))
    try:
        _cancel_running_jobs(jobs)
    except Exception:
        pass
    try:
        script_service.shutdown_running_scripts(grace_seconds=1.0)
    except Exception:
        pass
    os._exit(0)


def _choose_folder(default_dir: Path) -> str:
    with system_picker._PICKER_LOCK:
        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose folder with prompt "Select Data Folder" '
                f'default location POSIX file "{_applescript_string(default_dir)}")'
            )
            out = system_picker.subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        if sys.platform == "win32":
            return _choose_windows_folder(default_dir)
        return _choose_tk_folder(default_dir)


def _choose_file(default_dir: Path) -> str:
    with system_picker._PICKER_LOCK:
        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose file with prompt "Select File" '
                f'default location POSIX file "{_applescript_string(default_dir)}")'
            )
            out = system_picker.subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        if sys.platform == "win32":
            return _choose_windows_file(default_dir)
        return _choose_tk_file(default_dir)


def register_system_routes(app, ctx) -> None:
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])
    jobs = ctx.get("jobs")

    @app.route("/api/system/select_folder", methods=["POST"])
    @request_schema(PickerRequest)
    def api_system_select_folder():
        try:
            payload = parse_json_payload(PickerRequest)
            default_dir = _default_picker_dir(base_dir, payload.start.strip())
            path = _choose_folder(default_dir)
            return api_ok({"path": path, "cancelled": not bool(path)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except PickerUnavailableError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/system/select_file", methods=["POST"])
    @request_schema(PickerRequest)
    def api_system_select_file():
        try:
            payload = parse_json_payload(PickerRequest)
            default_dir = _default_picker_dir(base_dir, payload.start.strip())
            path = _choose_file(default_dir)
            return api_ok({"path": path, "cancelled": not bool(path)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except PickerUnavailableError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/system/logout", methods=["POST"])
    def api_system_logout():
        try:
            cancelled_jobs = _cancel_running_jobs(jobs)
            shutdown_handler = app.config.get("DATAPROCESS_LOGOUT_HANDLER")
            if callable(shutdown_handler):
                shutdown_handler(jobs)
            else:
                threading.Thread(
                    target=_shutdown_current_process,
                    args=(jobs,),
                    daemon=True,
                    name="dataprocess-logout-shutdown",
                ).start()
            return api_ok(
                {
                    "message": "DataProcess Web is closing...",
                    "cancelled_jobs": cancelled_jobs,
                    "shutdown": True,
                }
            )
        except Exception:
            return err(traceback.format_exc())
