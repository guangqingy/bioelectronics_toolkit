"""Page-render routes only.

Every endpoint here returns a Jinja-rendered HTML response. JSON API endpoints
live in their domain-specific modules, such as ``abf_viewer.py`` for
``/api/abf/*``.

If a route in this file starts returning JSON, move it to the domain module.
"""

from __future__ import annotations

from flask import render_template


def register_page_routes(app, ctx) -> None:
    has_rhd = ctx.HAS_RHD

    @app.route("/")
    def index():
        return render_template("index.html", active="index")

    @app.route("/csv")
    def csv_viewer():
        return render_template("csv_viewer.html", active="csv_viewer")

    @app.route("/abf/viewer")
    def abf_viewer():
        return render_template("abf_viewer.html", active="abf_viewer")

    @app.route("/abf/batch")
    def abf_batch():
        return render_template("abf_batch.html", active="abf_batch")

    @app.route("/abf/figure")
    def abf_figure():
        return render_template("abf_figure.html", active="abf_figure")

    @app.route("/abf/peaks")
    def abf_peaks():
        return render_template("abf_peaks.html", active="abf_peaks")

    @app.route("/echem/photocurrent")
    def echem_photocurrent():
        return render_template("echem_photocurrent.html", active="echem_photocurrent")

    @app.route("/echem/photovoltage")
    def echem_photovoltage():
        return render_template("echem_photovoltage.html", active="echem_photovoltage")

    @app.route("/echem/lineshape")
    def echem_lineshape():
        return render_template("echem_lineshape.html", active="echem_lineshape")

    @app.route("/echem/quant")
    def echem_quant():
        return render_template("echem_quant.html", active="echem_quant")

    @app.route("/emg/analysis")
    def emg_analysis():
        return render_template("emg_analysis.html", active="emg_analysis", has_rhd=has_rhd)

    @app.route("/emg/peak-selection")
    def emg_peak_selection():
        return render_template("emg_peak_selection.html", active="emg_peak_selection")

    @app.route("/fluorescence")
    def fluorescence():
        return render_template("fluorescence_stack.html", active="fluorescence")

    @app.route("/fluorescence/roi")
    def fluorescence_roi():
        return render_template("fluorescence_roi.html", active="fluorescence_roi")

    @app.route("/fluorescence/lif")
    def fluorescence_lif():
        return render_template("fluorescence_lif.html", active="fluorescence_lif")

    @app.route("/fluorescence/3d-stacking")
    def fluorescence_3d_stacking():
        return render_template("fluorescence_3d_stacking.html", active="fluorescence_3d_stacking")

    @app.route("/histology/naming")
    def histology_naming():
        return render_template("histology_naming.html", active="histology_naming")

    @app.route("/histology/analysis")
    def histology_analysis():
        return render_template("histology_analysis.html", active="histology_analysis")

    @app.route("/fluorescence/gif")
    def fluorescence_gif():
        return render_template("fluorescence_gif.html", active="fluorescence_gif")

    @app.route("/fluorescence/timecourse")
    def fluorescence_timecourse():
        return render_template(
            "fluorescence_timecourse.html",
            active="fluorescence_timecourse",
        )

    @app.route("/fluorescence/kymograph")
    def fluorescence_kymograph():
        return render_template(
            "fluorescence_kymograph.html",
            active="fluorescence_kymograph",
        )

    @app.route("/runs")
    def run_history_page():
        return render_template("run_history.html", active="run_history")
