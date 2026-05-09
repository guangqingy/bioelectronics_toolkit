from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

LUT_OPTIONS = ["Red", "Blue", "Gray", "Green", "Magenta", "Cyan", "Yellow"]


def import_tifffile(tifflib_module: Any = None):
    if tifflib_module is not None:
        return tifflib_module
    import tifffile

    return tifffile


def import_pillow(
    image_module: Any = None,
    image_draw_module: Any = None,
    image_font_module: Any = None,
):
    if image_module is not None and image_draw_module is not None and image_font_module is not None:
        return image_module, image_draw_module, image_font_module
    from PIL import Image, ImageDraw, ImageFont

    return Image, ImageDraw, ImageFont


def prepare_plane(raw: np.ndarray) -> np.ndarray:
    arr = np.asarray(raw)
    arr = np.squeeze(arr)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[-1] in {3, 4}:
        rgb = arr[..., :3].astype(np.float32)
        return np.mean(rgb, axis=-1)
    if arr.ndim >= 3:
        return arr.reshape((-1, arr.shape[-2], arr.shape[-1]))[0]
    raise ValueError(f"Unsupported TIFF plane shape for GIF: {arr.shape}")


def split_tiff_array_to_planes(arr: np.ndarray) -> list[np.ndarray]:
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return [arr]
    if arr.ndim == 3 and arr.shape[-1] in {3, 4}:
        return [prepare_plane(arr)]
    if arr.ndim >= 3:
        if arr.shape[-1] in {3, 4}:
            flat = arr.reshape((-1, arr.shape[-3], arr.shape[-2], arr.shape[-1]))
            return [prepare_plane(x) for x in flat]
        flat = arr.reshape((-1, arr.shape[-2], arr.shape[-1]))
        return [x for x in flat]
    raise ValueError(f"Unsupported TIFF shape for GIF: {arr.shape}")


def tiff_frame_count(tiff_path: Path, tifflib_module: Any = None) -> tuple[int, list[int]]:
    tifflib = import_tifffile(tifflib_module)
    with tifflib.TiffFile(str(tiff_path)) as tif:
        if len(tif.pages) == 1:
            arr0 = tif.pages[0].asarray()
            return len(split_tiff_array_to_planes(arr0)), list(np.asarray(arr0).shape)
        page0 = tif.pages[0].asarray()
        return len(tif.pages), list(np.asarray(page0).shape)


def parse_slice_spec(slice_spec: object, n_frames: int) -> list[int]:
    raw = str(slice_spec or "").strip().lower()
    if raw in {"", "all", "*"}:
        return list(range(n_frames))
    if n_frames <= 0:
        raise ValueError("TIFF has no readable slices")

    indices: list[int] = []
    for token in raw.split(","):
        tok = token.strip().replace(" ", "")
        if not tok:
            continue

        step = 1
        if ":" in tok:
            tok, step_raw = tok.split(":", 1)
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise ValueError(f"Invalid slice step: {token}") from exc
            if step <= 0:
                raise ValueError(f"Slice step must be positive: {token}")

        if "-" in tok:
            start_raw, end_raw = tok.split("-", 1)
            try:
                start = int(start_raw)
                end = int(end_raw)
            except ValueError as exc:
                raise ValueError(f"Invalid slice range: {token}") from exc
            if start <= 0 or end <= 0:
                raise ValueError(f"Slice numbers are 1-based and must be positive: {token}")
            if start > n_frames or end > n_frames:
                raise ValueError(
                    f"Slice range out of bounds: {token}; TIFF has {n_frames} slice(s)"
                )
            if start <= end:
                indices.extend(range(start - 1, end, step))
            else:
                indices.extend(range(start - 1, end - 2, -step))
        else:
            try:
                idx = int(tok)
            except ValueError as exc:
                raise ValueError(f"Invalid slice number: {token}") from exc
            if idx <= 0 or idx > n_frames:
                raise ValueError(f"Slice {idx} out of bounds; TIFF has {n_frames} slice(s)")
            indices.append(idx - 1)

    if not indices:
        raise ValueError("No slices selected")
    return indices


def read_selected_planes(
    tiff_path: Path,
    indices: list[int],
    tifflib_module: Any = None,
) -> list[np.ndarray]:
    tifflib = import_tifffile(tifflib_module)
    with tifflib.TiffFile(str(tiff_path)) as tif:
        if len(tif.pages) == 1:
            planes = split_tiff_array_to_planes(tif.pages[0].asarray())
            return [planes[i] for i in indices]
        return [prepare_plane(tif.pages[i].asarray()) for i in indices]


def normalize_to_uint8(frame: np.ndarray, p_low: float = 1.0, p_high: float = 99.0) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)

    lo, hi = np.percentile(finite, [p_low, p_high])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.min(finite))
        hi = float(np.max(finite))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)

    unit = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
    unit = np.nan_to_num(unit, nan=0.0, posinf=1.0, neginf=0.0)
    return np.round(unit * 255.0).astype(np.uint8)


def apply_lut(gray8: np.ndarray, lut: str) -> np.ndarray:
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


