#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataProcess Web Application

A browser frontend for electrophysiology, electrochemistry,
fluorescence imaging, and scripted data analysis.
"""

import base64
import io
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from web_api.abf_batch import register_abf_batch_routes
from web_api.abf_viewer import register_abf_viewer_routes
from web_api.csv_viewer import register_csv_viewer_routes
from web_api.echem_lineshape import register_echem_lineshape_routes
from web_api.echem_pc import register_echem_pc_routes
from web_api.echem_pv import register_echem_pv_routes
from web_api.emg_peaks import register_emg_peaks_routes
from web_api.file_profiles import register_file_profile_routes
from web_api.figure_generator import register_figure_generator_routes
from web_api.fluorescence import register_fluorescence_routes
from web_api.histology import register_histology_routes
from web_api.jobs import JobManager, register_job_routes
from web_api.lif_viewer import register_lif_viewer_routes
from web_api.preferences import register_preferences_routes
from web_api.response import api_error, register_api_envelope
from web_api.rhd_viewer import register_rhd_viewer_routes
from web_api.run_history import register_run_history_routes
from web_api.scripts_panel import register_scripts_panel_routes

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web_templates"),
    static_folder=str(BASE_DIR / "web_static"),
    static_url_path="/static",
)


@app.context_processor
def inject_template_defaults():
    return {"default_data_dir": str(BASE_DIR)}


plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.8,
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "grid.color": "#EEEEEE",
        "grid.linewidth": 0.5,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
    }
)
LINE_COLOR = "#3E6AE1"


try:
    import pyabf

    HAS_ABF = True
except ImportError:
    HAS_ABF = False

try:
    import importrhdutilities as rhd

    HAS_RHD = True
except ImportError:
    HAS_RHD = False

try:
    from scipy.signal import find_peaks, peak_widths, savgol_filter
    from scipy.stats import f_oneway, ttest_ind

    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    from statsmodels.stats.multicomp import MultiComparison

    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

try:
    import tifffile as tifflib

    HAS_TIFF = True
except ImportError:
    HAS_TIFF = False

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from readlif.reader import LifFile

    HAS_READLIF = True
except ImportError:
    HAS_READLIF = False


def fig_to_b64(fig, dpi=130, fmt="png"):
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def err(msg, code=400):
    return api_error(msg, code)


def browse_files(folder, exts):
    p = Path(folder)
    if not p.is_dir():
        return []
    result = []
    for f in sorted(p.iterdir()):
        if f.suffix.lower() in exts:
            result.append({"name": f.name, "path": str(f)})
    return result


def browse_files_recursive(folder, exts, max_files=300):
    p = Path(folder)
    if not p.is_dir():
        return []
    result = []
    for f in sorted(p.rglob("*")):
        if f.suffix.lower() in exts:
            result.append({"name": f.name, "path": str(f), "rel": str(f.relative_to(p))})
            if len(result) >= max_files:
                break
    return result


def float_or(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def int_or(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def request_data():
    if request.method == "GET":
        return request.args
    return request.json or {}


def applescript_string(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def apply_axes_limits(ax, xmin, xmax, ymin, ymax):
    if xmin is not None or xmax is not None:
        cur = ax.get_xlim()
        ax.set_xlim(
            xmin if xmin is not None else cur[0],
            xmax if xmax is not None else cur[1],
        )
    if ymin is not None or ymax is not None:
        cur = ax.get_ylim()
        ax.set_ylim(
            ymin if ymin is not None else cur[0],
            ymax if ymax is not None else cur[1],
        )


_web_api_ctx = {
    "err": err,
    "browse_files": browse_files,
    "browse_files_recursive": browse_files_recursive,
    "fig_to_b64": fig_to_b64,
    "float_or": float_or,
    "int_or": int_or,
    "request_data": request_data,
    "apply_axes_limits": apply_axes_limits,
    "BASE_DIR": BASE_DIR,
    "LINE_COLOR": LINE_COLOR,
    "HAS_ABF": HAS_ABF,
    "HAS_RHD": HAS_RHD,
    "HAS_SCIPY": HAS_SCIPY,
    "HAS_STATSMODELS": HAS_STATSMODELS,
    "HAS_TIFF": HAS_TIFF,
    "HAS_PIL": HAS_PIL,
    "HAS_READLIF": HAS_READLIF,
    "pyabf": globals().get("pyabf"),
    "rhd": globals().get("rhd"),
    "find_peaks": globals().get("find_peaks"),
    "savgol_filter": globals().get("savgol_filter"),
    "peak_widths": globals().get("peak_widths"),
    "f_oneway": globals().get("f_oneway"),
    "MultiComparison": globals().get("MultiComparison"),
    "tifflib": globals().get("tifflib"),
    "Image": globals().get("Image"),
    "ImageDraw": globals().get("ImageDraw"),
    "ImageFont": globals().get("ImageFont"),
    "LifFile": globals().get("LifFile"),
}

_job_manager = JobManager()
_web_api_ctx["jobs"] = _job_manager


register_api_envelope(app)
register_csv_viewer_routes(app, _web_api_ctx)
register_abf_viewer_routes(app, _web_api_ctx)
register_abf_batch_routes(app, _web_api_ctx)
register_figure_generator_routes(app, _web_api_ctx)
register_echem_pc_routes(app, _web_api_ctx)
register_echem_pv_routes(app, _web_api_ctx)
register_echem_lineshape_routes(app, _web_api_ctx)
register_rhd_viewer_routes(app, _web_api_ctx)
register_emg_peaks_routes(app, _web_api_ctx)
register_fluorescence_routes(app, _web_api_ctx)
register_lif_viewer_routes(app, _web_api_ctx)
register_histology_routes(app, _web_api_ctx)
register_scripts_panel_routes(app, _web_api_ctx)
register_preferences_routes(app, _web_api_ctx)
register_file_profile_routes(app, _web_api_ctx)
register_run_history_routes(app, _web_api_ctx)
register_job_routes(app, _web_api_ctx)


@app.route("/")
def index():
    return render_template("index.html", active="index")


@app.route("/csv")
def csv_viewer():
    return render_template("csv_viewer.html", active="csv_viewer")


@app.route("/abf/viewer")
def abf_viewer():
    return render_template("abf_viewer.html", active="abf_viewer", has_abf=HAS_ABF)


@app.route("/abf/batch")
def abf_batch():
    return render_template("abf_batch.html", active="abf_batch", has_abf=HAS_ABF)


@app.route("/abf/figure")
def abf_figure():
    return render_template("abf_figure.html", active="abf_figure")


@app.route("/abf/peaks")
def abf_peaks():
    return render_template("abf_peakdet.html", active="abf_peaks", has_abf=HAS_ABF)


@app.route("/echem/photocurrent")
def echem_pc():
    return render_template("echem_pc.html", active="echem_pc")


@app.route("/echem/photovoltage")
def echem_pv():
    return render_template("echem_pv.html", active="echem_pv")


@app.route("/echem/lineshape")
def echem_lineshape():
    return render_template("echem_lineshape.html", active="echem_lineshape")


@app.route("/emg/rhd")
def rhd_viewer():
    return render_template("rhd_viewer.html", active="rhd_viewer", has_rhd=HAS_RHD)


@app.route("/emg/peaks")
def emg_peaks():
    return render_template("emg_peaks.html", active="emg_peaks", has_scipy=HAS_SCIPY)


@app.route("/fluorescence")
def fluorescence():
    return render_template(
        "fluorescence.html",
        active="fluorescence",
        has_tiff=HAS_TIFF,
        has_pil=HAS_PIL,
    )


@app.route("/fluorescence/roi")
def fluorescence_roi():
    return render_template(
        "fluorescence_roi.html",
        active="fluorescence_roi",
        has_tiff=HAS_TIFF,
    )


@app.route("/fluorescence/lif")
def fluorescence_lif():
    return render_template(
        "fluorescence_lif.html",
        active="fluorescence_lif",
        has_readlif=HAS_READLIF,
        has_pil=HAS_PIL,
        has_tiff=HAS_TIFF,
    )


@app.route("/fluorescence/3d-stacking")
def fluorescence_3d_stacking():
    return render_template(
        "fluorescence_3d_stacking.html",
        active="fluorescence_3d_stacking",
        has_tiff=HAS_TIFF,
        has_pil=HAS_PIL,
    )


@app.route("/histology")
def histology():
    return render_template("histology.html", active="histology")


@app.route("/fluorescence/gif")
def fluorescence_gif():
    return render_template(
        "fluorescence_gif.html",
        active="fluorescence_gif",
        has_tiff=HAS_TIFF,
        has_pil=HAS_PIL,
    )


@app.route("/scripts")
@app.route("/scripts/<cat>")
def scripts(cat="photocurrent"):
    if cat not in {"photocurrent", "emg", "echem_curves", "viability"}:
        cat = "photocurrent"
    return render_template("scripts.html", active="scripts", cat=cat, cat_explicit=request.path != "/scripts")


@app.route("/runs")
def run_history_page():
    return render_template("run_history.html", active="run_history")


@app.route("/api/system/select_folder", methods=["POST"])
def api_system_select_folder():
    try:
        d = request.json or {}
        start = str(d.get("start", "") or "").strip()

        default_dir = Path(BASE_DIR)
        if start:
            try:
                p = Path(start).expanduser()
                if p.is_file():
                    default_dir = p.parent
                elif p.is_dir():
                    default_dir = p
                elif p.parent.exists():
                    default_dir = p.parent
            except Exception:
                pass

        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose folder with prompt "Select Data Folder" '
                f'default location POSIX file "{applescript_string(default_dir)}")'
            )
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if out.returncode != 0:
                return jsonify({"path": "", "cancelled": True})
            path = out.stdout.strip()
            return jsonify({"path": path, "cancelled": not bool(path)})

        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.update()
        path = filedialog.askdirectory(initialdir=str(default_dir))
        root.destroy()
        return jsonify({"path": path or "", "cancelled": not bool(path)})
    except Exception:
        return err(traceback.format_exc())


@app.route("/api/system/select_file", methods=["POST"])
def api_system_select_file():
    try:
        d = request.json or {}
        start = str(d.get("start", "") or "").strip()

        default_dir = Path(BASE_DIR)
        if start:
            try:
                p = Path(start).expanduser()
                if p.is_file():
                    default_dir = p.parent
                elif p.is_dir():
                    default_dir = p
                elif p.parent.exists():
                    default_dir = p.parent
            except Exception:
                pass

        if sys.platform == "darwin":
            script = (
                'POSIX path of (choose file with prompt "Select File" '
                f'default location POSIX file "{applescript_string(default_dir)}")'
            )
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if out.returncode != 0:
                return jsonify({"path": "", "cancelled": True})
            path = out.stdout.strip()
            return jsonify({"path": path, "cancelled": not bool(path)})

        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.update()
        path = filedialog.askopenfilename(initialdir=str(default_dir))
        root.destroy()
        return jsonify({"path": path or "", "cancelled": not bool(path)})
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
        return jsonify({"ok": True, "message": "DataProcess Web is closing..."})
    except Exception:
        return err(traceback.format_exc())


PORT = 7433


def _open_browser():
    time.sleep(0.8)
    webbrowser.open(f"http://localhost:{PORT}")


def main() -> None:
    os.chdir(BASE_DIR)
    if os.environ.get("DATAPROCESS_WEB_NO_BROWSER", "").strip().lower() not in {"1", "true", "yes", "on"}:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"\n  DataProcess Web  ->  http://localhost:{PORT}\n")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
