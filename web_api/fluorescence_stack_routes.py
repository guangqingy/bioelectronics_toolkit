from __future__ import annotations

import traceback
from pathlib import Path

import numpy as np
from flask import jsonify, request

from .jobs import submit_flask_route_job


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

    @app.route("/api/fluorescence/browse", methods=["POST"])
    def api_fl_browse():
        d = request.json or {}
        folder = d.get("folder", "")
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
    def api_fl_info():
        if not has_tiff:
            return err("tifffile not installed. Run: pip install tifffile")
        path = (request.json or {}).get("path", "")
        try:
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
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/preview_frame", methods=["POST"])
    def api_fl_preview_frame():
        if not has_tiff:
            return err("tifffile not installed")
        if not has_pil:
            return err("Pillow not installed. Run: pip install Pillow")
        d = request.json or {}
        path = d.get("path", "")
        frame_idx = int_or(d.get("frame", 0), 0)
        lut = d.get("lut", "Gray")
        p_low = float_or(d.get("p_low", 1.0), 1.0)
        p_high = float_or(d.get("p_high", 99.0), 99.0)
        mode = d.get("mode", "single")
        z_start = d.get("z_start", None)
        z_end = d.get("z_end", None)
        try:
            stack = tifflib.imread(path)
            z_start_i = None if z_start in {None, ""} else int_or(z_start, 0)
            z_end_i = None if z_end in {None, ""} else int_or(z_end, 0)
            frame, info = _fl_select_display_frame(stack, frame_idx, mode, z_start_i, z_end_i)
            b64 = _fl_frame_to_b64(frame, lut, p_low, p_high)
            return jsonify({"img": b64, **info})
        except ValueError as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_defaults", methods=["POST"])
    def api_fl_stack_defaults():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        try:
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
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_auto_range", methods=["POST"])
    def api_fl_stack_auto_range():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        path = str(d.get("path", "") or "").strip()
        page_index = int_or(d.get("page_index", 0), 0)
        background = _fl_clean_choice(d.get("background"), _FL_BACKGROUND_OPTIONS, "Off")
        denoise = _fl_clean_choice(d.get("denoise"), _FL_DENOISE_OPTIONS, "Off")
        try:
            p = Path(path)
            if not p.exists():
                return err(f"Input file not found: {path}")
            pages = _fl_read_tiff_as_pages(p)
            if page_index < 0 or page_index >= len(pages):
                return err(f"Invalid page index: {page_index}")
            vmin, vmax = _fl_compute_auto_range_with_processing(pages[page_index], background, denoise)
            return jsonify({"ok": True, "min": float(vmin), "max": float(vmax)})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_export", methods=["POST"])
    def api_fl_stack_export():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        input_path_str = str(d.get("input_path", "") or "").strip()
        raw_settings = d.get("settings")
        try:
            p_in = Path(input_path_str)
            if not p_in.exists():
                return err(f"Input file not found: {input_path_str}")
            pages = _fl_read_tiff_as_pages(p_in)
            settings = _fl_normalize_settings_for_pages(pages, raw_settings)
            result = _fl_export_with_settings(p_in, pages, settings)
            return jsonify({"ok": True, **result})
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/stack_export_job", methods=["POST"])
    def api_fl_stack_export_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/stack_export",
            "fluorescence.stack_export",
            "Export TIFF stack",
            api_fl_stack_export,
            request.json or {},
        )

    @app.route("/api/fluorescence/stack_export_batch", methods=["POST"])
    def api_fl_stack_export_batch():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        paths_raw = d.get("paths") or []
        use_template = _fl_bool(d.get("use_template", True), True)
        lock_ranges = _fl_bool(d.get("lock_ranges", False), False)
        template_settings = d.get("template_settings") if isinstance(d.get("template_settings"), list) else []

        if not isinstance(paths_raw, list) or not paths_raw:
            return err("paths must be a non-empty list")

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

        return jsonify(
            {
                "ok": True,
                "success": len(success_files),
                "failed": len(failed_files),
                "success_files": success_files,
                "failed_files": failed_files,
                "outputs": outputs,
            }
        )

    @app.route("/api/fluorescence/stack_export_batch_job", methods=["POST"])
    def api_fl_stack_export_batch_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/stack_export_batch",
            "fluorescence.stack_export_batch",
            "Batch export TIFF stacks",
            api_fl_stack_export_batch,
            request.json or {},
        )

    @app.route("/api/fluorescence/normalize", methods=["POST"])
    def api_fl_normalize():
        if not has_tiff:
            return err("tifffile not installed")
        d = request.json or {}
        input_path_str = d.get("input_path", "")
        output_path_str = d.get("output_path", "")
        low_pct = float_or(d.get("low_pct", 1.0), 1.0)
        high_pct = float_or(d.get("high_pct", 99.8), 99.8)
        dtype_name = d.get("dtype", "uint16")
        try:
            if not str(input_path_str or "").strip():
                return err("input_path is required")
            p_in = Path(input_path_str)
            if not p_in.exists():
                return err(f"Input file not found: {input_path_str}")
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

            return jsonify(
                {
                    "ok": True,
                    "output_path": output_path_str,
                    "n_frames": len(out_frames),
                    "dtype": str(out_stack.dtype),
                    "shape": list(out_stack.shape),
                    "preview": preview_b64,
                }
            )
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/normalize_job", methods=["POST"])
    def api_fl_normalize_job():
        return submit_flask_route_job(
            app,
            jobs,
            "/api/fluorescence/normalize",
            "fluorescence.normalize",
            "Normalize fluorescence TIFF",
            api_fl_normalize,
            request.json or {},
        )

    @app.route("/api/fluorescence/tiff_info_batch", methods=["POST"])
    def api_fl_tiff_info_batch():
        """Return frame count and shape for a list of TIFF files.

        Request body: { paths: list[str] }
        Response:     { info: { path: { n_frames, height, width } } }
        """
        if not has_tiff:
            return err("tifffile is required")
        paths = (request.json or {}).get("paths") or []
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

