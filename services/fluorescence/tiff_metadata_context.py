from __future__ import annotations

import json
import re as _re2
from pathlib import Path

import numpy as np

from services.fluorescence import route_helpers as fl_helpers


def build_tiff_metadata_context(*, tifflib, has_tiff: bool) -> dict:
    _fl_unit_to_um_scale = fl_helpers.unit_to_um_scale

    def _fl_infer_pixel_size_um_from_tiff(path: str) -> float | None:
        return fl_helpers.infer_pixel_size_um_from_tiff(path, has_tiff=has_tiff, tifflib=tifflib)

    def _fl_positive_float(v) -> float | None:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(x) or x <= 0:
            return None
        return x

    def _fl_metadata_z_spacing_um(payload: object) -> tuple[float | None, str]:
        if not isinstance(payload, dict):
            return None, ""

        calibration = payload.get("calibration")
        if isinstance(calibration, dict):
            z_um = _fl_positive_float(calibration.get("z_spacing_um"))
            if z_um is not None:
                return z_um, "metadata calibration.z_spacing_um"

        xml_meta = payload.get("leica_xml_metadata")
        dims = xml_meta.get("dimensions") if isinstance(xml_meta, dict) else None
        if isinstance(dims, list):
            for dim in dims:
                if not isinstance(dim, dict) or str(dim.get("DimID", "")) != "3":
                    continue
                n = _fl_positive_float(dim.get("NumberOfElements"))
                length = _fl_positive_float(abs(float(dim.get("Length", 0.0))) if dim.get("Length") is not None else None)
                unit = str(dim.get("Unit", "") or "").strip().lower()
                if n is not None and length is not None and n > 1:
                    scale = _fl_unit_to_um_scale(unit)
                    if scale is not None:
                        return (length * scale) / max(1, n - 1), "metadata Leica DimID 3 Length"
        return None, ""

    def _fl_tiff_calibration(tiff_path: Path) -> dict:
        pixel_um = None
        pixel_source = ""
        z_um = None
        z_source = ""

        meta_path = _fl_find_gif_metadata_json(tiff_path)
        if meta_path is not None:
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
                pixel_um, pixel_source = _fl_metadata_pixel_size_um(payload)
                z_um, z_source = _fl_metadata_z_spacing_um(payload)
            except Exception:
                pass

        try:
            with tifflib.TiffFile(str(tiff_path)) as tf:
                ij = tf.imagej_metadata or {}
                if z_um is None and isinstance(ij, dict):
                    z_um = _fl_positive_float(ij.get("spacing"))
                    if z_um is not None:
                        z_source = "ImageJ spacing"

                ome_xml = tf.ome_metadata
                if ome_xml:
                    if pixel_um is None:
                        m_val = _re2.search(r'PhysicalSizeX="([0-9eE+\\-.]+)"', ome_xml)
                        m_unit = _re2.search(r'PhysicalSizeXUnit="([^"]+)"', ome_xml)
                        if m_val:
                            px_val = _fl_positive_float(m_val.group(1))
                            unit_str = m_unit.group(1) if m_unit else "um"
                            scale = _fl_unit_to_um_scale(unit_str)
                            if px_val is not None and scale is not None:
                                pixel_um = px_val * scale
                                pixel_source = "OME PhysicalSizeX"
                    if z_um is None:
                        m_val = _re2.search(r'PhysicalSizeZ="([0-9eE+\\-.]+)"', ome_xml)
                        m_unit = _re2.search(r'PhysicalSizeZUnit="([^"]+)"', ome_xml)
                        if m_val:
                            z_val = _fl_positive_float(m_val.group(1))
                            unit_str = m_unit.group(1) if m_unit else "um"
                            scale = _fl_unit_to_um_scale(unit_str)
                            if z_val is not None and scale is not None:
                                z_um = z_val * scale
                                z_source = "OME PhysicalSizeZ"
        except Exception:
            pass

        if pixel_um is None:
            pixel_um = _fl_infer_pixel_size_um_from_tiff(str(tiff_path))
            if pixel_um is not None:
                pixel_source = "TIFF resolution metadata"

        pixel_um = _fl_positive_float(pixel_um) or 1.0
        z_um = _fl_positive_float(z_um) or pixel_um
        if not z_source:
            z_source = "fallback to XY pixel size"
        if not pixel_source:
            pixel_source = "fallback 1 um/px"
        return {
            "pixel_width_um": float(pixel_um),
            "pixel_height_um": float(pixel_um),
            "z_spacing_um": float(z_um),
            "pixel_source": pixel_source,
            "z_source": z_source,
            "metadata_path": str(meta_path) if meta_path is not None else "",
        }

    def _fl_clean_tiff_axes(axes: object, shape: tuple[int, ...]) -> str:
        raw = str(axes or "")
        if len(raw) == len(shape):
            return raw
        if len(shape) == 2:
            return "YX"
        if len(shape) == 3:
            if shape[-1] in {3, 4}:
                return "YXS"
            return "ZYX"
        if len(shape) == 4:
            if shape[-1] in {3, 4}:
                return "ZYXS"
            return "ZCYX"
        if len(shape) == 5:
            return "TZCYX"
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return letters[: max(0, len(shape) - 2)] + "YX"

    def _fl_tiff_axis_roles(axes: str, shape: tuple[int, ...]) -> dict:
        axis_list = list(axes)
        y_axis = axes.rfind("Y")
        x_axis = axes.rfind("X")
        if y_axis < 0 or x_axis < 0:
            y_axis = max(0, len(shape) - 2)
            x_axis = max(0, len(shape) - 1)

        z_axis = axes.find("Z")
        if z_axis < 0:
            for i, size in enumerate(shape):
                if i not in {y_axis, x_axis} and axis_list[i] not in {"C", "S"} and int(size) > 1:
                    z_axis = i
                    break

        c_axis = axes.find("C")
        t_axis = axes.find("T")
        s_axis = axes.find("S")
        if t_axis == z_axis:
            t_axis = -1
        extras = []
        for i, label in enumerate(axis_list):
            if i in {y_axis, x_axis, z_axis, c_axis, t_axis, s_axis}:
                continue
            extras.append({"axis": label or f"D{i}", "index": i, "count": int(shape[i])})
        return {"y": y_axis, "x": x_axis, "z": z_axis, "c": c_axis, "t": t_axis, "s": s_axis, "extras": extras}

    def _fl_tiff_series_info(tiff_path: Path) -> dict:
        with tifflib.TiffFile(str(tiff_path)) as tf:
            series = tf.series[0]
            shape = tuple(int(v) for v in series.shape)
            axes = _fl_clean_tiff_axes(getattr(series, "axes", ""), shape)
            dtype = str(series.dtype)
        roles = _fl_tiff_axis_roles(axes, shape)
        y_count = int(shape[roles["y"]]) if roles["y"] >= 0 else int(shape[-2])
        x_count = int(shape[roles["x"]]) if roles["x"] >= 0 else int(shape[-1])
        z_axis = roles["z"]
        c_axis = roles["c"]
        t_axis = roles["t"]
        z_count = int(shape[z_axis]) if z_axis >= 0 else 1
        c_count = int(shape[c_axis]) if c_axis >= 0 else 1
        t_count = int(shape[t_axis]) if t_axis >= 0 else 1
        cal = _fl_tiff_calibration(tiff_path)
        return {
            "path": str(tiff_path),
            "name": tiff_path.name,
            "shape": list(shape),
            "axes": axes,
            "dtype": dtype,
            "dimensions": {
                "x": x_count,
                "y": y_count,
                "z": z_count,
                "c": c_count,
                "t": t_count,
                "extras": roles["extras"],
            },
            "axis_roles": {k: v for k, v in roles.items() if k != "extras"},
            "calibration": cal,
            "can_3d": z_count > 1,
        }

    def _fl_tiff_read_array(tiff_path: Path) -> tuple[np.ndarray, str, dict]:
        with tifflib.TiffFile(str(tiff_path)) as tf:
            series = tf.series[0]
            arr = np.asarray(series.asarray())
            axes = _fl_clean_tiff_axes(getattr(series, "axes", ""), arr.shape)
        return arr, axes, _fl_tiff_axis_roles(axes, tuple(arr.shape))

    def _fl_tiff_plane_from_array(
        arr: np.ndarray,
        axes: str,
        roles: dict,
        z: int = 0,
        c: int = 0,
        t: int = 0,
        extra_indices: dict | None = None,
    ) -> np.ndarray:
        shape = tuple(arr.shape)
        indexer = []
        extra_indices = extra_indices if isinstance(extra_indices, dict) else {}
        for i, _size in enumerate(shape):
            if i == roles.get("y") or i == roles.get("x"):
                indexer.append(slice(None))
            elif i == roles.get("z"):
                indexer.append(max(0, min(int(z or 0), shape[i] - 1)))
            elif i == roles.get("c"):
                indexer.append(max(0, min(int(c or 0), shape[i] - 1)))
            elif i == roles.get("t"):
                indexer.append(max(0, min(int(t or 0), shape[i] - 1)))
            elif i == roles.get("s"):
                indexer.append(slice(None))
            else:
                raw = extra_indices.get(str(i), extra_indices.get(axes[i], 0))
                indexer.append(max(0, min(int(raw or 0), shape[i] - 1)))
        plane = np.asarray(arr[tuple(indexer)])
        plane = np.squeeze(plane)
        if plane.ndim == 3 and plane.shape[-1] in {3, 4}:
            return np.mean(plane[..., :3].astype(np.float32), axis=-1)
        if plane.ndim != 2:
            plane = plane.reshape((-1, plane.shape[-2], plane.shape[-1]))[0]
        return plane


    def _fl_json_float(v) -> float | None:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(x) or x <= 0:
            return None
        return x

    def _fl_find_gif_metadata_json(tiff_path: Path) -> Path | None:
        candidates = [
            tiff_path.with_name(f"{tiff_path.stem}_metadata.json"),
            tiff_path.with_suffix(".json"),
        ]
        if tiff_path.stem.endswith("_selected_stacks"):
            base = tiff_path.stem[: -len("_selected_stacks")]
            candidates.append(tiff_path.with_name(f"{base}_metadata.json"))
        for p in candidates:
            if p.exists() and p.is_file():
                return p
        return None

    def _fl_metadata_pixel_size_um(payload: object) -> tuple[float | None, str]:
        if not isinstance(payload, dict):
            return None, ""

        calibration = payload.get("calibration")
        if isinstance(calibration, dict):
            px_um = _fl_json_float(calibration.get("pixel_width_um"))
            if px_um is not None:
                return px_um, "metadata calibration.pixel_width_um"
            px_per_um = _fl_json_float(calibration.get("x_pixels_per_um"))
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata calibration.x_pixels_per_um"

        scale = payload.get("readlif_scale")
        if isinstance(scale, list) and scale:
            try:
                px_per_um = _fl_json_float(abs(float(scale[0])) if scale[0] is not None else None)
            except (TypeError, ValueError):
                px_per_um = None
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata readlif_scale[0]"

        scale_n = payload.get("readlif_scale_n")
        if isinstance(scale_n, dict):
            px_per_um = _fl_json_float(scale_n.get("1"))
            if px_per_um is not None:
                return 1.0 / px_per_um, "metadata readlif_scale_n.1"

        xml_meta = payload.get("leica_xml_metadata")
        dims = xml_meta.get("dimensions") if isinstance(xml_meta, dict) else None
        if isinstance(dims, list):
            for dim in dims:
                if not isinstance(dim, dict) or str(dim.get("DimID", "")) != "1":
                    continue
                n = _fl_json_float(dim.get("NumberOfElements"))
                length = _fl_json_float(abs(float(dim.get("Length", 0.0))) if dim.get("Length") is not None else None)
                unit = str(dim.get("Unit", "") or "").strip().lower()
                if n is not None and length is not None:
                    scale = _fl_unit_to_um_scale(unit)
                    if scale is not None:
                        return (length * scale) / n, "metadata Leica DimID 1 Length"

        return None, ""

    def _fl_resolve_gif_scale(tiff_path: Path, auto_scale: bool, manual_px_per_um: float) -> dict:
        manual = max(0.01, float(manual_px_per_um or 3.45))
        if auto_scale:
            meta_path = _fl_find_gif_metadata_json(tiff_path)
            if meta_path is not None:
                try:
                    px_um, source = _fl_metadata_pixel_size_um(json.loads(meta_path.read_text(encoding="utf-8")))
                    if px_um is not None:
                        return {
                            "pixels_per_um": 1.0 / px_um,
                            "pixel_size_um": px_um,
                            "source": source,
                            "metadata_path": str(meta_path),
                        }
                except Exception:
                    pass

            px_um = _fl_infer_pixel_size_um_from_tiff(str(tiff_path))
            if px_um is not None and px_um > 0:
                return {
                    "pixels_per_um": 1.0 / px_um,
                    "pixel_size_um": px_um,
                    "source": "TIFF resolution metadata",
                    "metadata_path": "",
                }

        return {
            "pixels_per_um": manual,
            "pixel_size_um": 1.0 / manual,
            "source": "manual px/um",
            "metadata_path": "",
        }



    return {
        "_fl_positive_float": _fl_positive_float,
        "_fl_tiff_plane_from_array": _fl_tiff_plane_from_array,
        "_fl_tiff_read_array": _fl_tiff_read_array,
        "_fl_tiff_series_info": _fl_tiff_series_info,
        "_fl_resolve_gif_scale": _fl_resolve_gif_scale,
    }
