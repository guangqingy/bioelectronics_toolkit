from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from pydantic import ValidationError

from .request_validation import RequestModel, parse_json_payload, validation_error_response
from .response import api_ok

_PICKER_LOCK = threading.Lock()


class PickerUnavailableError(RuntimeError):
    """Raised when a native file picker cannot be opened."""


class PickerRequest(RequestModel):
    start: str = ""


def _applescript_string(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _default_picker_dir(base_dir: Path, start: str) -> Path:
    default_dir = Path(base_dir)
    if not start:
        return default_dir
    try:
        p = Path(start).expanduser()
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p
        if p.parent.exists():
            return p.parent
    except Exception:
        pass
    return default_dir


def _allow_windows_foreground() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass


def _windows_picker_error(kind: str, detail: str = "") -> PickerUnavailableError:
    noun = "Folder" if kind == "folder" else "File"
    message = f"{noun} picker unavailable; please paste path manually."
    detail = detail.strip()
    if detail:
        message = f"{message} Detail: {detail}"
    return PickerUnavailableError(message)


def _run_windows_picker(script: str, default_dir: Path, kind: str) -> str:
    env = os.environ.copy()
    env["DP_PICKER_INITIAL_DIR"] = str(default_dir)
    _allow_windows_foreground()
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise _windows_picker_error(kind, str(exc)) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise _windows_picker_error(kind, detail)
    return proc.stdout.strip()


def _choose_windows_folder(default_dir: Path) -> str:
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$initial = $env:DP_PICKER_INITIAL_DIR
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select Data Folder'
$dialog.ShowNewFolderButton = $true
if ($initial -and [System.IO.Directory]::Exists($initial)) {
    $dialog.SelectedPath = $initial
}

$owner = New-Object System.Windows.Forms.Form
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
try {
    $owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $dialog.SelectedPath
    }
}
finally {
    $dialog.Dispose()
    $owner.Dispose()
}
"""
    return _run_windows_picker(script, default_dir, "folder")


def _choose_windows_file(default_dir: Path) -> str:
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$initial = $env:DP_PICKER_INITIAL_DIR
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Select File'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
if ($initial -and [System.IO.Directory]::Exists($initial)) {
    $dialog.InitialDirectory = $initial
}

$owner = New-Object System.Windows.Forms.Form
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
try {
    $owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $dialog.FileName
    }
}
finally {
    $dialog.Dispose()
    $owner.Dispose()
}
"""
    return _run_windows_picker(script, default_dir, "file")


def _choose_tk_folder(default_dir: Path) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    try:
        path = filedialog.askdirectory(initialdir=str(default_dir), parent=root)
    finally:
        root.destroy()
    return path or ""


def _choose_tk_file(default_dir: Path) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    try:
        path = filedialog.askopenfilename(initialdir=str(default_dir), parent=root)
    finally:
        root.destroy()
    return path or ""


def _choose_folder(default_dir: Path) -> str:
    with _PICKER_LOCK:
        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose folder with prompt "Select Data Folder" '
                f'default location POSIX file "{_applescript_string(default_dir)}")'
            )
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return out.stdout.strip() if out.returncode == 0 else ""
        if sys.platform == "win32":
            return _choose_windows_folder(default_dir)
        return _choose_tk_folder(default_dir)


def _choose_file(default_dir: Path) -> str:
    with _PICKER_LOCK:
        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose file with prompt "Select File" '
                f'default location POSIX file "{_applescript_string(default_dir)}")'
            )
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return out.stdout.strip() if out.returncode == 0 else ""
        if sys.platform == "win32":
            return _choose_windows_file(default_dir)
        return _choose_tk_file(default_dir)


def register_system_routes(app, ctx) -> None:
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])

    @app.route("/api/system/select_folder", methods=["POST"])
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
