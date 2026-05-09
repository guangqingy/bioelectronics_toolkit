from __future__ import annotations

import os
from pathlib import Path

from services.fluorescence import gif as fl_gif


def env_text(key: str, default: str = "") -> str:
    return os.environ.get(f"DP_{key.upper()}", default).strip()


def env_float(key: str, default: float) -> float:
    raw = env_text(key)
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    raw = env_text(key)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def main() -> None:
    input_raw = env_text("input_path")
    if not input_raw:
        raise RuntimeError("DP_INPUT_PATH is required")

    input_path = Path(input_raw).expanduser().resolve()
    if not input_path.exists():
        raise RuntimeError(f"Input TIFF not found: {input_path}")

    output_path = fl_gif.resolve_output_path(
        input_path,
        raw_output_path=env_text("output_path"),
        raw_output_dir=env_text("output_dir"),
    )

    fps = max(0.1, env_float("fps", 5.0))
    lut = env_text("lut", "Gray")
    scale_bar_um = max(0.0, env_float("scale_bar_um", 10.0))
    px_per_um = max(0.01, env_float("px_per_um", 3.45))
    add_timestamp = env_bool("add_timestamp", True)
    slice_spec = env_text("slice_spec", "")

    result = fl_gif.make_gif(
        input_path=input_path,
        output_path=output_path,
        fps=fps,
        lut=lut,
        scale_bar_um=scale_bar_um,
        pixels_per_um=px_per_um,
        add_timestamp=add_timestamp,
        slice_spec=slice_spec,
    )

    print(f"[OK] GIF: {result['output_path']}")
    print(f"[OK] Preview PNG: {result['preview_path']}")
    print(f"[OK] Frames: {result['n_frames']}")


if __name__ == "__main__":
    main()