def draw_scale_bar(
    img,
    scale_bar_um: float,
    pixels_per_um: float,
    image_draw_module: Any = None,
    image_font_module: Any = None,
) -> None:
    if scale_bar_um <= 0 or pixels_per_um <= 0:
        return

    _image_mod, image_draw_mod, image_font_mod = import_pillow(
        image_draw_module=image_draw_module,
        image_font_module=image_font_module,
    )
    draw = image_draw_mod.Draw(img)
    w_img, h_img = img.size
    bar_px = max(2, min(w_img - 24, int(round(scale_bar_um * pixels_per_um))))
    thickness = max(3, int(h_img * 0.01))
    margin = 12
    x2, y2 = w_img - margin, h_img - margin
    font = image_font_mod.load_default()
    scale_label = f"{scale_bar_um:g} um"
    bbox = draw.textbbox((0, 0), scale_label, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_x = max(3, min(x2 - text_w, x2 - bar_px))
    text_y = max(3, y2 - thickness - text_h - 5)
    bg_x0 = max(0, min(text_x, x2 - bar_px) - 3)
    bg_y0 = max(0, text_y - 2)
    bg_x1 = min(w_img, x2 + 3)
    bg_y1 = min(h_img, y2 + 3)

    draw.rectangle([bg_x0, bg_y0, bg_x1, bg_y1], fill=(0, 0, 0))
    draw.text((text_x, text_y), scale_label, fill=(255, 255, 255), font=font)
    draw.rectangle([x2 - bar_px, y2 - thickness, x2, y2], fill=(255, 255, 255))


def draw_timestamp(
    img,
    frame_idx: int,
    fps: float,
    label_mode: str = "time",
    image_draw_module: Any = None,
    image_font_module: Any = None,
) -> None:
    if fps <= 0:
        return

    mode = str(label_mode or "time").strip().lower()
    if mode in {"none", "off", "no"}:
        return
    if mode in {"frame", "index", "sequence", "seq"}:
        label = f"Frame {frame_idx + 1:03d}"
    else:
        label = f"t={frame_idx / fps:.2f}s"

    _image_mod, image_draw_mod, image_font_mod = import_pillow(
        image_draw_module=image_draw_module,
        image_font_module=image_font_module,
    )
    draw = image_draw_mod.Draw(img)
    font = image_font_mod.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([6, 6, 6 + text_w + 6, 6 + text_h + 6], fill=(0, 0, 0))
    draw.text((9, 9), label, fill=(255, 255, 255), font=font)


def render_frame(
    plane: np.ndarray,
    lut: str,
    frame_idx: int,
    fps: float,
    scale_bar_um: float,
    pixels_per_um: float,
    add_timestamp: bool,
    label_mode: str = "time",
    image_module: Any = None,
    image_draw_module: Any = None,
    image_font_module: Any = None,
):
    image_mod, image_draw_mod, image_font_mod = import_pillow(
        image_module=image_module,
        image_draw_module=image_draw_module,
        image_font_module=image_font_module,
    )
    gray8 = normalize_to_uint8(plane, p_low=1.0, p_high=99.0)
    rgb = apply_lut(gray8, lut)
    img = image_mod.fromarray(rgb, mode="RGB")
    draw_scale_bar(img, scale_bar_um, pixels_per_um, image_draw_mod, image_font_mod)
    if add_timestamp:
        draw_timestamp(img, frame_idx, fps, label_mode, image_draw_mod, image_font_mod)
    return img


def resolve_output_path(
    input_path: Path,
    raw_output_path: str = "",
    raw_output_dir: str = "",
) -> Path:
    if raw_output_path:
        out = Path(raw_output_path).expanduser()
        if not out.is_absolute():
            out = (input_path.parent / out).resolve()
        return out

    if raw_output_dir:
        out_dir = Path(raw_output_dir).expanduser()
        if not out_dir.is_absolute():
            out_dir = (input_path.parent / out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{input_path.stem}.gif"

    return input_path.with_suffix(".gif")


def save_gif(frames: list, output_path: Path, fps: float) -> None:
    if not frames:
        raise ValueError("No frames generated for GIF")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_ms = int(round(1000.0 / max(float(fps), 0.1)))
    frames[0].save(
        str(output_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
    )


def make_gif(
    input_path: Path,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    fps: float = 5.0,
    lut: str = "Gray",
    scale_bar_um: float = 10.0,
    pixels_per_um: float = 3.45,
    add_timestamp: bool = True,
    label_mode: str = "time",
    slice_spec: object = "",
    tifflib_module: Any = None,
    image_module: Any = None,
    image_draw_module: Any = None,
    image_font_module: Any = None,
) -> dict:
    input_path = Path(input_path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input TIFF not found: {input_path}")

    if output_path is None:
        raw_dir = "" if output_dir is None else str(output_dir)
        output_path = resolve_output_path(input_path, raw_output_dir=raw_dir)
    else:
        output_path = Path(output_path).expanduser()
        if not output_path.is_absolute():
            output_path = (input_path.parent / output_path).resolve()

    fps = max(0.1, float(fps))
    scale_bar_um = max(0.0, float(scale_bar_um))
    pixels_per_um = max(0.01, float(pixels_per_um))

    n_available, _shape = tiff_frame_count(input_path, tifflib_module)
    selected_indices = parse_slice_spec(slice_spec, n_available)
    planes = read_selected_planes(input_path, selected_indices, tifflib_module)
    frames = [
        render_frame(
            plane,
            lut=lut,
            frame_idx=i,
            fps=fps,
            scale_bar_um=scale_bar_um,
            pixels_per_um=pixels_per_um,
            add_timestamp=add_timestamp,
            label_mode=label_mode,
            image_module=image_module,
            image_draw_module=image_draw_module,
            image_font_module=image_font_module,
        )
        for i, plane in enumerate(planes)
    ]
    save_gif(frames, output_path, fps)

    preview_path = output_path.with_name(f"{output_path.stem}_preview.png")
    frames[0].save(str(preview_path), format="PNG")
    return {
        "output_path": str(output_path),
        "preview_path": str(preview_path),
        "n_frames": len(frames),
        "selected_slices": len(selected_indices),
        "source_frames": n_available,
    }
