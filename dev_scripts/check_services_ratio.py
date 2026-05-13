#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_API = ROOT / "web_api"
SERVICES = ROOT / "services"

IGNORED_WEB_MODULES = {
    "__init__",
    "context",
    "pages",
    "path_policy",
    "plot_export",
    "response",
}

SERVICE_ALIASES = {
    "abf_batch": ["abf"],
    "abf_viewer": ["abf"],
    "csv_viewer": ["csv_tools"],
    "echem_lineshape": ["echem"],
    "echem_pc": ["echem"],
    "echem_pv": ["echem"],
    "emg_peaks": ["emg", "rhd"],
    "fluorescence_3d": ["fluorescence"],
    "fluorescence_gif": ["fluorescence"],
    "fluorescence_roi": ["fluorescence"],
    "fluorescence_stack": ["fluorescence"],
    "lif_viewer": ["fluorescence"],
    "rhd_viewer": ["rhd"],
}


@dataclass(frozen=True)
class RatioRecord:
    web_module: str
    web_lines: int
    service_target: str
    service_lines: int
    ratio: float | None
    status: str


def _source_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    return len(text.splitlines())


def _service_candidates(module_name: str) -> list[Path]:
    stem = module_name
    alias_names = list(SERVICE_ALIASES.get(module_name, []))
    for suffix in ("_routes", "_viewer"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    names = [stem, *alias_names, *SERVICE_ALIASES.get(stem, [])]
    if stem.startswith("fluorescence"):
        names.append("fluorescence")
    if stem.startswith("echem"):
        names.append("echem")

    candidates: list[Path] = []
    for name in dict.fromkeys(names):
        candidates.extend([SERVICES / f"{name}.py", SERVICES / name])
    return candidates


def _service_line_count(module_name: str) -> tuple[str, int]:
    for candidate in _service_candidates(module_name):
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix(), _source_lines(candidate)
        if candidate.is_dir():
            files = sorted(p for p in candidate.rglob("*.py") if p.name != "__init__.py")
            if files:
                rel = candidate.relative_to(ROOT).as_posix()
                return rel, sum(_source_lines(path) for path in files)
    return "", 0


def collect_ratios(max_ratio: float = 2.0, min_web_lines: int = 200) -> list[RatioRecord]:
    records: list[RatioRecord] = []
    for path in sorted(WEB_API.glob("*.py")):
        stem = path.stem
        if stem in IGNORED_WEB_MODULES:
            continue
        web_lines = _source_lines(path)
        service_target, service_lines = _service_line_count(stem)
        ratio = (web_lines / service_lines) if service_lines else None
        status = "ok"
        if service_lines == 0 and web_lines >= min_web_lines:
            status = "missing_service"
        elif ratio is not None and ratio > max_ratio and web_lines >= min_web_lines:
            status = "route_too_thick"
        records.append(
            RatioRecord(
                web_module=path.relative_to(ROOT).as_posix(),
                web_lines=web_lines,
                service_target=service_target,
                service_lines=service_lines,
                ratio=ratio,
                status=status,
            )
        )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report web_api/services line-count balance.")
    parser.add_argument("--max-ratio", type=float, default=2.0)
    parser.add_argument("--min-web-lines", type=int, default=200)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--warn-only", action="store_true", help="Always exit 0.")
    args = parser.parse_args(argv)

    records = collect_ratios(max_ratio=args.max_ratio, min_web_lines=args.min_web_lines)
    offenders = [record for record in records if record.status != "ok"]

    if args.json:
        print(json.dumps([asdict(record) for record in records], indent=2, sort_keys=True))
    else:
        print("web_api/services ratio report")
        for record in offenders:
            ratio_text = "n/a" if record.ratio is None else f"{record.ratio:.2f}"
            service = record.service_target or "(no matching service)"
            print(
                f"- {record.status}: {record.web_module} "
                f"({record.web_lines} LOC) vs {service} ({record.service_lines} LOC), "
                f"ratio={ratio_text}"
            )
        if not offenders:
            print("- ok: no route modules exceeded the configured threshold")

    return 0 if args.warn_only or not offenders else 1


if __name__ == "__main__":
    raise SystemExit(main())
