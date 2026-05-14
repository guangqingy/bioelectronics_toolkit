# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Split stack browse/preview/export/normalize route helpers into focused
# services and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
from __future__ import annotations

import traceback
from pathlib import Path

import numpy as np
from flask import jsonify
from pydantic import ValidationError

from .fluorescence_request_schemas import (
    FluorescenceBrowseRequest,
    FluorescenceNormalizeRequest,
    FluorescencePathRequest,
    FluorescencePreviewFrameRequest,
    FluorescenceStackAutoRangeRequest,
    FluorescenceStackExportBatchRequest,
    FluorescenceStackExportRequest,
    FluorescenceTiffInfoBatchRequest,
)
from .jobs import submit_json_task
from .request_validation import parse_json_payload, request_schema, validation_error_response
from .response import api_ok


def register_fluorescence_stack_routes(app, fl):
    err = fl["err"]
    browse_files = fl["browse_files"]
    int_or = fl["int_or"]
    float_or = fl["float_or"]
    has_tiff = fl["has_tiff"]
    has_pil = fl["has_pil"]
    tifflib = fl["tifflib"]
    jobs = fl["jobs"]

    _FL_BACKGROUND_OPTIONS = fl["_FL_BACKGROUND_OPTIONS"]
    _FL_DENOISE_OPTIONS = fl["_FL_DENOISE_OPTIONS"]
    _FL_LUT_OPTIONS = fl["_FL_LUT_OPTIONS"]
    _fl_bool = fl["_fl_bool"]
    _fl_build_default_settings_for_pages = fl["_fl_build_default_settings_for_pages"]
    _fl_build_settings_from_template = fl["_fl_build_settings_from_template"]
    _fl_clean_choice = fl["_fl_clean_choice"]
    _fl_compute_auto_range_with_processing = fl["_fl_compute_auto_range_with_processing"]
    _fl_export_with_settings = fl["_fl_export_with_settings"]
    _fl_frame_to_b64 = fl["_fl_frame_to_b64"]
    _fl_is_generated_tiff = fl["_fl_is_generated_tiff"]
    _fl_normalize_settings_for_pages = fl["_fl_normalize_settings_for_pages"]
    _fl_read_tiff_as_pages = fl["_fl_read_tiff_as_pages"]
    _fl_resolve_gif_scale = fl["_fl_resolve_gif_scale"]
    _fl_select_display_frame = fl["_fl_select_display_frame"]
    _fl_tiff_gif_frame_count = fl["_fl_tiff_gif_frame_count"]

    def _stack_export_payload(d: dict) -> dict:
        if not has_tiff:
            raise ValueError("tifffile not installed")
        input_path_str = str(d.get("input_path", "") or "").strip()
        raw_settings = d.get("settings")
        p_in = Path(input_path_str)
        if not p_in.exists():
            raise ValueError(f"Input file not found: {input_path_str}")
        pages = _fl_read_tiff_as_pages(p_in)
        settings = _fl_normalize_settings_for_pages(pages, raw_settings)
        return {"ok": True, **_fl_export_with_settings(p_in, pages, settings)}

    def _stack_export_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Exporting TIFF stack")
        return _stack_export_payload(body)

    def _stack_export_batch_payload(d: dict) -> dict:
        if not has_tiff:
            raise ValueError("tifffile not installed")
        paths_raw = d.get("paths") or []
        use_template = _fl_bool(d.get("use_template", True), True)
        lock_ranges = _fl_bool(d.get("lock_ranges", False), False)
        template_settings = d.get("template_settings") if isinstance(d.get("template_settings"), list) else []

        if not isinstance(paths_raw, list) or not paths_raw:
            raise ValueError("paths must be a non-empty list")

        success_files: list[str] = []
        failed_files: list[dict] = []
        outputs: list[dict] = []

        for raw in paths_raw:
            p = Path(str(raw or "").strip())
            try:
                if not p.exists():
                    raise FileNotFoundError(f"Input file not found: {p}")
                pages = _fl_read_tiff_as_pages(p)
                if use_template and template_settings:
                    settings = _fl_build_settings_from_template(pages, template_settings, lock_ranges)
                else:
                    settings = _fl_build_default_settings_for_pages(pages)
                result = _fl_export_with_settings(p, pages, settings)
                success_files.append(str(p))
                outputs.append({"input": str(p), **result})
            except Exception as exc:
                failed_files.append({"input": str(p), "error": str(exc)})

        return {
            "ok": True,
            "success": len(success_files),
            "failed": len(failed_files),
            "success_files": success_files,
            "failed_files": failed_files,
            "outputs": outputs,
        }

    def _stack_export_batch_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Batch exporting TIFF stacks")
        return _stack_export_batch_payload(body)

    def _normalize_payload(d: dict) -> dict:
        if not has_tiff:
            raise ValueError("tifffile not installed")
        input_path_str = d.get("input_path", "")
        output_path_str = d.get("output_path", "")
        low_pct = float_or(d.get("low_pct", 1.0), 1.0)
        high_pct = float_or(d.get("high_pct", 99.8), 99.8)
        dtype_name = d.get("dtype", "uint16")
        if not str(input_path_str or "").strip():
            raise ValueError("input_path is required")
        p_in = Path(input_path_str)
        if not p_in.exists():
            raise ValueError(f"Input file not found: {input_path_str}")
        if not output_path_str:
            output_path_str = str(p_in.with_name(p_in.stem + "_normalized.tif"))
        elif not Path(output_path_str).is_absolute():
            output_path_str = str(p_in.parent / output_path_str)

        stack = tifflib.imread(str(p_in))
        frames = [stack] if stack.ndim == 2 else [stack[i] for i in range(stack.shape[0])]
        out_frames = []
        for frame in frames:
            arr_f = frame.astype(np.float32)
            lo, hi = np.percentile(arr_f, [low_pct, high_pct])
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = float(arr_f.min()), float(arr_f.max())
            norm = np.clip((arr_f - lo) / max(hi - lo, 1e-12), 0.0, 1.0)
            if dtype_name == "uint8":
                out_frames.append(np.round(norm * 255).astype(np.uint8))
            elif dtype_name == "float32":
                out_frames.append(norm.astype(np.float32))
            else:
                out_frames.append(np.round(norm * 65535).astype(np.uint16))

        out_stack = np.stack(out_frames) if len(out_frames) > 1 else out_frames[0]
        Path(output_path_str).parent.mkdir(parents=True, exist_ok=True)
        tifflib.imwrite(output_path_str, out_stack)

        preview_b64 = ""
        if has_pil:
            preview_b64 = _fl_frame_to_b64(out_frames[0], "Gray", 0.5, 99.5)

        return {
            "ok": True,
            "output_path": output_path_str,
            "n_frames": len(out_frames),
            "dtype": str(out_stack.dtype),
            "shape": list(out_stack.shape),
            "preview": preview_b64,
            "outputs": [{"path": output_path_str, "type": "tiff", "role": "normalized_tiff"}],
        }

    def _normalize_task(job_ctx, body: dict) -> dict:
        job_ctx.set_progress(0.2, "Normalizing fluorescence TIFF")
        return _normalize_payload(body)

    @app.route("/api/fluorescence/browse", methods=["POST"])
    @request_schema(FluorescenceBrowseRequest)
    def api_fl_browse():
        try:
            folder = parse_json_payload(FluorescenceBrowseRequest).folder
        except ValidationError as exc:
            return validation_error_response(exc)
        files = [
            f
            for f in browse_files(folder, {".tif", ".tiff"})
            if not _fl_is_generated_tiff(Path(str(f.get("path", "") or "")))
        ]
        p = Path(folder)
        if p.is_dir():
            for sub in sorted(p.iterdir()):
                if sub.is_dir() and not sub.name.startswith("."):
                    for f in sorted(sub.iterdir()):
                        if f.suffix.lower() in {".tif", ".tiff"} and not _fl_is_generated_tiff(f):
                            files.append({"name": sub.name + "/" + f.name, "path": str(f)})
        return jsonify({"files": files})

    @app.route("/api/fluorescence/info", methods=["POST"])
    @request_schema(FluorescencePathRequest)
    def api_fl_info():
        if not has_tiff:
            return err("tifffile not installed. Run: pip install tifffile")
        try:
            path = parse_json_payload(FluorescencePathRequest).path
            stack = tifflib.imread(path)
            if stack.ndim == 2:
                n_frames, h, w = 1, stack.shape[0], stack.shape[1]
            elif stack.ndim >= 3:
                n_frames = stack.shape[0]
                h = stack.shape[-2]
                w = stack.shape[-1]
            else:
                return err("Unsupported TIFF shape")
            return jsonify(
                {
                    "n_frames": n_frames,
                    "height": h,
                    "width": w,
                    "dtype": str(stack.dtype),
                    "shape": list(stack.shape),
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/preview_frame", methods=["POST"])
    @request_schema(FluorescencePreviewFrameRequest)
    def api_fl_preview_frame():
        if not has_tiff:
            return err("tifffile not installed")
        if not has_pil:
            return err("Pillow not installed. Run: pip install Pillow")
        try:
            d = parse_json_payload(FluorescencePreviewFrameRequest)
            path = d.path
            frame_idx = int_or(d.frame, 0)
            lut = d.lut
            p_low = float_or(d.p_low, 1.0)
            p_high = float_or(d.p_high, 99.0)
            mode = d.mode
            z_start = d.z_start
            z_end = d.z_end
            stack = tifflib.imread(path)
            z_start_i = None if z_start in {None, ""} else int_or(z_start, 0)
            z_end_i = None if z_end in {None, ""} else int_or(z_end, 0)
            frame, info = _fl_select_display_frame(stack, frame_idx, mode, z_start_i, z_end_i)
            b64 = _fl_frame_to_b64(frame, lut, p_low, p_high)
            return jsonify({"img": b64, **info})
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_defaults", methods=["POST"])
    @request_schema(FluorescencePathRequest)
    def api_fl_stack_defaults():
        if not has_tiff:
            return err("tifffile not installed")
        try:
            path = parse_json_payload(FluorescencePathRequest).path.strip()
            p = Path(path)
            if not p.exists():
                return err(f"Input file not found: {path}")
            pages = _fl_read_tiff_as_pages(p)
            settings = _fl_build_default_settings_for_pages(pages)
            return jsonify({
                "ok": True,
                "n_pages": len(pages),
                "settings": settings,
                "lut_options": _FL_LUT_OPTIONS,
                "background_options": _FL_BACKGROUND_OPTIONS,
                "denoise_options": _FL_DENOISE_OPTIONS,
            })
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_auto_range", methods=["POST"])
    @request_schema(FluorescenceStackAutoRangeRequest)
    def api_fl_stack_auto_range():
        if not has_tiff:
            return err("tifffile not installed")
        try:
            d = parse_json_payload(FluorescenceStackAutoRangeRequest)
            path = d.path.strip()
            page_index = int_or(d.page_index, 0)
            background = _fl_clean_choice(d.background, _FL_BACKGROUND_OPTIONS, "Off")
            denoise = _fl_clean_choice(d.denoise, _FL_DENOISE_OPTIONS, "Off")
            p = Path(path)
            if not p.exists():
                return err(f"Input file not found: {path}")
            pages = _fl_read_tiff_as_pages(p)
            if page_index < 0 or page_index >= len(pages):
                return err(f"Invalid page index: {page_index}")
            vmin, vmax = _fl_compute_auto_range_with_processing(pages[page_index], background, denoise)
            return jsonify({"ok": True, "min": float(vmin), "max": float(vmax)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_export", methods=["POST"])
    @request_schema(FluorescenceStackExportRequest)
    def api_fl_stack_export():
        try:
            d = parse_json_payload(FluorescenceStackExportRequest).model_dump()
            result = _stack_export_payload(d)
            return api_ok(result, outputs=result.get("outputs"))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_export_job", methods=["POST"])
    @request_schema(FluorescenceStackExportRequest)
    def api_fl_stack_export_job():
        try:
            body = parse_json_payload(FluorescenceStackExportRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.stack_export",
            "Export TIFF stack",
            _stack_export_task,
            body,
            metadata={"endpoint": "/api/fluorescence/stack_export"},
        )

    @app.route("/api/fluorescence/stack_export_batch", methods=["POST"])
    @request_schema(FluorescenceStackExportBatchRequest)
    def api_fl_stack_export_batch():
        try:
            d = parse_json_payload(FluorescenceStackExportBatchRequest).model_dump()
            result = _stack_export_batch_payload(d)
            return api_ok(result, outputs=result.get("outputs"))
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))

    @app.route("/api/fluorescence/stack_export_batch_job", methods=["POST"])
    @request_schema(FluorescenceStackExportBatchRequest)
    def api_fl_stack_export_batch_job():
        try:
            body = parse_json_payload(FluorescenceStackExportBatchRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.stack_export_batch",
            "Batch export TIFF stacks",
            _stack_export_batch_task,
            body,
            metadata={"endpoint": "/api/fluorescence/stack_export_batch"},
        )

    @app.route("/api/fluorescence/normalize", methods=["POST"])
    @request_schema(FluorescenceNormalizeRequest)
    def api_fl_normalize():
        try:
            d = parse_json_payload(FluorescenceNormalizeRequest).model_dump()
            result = _normalize_payload(d)
            return api_ok(result, outputs=result["outputs"])
        except ValidationError as exc:
            return validation_error_response(exc)
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/normalize_job", methods=["POST"])
    @request_schema(FluorescenceNormalizeRequest)
    def api_fl_normalize_job():
        try:
            body = parse_json_payload(FluorescenceNormalizeRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.normalize",
            "Normalize fluorescence TIFF",
            _normalize_task,
            body,
            metadata={"endpoint": "/api/fluorescence/normalize"},
        )

    @app.route("/api/fluorescence/tiff_info_batch", methods=["POST"])
    @request_schema(FluorescenceTiffInfoBatchRequest)
    def api_fl_tiff_info_batch():
        """Return frame count and shape for a list of TIFF files.

        Request body: { paths: list[str] }
        Response:     { info: { path: { n_frames, height, width } } }
        """
        if not has_tiff:
            return err("tifffile is required")
        try:
            paths = parse_json_payload(FluorescenceTiffInfoBatchRequest).paths
        except ValidationError as exc:
            return validation_error_response(exc)
        info = {}
        for raw in paths:
            p = Path(str(raw).strip())
            if not p.exists():
                info[str(raw)] = {"error": "not found"}
                continue
            try:
                n, shape = _fl_tiff_gif_frame_count(p)
                h = int(shape[-2]) if len(shape) >= 2 else 0
                w = int(shape[-1]) if len(shape) >= 2 else 0
                if len(shape) >= 3 and shape[-1] in {3, 4}:
                    h = int(shape[-3])
                    w = int(shape[-2])
                scale_info = _fl_resolve_gif_scale(p, True, 3.45)
                info[str(raw)] = {
                    "n_frames": n,
                    "height": h,
                    "width": w,
                    "shape": shape,
                    "pixel_size_um": scale_info["pixel_size_um"],
                    "pixels_per_um": scale_info["pixels_per_um"],
                    "scale_source": scale_info["source"],
                    "metadata_path": scale_info["metadata_path"],
                }
            except Exception as exc:
                info[str(raw)] = {"error": str(exc)}
        return jsonify({"info": info})
