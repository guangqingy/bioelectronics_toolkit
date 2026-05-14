from __future__ import annotations

import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from pydantic import ValidationError

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

            def _shutdown_server():
                time.sleep(0.25)
                try:
                    os.kill(os.getpid(), signal.SIGINT)
                except Exception:
                    os._exit(0)

            threading.Thread(target=_shutdown_server, daemon=True).start()
            return api_ok({"message": "DataProcess Web is closing..."})
        except Exception:
            return err(traceback.format_exc())
