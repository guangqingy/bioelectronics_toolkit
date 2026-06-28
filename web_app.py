#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataProcess Web Application

A browser frontend for electrophysiology, electrochemistry,
fluorescence imaging, and scripted data analysis.
"""

import argparse
import gzip
import hashlib
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import pyabf
import tifffile as tifflib
from flask import Flask, request, send_file, url_for
from PIL import Image, ImageDraw, ImageFont
from readlif.reader import LifFile
from scipy.signal import find_peaks, peak_widths, savgol_filter
from scipy.stats import f_oneway
from statsmodels.stats.multicomp import MultiComparison

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from web_api.api_docs import register_api_docs_routes
from web_api.abf_batch import register_abf_batch_routes
from web_api.abf_viewer import register_abf_viewer_routes
from web_api.common import (
    apply_axes_limits,
    browse_files,
    browse_files_recursive,
    fig_to_b64,
    float_or,
    int_or,
    request_data,
)
from web_api.context import WebApiContext
from web_api.csv_viewer import register_csv_viewer_routes
from web_api.echem_lineshape import register_echem_lineshape_routes
from web_api.echem_photocurrent import register_echem_photocurrent_routes
from web_api.echem_photovoltage import register_echem_photovoltage_routes
from web_api.emg_peak_selection import register_emg_peak_selection_routes
from web_api.figure_generator import register_figure_generator_routes
from web_api.file_profiles import register_file_profile_routes
from web_api.fluorescence import register_fluorescence_routes
from web_api.histology import register_histology_routes
from web_api.jobs import JobManager, register_job_routes
from web_api.fluorescence_lif import register_fluorescence_lif_routes
from web_api.pages import register_page_routes
from web_api.preferences import register_preferences_routes
from web_api.response import api_error, register_api_envelope
from web_api.emg_analysis import register_emg_analysis_routes
from web_api.run_history import register_run_history_routes
from web_api.system import register_system_routes
from web_api.telemetry import register_telemetry_routes
from services.io_guards import configure_pillow_image_limit
from services.matplotlib_utils import configure_defaults
from services.provenance import git_commit, project_version, version_label

APP_VERSION = project_version(BASE_DIR)
APP_COMMIT = git_commit(BASE_DIR)
APP_VERSION_LABEL = version_label(APP_VERSION, APP_COMMIT)


configure_defaults()
configure_pillow_image_limit(Image)
LINE_COLOR = "#3E6AE1"


rhd = None
try:
    from vendor.intan import importrhdutilities as rhd

    HAS_RHD = True
except ImportError:
    HAS_RHD = False


def err(msg, code=400):
    return api_error(msg, code)


def create_app(
    *,
    base_dir: Path = BASE_DIR,
    jobs: JobManager | None = None,
) -> Flask:
    flask_app = Flask(
        __name__,
        template_folder=str(base_dir / "web_templates"),
        static_folder=str(base_dir / "web_static"),
        static_url_path="/static",
    )
    flask_app.config["APP_VERSION"] = APP_VERSION
    flask_app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31_536_000

    def static_asset(filename: str) -> str:
        rel = str(filename).lstrip("/")
        path = base_dir / "web_static" / rel
        version = APP_COMMIT or APP_VERSION
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            version = f"{version}-{digest}"
        except OSError:
            pass
        return url_for("static", filename=rel, v=version)

    @flask_app.context_processor
    def inject_template_defaults():
        return {
            "app_commit": APP_COMMIT,
            "app_version": APP_VERSION,
            "app_version_label": APP_VERSION_LABEL,
            "default_data_dir": "",
            "default_examples_dir": "examples",
            "static_asset": static_asset,
        }

    @flask_app.after_request
    def optimize_delivery(response):
        if request.path.startswith("/static/"):
            response.cache_control.max_age = 31_536_000
            response.cache_control.public = True
            response.cache_control.immutable = True

        compressible = {
            "application/javascript",
            "application/json",
            "application/xml",
            "image/svg+xml",
            "text/css",
            "text/html",
            "text/javascript",
            "text/plain",
        }
        if (
            response.status_code == 200
            and request.method in {"GET", "POST"}
            and "gzip" in request.headers.get("Accept-Encoding", "").lower()
            and not response.headers.get("Content-Encoding")
            and response.mimetype in compressible
        ):
            response.direct_passthrough = False
            data = response.get_data()
            if len(data) >= 1024:
                response.set_data(gzip.compress(data, compresslevel=5))
                response.headers["Content-Encoding"] = "gzip"
                response.headers["Vary"] = "Accept-Encoding"
        return response

    @flask_app.route("/api/version")
    def api_version():
        return {
            "ok": True,
            "version": APP_VERSION,
            "commit": APP_COMMIT,
            "label": APP_VERSION_LABEL,
        }

    @flask_app.route("/favicon.ico")
    def favicon():
        return send_file(base_dir / "web_static" / "favicon.ico", mimetype="image/x-icon")

    job_manager = jobs or JobManager(persistence_path=base_dir / ".dataprocess_cache" / "jobs.sqlite")
    web_api_ctx = WebApiContext(
        err=err,
        browse_files=browse_files,
        browse_files_recursive=browse_files_recursive,
        fig_to_b64=fig_to_b64,
        float_or=float_or,
        int_or=int_or,
        request_data=request_data,
        apply_axes_limits=apply_axes_limits,
        BASE_DIR=base_dir,
        LINE_COLOR=LINE_COLOR,
        HAS_RHD=HAS_RHD,
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
        jobs=job_manager,
    )

    register_api_envelope(flask_app)
    register_page_routes(flask_app, web_api_ctx)
    register_csv_viewer_routes(flask_app, web_api_ctx)
    register_abf_viewer_routes(flask_app, web_api_ctx)
    register_abf_batch_routes(flask_app, web_api_ctx)
    register_figure_generator_routes(flask_app, web_api_ctx)
    register_echem_photocurrent_routes(flask_app, web_api_ctx)
    register_echem_photovoltage_routes(flask_app, web_api_ctx)
    register_echem_lineshape_routes(flask_app, web_api_ctx)
    register_emg_analysis_routes(flask_app, web_api_ctx)
    register_emg_peak_selection_routes(flask_app, web_api_ctx)
    register_fluorescence_routes(flask_app, web_api_ctx)
    register_fluorescence_lif_routes(flask_app, web_api_ctx)
    register_histology_routes(flask_app, web_api_ctx)
    register_preferences_routes(flask_app, web_api_ctx)
    register_file_profile_routes(flask_app, web_api_ctx)
    register_run_history_routes(flask_app, web_api_ctx)
    register_system_routes(flask_app, web_api_ctx)
    register_job_routes(flask_app, web_api_ctx)
    register_api_docs_routes(flask_app, web_api_ctx)
    register_telemetry_routes(flask_app, web_api_ctx)
    return flask_app


app = create_app()


PORT = 7433


def _open_browser():
    time.sleep(0.8)
    webbrowser.open(f"http://localhost:{PORT}")


def main(argv: list[str] | None = None) -> None:
    global PORT

    parser = argparse.ArgumentParser(description="Start the local DataProcess WebGUI server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=PORT, help="Port to serve on.")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser automatically."
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Validate runtime dependencies and bundled example files, then exit.",
    )
    args = parser.parse_args(argv)

    if args.self_check:
        from services.self_check import format_self_check_report, run_self_check

        report = run_self_check(BASE_DIR)
        print(format_self_check_report(report))
        raise SystemExit(0 if report["ok"] else 1)

    PORT = int(args.port)
    no_browser = os.environ.get("DATAPROCESS_WEB_NO_BROWSER", "").strip().lower()
    if not args.no_browser and no_browser not in {"1", "true", "yes", "on"}:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"\n  DataProcess Web  ->  http://localhost:{PORT}\n")
    app.run(host=args.host, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
