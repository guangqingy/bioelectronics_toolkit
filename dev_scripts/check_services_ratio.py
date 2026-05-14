#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEB_API = ROOT / "web_api"
SERVICES = ROOT / "services"
BASELINE_PATH = ROOT / "dev_scripts" / "services_ratio_baseline.json"
EPSILON = 1e-9

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
    route_to_service_ratio: float | None
    service_to_route_ratio: float
    status: str
    loc_budget_exception: bool = False


@dataclass(frozen=True)
class RatchetFinding:
    web_module: str
    kind: str
    current_ratio: float
    baseline_ratio: float | None
    message: str


def _source_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    return len(text.splitlines())


def _has_loc_budget_exception(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    header = "\n".join(text.splitlines()[:12])
    return "LOC budget exception:" in header


def _service_candidates(module_name: str) -> list[Path]:
    original_stem = module_name
    stem = module_name
    alias_names = list(SERVICE_ALIASES.get(module_name, []))
    for suffix in ("_routes", "_viewer"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    names = [original_stem, stem, *alias_names, *SERVICE_ALIASES.get(stem, [])]
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


def collect_ratios(
    max_ratio: float = 2.0,
    min_web_lines: int = 200,
    *,
    check_loc_budget: bool = False,
    max_web_lines: int = 200,
) -> list[RatioRecord]:
    records: list[RatioRecord] = []
    for path in sorted(WEB_API.glob("*.py")):
        stem = path.stem
        if stem in IGNORED_WEB_MODULES:
            continue
        web_lines = _source_lines(path)
        loc_budget_exception = _has_loc_budget_exception(path)
        service_target, service_lines = _service_line_count(stem)
        route_to_service = (web_lines / service_lines) if service_lines else None
        service_to_route = (service_lines / web_lines) if web_lines else 0.0

        status = "ok"
        if service_lines == 0 and web_lines >= min_web_lines:
            status = "missing_service"
        elif (
            route_to_service is not None
            and route_to_service > max_ratio
            and web_lines >= min_web_lines
        ):
            status = "route_too_thick"
        elif check_loc_budget and web_lines > max_web_lines and not loc_budget_exception:
            status = "route_over_loc_budget"

        records.append(
            RatioRecord(
                web_module=path.relative_to(ROOT).as_posix(),
                web_lines=web_lines,
                service_target=service_target,
                service_lines=service_lines,
                route_to_service_ratio=route_to_service,
                service_to_route_ratio=service_to_route,
                status=status,
                loc_budget_exception=loc_budget_exception,
            )
        )
    return records


def _baseline_payload(records: list[RatioRecord]) -> dict[str, Any]:
    return {
        "version": 1,
        "metric": "service_to_route_loc_ratio",
        "description": (
            "Higher is better. CI treats lower ratios than this baseline as "
            "route-thickening regressions."
        ),
        "modules": {
            record.web_module: {
                "web_lines": record.web_lines,
                "service_target": record.service_target,
                "service_lines": record.service_lines,
                "service_to_route_ratio": record.service_to_route_ratio,
                "route_to_service_ratio": (
                    None if record.route_to_service_ratio is None else record.route_to_service_ratio
                ),
            }
            for record in sorted(records, key=lambda item: item.web_module)
        },
    }


def _write_baseline(path: Path, records: list[RatioRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _baseline_payload(records)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_baseline(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_modules(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modules = payload.get("modules")
    return modules if isinstance(modules, dict) else {}


def compare_to_baseline(
    records: list[RatioRecord],
    baseline: dict[str, Any],
    *,
    min_new_service_route_ratio: float = 1.0,
) -> tuple[list[RatchetFinding], list[RatchetFinding]]:
    baseline_modules = _baseline_modules(baseline)
    failures: list[RatchetFinding] = []
    improvements: list[RatchetFinding] = []

    for record in records:
        current = record.service_to_route_ratio
        previous = baseline_modules.get(record.web_module)
        if previous is None:
            if current + EPSILON < min_new_service_route_ratio:
                failures.append(
                    RatchetFinding(
                        web_module=record.web_module,
                        kind="new_module_without_service_weight",
                        current_ratio=current,
                        baseline_ratio=None,
                        message=(
                            "New web_api module must start with service >= route "
                            f"(current service:route ratio {current:.3f})."
                        ),
                    )
                )
            else:
                improvements.append(
                    RatchetFinding(
                        web_module=record.web_module,
                        kind="new_module_ok",
                        current_ratio=current,
                        baseline_ratio=None,
                        message="New module meets service >= route baseline.",
                    )
                )
            continue

        try:
            old_ratio = float(previous.get("service_to_route_ratio", 0.0))
        except (TypeError, ValueError):
            old_ratio = 0.0

        if current + EPSILON < old_ratio:
            failures.append(
                RatchetFinding(
                    web_module=record.web_module,
                    kind="ratio_regression",
                    current_ratio=current,
                    baseline_ratio=old_ratio,
                    message=(
                        "Route layer grew relative to services. Move reusable logic "
                        "into services before adding this feature, or run "
                        "--update-baseline with PR justification."
                    ),
                )
            )
        elif current > old_ratio + EPSILON:
            improvements.append(
                RatchetFinding(
                    web_module=record.web_module,
                    kind="ratio_improved",
                    current_ratio=current,
                    baseline_ratio=old_ratio,
                    message="Service layer share improved.",
                )
            )

    return failures, improvements


def _print_records(records: list[RatioRecord], *, only_offenders: bool = True) -> None:
    selected = (
        [record for record in records if record.status != "ok"] if only_offenders else records
    )
    for record in selected:
        route_ratio = (
            "n/a"
            if record.route_to_service_ratio is None
            else f"{record.route_to_service_ratio:.2f}"
        )
        service_ratio = f"{record.service_to_route_ratio:.2f}"
        service = record.service_target or "(no matching service)"
        exception = " [LOC exception]" if record.loc_budget_exception else ""
        print(
            f"- {record.status}: {record.web_module} "
            f"({record.web_lines} route LOC) vs {service} ({record.service_lines} service LOC), "
            f"service:route={service_ratio}, route:service={route_ratio}{exception}"
        )


def _print_findings(title: str, findings: list[RatchetFinding]) -> None:
    if not findings:
        return
    print(title)
    for finding in findings:
        old = "new" if finding.baseline_ratio is None else f"{finding.baseline_ratio:.3f}"
        print(
            f"- {finding.kind}: {finding.web_module} "
            f"service:route {old} -> {finding.current_ratio:.3f}. {finding.message}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check web_api/services line-count ratchet.")
    parser.add_argument("--max-ratio", type=float, default=2.0)
    parser.add_argument("--min-web-lines", type=int, default=200)
    parser.add_argument(
        "--check-loc-budget",
        action="store_true",
        help="Also report route modules over the LOC budget.",
    )
    parser.add_argument("--max-web-lines", type=int, default=200)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write the current service:route ratios as the initial baseline and exit.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Explicitly accept the current ratios as the new baseline.",
    )
    parser.add_argument(
        "--min-new-service-route-ratio",
        type=float,
        default=1.0,
        help="Minimum service:route ratio required for modules not present in the baseline.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--warn-only", action="store_true", help="Always exit 0.")
    args = parser.parse_args(argv)

    records = collect_ratios(
        max_ratio=args.max_ratio,
        min_web_lines=args.min_web_lines,
        check_loc_budget=args.check_loc_budget,
        max_web_lines=args.max_web_lines,
    )

    if args.write_baseline:
        _write_baseline(args.baseline, records)
        print(f"Wrote services ratio baseline: {args.baseline.relative_to(ROOT)}")
        return 0

    if args.update_baseline:
        if args.baseline.exists():
            baseline = _load_baseline(args.baseline)
            failures, improvements = compare_to_baseline(
                records,
                baseline,
                min_new_service_route_ratio=args.min_new_service_route_ratio,
            )
            _print_findings("Accepted baseline regressions:", failures)
            _print_findings("Recorded baseline improvements:", improvements)
        _write_baseline(args.baseline, records)
        print(f"Updated services ratio baseline: {args.baseline.relative_to(ROOT)}")
        return 0

    if not args.baseline.exists():
        print(
            f"Missing services ratio baseline: {args.baseline.relative_to(ROOT)}. "
            "Run dev_scripts/check_services_ratio.py --write-baseline once."
        )
        return 0 if args.warn_only else 1

    baseline = _load_baseline(args.baseline)
    failures, improvements = compare_to_baseline(
        records,
        baseline,
        min_new_service_route_ratio=args.min_new_service_route_ratio,
    )
    loc_failures = [record for record in records if record.status == "route_over_loc_budget"]
    failed = bool(failures or loc_failures)

    if args.json:
        print(
            json.dumps(
                {
                    "records": [asdict(record) for record in records],
                    "failures": [asdict(finding) for finding in failures],
                    "improvements": [asdict(finding) for finding in improvements],
                    "loc_budget_failures": [asdict(record) for record in loc_failures],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("web_api/services baseline ratchet report")
        _print_findings("Failures:", failures)
        if loc_failures:
            print("LOC budget failures:")
            _print_records(loc_failures, only_offenders=False)
        if not failed:
            print("- ok: no service:route regressions")
        _print_findings("Improvements:", improvements)

    return 0 if args.warn_only or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
