from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from services.histology_common import normalize_rotate_deg
from services.histology_discovery import candidate_overview_files

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tifffile = None

try:
    import openslide  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    openslide = None

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None

def _array_to_uint8(arr: np.ndarray) -> np.ndarray:
    data = np.asarray(arr)
    if data.ndim == 2:
        data = data[..., np.newaxis]
    if data.dtype.kind in {"u", "i", "f"}:
        finite = data[np.isfinite(data)] if data.dtype.kind == "f" else data
        if finite.size == 0:
            return np.zeros(data.shape[:2] + ((3 if data.shape[-1] > 1 else 1),), dtype=np.uint8)
        lo = float(np.percentile(finite, 1.0))
        hi = float(np.percentile(finite, 99.8))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = float(np.nanmin(finite))
            hi = float(np.nanmax(finite))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            hi = lo + 1.0
        scaled = np.clip((data.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
        return np.round(scaled * 255.0).astype(np.uint8)
    return np.asarray(data, dtype=np.uint8)


def _to_pil(img: Any) -> Image.Image:
    if isinstance(img, Image.Image):
        return img.convert("RGB")
    arr = np.asarray(img)
    if arr.ndim == 2:
        arr8 = _array_to_uint8(arr)
        return Image.fromarray(arr8[..., 0], mode="L").convert("RGB")
    if arr.ndim == 3:
        if arr.shape[-1] == 1:
            arr8 = _array_to_uint8(arr[..., 0])
            return Image.fromarray(arr8[..., 0], mode="L").convert("RGB")
        if arr.shape[-1] >= 3:
            arr8 = _array_to_uint8(arr[..., :3])
            return Image.fromarray(arr8[..., :3], mode="RGB")
    arr8 = _array_to_uint8(arr)
    if arr8.ndim == 3 and arr8.shape[-1] >= 3:
        return Image.fromarray(arr8[..., :3], mode="RGB")
    return Image.fromarray(arr8.squeeze(), mode="L").convert("RGB")


def _fit_pil(img: Image.Image, max_side: int = 1600) -> Image.Image:
    out = img.copy()
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return out


def _rotate_clockwise(img: Image.Image, rotate_deg: int) -> Image.Image:
    deg = normalize_rotate_deg(rotate_deg)
    if deg == 0:
        return img
    # PIL rotates CCW for positive angles; histology UI asks for clockwise.
    return img.rotate(-deg, expand=True)


def _pil_to_b64(img: Image.Image) -> str:
    from io import BytesIO

    stream = BytesIO()
    img.save(stream, format="PNG")
    stream.seek(0)
    return base64.b64encode(stream.read()).decode("ascii")


def image_to_b64(img: Any, max_side: int = 1600) -> str:
    pil = _fit_pil(_to_pil(img), max_side=max_side)
    return _pil_to_b64(pil)


def _possible_label_paths(overview_path: Path) -> list[Path]:
    case_dir = overview_path.parent
    base = overview_path.stem
    candidates: list[Path] = []
    for stem in (base, base.replace("Overview", "Overview_")):
        sidecar = case_dir / f"_{stem}_"
        for stack_name in ("stack1", "stack10000", "stack0001"):
            for file_name in ("frame_t.ets", "frame_t_0.ets", "frame_t.tif", "frame_t.tiff"):
                candidates.append(sidecar / stack_name / file_name)
    return candidates


def _tiff_tag_str(page: Any, tag_name: str) -> str:
    try:
        tags = getattr(page, "tags", None)
        if not tags or tag_name not in tags:
            return ""
        value = tags[tag_name].value
        if isinstance(value, bytes):
            value = value.decode("utf-8", "ignore")
        return str(value).strip()
    except Exception:
        return ""


def _load_tifffile_preview_pair(
    path: Path,
) -> tuple[Image.Image | None, dict[str, Any], Image.Image | None, dict[str, Any]]:
    if tifffile is None:
        err = {"backend": "tifffile", "error": "tifffile unavailable"}
        return None, err, None, dict(err)

    try:
        with tifffile.TiffFile(str(path)) as tf:
            rgb_pages: list[dict[str, Any]] = []
            gray_pages: list[dict[str, Any]] = []
            for i, page in enumerate(tf.pages):
                shape = getattr(page, "shape", None)
                if not isinstance(shape, tuple) or len(shape) not in {2, 3}:
                    continue
                height = int(shape[0])
                width = int(shape[1])
                if min(height, width) < 32:
                    continue
                make = _tiff_tag_str(page, "Make")
                software = _tiff_tag_str(page, "Software")
                model = _tiff_tag_str(page, "Model")
                info = {
                    "index": i,
                    "shape": tuple(int(x) for x in shape),
                    "height": height,
                    "width": width,
                    "area": int(height * width),
                    "make": make,
                    "software": software,
                    "model": model,
                }
                if len(shape) == 3 and int(shape[-1]) >= 3:
                    rgb_pages.append(info)
                elif len(shape) == 2:
                    gray_pages.append(info)

            candidates = rgb_pages or gray_pages
            if not candidates:
                err = {"backend": "tifffile", "error": "no usable pages found"}
                return None, err, None, dict(err)

            preferred = [
                p
                for p in candidates
                if ("olympus" in (p.get("make") or "").lower())
                or ("xv imaging" in (p.get("software") or "").lower())
            ]
            cand = preferred or candidates

            portraits = [p for p in cand if p["height"] > p["width"]]
            label_page = (
                max(portraits, key=lambda p: p["area"])
                if portraits
                else min(cand, key=lambda p: p["area"])
            )

            others = [p for p in cand if p is not label_page]
            landscapes = [p for p in others if p["width"] >= p["height"]]
            if landscapes:
                main_page = max(landscapes, key=lambda p: p["area"])
            elif others:
                main_page = max(others, key=lambda p: p["area"])
            else:
                main_page = label_page

            def decode(page_info: dict[str, Any]) -> Image.Image:
                page = tf.pages[int(page_info["index"])]
                arr = np.asarray(page.asarray())
                if arr.ndim == 3 and arr.shape[-1] > 3:
                    arr = arr[..., :3]
                return _fit_pil(_to_pil(arr), 1600)

            main_img = None
            main_meta: dict[str, Any] = {
                "backend": "tifffile",
                "page": int(main_page["index"]),
                "shape": main_page["shape"],
                "make": main_page.get("make", ""),
                "software": main_page.get("software", ""),
                "model": main_page.get("model", ""),
                "source": str(path),
            }
            try:
                main_img = decode(main_page)
            except Exception as exc:
                main_meta["error"] = str(exc)

            label_img = None
            label_meta: dict[str, Any] = {
                "backend": "tifffile",
                "page": int(label_page["index"]),
                "shape": label_page["shape"],
                "make": label_page.get("make", ""),
                "software": label_page.get("software", ""),
                "model": label_page.get("model", ""),
                "source": str(path),
            }
            try:
                label_img = decode(label_page)
            except Exception as exc:
                label_meta["error"] = str(exc)

            return main_img, main_meta, label_img, label_meta
    except Exception as exc:
        err = {"backend": "tifffile", "error": str(exc)}
        return None, err, None, dict(err)


def _load_openslide_preview(path: Path) -> tuple[Image.Image | None, dict[str, Any]]:
    if openslide is None:
        return None, {"backend": "openslide", "error": "openslide unavailable"}
    try:
        slide = openslide.OpenSlide(str(path))
    except Exception as exc:
        return None, {"backend": "openslide", "error": str(exc)}

    info: dict[str, Any] = {"backend": "openslide"}
    try:
        main = slide.get_thumbnail((1600, 1600)).convert("RGB")
        info["main_source"] = str(path)
        info["series"] = 0
        return main, info
    finally:
        try:
            slide.close()
        except Exception:
            pass


def _load_openslide_associated(path: Path) -> tuple[Image.Image | None, dict[str, Any]]:
    if openslide is None:
        return None, {"backend": "openslide", "error": "openslide unavailable"}
    try:
        slide = openslide.OpenSlide(str(path))
    except Exception as exc:
        return None, {"backend": "openslide", "error": str(exc)}

    try:
        assoc = getattr(slide, "associated_images", None)
        if assoc:
            preferred = []
            for key in assoc.keys():
                low = str(key).lower()
                if "label" in low:
                    preferred.insert(0, key)
                elif "macro" in low or "overview" in low:
                    preferred.append(key)
            for key in preferred:
                try:
                    return assoc[key].convert("RGB"), {
                        "backend": "openslide",
                        "associated_name": str(key),
                        "main_source": str(path),
                    }
                except Exception:
                    continue
            for key in assoc.keys():
                try:
                    return assoc[key].convert("RGB"), {
                        "backend": "openslide",
                        "associated_name": str(key),
                        "main_source": str(path),
                    }
                except Exception:
                    continue
        return None, {"backend": "openslide", "error": "no associated image"}
    finally:
        try:
            slide.close()
        except Exception:
            pass


def _limit_text(s: str, max_len: int = 600) -> str:
    t = str(s or "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip() + "..."


def _ocr_pil(img: Image.Image, lang: str = "eng") -> tuple[str, str]:
    if pytesseract is None:
        return "", "pytesseract not installed"
    try:
        gray = img.convert("L")
        gray = ImageOps.autocontrast(gray)
        if max(gray.size) < 900:
            gray = gray.resize((gray.size[0] * 2, gray.size[1] * 2), Image.Resampling.BICUBIC)
        text = pytesseract.image_to_string(gray, lang=lang or "eng", config="--psm 6")
        return _limit_text(text), ""
    except Exception as exc:
        return "", str(exc)


def _load_raster_preview(path: Path) -> tuple[Image.Image | None, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff", ".vsi"}:
        main_img, main_meta, label_img, label_meta = _load_tifffile_preview_pair(path)
        if main_img is not None:
            return main_img, main_meta
        if label_img is not None:
            return label_img, label_meta
        error = main_meta.get("error") or label_meta.get("error") or "no usable raster page"
        return None, {"backend": "tifffile", "error": error, "source": str(path)}
    if suffix == ".ets":
        return None, {
            "backend": "ets",
            "error": "raw ETS sidecars must be converted to TIFF before preview",
            "source": str(path),
        }
    try:
        with Image.open(path) as img:
            return _fit_pil(img.convert("RGB"), 1600), {"backend": "pillow", "source": str(path)}
    except Exception as exc:
        return None, {"backend": "pillow", "error": str(exc), "source": str(path)}


def load_histology_preview_pair(
    overview_path: str | Path,
    rotate_deg: int = 0,
    do_ocr: bool = False,
    ocr_lang: str = "eng",
) -> dict[str, Any]:
    path = Path(overview_path).expanduser().resolve()
    case_dir = path.parent if path.is_file() else path
    if path.is_dir():
        overview_candidates = candidate_overview_files(path)
        if not overview_candidates:
            return {"error": f"No Overview.vsi found under {path}"}
        path = overview_candidates[0]
        case_dir = path.parent

    rotate_deg = normalize_rotate_deg(rotate_deg)

    result: dict[str, Any] = {
        "case_dir": str(case_dir),
        "overview_path": str(path),
        "case_name": case_dir.name,
        "rotate_deg": rotate_deg,
        "backend_main": "",
        "backend_label": "",
        "main_b64": "",
        "label_b64": "",
        "main_source": "",
        "label_source": "",
        "notes": [],
    }

    main_img: Image.Image | None = None
    main_meta: dict[str, Any] = {}
    label_img: Image.Image | None = None
    label_meta: dict[str, Any] = {}

    main_errors: list[str] = []
    label_errors: list[str] = []

    if path.is_file() and path.suffix.lower() in {".vsi", ".tif", ".tiff"}:
        tf_main, tf_main_meta, tf_label, tf_label_meta = _load_tifffile_preview_pair(path)
        if tf_main is not None:
            main_img, main_meta = tf_main, tf_main_meta
        elif tf_main_meta.get("error"):
            main_errors.append(f"tifffile: {tf_main_meta.get('error')}")

        if tf_label is not None:
            label_img, label_meta = tf_label, tf_label_meta
        elif tf_label_meta.get("error"):
            label_errors.append(f"tifffile: {tf_label_meta.get('error')}")

    if main_img is None:
        main_img, main_meta = _load_openslide_preview(path)
        if main_img is None and main_meta.get("error"):
            main_errors.append(f"openslide: {main_meta.get('error')}")
    if main_img is None:
        for candidate in _possible_label_paths(path):
            if candidate.exists():
                main_img, main_meta = _load_raster_preview(candidate)
                if main_img is not None:
                    result["notes"].append(f"Main preview loaded from sidecar: {candidate.name}")
                    break
                if main_meta.get("error"):
                    main_errors.append(f"{main_meta.get('backend')} sidecar: {main_meta.get('error')}")

    if label_img is None:
        label_img, label_meta = _load_openslide_associated(path)
        if label_img is None and label_meta.get("error"):
            label_errors.append(f"openslide: {label_meta.get('error')}")
    if label_img is None:
        for candidate in _possible_label_paths(path):
            if candidate.exists() and candidate != path:
                label_img, label_meta = _load_raster_preview(candidate)
                if label_img is not None:
                    result["notes"].append(f"Label preview loaded from sidecar: {candidate.name}")
                    break
                if label_meta.get("error"):
                    label_errors.append(f"{label_meta.get('backend')} sidecar: {label_meta.get('error')}")

    if rotate_deg and main_img is not None:
        main_img = _rotate_clockwise(main_img, rotate_deg)
    if rotate_deg and label_img is not None:
        label_img = _rotate_clockwise(label_img, rotate_deg)

    if main_img is not None:
        result["main_b64"] = _pil_to_b64(_fit_pil(_to_pil(main_img), 1600))
        result["backend_main"] = str(main_meta.get("backend", ""))
        if result["backend_main"] == "tifffile":
            page = main_meta.get("page")
            shape = main_meta.get("shape")
            result["main_source"] = f"{path.name} [page {page}, {shape}]"
        else:
            result["main_source"] = str(
                main_meta.get("main_source") or main_meta.get("source") or path
            )
    else:
        msg = "Main image preview could not be loaded."
        if main_errors:
            uniq: list[str] = []
            for e in main_errors:
                if e not in uniq:
                    uniq.append(e)
            msg += " Tried: " + "; ".join(uniq[:3])
        result["notes"].append(msg)

    if label_img is not None:
        result["label_b64"] = _pil_to_b64(_fit_pil(_to_pil(label_img), 1600))
        result["backend_label"] = str(label_meta.get("backend", ""))
        if result["backend_label"] == "tifffile":
            page = label_meta.get("page")
            shape = label_meta.get("shape")
            result["label_source"] = f"{path.name} [page {page}, {shape}]"
        else:
            result["label_source"] = str(
                label_meta.get("associated_name") or label_meta.get("source") or ""
            )
    else:
        msg = "Label/associated image preview could not be loaded."
        if label_errors:
            uniq2: list[str] = []
            for e in label_errors:
                if e not in uniq2:
                    uniq2.append(e)
            msg += " Tried: " + "; ".join(uniq2[:3])
        result["notes"].append(msg)

    if do_ocr:
        if pytesseract is None:
            result["notes"].append("OCR unavailable (install pytesseract + tesseract).")
        else:
            if label_img is not None:
                text, oerr = _ocr_pil(label_img, lang=ocr_lang)
                if text:
                    result["notes"].append("OCR(label): " + text)
                elif oerr:
                    result["notes"].append("OCR(label) failed: " + _limit_text(oerr, 200))
                else:
                    result["notes"].append("OCR(label): (no text)")
            else:
                result["notes"].append("OCR(label): (label preview not available)")

    return result
