# TODO(structure-debt): this route module exceeds the 200-line route budget.
# Move remaining LIF response assembly into services/fluorescence/lif_* helpers
# and track the GitHub issue draft in docs/loc_budget_issue_drafts.md.
import base64
import csv
import io
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from flask import jsonify
from pydantic import Field, ValidationError

from services.fluorescence import (
    lif_dimensions,
    lif_export,
    lif_metadata,
    lif_records,
    lif_volume,
    route_helpers,
)

from .jobs import route_response_to_payload, submit_json_task
from .request_validation import (
    RequestModel,
    parse_json_payload,
    request_schema,
    validation_error_response,
)


class LifBrowseRequest(RequestModel):
    folder: str = ""


class LifInfoRequest(RequestModel):
    path: str = Field(min_length=1)
    sort: str = "time"


class LifPreviewRequest(RequestModel):
    path: str = Field(min_length=1)
    image_index: Any = 0
    z: Any = 0
    t: Any = 0
    c: Any = 0
    m: Any = 0
    requested_dims: Any = Field(default_factory=dict)
    lut: str = "Gray"
    p_low: Any = 1.0
    p_high: Any = 99.0


class LifVolume3dRequest(LifPreviewRequest):
    channel_mode: str = "composite"
    max_points: Any = 70000
    max_xy: Any = 180
    max_z: Any = 80
    threshold_percentile: Any = 98.8


class LifExportVolume3dRequest(LifVolume3dRequest):
    max_points: Any = 110000
    max_xy: Any = 220
    max_z: Any = 120
    threshold_percentile: Any = 98.6
    output_name: str = ""
    output_dir: str = ""
    overwrite: Any = True


class LifExportManifestRequest(RequestModel):
    path: str = Field(min_length=1)
    order_indices: list[Any] = Field(default_factory=list)
    rename_map: Any = Field(default_factory=dict)


class LifExportTiffRequest(RequestModel):
    path: str = Field(min_length=1)
    image_index: Any = 0
    output_name: str = ""
    output_dir: str = ""
    overwrite: Any = True


class LifExportTiffBatchRequest(RequestModel):
    path: str = Field(min_length=1)
    order_indices: list[Any] = Field(default_factory=list)
    rename_map: Any = Field(default_factory=dict)
    output_dir: str = ""
    overwrite: Any = True


