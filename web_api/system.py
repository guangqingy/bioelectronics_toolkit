from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from flask import request

from .response import api_ok


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


def _choose_folder(default_dir: Path) -> str:
    if sys.platform == "darwin":
        script = (
            'POSIX path of (choose folder with prompt "Select Data Folder" '
            f'default location POSIX file "{_applescript_string(default_dir)}")'
        )
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else ""

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.update()
    path = filedialog.askdirectory(initialdir=str(default_dir))
    root.destroy()
    return path or ""


def _choose_file(default_dir: Path) -> str:
    if sys.platform == "darwin":
        script = (
            'POSIX path of (choose file with prompt "Select File" '
            f'default location POSIX file "{_applescript_string(default_dir)}")'
        )
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else ""

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.update()
    path = filedialog.askopenfilename(initialdir=str(default_dir))
    root.destroy()
    return path or ""


def register_system_routes(app, ctx) -> None:
    err = ctx["err"]
    base_dir = Path(ctx["BASE_DIR"])

    @app.route("/api/system/select_folder", methods=["POST"])
    def api_system_select_folder():
        try:
            d = request.json or {}
            default_dir = _default_picker_dir(base_dir, str(d.get("start", "") or "").strip())
            path = _choose_folder(default_dir)
            return api_ok({"path": path, "cancelled": not bool(path)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/system/select_file", methods=["POST"])
    def api_system_select_file():
        try:
            d = request.json or {}
            default_dir = _default_picker_dir(base_dir, str(d.get("start", "") or "").strip())
            path = _choose_file(default_dir)
            return api_ok({"path": path, "cancelled": not bool(path)})
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
