from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def save_gif_roi_kymograph_outputs(
    data: dict[str, Any],
    *,
    bool_value: Callable[[Any, bool], bool],
    sanitize_prefix: Callable[[Any, str], str],
    decode_base64_payload: Callable[[str], bytes],
    resolve_output_dir: Callable[[object, object, str], Path],
) -> dict[str, Any]:
    tiff_paths = data.get("tiff_paths") or []
    output_dir_raw = str(data.get("output_dir", "") or "").strip()
    prefix = sanitize_prefix(data.get("prefix", ""), "gif_roi_kymograph")
    save_heatmap_csv = bool_value(data.get("save_heatmap_csv", True), True)
    save_summary_csv = bool_value(data.get("save_summary_csv", True), True)
    save_plot = bool_value(data.get("save_plot", True), True)
    heatmap_csv = str(data.get("heatmap_csv", "") or "")
    summary_csv = str(data.get("summary_csv", "") or "")
    plot_png_b64 = str(data.get("plot_png_b64", "") or "")

    anchor = None
    if isinstance(tiff_paths, list):
        for raw in tiff_paths:
            candidate = Path(str(raw or "").strip())
            if candidate.exists():
                anchor = candidate.parent
                break

    if output_dir_raw:
        out_dir = Path(output_dir_raw).expanduser()
        if not out_dir.is_absolute():
            out_dir = (
                anchor / out_dir
                if anchor is not None
                else resolve_output_dir("", out_dir, "fluorescence_gif_kymograph")
            )
    else:
        out_dir = anchor or resolve_output_dir("", "", "fluorescence_gif_kymograph")
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    heatmap_path = ""
    summary_path = ""
    plot_path = ""
    if save_heatmap_csv and heatmap_csv.strip():
        path = out_dir / f"{prefix}_heatmap.csv"
        path.write_text(heatmap_csv, encoding="utf-8")
        heatmap_path = str(path)
        saved_paths.append(heatmap_path)
    if save_summary_csv and summary_csv.strip():
        path = out_dir / f"{prefix}_summary.csv"
        path.write_text(summary_csv, encoding="utf-8")
        summary_path = str(path)
        saved_paths.append(summary_path)
    if save_plot and plot_png_b64.strip():
        path = out_dir / f"{prefix}.png"
        path.write_bytes(decode_base64_payload(plot_png_b64))
        plot_path = str(path)
        saved_paths.append(plot_path)

    if not saved_paths:
        raise ValueError("No kymograph outputs to save")

    return {
        "ok": True,
        "output_dir": str(out_dir),
        "heatmap_csv_path": heatmap_path,
        "summary_csv_path": summary_path,
        "plot_path": plot_path,
        "saved_paths": saved_paths,
    }