def register_lif_viewer_routes(app, ctx):
    err = ctx["err"]
    browse_files = ctx["browse_files"]
    float_or = ctx["float_or"]
    int_or = ctx["int_or"]
    has_tiff = ctx["HAS_TIFF"]
    has_pil = ctx["HAS_PIL"]
    tifflib = ctx.get("tifflib")
    image_mod = ctx.get("Image")
    jobs = ctx.get("jobs")

    has_readlif = ctx.get("HAS_READLIF", False)
    LifFile = ctx.get("LifFile")
    _lif_cache = {}

    def _response_task(job_ctx, body: dict, handler, message: str) -> dict:
        job_ctx.set_progress(0.2, message)
        with app.app_context():
            return route_response_to_payload(handler(body or {}))

    def _lif_require_reader():
        if not has_readlif or LifFile is None:
            return "readlif is not installed. Run: python -m pip install readlif"
        return ""

    def _lif_apply_lut(gray8: np.ndarray, lut: str) -> np.ndarray:
        lut_name = (lut or "Gray").strip().lower()
        z = np.zeros_like(gray8)
        if lut_name == "red":
            return np.stack([gray8, z, z], axis=-1)
        if lut_name == "green":
            return np.stack([z, gray8, z], axis=-1)
        if lut_name == "blue":
            return np.stack([z, z, gray8], axis=-1)
        if lut_name == "magenta":
            return np.stack([gray8, z, gray8], axis=-1)
        if lut_name == "cyan":
            return np.stack([z, gray8, gray8], axis=-1)
        if lut_name == "yellow":
            return np.stack([gray8, gray8, z], axis=-1)
        return np.stack([gray8, gray8, gray8], axis=-1)

    def _lif_plane_to_b64(frame, lut: str, p_low: float, p_high: float) -> str:
        arr = np.asarray(frame)
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            arr = arr.reshape(arr.shape[-2], arr.shape[-1])

        arr = arr.astype(np.float32)
        lo, hi = np.percentile(arr, [p_low, p_high])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.min(arr)), float(np.max(arr))
        if hi <= lo:
            hi = lo + 1.0

        gray8 = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
        rgb = _lif_apply_lut(gray8, lut)
        img = image_mod.fromarray(rgb, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return base64.b64encode(buf.read()).decode()

    _lif_apply_orientation = lif_dimensions.apply_orientation
    _lif_sanitize_filename = lif_export.sanitize_filename
    _lif_output_name_for_record = lif_export.output_name_for_record
    _lif_unique_output_path = lif_export.unique_output_path
    _lif_manifest_rows = lif_export.manifest_rows
    _lif_get_plane_by_dimensions = lif_records.get_plane_by_dimensions
    _lif_get_plane = lif_records.get_plane
    _lif_volume3d_html = lif_volume.volume3d_html

    def _lif_load_records(path: str):
        return lif_records.load_records(
            path,
            lif_file_cls=LifFile,
            cache=_lif_cache,
            reader_error=_lif_require_reader(),
        )

    def _lif_build_volume3d_payload(
        lif,
        record: dict,
        requested_dims: dict | None = None,
        t: int = 0,
        m: int = 0,
        c: int = 0,
        channel_mode: str = "composite",
        max_points: int = 70000,
        max_xy: int = 180,
        max_z: int = 80,
        threshold_percentile: float = 98.8,
    ) -> dict:
        return lif_volume.build_volume3d_payload(
            lif,
            record,
            get_plane_by_dimensions=_lif_get_plane_by_dimensions,
            display_array=_lif_display_array,
            requested_dims=requested_dims,
            t=t,
            m=m,
            c=c,
            channel_mode=channel_mode,
            max_points=max_points,
            max_xy=max_xy,
            max_z=max_z,
            threshold_percentile=threshold_percentile,
        )

    def _lif_export_image_as_tiff(
        lif, record: dict, output_dir: Path, output_name: str, overwrite: bool = True
    ) -> dict:
        if not has_tiff or tifflib is None:
            raise RuntimeError(
                "tifffile is required for TIFF export. Run: python -m pip install tifffile"
            )
        return lif_export.export_image_as_tiff(
            lif,
            record,
            output_dir,
            output_name,
            tifflib_module=tifflib,
            get_plane_by_dimensions=_lif_get_plane_by_dimensions,
            display_array=_lif_display_array,
            overwrite=overwrite,
        )

    def _lif_normalize_2d_array(frame) -> np.ndarray:
        arr = np.asarray(frame)
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            arr = arr.reshape(arr.shape[-2], arr.shape[-1])
        return arr

    def _lif_display_array(frame, record: dict) -> np.ndarray:
        return _lif_apply_orientation(
            _lif_normalize_2d_array(frame), record.get("scan_orientation", {}) or {}
        )

    @app.route("/api/fluorescence/lif/browse", methods=["POST"])
    @request_schema(LifBrowseRequest)
    def api_lif_browse():
        try:
            folder = parse_json_payload(LifBrowseRequest).folder
        except ValidationError as exc:
            return validation_error_response(exc)
        files = browse_files(folder, {".lif"})
        return jsonify({"files": files})

    @app.route("/api/fluorescence/lif/info", methods=["POST"])
    @request_schema(LifInfoRequest)
    def api_lif_info():
        try:
            d = parse_json_payload(LifInfoRequest)
            path = d.path.strip()
            sort_mode = str(d.sort or "time").strip().lower()
            if sort_mode not in {"time", "original", "name"}:
                sort_mode = "time"
            _, records = _lif_load_records(path)
            sorted_records = sorted(
                records, key=lambda r: lif_metadata.record_sort_tuple(r, sort_mode)
            )
            for i, r in enumerate(sorted_records, start=1):
                r["display_order"] = i
                r["has_timestamp"] = r.get("sort_value") is not None
            timestamp_count = sum(1 for r in records if r.get("sort_value") is not None)
            return jsonify(
                {
                    "ok": True,
                    "path": path,
                    "name": Path(path).name,
                    "n_images": len(records),
                    "timestamp_count": timestamp_count,
                    "records": sorted_records,
                    "sort": sort_mode,
                    "readlif": has_readlif,
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/preview", methods=["POST"])
    @request_schema(LifPreviewRequest)
    def api_lif_preview():
        if not has_pil or image_mod is None:
            return err("Pillow is required for LIF previews. Run: python -m pip install Pillow")

        try:
            d = parse_json_payload(LifPreviewRequest)
            path = d.path.strip()
            image_index = int_or(d.image_index, 0)
            z = int_or(d.z, 0)
            t = int_or(d.t, 0)
            c = int_or(d.c, 0)
            m = int_or(d.m, 0)
            requested_dims = d.requested_dims if isinstance(d.requested_dims, dict) else {}
            lut = str(d.lut or "Gray")
            p_low = max(0.0, min(49.0, float_or(d.p_low, 1.0)))
            p_high = max(51.0, min(100.0, float_or(d.p_high, 99.0)))
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            image = lif.get_image(image_index)
            plane = _lif_display_array(
                _lif_get_plane(image, z=z, t=t, c=c, m=m, requested_dims=requested_dims),
                records[image_index],
            )
            b64 = _lif_plane_to_b64(plane, lut, p_low, p_high)
            return jsonify(
                {
                    "ok": True,
                    "img": b64,
                    "record": records[image_index],
                    "z": z,
                    "t": t,
                    "c": c,
                    "m": m,
                    "requested_dims": requested_dims,
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/volume3d", methods=["POST"])
    @request_schema(LifVolume3dRequest)
    def api_lif_volume3d():
        try:
            d = parse_json_payload(LifVolume3dRequest)
            path = d.path.strip()
            image_index = int_or(d.image_index, 0)
            t = int_or(d.t, 0)
            c = int_or(d.c, 0)
            m = int_or(d.m, 0)
            requested_dims = d.requested_dims if isinstance(d.requested_dims, dict) else {}
            channel_mode = str(d.channel_mode or "composite").strip().lower()
            if channel_mode not in {"composite", "current"}:
                channel_mode = "composite"
            max_points = int_or(d.max_points, 70000)
            max_xy = int_or(d.max_xy, 180)
            max_z = int_or(d.max_z, 80)
            threshold_percentile = float_or(d.threshold_percentile, 98.8)
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            payload = _lif_build_volume3d_payload(
                lif,
                records[image_index],
                requested_dims=requested_dims,
                t=t,
                m=m,
                c=c,
                channel_mode=channel_mode,
                max_points=max_points,
                max_xy=max_xy,
                max_z=max_z,
                threshold_percentile=threshold_percentile,
            )
            return jsonify({"ok": True, "volume": payload})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_volume3d", methods=["POST"])
    @request_schema(LifExportVolume3dRequest)
    def api_lif_export_volume3d(payload=None):
        try:
            d = (
                parse_json_payload(LifExportVolume3dRequest).model_dump()
                if payload is None
                else payload
            )
            path = str(d.get("path", "") or "").strip()
            image_index = int_or(d.get("image_index", 0), 0)
            output_name = str(d.get("output_name", "") or "").strip()
            output_dir_raw = str(d.get("output_dir", "") or "").strip()
            overwrite = bool(d.get("overwrite", True))
            t = int_or(d.get("t", 0), 0)
            c = int_or(d.get("c", 0), 0)
            m = int_or(d.get("m", 0), 0)
            requested_dims = (
                d.get("requested_dims") if isinstance(d.get("requested_dims"), dict) else {}
            )
            channel_mode = str(d.get("channel_mode", "composite") or "composite").strip().lower()
            if channel_mode not in {"composite", "current"}:
                channel_mode = "composite"
            max_points = int_or(d.get("max_points", 110000), 110000)
            max_xy = int_or(d.get("max_xy", 220), 220)
            max_z = int_or(d.get("max_z", 120), 120)
            threshold_percentile = float_or(d.get("threshold_percentile", 98.6), 98.6)
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            p = Path(path).expanduser()
            output_dir = (
                Path(output_dir_raw).expanduser()
                if output_dir_raw
                else p.with_name(f"{p.stem}_lif_exports")
            )
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            record = records[image_index]
            output_name = output_name or _lif_output_name_for_record(record)
            safe_name = _lif_sanitize_filename(output_name, f"image_{image_index + 1}")
            out_path = _lif_unique_output_path(
                output_dir / f"{safe_name}_3d_viewer.html", overwrite
            )
            payload = _lif_build_volume3d_payload(
                lif,
                record,
                requested_dims=requested_dims,
                t=t,
                m=m,
                c=c,
                channel_mode=channel_mode,
                max_points=max_points,
                max_xy=max_xy,
                max_z=max_z,
                threshold_percentile=threshold_percentile,
            )
            out_path.write_text(_lif_volume3d_html(payload), encoding="utf-8")
            return jsonify(
                {
                    "ok": True,
                    "output_path": str(out_path),
                    "image_index": image_index,
                    "name": record.get("name", ""),
                    "output_name": output_name,
                    "n_points": payload.get("render", {}).get("n_points", 0),
                    "z_sampled": payload.get("dimensions", {}).get("z_sampled", 0),
                    "channels_rendered": payload.get("dimensions", {}).get("channels_rendered", []),
                    "calibration": payload.get("calibration", {}),
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_volume3d_job", methods=["POST"])
    @request_schema(LifExportVolume3dRequest)
    def api_lif_export_volume3d_job():
        try:
            body = parse_json_payload(LifExportVolume3dRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.lif_export_volume3d",
            "Export LIF 3D viewer",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_lif_export_volume3d, "Exporting LIF 3D viewer"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/lif/export_volume3d"},
        )

    @app.route("/api/fluorescence/lif/export_manifest", methods=["POST"])
    @request_schema(LifExportManifestRequest)
    def api_lif_export_manifest():
        try:
            d = parse_json_payload(LifExportManifestRequest)
            path = d.path.strip()
            order_indices_raw = d.order_indices or []
            rename_map = d.rename_map if isinstance(d.rename_map, dict) else {}
            order_indices = []
            for raw in order_indices_raw:
                try:
                    order_indices.append(int(raw))
                except Exception:
                    pass
            _, records = _lif_load_records(path)
            rows = _lif_manifest_rows(records, order_indices, rename_map)
            p = Path(path).expanduser()
            out_path = p.with_name(f"{p.stem}_lif_time_order.csv")
            fields = [
                "display_order",
                "original_order",
                "image_index",
                "acquired_at",
                "timestamp_source",
                "folder",
                "name",
                "display_name",
                "full_name",
                "x",
                "y",
                "z",
                "t",
                "mosaic_tiles",
                "channels",
                "bit_depth",
            ]
            with out_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            return jsonify({"ok": True, "output_path": str(out_path), "rows": len(rows)})
        except ValidationError as exc:
            return validation_error_response(exc)
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff", methods=["POST"])
    @request_schema(LifExportTiffRequest)
    def api_lif_export_tiff(payload=None):
        try:
            d = (
                parse_json_payload(LifExportTiffRequest).model_dump()
                if payload is None
                else payload
            )
            path = str(d.get("path", "") or "").strip()
            image_index = int_or(d.get("image_index", 0), 0)
            output_name = str(d.get("output_name", "") or "").strip()
            output_dir_raw = str(d.get("output_dir", "") or "").strip()
            overwrite = bool(d.get("overwrite", True))
            lif, records = _lif_load_records(path)
            if image_index < 0 or image_index >= len(records):
                return err(f"Invalid image index: {image_index}")
            p = Path(path).expanduser()
            output_dir = (
                Path(output_dir_raw).expanduser()
                if output_dir_raw
                else p.with_name(f"{p.stem}_lif_exports")
            )
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir
            record = records[image_index]
            output_name = output_name or _lif_output_name_for_record(record)
            result = _lif_export_image_as_tiff(
                lif, record, output_dir, output_name, overwrite=overwrite
            )
            return jsonify({"ok": True, **result})
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff_job", methods=["POST"])
    @request_schema(LifExportTiffRequest)
    def api_lif_export_tiff_job():
        try:
            body = parse_json_payload(LifExportTiffRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.lif_export_tiff",
            "Export selected LIF TIFF",
            lambda job_ctx, body: _response_task(
                job_ctx, body, api_lif_export_tiff, "Exporting selected LIF TIFF"
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/lif/export_tiff"},
        )

    @app.route("/api/fluorescence/lif/export_tiff_batch", methods=["POST"])
    @request_schema(LifExportTiffBatchRequest)
    def api_lif_export_tiff_batch(payload=None, job_ctx=None):
        try:
            d = (
                parse_json_payload(LifExportTiffBatchRequest).model_dump()
                if payload is None
                else payload
            )
            path = str(d.get("path", "") or "").strip()
            order_indices_raw = d.get("order_indices") or []
            rename_map = d.get("rename_map") if isinstance(d.get("rename_map"), dict) else {}
            output_dir_raw = str(d.get("output_dir", "") or "").strip()
            overwrite = bool(d.get("overwrite", True))

            order_indices = []
            if isinstance(order_indices_raw, list):
                for raw in order_indices_raw:
                    try:
                        order_indices.append(int(raw))
                    except Exception:
                        pass
            lif, records = _lif_load_records(path)
            by_index = {int(r["index"]): r for r in records}
            ordered = (
                [by_index[i] for i in order_indices if i in by_index]
                if order_indices
                else sorted(records, key=lambda r: lif_metadata.record_sort_tuple(r, "time"))
            )
            p = Path(path).expanduser()
            output_dir = (
                Path(output_dir_raw).expanduser()
                if output_dir_raw
                else p.with_name(f"{p.stem}_lif_exports")
            )
            if not output_dir.is_absolute():
                output_dir = p.parent / output_dir

            outputs = []
            failed = []
            for display_order, record in route_helpers.iter_with_job_progress(
                ordered, job_ctx, start=0.15, span=0.75, label="Exporting LIF image"
            ):
                try:
                    output_name = (
                        f"{display_order:03d}_{_lif_output_name_for_record(record, rename_map)}"
                    )
                    outputs.append(
                        _lif_export_image_as_tiff(
                            lif, record, output_dir, output_name, overwrite=overwrite
                        )
                    )
                except Exception as exc:
                    failed.append(
                        {
                            "image_index": record.get("index"),
                            "name": record.get("name", ""),
                            "error": str(exc),
                        }
                    )

            return jsonify(
                {
                    "ok": True,
                    "output_dir": str(output_dir),
                    "success": len(outputs),
                    "failed": len(failed),
                    "outputs": outputs,
                    "failed_files": failed,
                }
            )
        except ValidationError as exc:
            return validation_error_response(exc)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            return err(str(exc))
        except Exception:
            return err(traceback.format_exc())

    @app.route("/api/fluorescence/lif/export_tiff_batch_job", methods=["POST"])
    @request_schema(LifExportTiffBatchRequest)
    def api_lif_export_tiff_batch_job():
        try:
            body = parse_json_payload(LifExportTiffBatchRequest).model_dump()
        except ValidationError as exc:
            return validation_error_response(exc)
        return submit_json_task(
            jobs,
            "fluorescence.lif_export_tiff_batch",
            "Export all LIF TIFFs",
            lambda job_ctx, body: _response_task(
                job_ctx,
                body,
                lambda payload: api_lif_export_tiff_batch(payload, job_ctx=job_ctx),
                "Exporting LIF TIFF batch",
            ),
            body,
            metadata={"endpoint": "/api/fluorescence/lif/export_tiff_batch"},
        )
