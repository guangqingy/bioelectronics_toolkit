from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pyabf
import tifffile


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _output_dir(root: Path) -> Path:
    raw = os.environ.get("DP_OUTPUT_DIR", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        return path
    return root / ".dataprocess_cache" / "exports" / "pipeline_examples"


def main() -> None:
    root = _repo_root()
    examples = root / "examples"
    out_dir = _output_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    echem = pd.read_csv(examples / "sample_echem_photocurrent.csv")
    stack = tifffile.imread(str(examples / "sample_fluorescence_stack.tif"))
    recording = pyabf.ABF(str(examples / "sample_patch_clamp.abf"))
    recording.setSweep(0)

    rows = [
        {
            "dataset": "sample_echem_photocurrent.csv",
            "kind": "csv_trace",
            "n_points": int(len(echem)),
            "min": float(echem["current_mA"].min()),
            "max": float(echem["current_mA"].max()),
            "mean": float(echem["current_mA"].mean()),
        },
        {
            "dataset": "sample_fluorescence_stack.tif",
            "kind": "tiff_stack",
            "n_points": int(stack.size),
            "min": float(stack.min()),
            "max": float(stack.max()),
            "mean": float(stack.mean()),
        },
        {
            "dataset": "sample_patch_clamp.abf",
            "kind": "abf_sweep",
            "n_points": int(recording.sweepPointCount),
            "min": float(recording.sweepY.min()),
            "max": float(recording.sweepY.max()),
            "mean": float(recording.sweepY.mean()),
        },
    ]
    summary = pd.DataFrame(rows)
    csv_path = out_dir / "example_pipeline_summary.csv"
    json_path = out_dir / "example_pipeline_summary.json"
    png_path = out_dir / "example_pipeline_summary.png"
    summary.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    fig, axes = plt.subplots(1, 3, figsize=(10, 3.2))
    axes[0].plot(echem["time_s"], echem["current_mA"], color="#3E6AE1", linewidth=1.2)
    axes[0].set_title("EChem")
    axes[0].set_xlabel("s")
    axes[0].set_ylabel("mA")
    axes[1].imshow(stack.max(axis=0), cmap="viridis")
    axes[1].set_title("TIFF max")
    axes[1].axis("off")
    axes[2].plot(recording.sweepX, recording.sweepY, color="#027A48", linewidth=1.0)
    axes[2].set_title("ABF sweep")
    axes[2].set_xlabel("s")
    axes[2].set_ylabel(recording.sweepLabelY)
    fig.tight_layout()
    fig.savefig(png_path, dpi=140)
    plt.close(fig)

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    main()
