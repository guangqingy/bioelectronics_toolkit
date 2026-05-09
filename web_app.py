#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataProcess Web Application

A browser frontend for electrophysiology, electrochemistry,
fluorescence imaging, and scripted data analysis.
"""

import argparse
import base64
import io
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, request, send_from_directory

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from web_api.abf_batch import register_abf_batch_routes
from web_api.abf_viewer import register_abf_viewer_routes
from web_api.context import WebApiContext
from web_api.csv_viewer import register_csv_viewer_routes
from web_api.echem_lineshape import register_echem_lineshape_routes
from web_api.echem_pc import register_echem_pc_routes
from web_api.echem_pv import register_echem_pv_routes
from web_api.emg_peaks import register_emg_peaks_routes
from web_api.figure_generator import register_figure_generator_routes
from web_api.file_profiles import register_file_profile_routes
from web_api.fluorescence import register_fluorescence_routes
from web_api.histology import register_histology_routes
from web_api.jobs import JobManager, register_job_routes
from web_api.lif_viewer import register_lif_viewer_routes
from web_api.pages import register_page_routes
from web_api.preferences import register_preferences_routes
from web_api.response import api_error, register_api_envelope
from web_api.rhd_viewer import register_rhd_viewer_routes
from web_api.run_history import register_run_history_routes
from web_api.scripts_panel import register_scripts_panel_routes
from web_api.system import register_system_routes

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "web_templates"),
    static_folder=str(BASE_DIR / "web_static"),
    static_url_path="/static",
)


@app.context_processor
def inject_template_defaults():
    return {"default_data_dir": str(BASE_DIR)}


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.ico")


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


pyabf = None
try:
    import pyabf

    HAS_ABF = True
except ImportError:
    HAS_ABF = False

rhd = None
try:
    from vendor.intan import importrhdutilities as rhd

    HAS_RHD = True
except ImportError:
    HAS_RHD = False

find_peaks = None
peak_widths = None
savgol_filter = None
f_oneway = None
try:
    from scipy.signal import find_peaks, peak_widths, savgol_filter
    from scipy.stats import f_oneway

    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

MultiComparison = None
try:
    from statsmodels.stats.multicomp import MultiComparison

    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False

tifflib = None
try:
    import tifffile as tifflib

    HAS_TIFF = True
except ImportError:
    HAS_TIFF = False

Image = None
ImageDraw = None
ImageFont = None
try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

LifFile = None
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


_job_manager = JobManager()
_web_api_ctx = WebApiContext(
    err=err,
    browse_files=browse_files,
    browse_files_recursive=browse_files_recursive,
    fig_to_b64=fig_to_b64,
    float_or=float_or,
    int_or=int_or,
    request_data=request_data,
    apply_axes_limits=apply_axes_limits,
    BASE_DIR=BASE_DIR,
    LINE_COLOR=LINE_COLOR,
    HAS_ABF=HAS_ABF,
    HAS_RHD=HAS_RHD,
    HAS_SCIPY=HAS_SCIPY,
    HAS_STATSMODELS=HAS_STATSMODELS,
    HAS_TIFF=HAS_TIFF,
    HAS_PIL=HAS_PIL,
    HAS_READLIF=HAS_READLIF,
    pyabf=pyabf,
    rhd=rhd,
    find_peaks=find_peaks,
    savgol_filter=savgol_filter,
    peak_widths=peak_widths,
    f_oneway=f_oneway,
    MultiComparison=MultiComparison,
    tifflib=tifflib,
    Image=Image,
    ImageDraw=ImageDraw,
    ImageFont=ImageFont,
    LifFile=LifFile,
    jobs=_job_manager,
)


register_api_envelope(app)
register_page_routes(app, _web_api_ctx)
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
register_system_routes(app, _web_api_ctx)
register_job_routes(app, _web_api_ctx)


PORT = 7433


def _open_browser():
    time.sleep(0.8)
    webbrowser.open(f"http://localhost:{PORT}")


def main(argv: list[str] | None = None) -> None:
    global PORT

    parser = argparse.ArgumentParser(description="Start the local DataProcess WebGUI server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=PORT, help="Port to serve on.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
    args = parser.parse_args(argv)

    PORT = int(args.port)
    os.chdir(BASE_DIR)
    no_browser = os.environ.get("DATAPROCESS_WEB_NO_BROWSER", "").strip().lower()
    if not args.no_browser and no_browser not in {"1", "true", "yes", "on"}:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"\n  DataProcess Web  ->  http://localhost:{PORT}\n")
    app.run(host=args.host, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
