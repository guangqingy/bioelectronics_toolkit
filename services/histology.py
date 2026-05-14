from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

try:
    import tifffile  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    tifffile = None

try:
    import openslide  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    openslide = None

try:
    import bioformats  # type: ignore
    import javabridge  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    bioformats = None
    javabridge = None

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None

_VM_READY = False
_QUPATH_PROJECT_CACHE: list[Path] | None = None


def sanitize_name(value: str, fallback: str = "Untitled") -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw:
        raw = fallback
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
    raw = raw.strip("._-")
    return raw or fallback


def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_bool(v: Any, default: bool = False) -> bool:
    return _bool(v, default=default)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _normalize_rotate_deg(v: Any) -> int:
    deg = _int(v, 0)
    if deg not in {0, 90, 180, 270}:
        return 0
    return deg


def normalize_rotate_deg(v: Any) -> int:
    return _normalize_rotate_deg(v)


def _ensure_vm() -> bool:
    global _VM_READY
    if _VM_READY:
        return True
    if bioformats is None or javabridge is None:
        return False
    try:
        env = javabridge.get_env()
        if env is not None:
            _VM_READY = True
            return True
    except Exception:
        pass
    try:
        javabridge.start_vm(class_path=bioformats.JARS, run_headless=True)
        _VM_READY = True
        return True
    except Exception:
        return False


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
    deg = _normalize_rotate_deg(rotate_deg)
    if deg == 0:
        return img
    # PIL rotates CCW for positive angles; we want clockwise.
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


def _candidate_overview_files(case_dir: Path) -> list[Path]:
    results: list[Path] = []
    for pattern in ("*Overview.vsi", "*overview.vsi", "*OVERVIEW.vsi"):
        results.extend(sorted(case_dir.glob(pattern)))
        results.extend(sorted(case_dir.rglob(pattern)))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in results:
        if path not in seen and path.is_file():
            unique.append(path)
            seen.add(path)
    return unique


