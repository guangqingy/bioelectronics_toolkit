"""Page-render routes only.

Every endpoint here returns a Jinja-rendered HTML response. JSON API endpoints
live in their domain-specific modules, such as ``abf_viewer.py`` for
``/api/abf/*``.

If a route in this file starts returning JSON, move it to the domain module.
"""

from __future__ import annotations

from flask import render_template, request

from pipelines.registry import default_category_id, pipeline_catalog, pipeline_category_ids


def register_page_routes(app, ctx) -> None:
    base_dir = ctx["BASE_DIR"]
    has_abf = ctx["HAS_ABF"]
    has_rhd = ctx["HAS_RHD"]
    has_scipy = ctx["HAS_SCIPY"]
    has_tiff = ctx["HAS_TIFF"]
    has_pil = ctx["HAS_PIL"]
    has_readlif = ctx["HAS_READLIF"]

    @app.route("/")
    def index():
        return render_template("index.html", active="index")

    @app.route("/csv")
    def csv_viewer():
        return render_template("csv_viewer.html", active="csv_viewer")

    @app.route("/abf/viewer")
    def abf_viewer():
        return render_template("abf_viewer.html", active="abf_viewer", has_abf=has_abf)

    @app.route("/abf/batch")
    def abf_batch():
        return render_template("abf_batch.html", active="abf_batch", has_abf=has_abf)

    @app.route("/abf/figure")
    def abf_figure():
        return render_template("abf_figure.html", active="abf_figure")

    @app.route("/abf/peaks")
    def abf_peaks():
        return render_template("abf_peakdet.html", active="abf_peaks", has_abf=has_abf)

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
        return render_template("rhd_viewer.html", active="rhd_viewer", has_rhd=has_rhd)

    @app.route("/emg/peaks")
    def emg_peaks():
        return render_template("emg_peaks.html", active="emg_peaks", has_scipy=has_scipy)

    @app.route("/fluorescence")
    def fluorescence():
        return render_template(
            "fluorescence.html",
            active="fluorescence",
            has_tiff=has_tiff,
            has_pil=has_pil,
        )

    @app.route("/fluorescence/roi")
    def fluorescence_roi():
        return render_template(
            "fluorescence_roi.html",
            active="fluorescence_roi",
            has_tiff=has_tiff,
        )

    @app.route("/fluorescence/lif")
    def fluorescence_lif():
        return render_template(
            "fluorescence_lif.html",
            active="fluorescence_lif",
            has_readlif=has_readlif,
            has_pil=has_pil,
            has_tiff=has_tiff,
        )

    @app.route("/fluorescence/3d-stacking")
    def fluorescence_3d_stacking():
        return render_template(
            "fluorescence_3d_stacking.html",
            active="fluorescence_3d_stacking",
            has_tiff=has_tiff,
            has_pil=has_pil,
        )

    @app.route("/histology/naming")
    def histology_naming():
        return render_template("histology_naming.html", active="histology_naming")

    @app.route("/histology/analysis")
    def histology_analysis():
        return render_template("histology_analysis.html", active="histology_analysis")

    @app.route("/fluorescence/gif")
    def fluorescence_gif():
        return render_template(
            "fluorescence_gif.html",
            active="fluorescence_gif",
            has_tiff=has_tiff,
            has_pil=has_pil,
        )

    @app.route("/fluorescence/timecourse")
    def fluorescence_timecourse():
        return render_template(
            "fluorescence_timecourse.html",
            active="fluorescence_timecourse",
            has_tiff=has_tiff,
            has_pil=has_pil,
        )

    @app.route("/fluorescence/kymograph")
    def fluorescence_kymograph():
        return render_template(
            "fluorescence_kymograph.html",
            active="fluorescence_kymograph",
            has_tiff=has_tiff,
            has_pil=has_pil,
        )

    @app.route("/scripts")
    @app.route("/scripts/<cat>")
    def scripts(cat=None):
        valid_categories = set(pipeline_category_ids())
        fallback_category = default_category_id()
        if cat not in valid_categories:
            cat = fallback_category
        return render_template(
            "scripts.html",
            active="scripts",
            cat=cat,
            cat_explicit=request.path != "/scripts",
            pipeline_catalog=pipeline_catalog(base_dir, include_availability=True),
            pipeline_default_category=fallback_category,
        )

    @app.route("/runs")
    def run_history_page():
        return render_template("run_history.html", active="run_history")
