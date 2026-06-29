from __future__ import annotations

import re
import unittest
from pathlib import Path

from web_app import app

ROOT = Path(__file__).resolve().parents[1]
WEB_API = ROOT / "web_api"
WEB_STATIC = ROOT / "web_static"
WEB_TEMPLATES = ROOT / "web_templates"

API_LITERAL_RE = re.compile(r"['\"](/api/[A-Za-z0-9_./-]+)['\"]")
STATIC_ASSET_RE = re.compile(r"static_asset\(['\"]([^'\"]+)['\"]\)")
STATIC_URL_RE = re.compile(r"['\"](/static/[^'\"]+)['\"]")
PAGE_URL_RE = re.compile(r"(?:href=|url:|href:)\s*['\"]([^'\"]+)['\"]")


def _text_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".js", ".css"}
    )


def _source_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    ignored_names = {"CHANGELOG.md", "PM_assessment_2026-06.md"}
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in ignored_names
        and path.suffix in {".py", ".js", ".html", ".css", ".md", ".toml"}
        and "__pycache__" not in path.parts
    )


def _literal_api_refs() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in [*_text_files(WEB_STATIC), *_text_files(WEB_TEMPLATES)]:
        text = path.read_text(encoding="utf-8")
        for match in API_LITERAL_RE.finditer(text):
            refs.setdefault(match.group(1), set()).add(str(path.relative_to(ROOT)))
    return refs


def _literal_page_refs() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in [*_text_files(WEB_STATIC), *_text_files(WEB_TEMPLATES)]:
        text = path.read_text(encoding="utf-8")
        for match in PAGE_URL_RE.finditer(text):
            url = match.group(1)
            if not url.startswith("/") or url.startswith(("/api/", "/static/")):
                continue
            page = url.split("?", 1)[0].split("#", 1)[0]
            refs.setdefault(page, set()).add(str(path.relative_to(ROOT)))
    return refs


def _literal_static_refs() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in [*_text_files(WEB_STATIC), *_text_files(WEB_TEMPLATES)]:
        text = path.read_text(encoding="utf-8")
        for match in STATIC_ASSET_RE.finditer(text):
            refs.setdefault(match.group(1), set()).add(str(path.relative_to(ROOT)))
        for match in STATIC_URL_RE.finditer(text):
            refs.setdefault(match.group(1).removeprefix("/static/"), set()).add(
                str(path.relative_to(ROOT))
            )
    return refs


class ProjectInterfaceContractTests(unittest.TestCase):
    def test_static_api_references_resolve_to_flask_routes(self) -> None:
        routes = {rule.rule for rule in app.url_map.iter_rules()}
        missing = {
            ref: sorted(locations)
            for ref, locations in _literal_api_refs().items()
            if ref not in routes
        }

        self.assertEqual(missing, {})

    def test_openapi_paths_cover_registered_api_routes(self) -> None:
        with app.test_client() as client:
            spec = client.get("/api/openapi.json").get_json()

        documented = set(spec["paths"])
        registered = {
            rule.rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/") and "<" not in rule.rule
        }

        self.assertEqual(sorted(registered - documented), [])
        self.assertEqual(sorted(documented - registered), [])

    def test_static_page_links_resolve_to_flask_routes(self) -> None:
        routes = {rule.rule for rule in app.url_map.iter_rules() if "<" not in rule.rule}
        missing = {
            ref: sorted(locations)
            for ref, locations in _literal_page_refs().items()
            if ref not in routes
        }

        self.assertEqual(missing, {})

    def test_static_asset_references_exist(self) -> None:
        missing = {
            ref: sorted(locations)
            for ref, locations in _literal_static_refs().items()
            if not (WEB_STATIC / ref).exists()
        }

        self.assertEqual(missing, {})

    def test_flask_endpoint_names_are_canonical(self) -> None:
        offenders = sorted(
            rule.endpoint
            for rule in app.url_map.iter_rules()
            if "_compat" in rule.endpoint or rule.endpoint.endswith("_alias")
        )

        self.assertEqual(offenders, [])

    def test_removed_facade_module_names_do_not_return(self) -> None:
        removed = [
            "echem_pc.py",
            "echem_pv.py",
            "emg_peaks.py",
            "rhd_request_schemas.py",
            "rhd_viewer.py",
            "lif_viewer.py",
            "rhd_processing.py",
        ]
        present = [
            str(path.relative_to(ROOT))
            for root in [WEB_API, ROOT / "services", ROOT / "desktop_apps" / "launchers"]
            for name in removed
            for path in root.glob(name)
        ]
        route_shims = sorted(str(path.relative_to(ROOT)) for path in WEB_API.glob("*_routes.py"))

        self.assertEqual(sorted(present), [])
        self.assertEqual(route_shims, [])

    def test_removed_public_interface_strings_do_not_return_to_app_source(self) -> None:
        removed_patterns = [
            "/api/rhd",
            "/api/echem_pv",
            "/api/emg/load",
            "/api/emg/detect",
            "/emg/rhd",
            "/emg/peaks",
            "bte-rhd-viewer",
            "bte-emg-viewer",
            "bte-emg-peaks",
            "bte-echem-pc",
            "bte-echem-pv",
            "rhd_viewer",
            "emg_peaks",
            "echem_pc",
            "echem_pv",
        ]
        source_roots = [
            ROOT / "desktop_apps",
            ROOT / "services",
            ROOT / "web_api",
            WEB_STATIC,
            WEB_TEMPLATES,
            ROOT / "pyproject.toml",
            ROOT / "README.md",
            ROOT / "docs",
        ]
        offenders: dict[str, list[str]] = {}
        for root in source_roots:
            for path in _source_files(root):
                text = path.read_text(encoding="utf-8")
                hits = [pattern for pattern in removed_patterns if pattern in text]
                if hits:
                    offenders[str(path.relative_to(ROOT))] = hits

        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
