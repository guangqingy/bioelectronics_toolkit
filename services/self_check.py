from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import numpy as np
import pyabf
import tifffile

from services import echem
from services.provenance import runtime_provenance

CHECK_IMPORTS = (
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "tifffile",
    "PIL",
    "imagecodecs",
    "pyabf",
    "readlif",
    "statsmodels",
)


def _check_imports() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for module_name in CHECK_IMPORTS:
        try:
            importlib.import_module(module_name)
            checks.append({"name": f"import:{module_name}", "ok": True, "message": "available"})
        except Exception as exc:
            checks.append({"name": f"import:{module_name}", "ok": False, "message": str(exc)})
    return checks


def _check_csv(path: Path) -> dict[str, Any]:
    t, signal, t_col, v_col = echem.load_photocurrent(path)
    ok = len(t) == 600 and t_col == "time_s" and v_col == "current_mA"
    ok = ok and np.isclose(float(np.max(signal)), 0.0463818255506895, rtol=1e-7)
    return {
        "name": "example:sample_echem_photocurrent.csv",
        "ok": bool(ok),
        "message": f"{len(t)} rows, columns={t_col}/{v_col}",
    }


def _check_tiff(path: Path) -> dict[str, Any]:
    arr = tifffile.imread(str(path))
    ok = arr.shape == (10, 64, 64) and str(arr.dtype) == "uint16"
    ok = ok and int(arr.max()) == 58840 and int(arr.sum()) == 253060708
    return {
        "name": "example:sample_fluorescence_stack.tif",
        "ok": bool(ok),
        "message": f"shape={arr.shape}, dtype={arr.dtype}",
    }


def _check_abf(path: Path) -> dict[str, Any]:
    recording = pyabf.ABF(str(path))
    recording.setSweep(0)
    ok = recording.sweepCount == 2 and recording.channelCount == 1
    ok = ok and recording.sweepPointCount == 5000 and recording.dataRate == 5000
    ok = ok and np.isclose(float(np.max(recording.sweepY)), 30.9600830078125, rtol=1e-7)
    return {
        "name": "example:sample_patch_clamp.abf",
        "ok": bool(ok),
        "message": (
            f"{recording.sweepCount} sweeps, {recording.channelCount} channel(s), "
            f"{recording.sweepPointCount} points"
        ),
    }


def run_self_check(base_dir: str | Path) -> dict[str, Any]:
    root = Path(base_dir).expanduser().resolve()
    checks = _check_imports()
    examples = root / "examples"
    example_checks = (
        (examples / "sample_echem_photocurrent.csv", _check_csv),
        (examples / "sample_fluorescence_stack.tif", _check_tiff),
        (examples / "sample_patch_clamp.abf", _check_abf),
    )
    for path, check_func in example_checks:
        if not path.is_file():
            checks.append({"name": f"example:{path.name}", "ok": False, "message": "missing"})
            continue
        try:
            checks.append(check_func(path))
        except Exception as exc:
            checks.append({"name": f"example:{path.name}", "ok": False, "message": str(exc)})

    return {
        "ok": all(bool(item.get("ok")) for item in checks),
        "root": str(root),
        "provenance": runtime_provenance(root),
        "checks": checks,
    }


def format_self_check_report(report: dict[str, Any]) -> str:
    status = "PASS" if report.get("ok") else "FAIL"
    lines = [f"DataProcess self-check: {status}", f"Root: {report.get('root', '')}"]
    provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
    if provenance:
        lines.append(f"Version: {provenance.get('label') or provenance.get('version')}")
    lines.append("")
    for item in report.get("checks", []):
        mark = "ok" if item.get("ok") else "FAIL"
        lines.append(f"[{mark}] {item.get('name', '')}: {item.get('message', '')}")
    return "\n".join(lines)
