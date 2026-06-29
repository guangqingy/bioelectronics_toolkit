from flask import Response, jsonify
from pydantic import ConfigDict, Field

from services import emg_peak_selection as emg_peak_selection_service
from web_api.common import mode_is_save

from .jobs import submit_json_task
from .request_validation import OptFloat, OptInt, RequestModel, api_endpoint
from .response import api_ok, attachment_content_disposition


class EmgPeakSelectionBrowseRequest(RequestModel):
    folder: str = ""


class EmgPeakSelectionChannelRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""


class EmgPeakSelectionLoadRequest(RequestModel):
    folder: str = Field(min_length=1)
    subfolder: str = Field(min_length=1)
    channel: str = Field(min_length=1)


class EmgPeakSelectionPlotRequest(RequestModel):
    path: str = Field(min_length=1)
    x_min: OptFloat = None
    x_max: OptFloat = None
    y_min: OptFloat = None
    y_max: OptFloat = None
    invert_signal: bool = False


class EmgPeakSelectionDetectRequest(EmgPeakSelectionPlotRequest):
    pk_height: OptFloat = None
    pk_prom: OptFloat = None
    pk_dist: OptInt = 100
    pk_minw: OptFloat = None
    pk_wlen: OptFloat = None
    pk_dur: OptFloat = None
    polarity: str = "both"
    adaptive_sigma: bool = False
    sigma_prom: OptFloat = 1.0
    sigma_height: OptFloat = 1.0


class EmgPeakEntry(RequestModel):
    model_config = ConfigDict(extra="allow")

    idx: OptInt = None
    peak_idx: OptInt = None
    time: OptFloat = None
    time_s: OptFloat = None
    height: OptFloat = None
    height_uV: OptFloat = None
    duration: OptFloat = None
    duration_ms: OptFloat = None
    fwhm_ms: OptFloat = None
    group: str = ""
    removed: bool = False
    baseline: bool = False
    source_kind: str | None = None
    segment_start_s: OptFloat = None
    segment_end_s: OptFloat = None
    baseline_fill_seed: OptInt = None
    baseline_rep: OptInt = None


class EmgPeakSelectionGroupedExportRequest(RequestModel):
    folder: str = ""
    subfolder: str = ""
    channel: str = ""
    path: str = ""
    peaks: list[EmgPeakEntry] = Field(default_factory=list)
    linked_channels: list[str] = Field(default_factory=list)
    half_ms: OptFloat = 100.0
    invert_signal: bool = False
    mode: str = "download"


class EmgPeakSelectionLoadCsvRequest(RequestModel):
    path: str = Field(min_length=1)


class EmgPeakSelectionDetectPeaksRequest(RequestModel):
    path: str = Field(min_length=1)
    height: OptFloat = None
    prominence: OptFloat = None
    distance: OptInt = 100
    duration: OptFloat = None


class EmgPeakSelectionExportPeaksRequest(RequestModel):
    path: str = ""
    peaks: list[EmgPeakEntry] = Field(default_factory=list)
    mode: str = "download"


def register_emg_peak_selection_routes(app, ctx):
    jobs = ctx.jobs
    service = emg_peak_selection_service.EmgPeakSelectionService(
        find_peaks=ctx.find_peaks,
        peak_widths=ctx.peak_widths,
        fig_to_b64=ctx.fig_to_b64,
        line_color=ctx.LINE_COLOR,
        mode_is_save=mode_is_save,
    )

    def _download_or_save(result: dict):
        if result["kind"] == "save":
            data = result["data"]
            return api_ok(data, outputs=data["outputs"])
        return Response(
            result["payload"],
            mimetype=result["mimetype"],
            headers={
                "Content-Disposition": attachment_content_disposition(result["download_name"])
            },
        )

    def _emg_grouped_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG grouped peaks")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.grouped_export_payload(save_body)["data"]

    def _emg_export_peaks_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting EMG peaks CSV")
        save_body = dict(body or {})
        save_body["mode"] = "save"
        return service.export_peaks_payload(save_body)["data"]

    @app.route("/api/emg/peak-selection/browse", methods=["POST"])
    @api_endpoint(EmgPeakSelectionBrowseRequest, dump=False)
    def api_emg_peak_selection_browse(payload):
        return jsonify(service.browse_payload(payload.folder))

    @app.route("/api/emg/peak-selection/load_channels", methods=["POST"])
    @api_endpoint(EmgPeakSelectionChannelRequest, dump=False)
    def api_emg_peak_selection_load_channels(d):
        return jsonify(service.channel_payload(d.folder, d.subfolder))

    @app.route("/api/emg/peak-selection/load", methods=["POST"])
    @api_endpoint(EmgPeakSelectionLoadRequest, dump=False)
    def api_emg_peak_selection_load(d):
        return jsonify(service.load_duration_payload(d.folder, d.subfolder, d.channel))

    @app.route("/api/emg/peak-selection/plot", methods=["POST"])
    @api_endpoint(EmgPeakSelectionPlotRequest)
    def api_emg_peak_selection_plot(body):
        return jsonify(service.plot_payload(body))

    @app.route("/api/emg/peak-selection/trace_data", methods=["POST"])
    @api_endpoint(EmgPeakSelectionPlotRequest)
    def api_emg_peak_selection_trace_data(body):
        return jsonify(service.trace_data_payload(body))

    @app.route("/api/emg/peak-selection/detect", methods=["POST"])
    @api_endpoint(EmgPeakSelectionDetectRequest)
    def api_emg_peak_selection_detect(body):
        return jsonify(service.detect_payload(body))

    @app.route("/api/emg/peak-selection/export", methods=["POST"])
    @api_endpoint(EmgPeakSelectionGroupedExportRequest)
    def api_emg_peak_selection_export(body):
        return _download_or_save(service.grouped_export_payload(body))

    @app.route("/api/emg/peak-selection/export_job", methods=["POST"])
    @api_endpoint(EmgPeakSelectionGroupedExportRequest)
    def api_emg_peak_selection_export_job(body):
        return submit_json_task(
            jobs,
            "emg_peak_selection.export",
            "Export EMG grouped peaks",
            _emg_grouped_export_task,
            body,
            metadata={"endpoint": "/api/emg/peak-selection/export"},
        )

    @app.route("/api/emg/peak-selection/load_csv", methods=["POST"])
    @api_endpoint(EmgPeakSelectionLoadCsvRequest, dump=False)
    def api_emg_peak_selection_load_csv(payload):
        return jsonify(service.load_csv_payload(payload.path))

    @app.route("/api/emg/peak-selection/detect_peaks", methods=["POST"])
    @api_endpoint(EmgPeakSelectionDetectPeaksRequest)
    def api_emg_peak_selection_detect_peaks(body):
        return jsonify(service.detect_peaks_payload(body))

    @app.route("/api/emg/peak-selection/export_peaks", methods=["POST"])
    @api_endpoint(EmgPeakSelectionExportPeaksRequest)
    def api_emg_peak_selection_export_peaks(body):
        return _download_or_save(service.export_peaks_payload(body))

    @app.route("/api/emg/peak-selection/export_peaks_job", methods=["POST"])
    @api_endpoint(EmgPeakSelectionExportPeaksRequest)
    def api_emg_peak_selection_export_peaks_job(body):
        return submit_json_task(
            jobs,
            "emg_peak_selection.export_peaks",
            "Export EMG peaks CSV",
            _emg_export_peaks_task,
            body,
            metadata={"endpoint": "/api/emg/peak-selection/export_peaks"},
        )