def find_histology_cases(project_root: str | Path) -> list[dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return []

    cases: list[dict[str, Any]] = []
    for vsi in _candidate_overview_files(root):
        case_dir = vsi.parent
        if case_dir.name.startswith("_"):
            case_dir = case_dir.parent
        if not case_dir.is_dir():
            continue
        cases.append(
            {
                "case_dir": str(case_dir),
                "case_name": case_dir.name,
                "overview_path": str(vsi),
                "overview_name": vsi.name,
                "qupath_name": read_qupath_display_name(case_dir) or "",
            }
        )

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in cases:
        key = str(case.get("overview_path") or "")
        if key in seen:
            continue
        unique.append(case)
        seen.add(key)
    return unique


def read_qupath_display_name(case_dir: str | Path) -> str:
    case = Path(case_dir).expanduser().resolve()
    search_roots = [case]
    if case.parent != case:
        search_roots.append(case.parent)
    for root in search_roots:
        for json_path in root.rglob("server.json"):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
                name = str(metadata.get("name", "")).strip()
                if name:
                    return name
            except Exception:
                continue
    return ""


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


def _load_tifffile_preview_pair(path: Path) -> tuple[Image.Image | None, dict[str, Any], Image.Image | None, dict[str, Any]]:
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
            label_page = max(portraits, key=lambda p: p["area"]) if portraits else min(cand, key=lambda p: p["area"])

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
                    return assoc[key].convert("RGB"), {"backend": "openslide", "associated_name": str(key), "main_source": str(path)}
                except Exception:
                    continue
            for key in assoc.keys():
                try:
                    return assoc[key].convert("RGB"), {"backend": "openslide", "associated_name": str(key), "main_source": str(path)}
                except Exception:
                    continue
        return None, {"backend": "openslide", "error": "no associated image"}
    finally:
        try:
            slide.close()
        except Exception:
            pass


def _load_bioformats_series(path: Path, series: int) -> tuple[Image.Image | None, dict[str, Any]]:
    if bioformats is None or javabridge is None:
        return None, {"backend": "bioformats", "error": "bioformats unavailable"}
    if not _ensure_vm():
        return None, {"backend": "bioformats", "error": "bioformats JVM unavailable"}
    try:
        arr = bioformats.load_image(str(path), series=series, rescale=False)
    except Exception as exc:
        return None, {"backend": "bioformats", "error": str(exc)}
    try:
        return _fit_pil(_to_pil(arr), 1600), {"backend": "bioformats", "series": series, "source": str(path)}
    except Exception as exc:
        return None, {"backend": "bioformats", "error": str(exc)}


def _limit_text(s: str, max_len: int = 600) -> str:
    t = str(s or "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    if len(t) <= max_len:
        return t
    return t[:max_len].rstrip() + "…"


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


def load_histology_preview_pair(
    overview_path: str | Path,
    rotate_deg: int = 0,
    do_ocr: bool = False,
    ocr_lang: str = "eng",
) -> dict[str, Any]:
    path = Path(overview_path).expanduser().resolve()
    case_dir = path.parent if path.is_file() else path
    if path.is_dir():
        overview_candidates = _candidate_overview_files(path)
        if not overview_candidates:
            return {"error": f"No Overview.vsi found under {path}"}
        path = overview_candidates[0]
        case_dir = path.parent

    rotate_deg = _normalize_rotate_deg(rotate_deg)

    result: dict[str, Any] = {
        "case_dir": str(case_dir),
        "overview_path": str(path),
        "case_name": case_dir.name,
        "qupath_name": read_qupath_display_name(case_dir) or "",
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

    # For VS200 Overview.vsi, tifffile often works without OpenSlide/Bio-Formats.
    if path.is_file() and path.suffix.lower() == ".vsi":
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
        main_img, main_meta = _load_bioformats_series(path, 0)
        if main_img is None and main_meta.get("error"):
            main_errors.append(f"bioformats: {main_meta.get('error')}")
    if main_img is None:
        for candidate in _possible_label_paths(path):
            if candidate.exists():
                main_img, main_meta = _load_bioformats_series(candidate, 0)
                if main_img is not None:
                    result["notes"].append(f"Main preview loaded from sidecar: {candidate.name}")
                    break
                if main_meta.get("error"):
                    main_errors.append(f"bioformats sidecar: {main_meta.get('error')}")

    if label_img is None:
        label_img, label_meta = _load_openslide_associated(path)
        if label_img is None and label_meta.get("error"):
            label_errors.append(f"openslide: {label_meta.get('error')}")
    if label_img is None:
        label_img, label_meta = _load_bioformats_series(path, 1)
        if label_img is None and label_meta.get("error"):
            label_errors.append(f"bioformats: {label_meta.get('error')}")
    if label_img is None:
        for candidate in _possible_label_paths(path):
            if candidate.exists() and candidate != path:
                label_img, label_meta = _load_bioformats_series(candidate, 0)
                if label_img is not None:
                    result["notes"].append(f"Label preview loaded from sidecar: {candidate.name}")
                    break
                if label_meta.get("error"):
                    label_errors.append(f"bioformats sidecar: {label_meta.get('error')}")

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
            result["main_source"] = str(main_meta.get("main_source") or main_meta.get("source") or path)
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
            result["label_source"] = str(label_meta.get("associated_name") or label_meta.get("source") or "")
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


def _write_server_json(json_path: Path, old_path: Path, new_path: Path, new_name: str) -> bool:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    changed = False
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    if isinstance(metadata, dict):
        if metadata.get("name") != new_name:
            metadata["name"] = new_name
            data["metadata"] = metadata
            changed = True

    uri = str(data.get("uri", "")) if isinstance(data, dict) else ""
    old_uri = old_path.as_posix()
    new_uri = new_path.as_posix()
    if old_uri in uri:
        data["uri"] = uri.replace(old_uri, new_uri)
        changed = True
    elif old_path.name in uri and new_path.name != old_path.name:
        data["uri"] = uri.replace(old_path.name, new_path.name)
        changed = True

    if not changed:
        return False

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _update_qupath_project(qpproj_path: Path, old_path: Path, new_path: Path, new_name: str) -> dict[str, Any]:
    """Update a QuPath project.qpproj to reflect a renamed folder.

    QuPath's Project image list shows `imageName` from project.qpproj.
    The server URI for each entry also lives in project.qpproj (and often
    duplicated in data/<entryID>/server.json).
    """

    try:
        data = json.loads(qpproj_path.read_text(encoding="utf-8"))
    except Exception:
        return {"project": str(qpproj_path), "updated": False, "error": "failed to read/parse"}

    if not isinstance(data, dict):
        return {"project": str(qpproj_path), "updated": False, "error": "invalid json"}

    images = data.get("images")
    if not isinstance(images, list):
        return {"project": str(qpproj_path), "updated": False, "error": "missing images"}

    changed = False
    updated_entry_ids: list[int] = []

    old_uri = old_path.as_posix()
    new_uri = new_path.as_posix()

    for img in images:
        if not isinstance(img, dict):
            continue
        sb = img.get("serverBuilder")
        if not isinstance(sb, dict):
            continue

        uri = str(sb.get("uri", "") or "")
        uri2 = uri
        if old_uri and old_uri in uri2:
            uri2 = uri2.replace(old_uri, new_uri)
        elif old_path.name and old_path.name in uri2 and new_path.name != old_path.name:
            uri2 = uri2.replace(old_path.name, new_path.name)

        if uri2 == uri:
            continue

        sb["uri"] = uri2
        img["serverBuilder"] = sb
        changed = True

        # Update name shown in the QuPath image list.
        if str(img.get("imageName", "") or "") != new_name:
            img["imageName"] = new_name
            changed = True

        # Some projects also store metadata.name at different levels.
        meta_img = img.get("metadata")
        if isinstance(meta_img, dict) and meta_img.get("name") != new_name:
            meta_img["name"] = new_name
            img["metadata"] = meta_img
            changed = True

        meta_sb = sb.get("metadata")
        if isinstance(meta_sb, dict) and meta_sb.get("name") != new_name:
            meta_sb["name"] = new_name
            sb["metadata"] = meta_sb
            img["serverBuilder"] = sb
            changed = True

        entry_id = img.get("entryID")
        if entry_id is not None:
            try:
                updated_entry_ids.append(int(entry_id))
            except Exception:
                pass

    updated_server_json: list[str] = []
    if changed:
        # Update modify timestamp if present.
        if "modifyTimestamp" in data:
            try:
                data["modifyTimestamp"] = int(time.time() * 1000)
            except Exception:
                pass
        qpproj_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Keep per-entry server.json in sync when present.
        proj_dir = qpproj_path.parent
        for eid in sorted(set(updated_entry_ids)):
            sj = proj_dir / "data" / str(eid) / "server.json"
            if not sj.exists():
                continue
            if _write_server_json(sj, old_path, new_path, new_name):
                updated_server_json.append(str(sj))

    return {
        "project": str(qpproj_path),
        "updated": bool(changed),
        "updated_entry_ids": sorted(set(updated_entry_ids)),
        "updated_server_json": updated_server_json,
    }


def _get_qupath_project_candidates(dataset_parent: Path) -> list[Path]:
    """Find likely QuPath project files without scanning huge raw datasets."""

    global _QUPATH_PROJECT_CACHE
    candidates: list[Path] = []

    # Common: projects are stored under the app workspace.
    if _QUPATH_PROJECT_CACHE is None:
        try:
            workspace_root = Path(__file__).resolve().parents[1]
            _QUPATH_PROJECT_CACHE = list(workspace_root.rglob("project.qpproj"))
        except Exception:
            _QUPATH_PROJECT_CACHE = []
    candidates.extend(_QUPATH_PROJECT_CACHE)

    # Also check if a project is next to the dataset root (non-recursive).
    try:
        candidates.extend(list(dataset_parent.glob("*.qpproj")))
    except Exception:
        pass

    # Unique + existing
    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp in seen or not rp.is_file():
            continue
        uniq.append(rp)
        seen.add(rp)
    return uniq


def _parse_qupath_project_paths(value: Any) -> list[Path]:
    if value is None:
        return []
    items: list[str] = []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        s = str(value).strip()
        if not s:
            return []
        items = re.split(r"[\n,;]+", s)

    out: list[Path] = []
    seen: set[Path] = set()
    for raw in items:
        p = str(raw or "").strip()
        if not p:
            continue
        path = Path(p).expanduser()
        try:
            path = path.resolve()
        except Exception:
            pass
        if path.suffix.lower() != ".qpproj":
            continue
        if path in seen:
            continue
        if path.is_file():
            out.append(path)
            seen.add(path)
    return out


def _qupath_uri_to_posix_path(uri: Any) -> str:
    s = str(uri or "").strip()
    if not s:
        return ""
    if s.startswith("file:"):
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(s)
            path = unquote(parsed.path or "")
            if not path:
                path = s[len("file:") :]
            if re.match(r"^/[A-Za-z]:/", path):
                path = path[1:]
            return path
        except Exception:
            return s[len("file:") :]
    return s


def _write_server_json_name_only(json_path: Path, new_name: str) -> bool:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False

    changed = False
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("name") != new_name:
        metadata["name"] = new_name
        data["metadata"] = metadata
        changed = True

    if not changed:
        return False

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _sync_qupath_project_names_from_cases(
    qpproj_path: Path,
    cases: list[dict[str, Any]],
    update_server_json: bool = True,
) -> dict[str, Any]:
    """Sync QuPath `imageName` to match histology case folder names.

    Matching rule: If `serverBuilder.uri` path falls under a case folder,
    then that entry's `imageName` is set to the case folder name.
    """

    try:
        data = json.loads(qpproj_path.read_text(encoding="utf-8"))
    except Exception:
        return {"project": str(qpproj_path), "updated": False, "error": "failed to read/parse"}

    if not isinstance(data, dict):
        return {"project": str(qpproj_path), "updated": False, "error": "invalid json"}

    images = data.get("images")
    if not isinstance(images, list):
        return {"project": str(qpproj_path), "updated": False, "error": "missing images"}

    prefixes: list[tuple[str, str, str]] = []
    for case in cases or []:
        case_dir_raw = str((case or {}).get("case_dir", "") or "").strip()
        if not case_dir_raw:
            continue
        p = Path(case_dir_raw).expanduser()
        try:
            p = p.resolve()
        except Exception:
            pass
        case_name = str((case or {}).get("case_name", "") or "").strip() or p.name
        prefix = p.as_posix().rstrip("/") + "/"
        prefixes.append((prefix, case_name, p.as_posix()))

    prefixes.sort(key=lambda t: len(t[0]), reverse=True)
    matched_case_dirs: set[str] = set()

    changed = False
    updated_images_count = 0
    updated_entry_ids: list[int] = []
    entry_name_map: dict[int, str] = {}
    updated_details: list[dict[str, Any]] = []
    matched_images = 0
    unmatched_images = 0

    for img in images:
        if not isinstance(img, dict):
            continue
        sb = img.get("serverBuilder")
        if not isinstance(sb, dict):
            continue
        uri = sb.get("uri")
        path_posix = _qupath_uri_to_posix_path(uri)
        if not path_posix:
            continue

        desired_name: str | None = None
        matched_case_dir: str | None = None
        for prefix, case_name, case_dir_posix in prefixes:
            if path_posix.startswith(prefix):
                desired_name = case_name
                matched_case_dir = case_dir_posix
                break

        if desired_name is None:
            unmatched_images += 1
            continue

        matched_images += 1
        if matched_case_dir:
            matched_case_dirs.add(matched_case_dir)

        current_name = str(img.get("imageName", "") or "")
        if current_name == desired_name:
            continue

        img["imageName"] = desired_name
        changed = True
        updated_images_count += 1

        meta_img = img.get("metadata")
        if isinstance(meta_img, dict) and meta_img.get("name") != desired_name:
            meta_img["name"] = desired_name
            img["metadata"] = meta_img

        meta_sb = sb.get("metadata")
        if isinstance(meta_sb, dict) and meta_sb.get("name") != desired_name:
            meta_sb["name"] = desired_name
            sb["metadata"] = meta_sb
            img["serverBuilder"] = sb

        entry_id = img.get("entryID")
        if entry_id is not None:
            try:
                eid_int = int(entry_id)
                updated_entry_ids.append(eid_int)
                entry_name_map[eid_int] = desired_name
            except Exception:
                pass

        if len(updated_details) < 25:
            updated_details.append(
                {
                    "entryID": img.get("entryID"),
                    "old": current_name,
                    "new": desired_name,
                    "uri": str(uri or ""),
                }
            )

    updated_server_json: list[str] = []
    if changed:
        if "modifyTimestamp" in data:
            try:
                data["modifyTimestamp"] = int(time.time() * 1000)
            except Exception:
                pass
        qpproj_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if update_server_json and entry_name_map:
            proj_dir = qpproj_path.parent
            for eid, name in sorted(entry_name_map.items()):
                sj = proj_dir / "data" / str(eid) / "server.json"
                if not sj.exists():
                    continue
                if _write_server_json_name_only(sj, name):
                    updated_server_json.append(str(sj))

    unmatched_case_count = 0
    if prefixes:
        all_case_dirs = {cd for (_prefix, _name, cd) in prefixes}
        unmatched_case_count = len(all_case_dirs - matched_case_dirs)

    return {
        "project": str(qpproj_path),
        "updated": bool(changed),
        "updated_images": int(updated_images_count),
        "updated_entry_ids": sorted(set(updated_entry_ids)),
        "updated_server_json": updated_server_json,
        "matched_images": matched_images,
        "unmatched_images": unmatched_images,
        "unmatched_cases": unmatched_case_count,
        "details": updated_details,
    }


def sync_qupath_names_from_histology_cases(
    cases: list[dict[str, Any]],
    qupath_project: Any,
    update_server_json: bool = True,
) -> dict[str, Any]:
    qpprojs = _parse_qupath_project_paths(qupath_project)
    if not qpprojs:
        raise ValueError("QuPath project must be an existing .qpproj file")

    results: list[dict[str, Any]] = []
    total_updated = 0
    total_matched_images = 0
    total_unmatched_images = 0
    total_unmatched_cases = 0
    updated_projects: list[str] = []

    for qpproj in qpprojs:
        info = _sync_qupath_project_names_from_cases(qpproj, cases, update_server_json=update_server_json)
        if info.get("error"):
            raise ValueError(f"{qpproj}: {info.get('error')}")
        results.append(info)
        if info.get("updated"):
            updated_projects.append(str(qpproj))
            total_updated += int(info.get("updated_images") or 0)
        total_matched_images += int(info.get("matched_images") or 0)
        total_unmatched_images += int(info.get("unmatched_images") or 0)
        total_unmatched_cases = max(total_unmatched_cases, int(info.get("unmatched_cases") or 0))

    return {
        "updated_projects": sorted(set(updated_projects)),
        "updated_images": total_updated,
        "matched_images": total_matched_images,
        "unmatched_images": total_unmatched_images,
        "unmatched_cases": total_unmatched_cases,
        "results": results,
    }


def rename_histology_case(
    case_dir: str | Path,
    new_name: str,
    update_server_json: bool = True,
    qupath_project: str | Path | list[str] | None = None,
) -> dict[str, Any]:
    old_path = Path(case_dir).expanduser().resolve()
    if not old_path.exists():
        raise FileNotFoundError(f"Case folder not found: {old_path}")

    clean_name = sanitize_name(new_name, fallback=old_path.name)
    new_path = old_path.with_name(clean_name)
    if new_path.exists() and new_path != old_path:
        raise FileExistsError(f"Target folder already exists: {new_path}")

    qupath_projects: list[Path] = []
    if update_server_json and qupath_project:
        qupath_projects = _parse_qupath_project_paths(qupath_project)
        if not qupath_projects:
            raise ValueError("QuPath project must be an existing .qpproj file")

    old_path.rename(new_path)

    updated_server_json: list[str] = []
    updated_qupath_projects: list[str] = []
    if update_server_json:
        candidates = qupath_projects or _get_qupath_project_candidates(new_path.parent)
        for qpproj in candidates:
            # Fast skip to avoid parsing unrelated projects.
            try:
                head = qpproj.read_text(encoding="utf-8", errors="ignore")
                if old_path.as_posix() not in head and old_path.name not in head:
                    continue
            except Exception:
                pass
            info = _update_qupath_project(qpproj, old_path, new_path, clean_name)
            if info.get("updated"):
                updated_qupath_projects.append(str(qpproj))
                updated_server_json.extend(list(info.get("updated_server_json") or []))

    rename_map = new_path.parent / "histology_rename_map.csv"
    rows: list[dict[str, Any]] = []
    if rename_map.exists():
        try:
            import pandas as pd

            raw_rows = pd.read_csv(rename_map).to_dict(orient="records")
            rows = [{str(k): v for k, v in row.items()} for row in (raw_rows or [])]
        except Exception:
            rows = []
    rows = [row for row in rows if str(row.get("old_name", "")) != old_path.name]
    rows.append(
        {
            "old_name": old_path.name,
            "new_name": clean_name,
            "old_path": str(old_path),
            "new_path": str(new_path),
        }
    )
    try:
        import pandas as pd

        pd.DataFrame(rows).to_csv(rename_map, index=False)
    except Exception:
        pass

    return {
        "old_path": str(old_path),
        "new_path": str(new_path),
        "new_name": clean_name,
        "updated_server_json": sorted(set(updated_server_json)),
        "updated_qupath_projects": sorted(set(updated_qupath_projects)),
        "rename_map": str(rename_map),
    }
