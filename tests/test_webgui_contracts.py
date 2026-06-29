from __future__ import annotations

import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from web_api import system as system_api
from web_api.jobs import JobManager
from web_api.response import api_error, attachment_content_disposition, make_envelope


def _api_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class ApiEnvelopeTests(unittest.TestCase):
    def test_legacy_saved_path_is_kept_in_data_and_inferred_as_output(self) -> None:
        envelope = make_envelope({"ok": True, "saved_path": "/tmp/result.csv", "rows": 3})

        self.assertTrue(envelope["ok"])
        self.assertNotIn("saved_path", envelope)
        self.assertEqual(envelope["data"]["saved_path"], "/tmp/result.csv")
        self.assertEqual(envelope["outputs"], [{"path": "/tmp/result.csv", "type": "csv"}])

    def test_batch_output_records_are_inferred_without_losing_legacy_shape(self) -> None:
        payload = {
            "ok": True,
            "outputs": [
                {
                    "input": "/tmp/source.tif",
                    "combined_tiff": "/tmp/source_selected_stacks.tif",
                    "stack_files": ["/tmp/source_stack1_blue.tif", "/tmp/source_stack2_red.tif"],
                    "json": "/tmp/source_display_settings.json",
                }
            ],
        }

        envelope = make_envelope(payload)
        output_paths = {item["path"] for item in envelope["outputs"]}

        self.assertIn("/tmp/source_selected_stacks.tif", output_paths)
        self.assertIn("/tmp/source_stack1_blue.tif", output_paths)
        self.assertIn("/tmp/source_stack2_red.tif", output_paths)
        self.assertIn("/tmp/source_display_settings.json", output_paths)
        self.assertEqual(envelope["data"]["outputs"][0]["input"], "/tmp/source.tif")

    def test_source_metadata_path_is_not_inferred_as_generated_output(self) -> None:
        envelope = make_envelope(
            {
                "ok": True,
                "output_path": "/tmp/movie.gif",
                "metadata_path": "/tmp/source_display_settings.json",
            }
        )

        self.assertEqual(envelope["outputs"], [{"path": "/tmp/movie.gif", "type": "gif"}])

    def test_explicit_output_records_take_precedence_over_legacy_paths(self) -> None:
        envelope = make_envelope(
            {
                "ok": True,
                "saved_path": "/tmp/result.csv",
                "outputs": [{"path": "/tmp/result.csv", "type": "csv", "role": "full_csv"}],
            }
        )

        self.assertEqual(
            envelope["outputs"],
            [{"path": "/tmp/result.csv", "type": "csv", "role": "full_csv"}],
        )

    def test_traceback_errors_are_redacted_outside_debug(self) -> None:
        app = Flask(__name__)
        app.config["DEBUG"] = False

        with app.app_context():
            response, code = api_error("Traceback (most recent call last):\n  File x.py", 400)

        payload = response.get_json()
        self.assertEqual(code, 500)
        self.assertIn("operation failed", payload["error"])
        self.assertNotIn("Traceback (most recent call last)", payload["error"])
        self.assertRegex(payload["data"]["id"], r"^[0-9a-f]{8}$")
        self.assertNotIn("technical_details", payload["data"])

    def test_traceback_errors_include_details_in_debug(self) -> None:
        app = Flask(__name__)
        app.config["DEBUG"] = True

        with app.app_context():
            response, code = api_error("Traceback (most recent call last):\n  File x.py", 400)

        payload = response.get_json()
        self.assertEqual(code, 500)
        self.assertIn("Traceback (most recent call last)", payload["error"])
        self.assertEqual(
            payload["data"]["technical_details"], "Traceback (most recent call last):\n  File x.py"
        )

    def test_attachment_content_disposition_quotes_and_encodes_filename(self) -> None:
        header = attachment_content_disposition('resume report "alpha"\r\n.csv')

        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)
        self.assertIn('filename="resume report _alpha_  .csv"', header)
        self.assertIn("filename*=UTF-8''resume%20report%20%22alpha%22%20%20.csv", header)


class JobManagerContractTests(unittest.TestCase):
    def test_job_record_gets_inferred_outputs(self) -> None:
        manager = JobManager()

        def target(_ctx):
            return {
                "ok": True,
                "outputs": [
                    {
                        "input": "/tmp/source.tif",
                        "combined_tiff": "/tmp/source_selected_stacks.tif",
                        "stack_files": ["/tmp/source_stack1_blue.tif"],
                    }
                ],
            }

        submitted = manager.submit("test", "Batch export", target)
        job = self._wait_for_job(manager, submitted["job_id"])

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["data"]["outputs"][0]["input"], "/tmp/source.tif")
        self.assertEqual(
            {item["path"] for item in job["outputs"]},
            {"/tmp/source_selected_stacks.tif", "/tmp/source_stack1_blue.tif"},
        )

    def test_job_records_persist_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dp_jobs_") as tmp:
            db_path = Path(tmp) / "jobs.sqlite"
            manager = JobManager(persistence_path=db_path)

            submitted = manager.submit(
                "test",
                "Persisted job",
                lambda _ctx: {"ok": True, "message": "done"},
            )
            job = self._wait_for_job(manager, submitted["job_id"])
            self.assertEqual(job["status"], "succeeded")

            restored = JobManager(persistence_path=db_path)
            restored_job = restored.get(submitted["job_id"])
            self.assertIsNotNone(restored_job)
            self.assertEqual(restored_job["status"], "succeeded")

    @staticmethod
    def _wait_for_job(manager: JobManager, job_id: str) -> dict:
        for _ in range(50):
            job = manager.get(job_id) or {}
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for job {job_id}")


class WebAppSmokeTests(unittest.TestCase):
    PAGE_ROUTES = (
        "/",
        "/csv",
        "/abf/viewer",
        "/abf/peaks",
        "/abf/batch",
        "/abf/figure",
        "/emg/analysis",
        "/emg/peak-selection",
        "/echem/photocurrent",
        "/echem/photovoltage",
        "/echem/lineshape",
        "/fluorescence",
        "/fluorescence/3d-stacking",
        "/fluorescence/roi",
        "/fluorescence/gif",
        "/fluorescence/timecourse",
        "/fluorescence/kymograph",
        "/fluorescence/lif",
        "/histology/naming",
        "/histology/analysis",
        "/runs",
    )

    @classmethod
    def setUpClass(cls) -> None:
        from web_app import app

        cls.client = app.test_client()

    @staticmethod
    def _page_script_refs(template_text: str) -> list[str]:
        return re.findall(r"static_asset\(['\"]js/pages/([^'\"]+)['\"]\)", template_text)

    @staticmethod
    def _strip_js_comments_and_strings(source: str) -> str:
        out: list[str] = []
        i = 0
        state = "code"
        quote = ""
        while i < len(source):
            ch = source[i]
            nxt = source[i + 1] if i + 1 < len(source) else ""
            if state == "code":
                if ch == "/" and nxt == "/":
                    state = "line"
                    out.append(" ")
                    i += 2
                    continue
                if ch == "/" and nxt == "*":
                    state = "block"
                    out.append(" ")
                    i += 2
                    continue
                if ch in {"'", '"', "`"}:
                    state = "string"
                    quote = ch
                    out.append(" ")
                    i += 1
                    continue
                out.append(ch)
                i += 1
                continue
            if state == "line":
                out.append("\n" if ch == "\n" else " ")
                if ch == "\n":
                    state = "code"
                i += 1
                continue
            if state == "block":
                if ch == "*" and nxt == "/":
                    state = "code"
                    out.append(" ")
                    i += 2
                    continue
                out.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            if ch == "\\":
                out.append(" ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            if ch == quote:
                state = "code"
            i += 1
        return "".join(out)

    @classmethod
    def _top_level_js_lexicals(cls, source: str) -> list[str]:
        code = cls._strip_js_comments_and_strings(source)
        token_re = re.compile(r"[{}]|\b(?:const|let|class)\s+([A-Za-z_$][\w$]*)")
        depth = 0
        names: list[str] = []
        for match in token_re.finditer(code):
            token = match.group(0)
            if token == "{":
                depth += 1
            elif token == "}":
                depth = max(0, depth - 1)
            elif depth == 0 and match.group(1):
                names.append(match.group(1))
        return names

    @classmethod
    def _top_level_js_functions(cls, source: str) -> list[str]:
        code = cls._strip_js_comments_and_strings(source)
        token_re = re.compile(r"[{}]|\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")
        depth = 0
        names: list[str] = []
        for match in token_re.finditer(code):
            token = match.group(0)
            if token == "{":
                depth += 1
            elif token == "}":
                depth = max(0, depth - 1)
            elif depth == 0 and match.group(1):
                names.append(match.group(1))
        return names

    def test_all_web_pages_render(self) -> None:
        for route in self.PAGE_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)

    def test_histology_root_is_not_a_module_page(self) -> None:
        response = self.client.get("/histology")
        self.assertEqual(response.status_code, 404)

    def test_templates_do_not_expose_developer_absolute_paths(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for folder in (root / "web_templates", root / "web_static"):
            for source in folder.rglob("*"):
                if source.suffix not in {".html", ".js", ".css"}:
                    continue
                text = source.read_text(encoding="utf-8")
                if "/" + "Users/" + "guangqing" in text or "Desktop" + "/" + "UChicago" in text:
                    offenders.append(str(source.relative_to(root)))
        self.assertEqual([], offenders)

    def test_rendered_pages_do_not_expose_developer_absolute_paths(self) -> None:
        needles = ("/" + "Users/" + "guangqing", "Desktop" + "/" + "UChicago")
        for route in ("/", "/runs", "/abf/viewer", "/fluorescence/roi?demo=fluorescence"):
            with self.subTest(route=route):
                response = self.client.get(route)
                html = response.data.decode("utf-8")
                for needle in needles:
                    self.assertNotIn(needle, html)

    def test_main_stylesheet_is_bundled_without_runtime_imports(self) -> None:
        root = Path(__file__).resolve().parents[1]
        style = (root / "web_static" / "style.css").read_text(encoding="utf-8")
        reset = (root / "web_static" / "style" / "_reset.css").read_text(encoding="utf-8")
        forms = (root / "web_static" / "style" / "_forms.css").read_text(encoding="utf-8")
        status = (root / "web_static" / "style" / "_status.css").read_text(
            encoding="utf-8"
        )
        modals = (root / "web_static" / "style" / "_modals_base.css").read_text(
            encoding="utf-8"
        )
        dom_js = (root / "web_static" / "js" / "dp_dom.js").read_text(encoding="utf-8")

        self.assertNotIn("@import", style)
        self.assertIn("DataProcess Web", style)
        self.assertIn("color-scheme: light dark", style)
        self.assertIn('"PingFang SC"', reset)
        self.assertIn('appearance: none', forms)
        self.assertIn('input[type="search"]', forms)
        self.assertIn(".checkbox-row input[type=\"checkbox\"]:checked", style)
        self.assertIn(".dp-check:checked", style)
        self.assertIn("overflow-wrap: anywhere", status)
        self.assertIn("display: flex", status)
        self.assertIn("flex: 0 0 34px", status)
        self.assertIn(".status-message", status)
        self.assertIn(".status-bar.status-ok", status)
        self.assertIn("dpCompactStatusMessage", dom_js)
        self.assertIn(".modal-actions", modals)
        self.assertIn("width: auto", modals)
        self.assertIn('class="modal-actions"', dom_js)
        self.assertNotIn('prefs-actions" style="margin-top:14px"', dom_js)

    def test_static_styles_avoid_hardcoded_light_backgrounds(self) -> None:
        root = Path(__file__).resolve().parents[1]
        light_bg_re = re.compile(
            r"background(?:-color)?\s*:\s*"
            r"(?:#fff|#fb|#f8|#fee|linear-gradient\([^;]*(?:#fff|#fb|#f1|#e6))",
            re.I,
        )
        offenders = []
        for folder in (root / "web_templates", root / "web_static"):
            for source in folder.rglob("*"):
                if source.suffix not in {".html", ".js", ".css"}:
                    continue
                if "vendor" in source.parts:
                    continue
                for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                    if light_bg_re.search(line):
                        offenders.append(f"{source.relative_to(root)}:{lineno}: {line.strip()}")

        self.assertEqual([], offenders)

    def test_checkbox_controls_use_consistent_layout_styles(self) -> None:
        root = Path(__file__).resolve().parents[1]
        label_re = re.compile(r"<label\b(?P<attrs>[^>]*)>(?P<body>.*?)</label>", re.S | re.I)
        input_re = re.compile(r"<input\b[^>]*\btype=[\"']checkbox[\"'][^>]*>", re.I)
        allowed_label_classes = (
            "checkbox-row",
            "prefs-field-check",
            "histology-channel-chip",
        )
        offenders = []

        for template in (root / "web_templates").rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            covered_spans: list[tuple[int, int]] = []
            for label_match in label_re.finditer(text):
                body = label_match.group("body")
                if not input_re.search(body):
                    continue
                line = text[: label_match.start()].count("\n") + 1
                attrs = label_match.group("attrs")
                if not any(name in attrs for name in allowed_label_classes):
                    offenders.append(
                        f"{template.relative_to(root)}:{line}: checkbox label lacks a shared class"
                    )
                if "checkbox-row" in attrs and "<span" not in body.lower():
                    offenders.append(
                        f"{template.relative_to(root)}:{line}: checkbox-row label text must be wrapped in span"
                    )
                covered_spans.append(label_match.span())
            for input_match in input_re.finditer(text):
                if not any(start <= input_match.start() < end for start, end in covered_spans):
                    line = text[: input_match.start()].count("\n") + 1
                    offenders.append(
                        f"{template.relative_to(root)}:{line}: checkbox input is not wrapped by a styled label"
                    )

        for script in (root / "web_static" / "js").rglob("*.js"):
            text = script.read_text(encoding="utf-8")
            for input_match in input_re.finditer(text):
                input_html = input_match.group(0)
                context = text[max(0, input_match.start() - 180) : input_match.end() + 180]
                if (
                    "dp-check" not in input_html
                    and "checkbox-row" not in context
                    and "prefs-field-check" not in context
                    and "histology-channel-chip" not in context
                ):
                    line = text[: input_match.start()].count("\n") + 1
                    offenders.append(
                        f"{script.relative_to(root)}:{line}: dynamic checkbox lacks dp-check"
                    )

        self.assertEqual([], offenders)

    def test_static_page_scripts_are_deferred(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            for line in template.read_text(encoding="utf-8").splitlines():
                if '<script ' in line and 'src="/static/' in line and "defer" not in line:
                    offenders.append(f"{template.name}: {line.strip()}")

        self.assertEqual([], offenders)

    def test_page_level_scripts_are_cache_busted(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            if 'src="/static/js/pages/' in text or "src='/static/js/pages/" in text:
                offenders.append(str(template.relative_to(root)))

        self.assertEqual([], offenders)

    def test_rendered_static_assets_are_cache_busted_and_served(self) -> None:
        asset_re = re.compile(r"<(?:script|link)\b[^>]+(?:src|href)=['\"]([^'\"]+)['\"]", re.I)
        for route in self.PAGE_ROUTES:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                html = response.data.decode("utf-8")
                for asset in asset_re.findall(html):
                    if asset.startswith(("/static/js/pages/", "/static/css/")):
                        self.assertIn("?v=", asset)
                    if asset.startswith("/static/"):
                        asset_response = self.client.get(asset)
                        self.assertEqual(asset_response.status_code, 200, asset)
                        self.assertGreater(len(asset_response.data), 0, asset)
                        asset_response.close()

    def test_page_script_dom_ids_are_declared_or_known_dynamic(self) -> None:
        root = Path(__file__).resolve().parents[1]
        base = (root / "web_templates" / "base.html").read_text(encoding="utf-8")
        base_ids = set(re.findall(r"\bid=['\"]([^'\"]+)['\"]", base))
        id_ref_re = re.compile(r"document\.getElementById\(['\"]([^'\"]+)['\"]\)")
        known_dynamic_ids = {
            ("fluorescence_3d_stacking.html", "fluorescence_3d_files_preview.js", "chanSwatch_${i}"),
            ("fluorescence_lif.html", "fluorescence_lif_files.js", "renameInput"),
            ("fluorescence_roi.html", "fluorescence_roi_exports.js", "roiRadialResultCard"),
            ("run_history.html", "run_history.js", "runCompareBody"),
            ("run_history.html", "run_history.js", "runPreflightBody"),
        }
        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            template_text = template.read_text(encoding="utf-8")
            template_ids = base_ids | set(re.findall(r"\bid=['\"]([^'\"]+)['\"]", template_text))
            for script_name in self._page_script_refs(template_text):
                script_path = root / "web_static" / "js" / "pages" / script_name
                script_text = script_path.read_text(encoding="utf-8")
                for match in id_ref_re.finditer(script_text):
                    dom_id = match.group(1)
                    key = (template.name, script_name, dom_id)
                    if dom_id not in template_ids and key not in known_dynamic_ids:
                        line = script_text[: match.start()].count("\n") + 1
                        offenders.append(f"{template.name}:{script_name}:{line}: #{dom_id}")

        self.assertEqual([], offenders)

    def test_multi_script_pages_do_not_redeclare_global_lexicals(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            scripts = self._page_script_refs(template.read_text(encoding="utf-8"))
            if len(scripts) < 2:
                continue
            seen: dict[str, str] = {}
            for script_name in scripts:
                script_path = root / "web_static" / "js" / "pages" / script_name
                for name in self._top_level_js_lexicals(script_path.read_text(encoding="utf-8")):
                    if name in seen:
                        offenders.append(f"{template.name}: {name} in {seen[name]} and {script_name}")
                    else:
                        seen[name] = script_name

        self.assertEqual([], offenders)

    def test_multi_script_pages_do_not_redeclare_global_functions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            scripts = self._page_script_refs(template.read_text(encoding="utf-8"))
            if len(scripts) < 2:
                continue
            seen: dict[str, str] = {}
            for script_name in scripts:
                script_path = root / "web_static" / "js" / "pages" / script_name
                for name in self._top_level_js_functions(script_path.read_text(encoding="utf-8")):
                    if name in seen:
                        offenders.append(f"{template.name}: {name} in {seen[name]} and {script_name}")
                    else:
                        seen[name] = script_name

        self.assertEqual([], offenders)

    def test_page_scripts_export_through_dp_page_bridge(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for script in sorted((root / "web_static" / "js" / "pages").glob("*.js")):
            text = script.read_text(encoding="utf-8")
            if self._top_level_js_functions(text) and "window.DP.page" not in text:
                offenders.append(script.name)

        self.assertEqual([], offenders)

    def test_delegated_event_handlers_reference_available_functions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        inline_handler_re = re.compile(
            r"\bon(?:click|change|input|submit|mousedown|mouseup|mousemove|mouseleave|wheel)"
            r"=['\"]([^'\"]+)['\"]"
        )
        delegated_handler_re = re.compile(
            r"\bdata-dp-(?:click|change|input|submit|mousedown|mouseup|mousemove|mouseleave|wheel)"
            r"=['\"]([^'\"]+)['\"]"
        )
        call_re = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")
        dp_call_re = re.compile(r"\bDP\.([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*\(")
        js_root = root / "web_static" / "js"
        common_js = "\n".join(path.read_text(encoding="utf-8") for path in js_root.glob("dp_*.js"))
        page_js_paths = sorted((js_root / "pages").glob("*.js"))
        all_js = common_js + "\n" + "\n".join(path.read_text(encoding="utf-8") for path in page_js_paths)
        allowed_names = {
            "alert",
            "clearTimeout",
            "confirm",
            "decodeURIComponent",
            "encodeURIComponent",
            "if",
            "parseFloat",
            "parseInt",
            "setTimeout",
        }
        allowed_prefixes = (
            "Array.",
            "DP.",
            "JSON.",
            "Math.",
            "Number.",
            "Object.",
            "String.",
            "console.",
            "document.",
            "event.",
        )

        def dp_exports(namespace: str) -> set[str]:
            exports: set[str] = set()
            direct_re = re.compile(rf"\bwindow\.DP\.{re.escape(namespace)}\.([A-Za-z_$][\w$]*)\s*=")
            exports.update(direct_re.findall(all_js))
            assign_patterns = [
                rf"Object\.assign\(window\.DP\.{re.escape(namespace)},\s*\{{(?P<body>.*?)\}}\);",
                rf"window\.DP\.{re.escape(namespace)}\s*=\s*Object\.assign\([^,]+,\s*\{{(?P<body>.*?)\}}\);",
            ]
            key_re = re.compile(r"(?:^|[,{\n]\s*)([A-Za-z_$][\w$]*)\s*(?=[:,])", re.M)
            for pattern in assign_patterns:
                for match in re.finditer(pattern, all_js, re.S):
                    exports.update(key_re.findall(match.group("body")))
            return exports

        dp_namespace_exports: dict[str, set[str]] = {}

        def has_dp_export(namespace: str, method: str) -> bool:
            if namespace not in dp_namespace_exports:
                dp_namespace_exports[namespace] = dp_exports(namespace)
            return method in dp_namespace_exports[namespace]

        def has_page_function(name: str, source: str) -> bool:
            has_function = re.search(rf"\bfunction\s+{re.escape(name)}\s*\(", source)
            has_export = re.search(rf"['\"]{re.escape(name)}['\"]", source)
            return bool(has_function or has_export)

        def handler_matches(label: str, text: str):
            for match in delegated_handler_re.finditer(text):
                line = text[: match.start()].count("\n") + 1
                yield f"{label}:{line}", match.group(1)

        offenders = []
        for template in (root / "web_templates").rglob("*.html"):
            template_text = template.read_text(encoding="utf-8")
            for handler in inline_handler_re.findall(template_text):
                offenders.append(f"{template.name}: inline handler remains: {handler}")
            page_sources = [
                (
                    script_name,
                    (root / "web_static" / "js" / "pages" / script_name).read_text(
                        encoding="utf-8"
                    ),
                )
                for script_name in self._page_script_refs(template_text)
            ]
            page_js = "\n".join(text for _script_name, text in page_sources)
            source = f"{template_text}\n{page_js}\n{common_js}"
            handler_sources = [(template.name, template_text), *page_sources]
            for location, handler in (
                item for label, text in handler_sources for item in handler_matches(label, text)
            ):
                for namespace, method in dp_call_re.findall(handler):
                    if namespace == "page":
                        if not has_page_function(method, source):
                            offenders.append(f"{location}: missing DP.page.{method}")
                    elif not has_dp_export(namespace, method):
                        offenders.append(f"{location}: missing DP.{namespace}.{method}")
                for name in call_re.findall(handler):
                    if name in allowed_names:
                        continue
                    if name.startswith(allowed_prefixes):
                        continue
                    has_global_function = re.search(
                        rf"\bfunction\s+{re.escape(name)}\s*\(",
                        source,
                    )
                    has_window_export = re.search(rf"\bwindow\.{re.escape(name)}\s*=", source)
                    if not has_global_function and not has_window_export:
                        offenders.append(f"{location}: missing {name}")

        self.assertEqual([], offenders)

    def test_abf_viewer_does_not_auto_scan_empty_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "abf_viewer.html").read_text(encoding="utf-8")
        page_js = (root / "web_static" / "js" / "pages" / "abf_viewer.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (folderInput.value.trim())", page_js)
        self.assertIn('setStatusBar("Choose an ABF folder to begin.", "")', page_js)
        self.assertIn('data-rnorm-state="checked"', template)
        self.assertIn('id="folderAutoRefresh"', template)
        self.assertIn('id="queueList"', template)
        self.assertIn("queueExportAllCsv", template)
        self.assertIn("openLatestFile", page_js)
        self.assertIn("function updateAbfParameterGroups()", page_js)
        self.assertIn('params.get("rnorm")', page_js)
        self.assertIn("rNorm.checked = true", page_js)
        self.assertIn('dpBindToggleGroups("rNorm", "data-rnorm-state")', page_js)
        self.assertNotIn('DEFAULT_DATA_DIR + "/examples"', template)

    def test_nav_exposes_domain_groups_and_version(self) -> None:
        response = self.client.get("/")
        html = response.data.decode("utf-8")
        self.assertIn("Fluorescence", html)
        self.assertIn("Timecourse", html)
        self.assertIn("Kymograph", html)
        self.assertIn("Photocurrent", html)
        self.assertIn("EMG Analysis", html)
        self.assertIn("Peak Selection", html)
        self.assertNotIn("RHD Viewer (Intan)", html)
        self.assertNotIn("RHD Peak Selector", html)
        self.assertIn("Waveform Averager", html)
        self.assertIn("CSV Viewer", html)
        self.assertIn("Histology", html)
        self.assertIn('href="/histology/naming"', html)
        self.assertIn('href="/histology/analysis"', html)
        self.assertIn("Command Palette", html)
        self.assertIn("commandPalette", html)
        self.assertIn('data-dp-click="logoutServer()"', html)
        self.assertIn("v0.7.0", html)
        self.assertNotIn("unknown", html.lower())

    def test_histology_naming_page_exposes_naming_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        response = self.client.get("/histology/naming")
        html = response.data.decode("utf-8")
        page_js = (root / "web_static" / "js" / "pages" / "histology_naming.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Histology Naming", html)
        self.assertIn("DataProcess Project", html)
        self.assertIn("histologyProjectPath", html)
        self.assertIn("histology_project.dphistology", html)
        self.assertIn("Exported TIFF", html)
        self.assertIn("Raw Olympus", html)
        self.assertIn("Create Analysis Project", html)
        self.assertIn("Rename Case", html)
        self.assertIn("VSI Label Preview", html)
        self.assertIn("Corresponding Files", html)
        self.assertIn("histology-naming-grid", html)
        self.assertIn("histology_naming.js", html)
        self.assertIn("function loadHistologyProjectEntryPreview", page_js)
        self.assertIn("function scanHistologyTiffProject", page_js)
        self.assertIn("function createHistologyTiffProject", page_js)
        self.assertIn("/api/histology/project/scan_tiff", page_js)
        self.assertIn("/api/histology/project/create_from_tiff", page_js)
        self.assertIn("/api/histology/label_preview", page_js)
        self.assertIn("histologyEntryVsiPath", page_js)
        self.assertNotIn("Load Case Folder", html)
        self.assertNotIn("Add ETS To Project", html)
        self.assertNotIn("Rename Folder", html)
        self.assertNotIn("histologyNamingControls", html)
        self.assertNotIn("histologyProjectPreviewPath", page_js)
        self.assertNotIn("api('/api/histology/project/image_preview'", page_js)
        self.assertNotIn("api('/api/histology/file/image_preview'", page_js)
        self.assertNotIn("/api/histology/preview", page_js)
        self.assertNotIn("Promise.all([mainRequest, labelRequest])", page_js)
        self.assertNotIn("applyRename", page_js)
        self.assertNotIn("histologyAnalysisCanvas", html)

    def test_histology_analysis_page_exposes_project_analysis_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        response = self.client.get("/histology/analysis")
        html = response.data.decode("utf-8")
        page_js = (root / "web_static" / "js" / "pages" / "histology_analysis.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Histology ROI Analysis", html)
        self.assertIn("DataProcess Project", html)
        self.assertIn("Test Image", html)
        self.assertIn("histologyImagePath", html)
        self.assertIn("histology_project.dphistology", html)
        self.assertIn("Load Project", html)
        self.assertIn("DP.folder.pickFile('projectPath','loadHistologyDataProject')", html)
        self.assertIn("DP.folder.pickFile('histologyImagePath','loadHistologyFileImage')", html)
        self.assertIn("Open Project", html)
        self.assertIn("Open File", html)
        self.assertIn("Load Image", html)
        self.assertIn("histology-analysis-workbench", html)
        self.assertIn("histologyAnalysisCanvas", html)
        self.assertIn("histologyViewToolbar", html)
        self.assertIn("histologyChannelView", html)
        self.assertIn("btnHistologyRotateLeft", html)
        self.assertIn("histologyZoomSlider", html)
        self.assertIn("histologyRoiLabelInline", html)
        self.assertIn("Start ROI", html)
        self.assertIn('href="/histology/naming"', html)
        self.assertIn('href="/histology/analysis"', html)
        self.assertIn("Analyze SMA + Macrophage", html)
        self.assertIn("Analyze Saved ROI (all)", html)
        self.assertIn("histologyNormalizeGroup", html)
        self.assertIn("histologySummaryGroupBy", html)
        self.assertIn("histologyExcludeZeroObservations", html)
        self.assertIn("roiShrinkPercent", html)
        self.assertIn("histologyDebugRoiSelect", html)
        self.assertIn("histologyRoiDebugPreview", html)
        self.assertIn("histologyProjectBatchResults", html)
        self.assertIn("DAPI / Blue", html)
        self.assertIn("FITC / Green", html)
        self.assertIn("Cy5 / Red", html)
        self.assertIn("Advanced Detection", html)
        self.assertIn("histology_analysis.js", html)
        self.assertIn("function histologyAnalysisProjectPathError", page_js)
        self.assertIn("function loadHistologyFileImage", page_js)
        self.assertIn("/api/histology/file/image_preview", page_js)
        self.assertIn("/api/histology/file/image_region_preview", page_js)
        self.assertIn("/api/histology/project/image_region_preview", page_js)
        self.assertIn("/api/histology/project/analysis/debug_roi", page_js)
        self.assertIn("/api/histology/file/analysis/run_job", page_js)
        self.assertIn("/api/histology/project/analysis/run_saved_job", page_js)
        self.assertIn("function previewHistologyRoiDebug", page_js)
        self.assertIn("function renderHistologyRoiDebugResult", page_js)
        self.assertIn("function analyzeHistologyProjectSavedRois", page_js)
        self.assertIn("function renderHistologyProjectBatchResults", page_js)
        self.assertIn("function histologyRotatePreview", page_js)
        self.assertIn("function histologyFitPreview", page_js)
        self.assertIn("function histologyViewportToLocal", page_js)
        self.assertIn("function histologyRoisForApi", page_js)
        self.assertIn("function histologyPreviewWarningText", page_js)
        self.assertIn("function histologyPreviewMetaText", page_js)
        self.assertIn("function histologyHandleViewChannelChanged", page_js)
        self.assertIn("selected_channels: histologySelectedViewChannels()", page_js)
        self.assertIn("warning ? 'warning' : 'ok'", page_js)
        self.assertIn("function histologyScheduleDetailPreview", page_js)
        self.assertIn("function histologyHandleWheel", page_js)
        self.assertIn("function histologyPointerShouldPan", page_js)
        self.assertIn("gesturechange", page_js)
        self.assertIn("overscroll-behavior: contain", html)
        self.assertIn("not a DataProcess project", page_js)
        self.assertNotIn("histologyRoiControls", html)
        self.assertNotIn("histologyNamingControls", html)
        self.assertNotIn("button-row", html)
        self.assertNotIn("var(--line)", html)

    def test_histology_analysis_api_runs_on_exported_tiff_project(self) -> None:
        try:
            import numpy as np
            import tifffile
        except ImportError as exc:
            self.skipTest(f"histology analysis optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_histology_api_") as tmp:
            root = Path(tmp)
            raw = root / "raw_olympus" / "5-CB"
            (raw / "_Tray04_Slide01_01_" / "stack1").mkdir(parents=True)
            (raw / "Tray04_Slide01_01.vsi").write_bytes(b"raw-vsi")
            (raw / "_Tray04_Slide01_01_" / "stack1" / "frame_t_0.ets").write_bytes(b"SIS\x00")
            exported = root / "exported_tiff"
            exported.mkdir()
            hoechst = np.zeros((20, 20), dtype=np.uint16)
            fitc = np.zeros((20, 20), dtype=np.uint16)
            cy5 = np.zeros((20, 20), dtype=np.uint16)
            hoechst[2:18, 2:18] = 1400
            fitc[3:15, 3:15] = 50000
            cy5[7:18, 7:18] = 48000
            tifffile.imwrite(exported / "5-CB_Hoechst.tif", hoechst)
            tifffile.imwrite(exported / "5-CB_FITC.tif", fitc)
            tifffile.imwrite(exported / "5-CB_Cy5.tif", cy5)
            image = root / "5-CB_composite.tif"
            arr = np.stack([cy5, fitc, hoechst], axis=-1).astype(np.uint16)
            tifffile.imwrite(image, arr)
            rois = [
                {
                    "id": "roi_api",
                    "label": "ROI API",
                    "points": [{"x": 1, "y": 1}, {"x": 18, "y": 1}, {"x": 18, "y": 18}, {"x": 1, "y": 18}],
                }
            ]
            project = root / "study.dphistology"

            scan_response = self.client.post(
                "/api/histology/project/scan_tiff",
                json={
                    "exported_dir": str(exported),
                    "raw_dir": str(root / "raw_olympus"),
                    "analysis_dir": str(root / "analysis"),
                },
            )
            project_response = self.client.post(
                "/api/histology/project/create_from_tiff",
                json={
                    "project_path": str(project),
                    "exported_dir": str(exported),
                    "raw_dir": str(root / "raw_olympus"),
                    "analysis_dir": str(root / "analysis"),
                },
            )
            project_payload = project_response.get_json()
            project_data = _api_data(project_payload)
            entry_id = project_data["entries"][0]["entry_id"]
            preview_response = self.client.post(
                "/api/histology/project/image_preview",
                json={"project_path": project_data["project_path"], "entry_id": entry_id},
            )
            project_region_response = self.client.post(
                "/api/histology/project/image_region_preview",
                json={
                    "project_path": project_data["project_path"],
                    "entry_id": entry_id,
                    "x": 2,
                    "y": 2,
                    "width": 10,
                    "height": 10,
                    "max_side": 512,
                },
            )
            analysis_response = self.client.post(
                "/api/histology/project/analysis/run",
                json={
                    "project_path": project_data["project_path"],
                    "entry_id": entry_id,
                    "rois": rois,
                    "parameters": {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 100,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 100,
                        "background_mode": "none",
                    },
                },
            )
            debug_response = self.client.post(
                "/api/histology/project/analysis/debug_roi",
                json={
                    "project_path": project_data["project_path"],
                    "entry_id": entry_id,
                    "roi_index": 0,
                    "parameters": {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 100,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 100,
                        "background_mode": "none",
                        "roi_shrink_percent": 20,
                    },
                    "before_parameters": {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 100,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 100,
                        "background_mode": "none",
                        "roi_shrink_percent": 0,
                    },
                    "selected_channels": ["FITC", "Cy5", "Hoechst"],
                },
            )
            batch_response = self.client.post(
                "/api/histology/project/analysis/run_saved",
                json={
                    "project_path": project_data["project_path"],
                    "parameters": {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 100,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 100,
                        "background_mode": "none",
                        "summary_normalize_to_group": "1",
                    },
                },
            )
            file_preview_response = self.client.post(
                "/api/histology/file/image_preview",
                json={"image_path": str(image)},
            )
            file_region_response = self.client.post(
                "/api/histology/file/image_region_preview",
                json={"image_path": str(image), "x": 2, "y": 2, "width": 8, "height": 8},
            )
            file_analysis_response = self.client.post(
                "/api/histology/file/analysis/run",
                json={
                    "image_path": str(image),
                    "rois": rois,
                    "parameters": {
                        "sma_channel": "green",
                        "sma_threshold_method": "manual",
                        "sma_threshold": 100,
                        "macrophage_channel": "red",
                        "macrophage_threshold_method": "manual",
                        "macrophage_threshold": 100,
                        "background_mode": "none",
                    },
                },
            )

            scan_payload = scan_response.get_json()
            preview_payload = preview_response.get_json()
            project_region_payload = project_region_response.get_json()
            analysis_payload = analysis_response.get_json()
            debug_payload = debug_response.get_json()
            batch_payload = batch_response.get_json()
            file_preview_payload = file_preview_response.get_json()
            file_region_payload = file_region_response.get_json()
            file_analysis_payload = file_analysis_response.get_json()
            scan_data = _api_data(scan_payload)
            preview_data = _api_data(preview_payload)
            project_region_data = _api_data(project_region_payload)
            analysis_data = _api_data(analysis_payload)
            debug_data = _api_data(debug_payload)
            batch_data = _api_data(batch_payload)
            file_preview_data = _api_data(file_preview_payload)
            file_region_data = _api_data(file_region_payload)
            file_analysis_data = _api_data(file_analysis_payload)
            self.assertEqual(scan_response.status_code, 200)
            self.assertTrue(scan_payload["ok"])
            self.assertEqual(scan_data["sample_count"], 1)
            self.assertEqual(scan_data["raw_olympus_file_count"], 2)
            self.assertEqual(project_response.status_code, 200)
            self.assertTrue(project_payload["ok"])
            self.assertEqual(project_data["protocol"], "dataprocess-tiff-histology")
            self.assertEqual(project_data["entry_count"], 1)
            self.assertTrue(Path(project_data["raw_olympus_index_path"]).is_file())
            self.assertIn("FITC", project_data["entries"][0]["image_files"])
            self.assertEqual(preview_response.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertEqual(preview_data["width"], 20)
            self.assertIn("FITC", preview_data["preview_channels"])
            self.assertEqual(project_region_response.status_code, 200)
            self.assertTrue(project_region_payload["ok"])
            self.assertEqual(project_region_data["region_width"], 10)
            self.assertTrue(project_region_data["img"])
            self.assertEqual(analysis_response.status_code, 200)
            self.assertTrue(analysis_payload["ok"])
            self.assertGreater(analysis_data["results"][0]["sma_positive_px"], 0)
            self.assertGreaterEqual(analysis_data["results"][0]["sma_object_count"], 1)
            self.assertTrue(Path(analysis_data["analysis_path"]).exists())
            self.assertTrue(Path(analysis_data["project_path"]).exists())
            self.assertTrue(Path(analysis_data["cache_dir"]).is_dir())
            self.assertEqual(debug_response.status_code, 200)
            self.assertTrue(debug_payload["ok"])
            self.assertEqual(debug_data["kind"], "histology_roi_debug")
            self.assertEqual(debug_data["roi_index"], 0)
            self.assertEqual(debug_data["roi_shrink_percent"], 20)
            self.assertTrue(debug_data["img"])
            self.assertIn("sma", debug_data["before"])
            self.assertLess(debug_data["after"]["area_px"], debug_data["before"]["area_px"])
            self.assertEqual(batch_response.status_code, 200)
            self.assertTrue(batch_payload["ok"])
            self.assertEqual(batch_data["kind"], "histology_saved_roi_batch_analysis")
            self.assertTrue(Path(batch_data["roi_table_path"]).exists())
            self.assertTrue(Path(batch_data["image_table_path"]).exists())
            self.assertTrue(Path(batch_data["summary_table_path"]).exists())
            self.assertTrue(Path(batch_data["statistics_path"]).exists())
            self.assertEqual(len(batch_data["plots"]), 4)
            self.assertTrue(all(Path(plot["path"]).exists() for plot in batch_data["plots"]))
            self.assertEqual(file_preview_response.status_code, 200)
            self.assertTrue(file_preview_payload["ok"])
            self.assertEqual(file_preview_data["width"], 20)
            self.assertEqual(file_region_response.status_code, 200)
            self.assertTrue(file_region_payload["ok"])
            self.assertEqual(file_region_data["region_width"], 8)
            self.assertEqual(file_analysis_response.status_code, 200)
            self.assertTrue(file_analysis_payload["ok"])
            self.assertEqual(file_analysis_data["kind"], "single_file_histology_analysis")
            self.assertGreater(file_analysis_data["results"][0]["macrophage_positive_px"], 0)

    def test_emg_analysis_exposes_preview_merge_downsample_and_view_first_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "emg_analysis.html").read_text(encoding="utf-8")
        modules = (
            "emg_analysis_state_profiles.js",
            "emg_analysis_files_queue.js",
            "emg_analysis_plot.js",
            "emg_analysis_exports.js",
            "emg_analysis_rename.js",
        )
        js_source = "\n".join(
            (root / "web_static" / "js" / "pages" / module).read_text(encoding="utf-8")
            for module in modules
        )
        source = template + "\n" + js_source

        self.assertIn('id="previewDownsample"', template)
        self.assertIn('id="previewMergePair"', template)
        self.assertIn('id="invertY"', template)
        for module in modules:
            self.assertIn(f"static_asset('js/pages/{module}')", template)
        self.assertIn("function reloadCurrentEmgAnalysisFile()", source)
        self.assertIn("function renderEmgAnalysisFileList(options)", source)
        self.assertIn("Merge paired folder recordings for preview", template)
        self.assertIn("Invert Y polarity", template)
        self.assertIn("Merge paired folder recordings for export", template)
        self.assertIn("Export all channels as one wide TSV", template)
        self.assertIn("split(/[\\\\/]/)", source)
        self.assertIn('id="filterType"', template)
        self.assertIn('id="processType"', template)
        self.assertIn('data-filter-mode="notch"', template)
        self.assertIn('data-process-mode="smooth"', template)
        self.assertIn("function updateEmgAnalysisParameterGroups()", source)
        self.assertIn("function previewInvertYEnabled()", source)
        self.assertIn("dpBindParamGroups('processType', 'data-process-mode')", source)
        self.assertIn('id="envelopeSmoothMs"', template)
        self.assertIn('id="smoothMethod"', template)
        self.assertIn('id="fftWindow"', template)
        self.assertIn('id="fftMaxHz"', template)
        self.assertIn('id="stftOverlapPct"', template)
        self.assertIn('id="figWidthIn"', template)
        self.assertIn('id="traceLineWidth"', template)
        self.assertIn("function currentFigureParams()", source)
        self.assertIn('id="processArea"', template)
        self.assertIn("/api/emg/analysis/process", source)
        self.assertIn("/api/emg/analysis/export_processing_job", source)
        self.assertIn('id="renameFind"', template)
        self.assertIn("Quick Rename", template)
        self.assertIn("Use Selected Token", template)
        self.assertIn('id="btnQuickRenamePreview"', template)
        self.assertIn('id="btnQuickRenameApply"', template)
        self.assertIn(".rhd,.xml,.csv,.txt,.tsv,.json,.png,.svg", template)
        self.assertIn('id="renamePreviewArea"', template)
        self.assertIn("/api/emg/analysis/rename/preview", source)
        self.assertIn("function emgAnalysisRecordingToken", source)
        self.assertIn("function autoFillEmgAnalysisRenameToken", source)
        self.assertIn("function useSelectedEmgAnalysisToken", source)
        self.assertIn("function previewQuickEmgAnalysisRename", source)
        self.assertIn("autoFillEmgAnalysisRenameToken(path)", source)
        self.assertIn("function remapEmgAnalysisPathAfterRename(path, changes)", source)
        self.assertIn("selectedPath: _currentFile", source)
        self.assertIn("updatedRoot || data.root || payload.root", source)
        self.assertIn("function currentEmgAnalysisBatchPaths", source)
        self.assertIn("_metadata.source_paths", source)
        self.assertIn("function currentProcessingPayload(extra)", source)
        self.assertIn("function exportProcessing(fmt)", source)
        self.assertIn("Export SVG", template)
        self.assertIn("let _fileLoadSeq", source)
        self.assertIn("let _plotSeq", source)
        self.assertIn("merge_pair: previewMergeEnabled()", source)
        self.assertLess(template.index("View Window"), template.index("Export Options"))
        self.assertLess(template.index("Export Current"), template.index("Batch Export Queue"))

    def test_fluorescence_lif_uses_page_specific_js_modules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "fluorescence_lif.html").read_text(encoding="utf-8")
        modules = (
            "fluorescence_lif_state.js",
            "fluorescence_lif_files.js",
            "fluorescence_lif_preview.js",
            "fluorescence_lif_exports.js",
        )

        for module in modules:
            self.assertIn(f"static_asset('js/pages/{module}')", template)
            self.assertTrue((root / "web_static" / "js" / "pages" / module).exists())
        self.assertNotIn("window.LIF_VIEWER_FLAGS", template)
        self.assertNotIn("function loadLifPreview()", template)

    def test_fluorescence_3d_uses_page_specific_js_modules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "fluorescence_3d_stacking.html").read_text(
            encoding="utf-8"
        )
        modules = (
            "fluorescence_3d_state.js",
            "fluorescence_3d_files_preview.js",
            "fluorescence_3d_volume_payload.js",
            "fluorescence_3d_three_viewer.js",
            "fluorescence_3d_exports.js",
        )

        for module in modules:
            self.assertIn(f"static_asset('js/pages/{module}')", template)
            self.assertTrue((root / "web_static" / "js" / "pages" / module).exists())
        self.assertNotIn("window.FL3D_FLAGS", template)
        self.assertNotIn("function renderVolume3D(volume)", template)

    def test_emg_peak_selection_uses_workflow_specific_js_modules(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "emg_peak_selection.html").read_text(encoding="utf-8")
        modules = (
            "emg_peak_selection_state.js",
            "emg_peak_selection_browser.js",
            "emg_peak_selection_detection.js",
            "emg_peak_selection_table_edit.js",
            "emg_peak_selection_export.js",
        )
        js_source = "\n".join(
            (root / "web_static" / "js" / "pages" / module).read_text(encoding="utf-8")
            for module in modules
        )
        detection_source = (
            root / "web_static" / "js" / "pages" / "emg_peak_selection_detection.js"
        ).read_text(encoding="utf-8")

        for module in modules:
            self.assertIn(f"static_asset('js/pages/{module}')", template)
            self.assertTrue((root / "web_static" / "js" / "pages" / module).exists())
        self.assertIn("function detectPeaks()", js_source)
        self.assertIn('id="plotCoordReadout"', template)
        self.assertIn('id="processedPlotArea"', template)
        self.assertIn('id="exportResultCard"', template)
        self.assertIn('id="invertSignal"', template)
        self.assertIn('id="linkedChannelList"', template)
        self.assertIn('id="linkedExportEnabled"', template)
        self.assertIn('id="grpTargetCount"', template)
        self.assertIn('id="baselinePerGroup"', template)
        self.assertIn('id="baselineThresholdScale"', template)
        self.assertIn('id="baselineStart"', template)
        self.assertIn('id="baselineEnd"', template)
        self.assertIn('id="baselineSeed"', template)
        self.assertIn("Linked Channel Export", template)
        self.assertIn("Polarity inverse", template)
        self.assertIn("linked-channel-picker", template)
        self.assertIn("Baseline Fill", template)
        self.assertIn("Preview Window (s)", template)
        self.assertIn("function resetPreviewWindow()", js_source)
        self.assertIn("function fillMissingGroupsWithBaseline()", js_source)
        self.assertIn("function usePreviewWindowForBaseline()", js_source)
        self.assertIn("function detectBaselineCandidatePeaks(", js_source)
        self.assertIn("function chooseBaselineCandidates(", js_source)
        self.assertIn("function seededBaselineRandom(", js_source)
        self.assertIn("function emgGroupedSegmentHalfMs()", js_source)
        self.assertIn("baseline_threshold_scale", js_source)
        self.assertIn("source_kind: 'baseline'", js_source)
        self.assertIn("baseline_source_start_s", js_source)
        self.assertIn("baseline_fill_seed", js_source)
        self.assertIn("baseline_rep", js_source)
        self.assertIn("segment_half_ms: halfMs", js_source)
        self.assertIn("function collectLinkedChannelNames()", js_source)
        self.assertIn("function renderLinkedChannelList()", js_source)
        self.assertIn("linked_channels: linkedChannels", js_source)
        self.assertIn("function isEmgSignalInverted()", js_source)
        self.assertIn("invert_signal", js_source)
        self.assertIn("function installEmgPeakSelectionPlotInteractions()", js_source)
        self.assertIn("dragZoom: false", js_source)
        self.assertIn("onCursor: pos => updateEmgPeakSelectionPlotReadouts(pos.x, pos.y)", js_source)
        self.assertIn("function refreshProcessedPeakOverlay()", js_source)
        self.assertIn("dpRenderTrace('processedPlotArea'", js_source)
        self.assertIn("refreshProcessedPeakOverlay();", js_source)
        self.assertIn("emg-peak-marker-label", js_source)
        self.assertIn("event.shiftKey", js_source)
        self.assertIn("function resetPeakSelectionAnchor()", js_source)
        self.assertIn("showProcessedPeakPlot(data.img", detection_source)
        self.assertNotIn("setPlot('plotArea', data.img)", detection_source)
        self.assertIn("function renderEmgPeakSelectionExportOutputs(data)", js_source)
        self.assertNotIn("d.summary_path ? (' | summary: '", js_source)
        self.assertIn("function autoGroupByTime()", js_source)
        self.assertIn("function exportGrouped()", js_source)
        self.assertNotIn("let _currentFolder = null", template)

    def test_emg_baseline_segments_keep_peak_file_naming(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "services" / "emg_peak_selection.py").read_text(encoding="utf-8")
        self.assertIn('out_file = group_dir / f"peak_{channel}_{index:04d}_t{peak_time:.6f}s.csv"', source)
        self.assertNotIn('f"baseline_{channel}', source)

    def test_uplot_trace_renderer_fits_panel_without_side_legend(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "web_static" / "js" / "dp_uplot.js").read_text(encoding="utf-8")
        style = (root / "web_static" / "style" / "_files_plots.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("function dpGetTrace(containerId)", source)
        self.assertIn("legend: { show: false, live: false }", source)
        self.assertIn("opts.dragZoom !== false", source)
        self.assertIn("opts.onCursor", source)
        self.assertIn("plot-cursor-readout", source)
        self.assertIn("function cursorInsidePlot(u, left, top)", source)
        self.assertIn("updateCursorReadout(cursorReadout, NaN, NaN)", source)
        self.assertIn("function inlineStylePixels(el, prop)", source)
        self.assertIn("const explicitHeight = inlineStylePixels(el, 'height') - pad", source)
        self.assertIn(".plot-cursor-readout", style)
        self.assertIn(".u-legend", style)
        self.assertIn("chartHeight(el, width, opts)", source)
        self.assertNotIn("getBoundingClientRect().height", source)
        self.assertIn("cssPixels(el, 'min-height')", source)
        self.assertIn("opts.maxHeight", source)
        self.assertIn(".plot-area.is-uplot", style)

    def test_csv_viewer_prefers_time_columns_and_guards_constant_x(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "web_static" / "js" / "pages" / "csv_viewer.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function suggestCsvColumns(columns)", source)
        self.assertIn("'time_s'", source)
        self.assertIn("'t_abs_s'", source)
        self.assertIn("'t_rel_ms'", source)
        self.assertIn("'value_uV'", source)
        self.assertIn("'segmentstart'", source)
        self.assertIn("setCsvColumnOptions(_columns)", source)
        self.assertIn("x_unique_count", source)
        self.assertIn("has only one distinct numeric value", source)

    def test_lineshape_selection_supports_shift_ranges(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "web_static" / "js" / "pages" / "echem_lineshape.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("toggleSourceIndex(${i}, event)", source)
        self.assertIn("toggleSample(${i}, event)", source)
        self.assertIn("toggleSample(${idx}, event)", source)
        self.assertIn("event.shiftKey", source)
        self.assertIn("_lastSourceIndex", source)
        self.assertIn("_lastSampleIndex", source)

    def test_uplot_helper_is_cache_busted_on_all_uplot_pages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        templates = [
            "abf_viewer.html",
            "csv_viewer.html",
            "echem_lineshape.html",
            "echem_photocurrent.html",
            "echem_photovoltage.html",
            "emg_peak_selection.html",
        ]
        for name in templates:
            with self.subTest(template=name):
                template = (root / "web_templates" / name).read_text(encoding="utf-8")
                self.assertIn("static_asset('js/dp_uplot.js')", template)
                self.assertNotIn('src="/static/js/dp_uplot.js"', template)

    def test_rhd_preview_plot_accepts_merge_and_downsample(self) -> None:
        import numpy as np

        import web_app

        if not web_app.HAS_RHD:
            self.skipTest("Intan RHD parser is not available")

        def fake_recording_metadata(path, _rhd_module, do_merge):
            n = 120_000 if do_merge else 60_000
            return {
                "channels": ["A-000", "A-001"],
                "channels_meta": [
                    {
                        "idx": 0,
                        "name": "A-000",
                        "native_name": "native-000",
                        "label": "A-000",
                        "type": "amplifier",
                    },
                    {
                        "idx": 1,
                        "name": "A-001",
                        "native_name": "native-001",
                        "label": "A-001",
                        "type": "amplifier",
                    },
                ],
                "sample_rate": 1000.0,
                "sampling_rate": 1000.0,
                "n_samples": n,
                "duration_s": n / 1000.0,
                "duration": n / 1000.0,
                "num_amplifiers": 2,
                "merged_pair": bool(do_merge),
                "merged_folder": bool(do_merge),
                "base_stem": "record_0001",
                "source_path": str(path),
                "source_paths": [str(path)],
                "segment_count": 2 if do_merge else 1,
            }

        def fake_load_channel_with_merge_option(path, _rhd_module, ch_in, do_merge):
            n = 120_000 if do_merge else 60_000
            t = np.arange(n, dtype=float) / 1000.0
            y = np.cos(t) if ch_in == "A-001" else np.sin(t)
            ch = 1 if ch_in == "A-001" else 0
            ch_name = "A-001" if ch == 1 else "A-000"
            return t, 1000.0, ["A-000", "A-001"], y, ch, ch_name, "record_0001", bool(do_merge), 2

        with (
            mock.patch(
                "services.emg_analysis.rhd_service.recording_metadata_with_merge_option",
                side_effect=fake_recording_metadata,
            ),
            mock.patch(
                "services.emg_analysis.rhd_service.load_channel_with_merge_option",
                side_effect=fake_load_channel_with_merge_option,
            ),
        ):
            loaded = self.client.post(
                "/api/emg/analysis/load",
                json={"path": "/tmp/record_0100.rhd", "merge_pair": True},
            )
            loaded_payload = loaded.get_json()
            loaded_data = _api_data(loaded_payload)
            self.assertEqual(loaded.status_code, 200)
            self.assertTrue(loaded_data["merged_pair"])
            self.assertEqual(loaded_data["n_samples"], 120_000)

            plot = self.client.post(
                "/api/emg/analysis/plot",
                json={
                    "path": "/tmp/record_0100.rhd",
                    "channel": "A-001",
                    "merge_pair": True,
                    "invert_y": True,
                    "downsample": 10,
                },
            )
            plot_payload = plot.get_json()
            plot_data = _api_data(plot_payload)
            self.assertEqual(plot.status_code, 200)
            self.assertTrue(plot_data["img"])
            self.assertEqual(plot_data["downsample"], 10)
            self.assertEqual(plot_data["plotted_points"], 12_000)
            self.assertTrue(plot_data["inverted_y"])

            processed = self.client.post(
                "/api/emg/analysis/process",
                json={
                    "path": "/tmp/record_0100.rhd",
                    "channel": "A-001",
                    "merge_pair": True,
                    "x_min": 0,
                    "x_max": 1,
                    "filter_type": "highpass",
                    "filter_low_hz": 1,
                    "process_type": "fft",
                    "fft_window": "hamming",
                    "fft_max_hz": 100,
                    "fft_log": True,
                    "fig_width_in": 6,
                    "fig_height_in": 2.5,
                    "trace_line_width": 1.5,
                    "show_grid": False,
                },
            )
            processed_payload = processed.get_json()
            processed_data = _api_data(processed_payload)
            self.assertEqual(processed.status_code, 200)
            self.assertTrue(processed_data["img"])
            self.assertEqual(processed_data["process_type"], "fft")
            self.assertEqual(processed_data["fft_window"], "hamming")
            self.assertLessEqual(processed_data["frequency_max"], 100)

    def test_emg_analysis_processing_exports_csv_png_and_svg(self) -> None:
        import numpy as np

        import web_app

        if not web_app.HAS_RHD:
            self.skipTest("Intan RHD parser is not available")

        def fake_load_channel_with_merge_option(path, _rhd_module, ch_in, do_merge):
            n = 1000
            t = np.arange(n, dtype=float) / 1000.0
            y = np.sin(2 * np.pi * 20 * t)
            return t, 1000.0, ["A-000"], y, 0, "A-000", Path(path).stem, bool(do_merge), 1

        with tempfile.TemporaryDirectory(prefix="dataprocess_emg_analysis_processing_") as tmp:
            src = Path(tmp) / "record_0000.rhd"
            src.write_bytes(b"placeholder")
            saved = {}
            with mock.patch(
                "services.emg_analysis.rhd_service.load_channel_with_merge_option",
                side_effect=fake_load_channel_with_merge_option,
            ):
                for fmt in ("csv", "png", "svg"):
                    response = self.client.post(
                        "/api/emg/analysis/export_processing",
                        json={
                            "path": str(src),
                            "channel": "A-000",
                            "fmt": fmt,
                            "mode": "save",
                            "process_type": "fft",
                            "fft_window": "hann",
                            "fft_max_hz": 100,
                            "x_min": 0,
                            "x_max": 0.5,
                        },
                    )
                    payload = response.get_json()
                    data = _api_data(payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(payload["ok"])
                    out_path = Path(data["saved_path"])
                    self.assertTrue(out_path.exists())
                    self.assertTrue(out_path.name.endswith(f"_fft_1.{fmt}"))
                    saved[fmt] = out_path

            self.assertIn("frequency_hz,amplitude_uV", saved["csv"].read_text(encoding="utf-8"))
            self.assertTrue(saved["png"].read_bytes().startswith(b"\x89PNG"))
            self.assertIn("<svg", saved["svg"].read_text(encoding="utf-8"))

    def test_emg_analysis_svg_export_is_clean_and_numbered(self) -> None:
        import numpy as np

        import web_app

        if not web_app.HAS_RHD:
            self.skipTest("Intan RHD parser is not available")

        def fake_load_channel_with_merge_option(path, _rhd_module, ch_in, do_merge):
            n = 1000
            t = np.arange(n, dtype=float) / 1000.0
            y = np.sin(2 * np.pi * 20 * t)
            return t, 1000.0, ["A-000"], y, 0, "A-000", Path(path).stem, bool(do_merge), 1

        with tempfile.TemporaryDirectory(prefix="dataprocess_emg_analysis_svg_") as tmp:
            src = Path(tmp) / "record_0000.rhd"
            src.write_bytes(b"placeholder")

            blocked = self.client.get(
                "/api/emg/analysis/export_channel",
                query_string={
                    "path": str(src),
                    "channel": "A-000",
                    "fmt": "svg",
                    "mode": "save",
                },
            )
            blocked_payload = blocked.get_json()
            self.assertEqual(blocked.status_code, 405)
            self.assertFalse(blocked_payload["ok"])
            self.assertIn("requires POST", blocked_payload["error"])

            def export_channel_job(body: dict) -> dict:
                started = self.client.post("/api/emg/analysis/export_channel_job", json=body)
                started_payload = started.get_json()
                started_data = _api_data(started_payload)
                self.assertEqual(started.status_code, 200)
                self.assertTrue(started_payload["ok"])
                job = self._wait_for_api_job(started_data["job_id"])
                self.assertEqual(job["status"], "succeeded")
                return job["data"]

            with mock.patch(
                "services.emg_analysis.rhd_service.load_channel_with_merge_option",
                side_effect=fake_load_channel_with_merge_option,
            ):
                first_data = export_channel_job(
                    {
                        "path": str(src),
                        "channel": "A-000",
                        "fmt": "svg",
                        "mode": "save",
                        "x_min": 0,
                        "x_max": 0.25,
                        "filter_type": "notch",
                        "filter_notch_hz": 60,
                        "fig_width_in": 5,
                        "fig_height_in": 3,
                        "trace_line_width": 2.5,
                        "trace_color": "#ff0000",
                    },
                )
                second_data = export_channel_job(
                    {
                        "path": str(src),
                        "channel": "A-000",
                        "fmt": "svg",
                        "mode": "save",
                        "x_min": 0,
                        "x_max": 0.25,
                    },
                )
                csv_data = export_channel_job(
                    {
                        "path": str(src),
                        "channel": "A-000",
                        "fmt": "csv",
                        "mode": "save",
                        "invert_y": "1",
                    },
                )

            first_path = Path(first_data["saved_path"])
            second_path = Path(second_data["saved_path"])
            csv_path = Path(csv_data["saved_path"])
            self.assertTrue(first_path.name.endswith("_1.svg"))
            self.assertTrue(second_path.name.endswith("_2.svg"))
            svg = first_path.read_text(encoding="utf-8")
            self.assertIn('width="360"', svg)
            self.assertIn('height="216"', svg)
            self.assertIn('stroke="#ff0000"', svg)
            self.assertIn('stroke-width="2.5"', svg)
            csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertLess(float(csv_lines[2].split(",")[1]), 0)
            self.assertIn("<polyline", svg)
            self.assertNotIn("<g", svg)
            self.assertNotIn("<rect", svg)
            self.assertNotIn("grid", svg.lower())

    def test_csv_svg_export_uses_clean_numbered_trace_svg(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_csv_svg_") as tmp:
            source = Path(tmp) / "trace.csv"
            source.write_text("time,value\n0,1\n1,2\n2,0\n", encoding="utf-8")
            saved_paths = []

            for _ in range(2):
                started = self.client.post(
                    "/api/csv/export_job",
                    json={
                        "path": str(source),
                        "x_col": "time",
                        "y_col": "value",
                        "fmt": "svg",
                        "mode": "save",
                    },
                )
                started_payload = started.get_json()
                started_data = _api_data(started_payload)
                self.assertEqual(started.status_code, 200)
                self.assertTrue(started_payload["ok"])
                job = self._wait_for_api_job(started_data["job_id"])
                self.assertEqual(job["status"], "succeeded")
                saved_paths.append(Path(job["outputs"][0]["path"]))

            self.assertTrue(saved_paths[0].name.endswith("_1.svg"))
            self.assertTrue(saved_paths[1].name.endswith("_2.svg"))
            svg = saved_paths[0].read_text(encoding="utf-8")
            self.assertIn("<polyline", svg)
            self.assertNotIn("<g", svg)
            self.assertNotIn("<rect", svg)
            self.assertNotIn("grid", svg.lower())

    def test_emg_analysis_rename_previews_and_applies_folder_and_file_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_rhd_rename_") as tmp:
            root = Path(tmp) / "rough_session"
            root.mkdir()
            source = root / "rough_session_A-000.rhd"
            source.write_bytes(b"placeholder")

            preview = self.client.post(
                "/api/emg/analysis/rename/preview",
                json={
                    "root": str(root),
                    "find": "rough_session",
                    "replace": "clean_session",
                    "include_root": True,
                    "include_files": True,
                    "include_dirs": True,
                    "extensions": ".rhd,.csv",
                },
            )
            preview_payload = preview.get_json()
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertEqual(preview_payload["data"]["ready_count"], 2)
            self.assertEqual(preview_payload["data"]["conflict_count"], 0)

            started = self.client.post(
                "/api/emg/analysis/rename/apply_job",
                json={
                    "root": str(root),
                    "find": "rough_session",
                    "replace": "clean_session",
                    "include_root": True,
                    "include_files": True,
                    "include_dirs": True,
                    "extensions": ".rhd,.csv",
                    "confirm": True,
                },
            )
            started_payload = started.get_json()
            started_data = _api_data(started_payload)
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started_payload["ok"])
            job = self._wait_for_api_job(started_data["job_id"])
            self.assertEqual(job["status"], "succeeded")

            renamed_root = Path(tmp) / "clean_session"
            self.assertFalse(root.exists())
            self.assertTrue((renamed_root / "clean_session_A-000.rhd").exists())
            self.assertEqual(job["data"]["renamed_count"], 2)
            self.assertEqual(job["data"]["updated_root"], str(renamed_root))

    def test_version_api_omits_unknown_commit_from_display_label(self) -> None:
        response = self.client.get("/api/version")
        payload = response.get_json()
        data = _api_data(payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(data["version"], "0.7.0")
        self.assertTrue(data["label"].startswith("v0.7.0"))
        self.assertNotIn("unknown", data["label"].lower())

    def test_abf_batch_dry_run_reports_plan_without_moving_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_dry_run_") as tmp:
            root = Path(tmp)
            source = root / "ctrl_T1_sample_1_A_0001.abf"
            source.write_bytes(b"not a real abf")

            response = self.client.post(
                "/api/abf_batch/process",
                json={
                    "folder": str(root),
                    "main": "ctrl",
                    "treat": "T1",
                    "powers": "0, 1",
                    "move_files": True,
                    "reindex_seq": True,
                    "dry_run": True,
                },
            )
            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["data"]["dry_run"])
            self.assertGreaterEqual(payload["data"]["planned_count"], 1)
            self.assertTrue(source.exists())
            self.assertEqual(payload["data"]["operation_log_path"], "")
            self.assertFalse((root / ".dataprocess_cache" / "operation_logs").exists())

    def test_abf_batch_run_button_surfaces_confirmation_and_empty_results(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "web_templates" / "abf_batch.html").read_text(encoding="utf-8")
        js_source = (root / "web_static" / "js" / "pages" / "abf_batch.js").read_text(
            encoding="utf-8"
        )
        rendered = self.client.get("/abf/batch").data.decode("utf-8")

        self.assertIn('data-dp-click="DP.page.runBatch()"', template)
        self.assertIn("static_asset('js/pages/abf_batch.js')", template)
        self.assertRegex(rendered, r"/static/js/pages/abf_batch\.js\?v=")
        self.assertIn("Waiting for confirmation before moving or renaming files", js_source)
        self.assertIn("No matching files processed", js_source)
        self.assertIn("Enter folder, main token, and treatment token", js_source)
        self.assertIn("tokenListText(mains)", js_source)

    def test_abf_batch_job_endpoint_reports_empty_match_as_completed_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_empty_job_") as tmp:
            started = self.client.post(
                "/api/abf_batch/process_job",
                json={
                    "folder": tmp,
                    "main": "ctrl",
                    "treat": "T1",
                    "move_files": False,
                    "dry_run": False,
                },
            )
            payload = started.get_json()
            data = _api_data(payload)
            self.assertEqual(started.status_code, 200)
            self.assertTrue(payload["ok"])
            job = self._wait_for_api_job(data["job_id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["data"]["n"], 0)
            self.assertEqual(job["data"]["message"], "No matching files processed")

    def test_echem_photocurrent_and_photovoltage_export_preview_figures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_echem_figures_") as tmp:
            root = Path(tmp)
            pc_path = root / "pc.txt"
            pv_path = root / "pv.csv"
            pc_path.write_text(
                "Time/s\tI/mA\n0\t0\n0.001\t0.5\n0.002\t-0.3\n0.003\t0\n",
                encoding="utf-8",
            )
            pv_path.write_text(
                "Time/s,Voltage/V\n0,0\n0.01,0.1\n0.02,0\n0.03,-0.05\n",
                encoding="utf-8",
            )

            cases = [
                ("/api/echem/photocurrent/export_figure", pc_path, "png", "pc_preview.png"),
                ("/api/echem/photocurrent/export_figure", pc_path, "svg", "pc_preview_signal.svg"),
                ("/api/echem/photovoltage/export_figure", pv_path, "png", "pv_preview.png"),
                ("/api/echem/photovoltage/export_figure", pv_path, "svg", "pv_preview_signal.svg"),
            ]
            for endpoint, source, fmt, filename in cases:
                with self.subTest(endpoint=endpoint, fmt=fmt):
                    response = self.client.post(endpoint, json={"path": str(source), "fmt": fmt})
                    payload = response.get_json()

                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(payload["ok"])
                    out_path = root / filename
                    self.assertEqual(payload["data"]["saved_path"], str(out_path))
                    self.assertTrue(out_path.exists())
                    self.assertGreater(out_path.stat().st_size, 100)

    def test_fluorescence_refactor_keeps_route_contracts(self) -> None:
        routes = {str(rule.rule) for rule in self.client.application.url_map.iter_rules()}
        expected = {
            "/api/system/select_folder",
            "/api/system/select_file",
            "/api/system/logout",
            "/api/version",
            "/api/fluorescence/browse",
            "/api/fluorescence/stack_export",
            "/api/fluorescence/stack_export_job",
            "/api/fluorescence/3d/volume",
            "/api/fluorescence/3d/export_volume_job",
            "/api/fluorescence/3d/rotation_gif_preview",
            "/api/fluorescence/3d/export_rotation_gif_job",
            "/api/fluorescence/3d/intensity_distribution",
            "/api/fluorescence/gif_preview",
            "/api/fluorescence/make_gif_job",
            "/api/fluorescence/gif_roi/kymograph_export_job",
            "/api/fluorescence/roi/analyze_sequence",
            "/api/fluorescence/roi/export_sequence_gif_job",
            "/api/emg/analysis/export_channel_job",
            "/api/emg/analysis/export_processing",
            "/api/emg/analysis/export_processing_job",
        }
        self.assertTrue(expected.issubset(routes), sorted(expected - routes))

    def test_profile_and_page_settings_are_collapsed_as_advanced_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        generic = (root / "web_templates" / "partials" / "control_panel_extras.html").read_text(
            encoding="utf-8"
        )
        fluorescence = (root / "web_templates" / "fluorescence_stack.html").read_text(
            encoding="utf-8"
        )
        gif = (root / "web_templates" / "fluorescence_gif.html").read_text(encoding="utf-8")
        roi = (root / "web_templates" / "fluorescence_roi.html").read_text(encoding="utf-8")

        self.assertIn(
            '<details class="ctrl-section ctrl-details generic-file-profile-section"', generic
        )
        self.assertIn("Advanced: File Profile", generic)
        self.assertIn("Advanced: Page Settings", generic)
        self.assertIn("Advanced: File Profile", fluorescence)
        self.assertIn("Advanced: Page Settings", fluorescence)
        self.assertIn("Advanced: File Profile", gif)
        self.assertIn("Advanced: Page Settings", gif)
        self.assertIn("Advanced: File Profile", roi)
        self.assertIn("Advanced: Page Settings", roi)

    def test_windows_picker_failure_returns_error_without_tk_fallback(self) -> None:
        with (
            mock.patch.object(system_api.sys, "platform", "win32"),
            mock.patch.object(
                system_api,
                "_choose_windows_folder",
                side_effect=system_api._windows_picker_error("folder", "picker unavailable"),
            ),
            mock.patch.object(system_api, "_choose_tk_folder") as tk_fallback,
        ):
            response = self.client.post("/api/system/select_folder", json={"start": ""})

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Folder picker unavailable; please paste path manually.", payload["error"])
        tk_fallback.assert_not_called()

    def test_logout_cancels_running_jobs_before_shutdown_handler(self) -> None:
        app = Flask(__name__)
        manager = JobManager()
        started = threading.Event()

        def slow_task(job_ctx):
            started.set()
            for _ in range(200):
                job_ctx.check_cancelled()
                time.sleep(0.01)
            return {"ok": True}

        submitted = manager.submit("test", "Slow task", slow_task)
        self.assertTrue(started.wait(timeout=1.0))
        called = []
        system_api.register_system_routes(
            app,
            SimpleNamespace(
                err=api_error,
                BASE_DIR=Path(__file__).resolve().parents[1],
                jobs=manager,
            ),
        )
        app.config["DATAPROCESS_LOGOUT_HANDLER"] = lambda jobs: called.append(jobs)

        response = app.test_client().post("/api/system/logout", json={})

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["shutdown"])
        self.assertEqual(payload["data"]["cancelled_jobs"], 1)
        self.assertEqual(called, [manager])
        final = JobManagerContractTests._wait_for_job(manager, submitted["job_id"])
        self.assertEqual(final["status"], "cancelled")

    def test_extracted_page_assets_are_served(self) -> None:
        assets = (
            "/static/css/fluorescence_gif.css",
            "/static/js/pages/fluorescence_gif_files.js",
            "/static/js/pages/fluorescence_roi_profiles.js",
        )
        for route in assets:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 100)
                response.close()

    def test_core_js_exposes_command_palette_and_file_list_filter_contracts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js_root = root / "web_static" / "js"
        css_root = root / "web_static" / "style"
        palette_js = (js_root / "dp_palette.js").read_text(encoding="utf-8")
        api_js = (js_root / "dp_api.js").read_text(encoding="utf-8")
        events_js = (js_root / "dp_events.js").read_text(encoding="utf-8")
        core_js = (js_root / "dp_core.js").read_text(encoding="utf-8")
        keyboard_js = (js_root / "dp_keyboard.js").read_text(encoding="utf-8")
        dom_js = (js_root / "dp_dom.js").read_text(encoding="utf-8")
        params_js = (js_root / "dp_params.js").read_text(encoding="utf-8")
        reset_css = (css_root / "_reset.css").read_text(encoding="utf-8")

        self.assertIn("function openCommandPalette()", palette_js)
        self.assertIn("function closeCommandPalette()", palette_js)
        self.assertIn("metaKey || ev.ctrlKey", keyboard_js)
        self.assertIn("ev.key.toLowerCase() === 'k'", keyboard_js)
        self.assertIn("function filterFileList(listId)", dom_js)
        self.assertIn("function installFileListFilters()", dom_js)
        self.assertIn("function dpApplyParamGroups(selectId, attr)", params_js)
        self.assertIn("function dpApplyToggleGroups(controlId, attr)", params_js)
        self.assertIn("'logoutServer'", core_js)
        self.assertIn("'saveGenericFileProfile'", core_js)
        self.assertIn("window.DP.page[name] = window[name]", core_js)
        self.assertIn("load failed", api_js)
        self.assertIn("const DP_DELEGATED_EVENT_TYPES = ['click', 'change', 'input'];", events_js)
        self.assertNotIn("Function(", events_js)
        self.assertNotIn("eval(", events_js)
        self.assertNotIn("_envelope", api_js)
        self.assertNotIn("Object.assign({}, data, payload", api_js)
        self.assertIn("[hidden] { display: none !important; }", reset_css)
        self.assertNotIn("btn.click();\n        btn.click();", keyboard_js)

    def test_settings_modal_uses_tabs_instead_of_one_long_panel(self) -> None:
        root = Path(__file__).resolve().parents[1]
        modal = (root / "web_templates" / "partials" / "preferences_modal.html").read_text(
            encoding="utf-8"
        )
        settings_js = (root / "web_static" / "js" / "dp_settings_modal.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('data-prefs-tab="defaults"', modal)
        self.assertIn('data-prefs-tab="history"', modal)
        self.assertIn('data-prefs-tab-panel="jobs"', modal)
        self.assertIn('data-prefs-tab-panel="json"', modal)
        self.assertIn("function openPrefsTab(tab)", settings_js)

    def test_fluorescence_split_routes_handle_small_tiff(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"fluorescence optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_fl_test_") as tmp:
            source = Path(tmp) / "tiny_stack.tif"
            arr = np.arange(2 * 12 * 14, dtype=np.uint16).reshape(2, 12, 14)
            tifffile.imwrite(source, arr, photometric="minisblack", metadata={"axes": "ZYX"})

            info = self.client.post("/api/fluorescence/info", json={"path": str(source)})
            info_payload = info.get_json()
            self.assertEqual(info.status_code, 200)
            self.assertTrue(info_payload["ok"])
            self.assertEqual(info_payload["data"]["n_frames"], 2)

            preview = self.client.post(
                "/api/fluorescence/preview_frame",
                json={"path": str(source), "frame": 1, "lut": "Gray"},
            )
            preview_payload = preview.get_json()
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertTrue(preview_payload["data"]["img"])

            roi = self.client.post(
                "/api/fluorescence/roi/load_stack",
                json={"stack_path": str(source), "frame": 0, "lut": "Gray"},
            )
            roi_payload = roi.get_json()
            self.assertEqual(roi.status_code, 200)
            self.assertTrue(roi_payload["ok"])
            self.assertEqual(roi_payload["data"]["n_frames"], 2)

    def test_fluorescence_3d_rotation_gif_and_distribution_routes(self) -> None:
        try:
            import numpy as np
            import tifffile
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"fluorescence optional dependency missing: {exc}")

        with tempfile.TemporaryDirectory(prefix="dataprocess_fl_3d_test_") as tmp:
            source = Path(tmp) / "tiny_3d_stack.tif"
            arr = np.zeros((4, 16, 18), dtype=np.uint16)
            for z in range(arr.shape[0]):
                arr[z, 4 + z : 8 + z, 5 + z : 9 + z] = 100 + z * 50
            tifffile.imwrite(source, arr, photometric="minisblack", metadata={"axes": "ZYX"})

            preview = self.client.post(
                "/api/fluorescence/3d/rotation_gif_preview",
                json={
                    "path": str(source),
                    "channel_mode": "current",
                    "rotation_axis": "0.2x+0.8y",
                    "gif_frames": 8,
                    "gif_size": 280,
                    "gif_points": 1000,
                    "max_points": 1000,
                    "max_xy": 64,
                    "max_z": 4,
                    "threshold_percentile": 80,
                    "show_scale_bar": True,
                    "scale_bar_um": 5,
                },
            )
            preview_payload = preview.get_json()
            preview_data = _api_data(preview_payload)
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview_payload["ok"])
            self.assertTrue(preview_data["gif_b64"])
            self.assertIn("x", preview_data["axis"])
            self.assertIn("y", preview_data["axis"])

            volume = self.client.post(
                "/api/fluorescence/3d/volume",
                json={
                    "path": str(source),
                    "channel_mode": "current",
                    "max_points": 4000,
                    "max_xy": 64,
                    "max_z": 4,
                    "threshold_percentile": 80,
                    "interlayer_level": "high",
                    "density_mode": "low",
                    "density_radius_um": 3,
                    "density_min_neighbors": 2,
                },
            )
            volume_payload = volume.get_json()
            volume_data = _api_data(volume_payload)
            self.assertEqual(volume.status_code, 200)
            self.assertTrue(volume_payload["ok"])
            render = volume_data["volume"]["render"]
            self.assertGreater(render["n_points"], 0)
            self.assertEqual(render["interlayer_level"], "high")
            self.assertEqual(render["interlayer_steps"], 3)
            self.assertEqual(render["density_filter"]["mode"], "low")

            distribution = self.client.post(
                "/api/fluorescence/3d/intensity_distribution",
                json={
                    "path": str(source),
                    "distribution_channel": 0,
                    "distribution_axis": "z",
                    "distribution_metric": "mean",
                    "output_name": "tiny",
                },
            )
            distribution_payload = distribution.get_json()
            distribution_data = _api_data(distribution_payload)
            self.assertEqual(distribution.status_code, 200)
            self.assertTrue(distribution_payload["ok"])
            self.assertEqual(len(distribution_data["rows"]), 4)
            self.assertTrue(distribution_data["plot"])
            self.assertTrue(Path(distribution_data["csv_path"]).exists())

    def test_csv_export_and_job_contracts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_web_test_") as tmp:
            source = Path(tmp) / "trace.csv"
            source.write_text("time,value\n0,1\n1,2\n", encoding="utf-8")

            direct = self.client.get(
                "/api/csv/export_csv",
                query_string={"path": str(source), "mode": "save"},
            )
            direct_payload = direct.get_json()
            self.assertEqual(direct.status_code, 405)
            self.assertFalse(direct_payload["ok"])
            self.assertIn("requires POST", direct_payload["error"])

            plot_save = self.client.get(
                "/api/csv/export",
                query_string={
                    "path": str(source),
                    "x_col": "time",
                    "y_col": "value",
                    "mode": "save",
                },
            )
            plot_save_payload = plot_save.get_json()
            self.assertEqual(plot_save.status_code, 405)
            self.assertFalse(plot_save_payload["ok"])
            self.assertIn("requires POST", plot_save_payload["error"])

            abf_save = self.client.get(
                "/api/abf/export",
                query_string={"path": str(Path(tmp) / "missing.abf"), "mode": "save"},
            )
            abf_save_payload = abf_save.get_json()
            self.assertEqual(abf_save.status_code, 405)
            self.assertFalse(abf_save_payload["ok"])
            self.assertIn("requires POST", abf_save_payload["error"])

            emg_processing_save = self.client.get(
                "/api/emg/analysis/export_processing",
                query_string={
                    "path": str(Path(tmp) / "missing.rhd"),
                    "channel": "A-000",
                    "mode": "save",
                },
            )
            emg_processing_payload = emg_processing_save.get_json()
            self.assertEqual(emg_processing_save.status_code, 405)
            self.assertFalse(emg_processing_payload["ok"])
            self.assertIn("requires POST", emg_processing_payload["error"])

            download = self.client.get("/api/csv/export_csv", query_string={"path": str(source)})
            self.assertEqual(download.status_code, 200)
            self.assertIn("filename=", download.headers["Content-Disposition"])
            self.assertIn("filename*=", download.headers["Content-Disposition"])

            started = self.client.post("/api/csv/export_csv_job", json={"path": str(source)})
            started_payload = started.get_json()
            started_data = _api_data(started_payload)
            self.assertTrue(started_payload["ok"])

            job = self._wait_for_api_job(started_data["job_id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(Path(job["outputs"][0]["path"]).exists())
            self.assertEqual(job["data"]["saved_path"], job["outputs"][0]["path"])
            self.assertEqual(job["outputs"][0]["role"], "full_csv")

    def test_job_routes_do_not_wrap_flask_routes(self) -> None:
        web_api = Path(__file__).resolve().parents[1] / "web_api"
        offenders = []
        for source in web_api.glob("*.py"):
            text = source.read_text(encoding="utf-8")
            if "submit_flask_route_job" in text or "test_request_context" in text:
                offenders.append(source.name)
        self.assertEqual([], offenders)

    def test_web_app_keeps_page_and_system_routes_out_of_composition_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        web_app = (root / "web_app.py").read_text(encoding="utf-8")
        self.assertNotIn('@app.route("/")', web_app)
        self.assertNotIn("/api/system/select_folder", web_app)
        self.assertIn("register_page_routes", web_app)
        self.assertIn("register_system_routes", web_app)

    def test_run_history_package_job_uses_service_task_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_run_history_test_") as tmp:
            recorded = self.client.post(
                "/api/run_history/record",
                json={
                    "project_root": tmp,
                    "view": "test_view",
                    "title": "Test Run",
                    "status": "ok",
                    "parameters": {"alpha": 1},
                    "input_files": [],
                    "outputs": [],
                },
            )
            recorded_payload = recorded.get_json()
            recorded_data = _api_data(recorded_payload)
            self.assertEqual(recorded.status_code, 200)
            self.assertTrue(recorded_payload["ok"])

            started = self.client.post(
                "/api/run_history/package_job",
                json={"manifest_path": recorded_data["manifest_path"]},
            )
            started_payload = started.get_json()
            started_data = _api_data(started_payload)
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started_payload["ok"])

            job = self._wait_for_api_job(started_data["job_id"])
            self.assertEqual(job["status"], "succeeded")
            self.assertTrue(Path(job["data"]["package_path"]).exists())

    def test_openapi_json_is_available(self) -> None:
        response = self.client.get("/api/openapi.json")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("PickerRequest", payload["components"]["schemas"])
        for schema_name in [
            "AbfBatchProcessRequest",
            "AbfExportPeaksRequest",
            "AbfExportRequest",
            "AbfPlotRequest",
            "CsvExportRequest",
            "CsvMergeRequest",
            "EchemPhotocurrentDetectRequest",
            "EchemPhotovoltageDetectRequest",
            "EmgPeakSelectionDetectRequest",
            "EmgPeakSelectionGroupedExportRequest",
            "FigureRunRequest",
            "Fluorescence3dDistributionRequest",
            "Fluorescence3dRotationGifRequest",
            "Fluorescence3dVolumeRequest",
            "FluorescenceGifMergeRequest",
            "FluorescenceGifRenderRequest",
            "FluorescenceGifRoiAnalyzeRequest",
            "FluorescenceGifRoiKymographRequest",
            "FluorescenceRoiAnalyzeSequenceRequest",
            "FluorescenceRoiExportSequenceGifRequest",
            "FluorescenceRoiExportSequenceRequest",
            "FluorescenceRoiLoadStackRequest",
            "FluorescenceStackExportBatchRequest",
            "FluorescenceStackExportRequest",
            "HistologyDataProjectAnalyzeRoisRequest",
            "HistologyDataProjectImagePreviewRequest",
            "HistologyDataProjectImageRegionPreviewRequest",
            "HistologyDataProjectLoadRequest",
            "HistologyDataProjectRenameEntryRequest",
            "HistologyDataProjectRoiDebugRequest",
            "HistologyDataProjectSaveRoisRequest",
            "HistologyFileAnalyzeRoisRequest",
            "HistologyFileImagePreviewRequest",
            "HistologyFileImageRegionPreviewRequest",
            "HistologyLabelPreviewRequest",
            "HistologyTiffProjectCreateRequest",
            "HistologyTiffProjectScanRequest",
            "LifExportManifestRequest",
            "LifExportTiffBatchRequest",
            "LifExportTiffRequest",
            "LifExportVolume3dRequest",
            "LifPreviewRequest",
            "LineshapePlotRequest",
            "TelemetryEventRequest",
            "PreferencesSaveRequest",
            "FileProfileSaveRequest",
            "EmgAnalysisExportAllRequest",
            "EmgAnalysisExportQueueRequest",
            "EmgAnalysisProcessingRequest",
            "EmgAnalysisRenameApplyRequest",
            "EmgAnalysisRenamePreviewRequest",
            "EmgAnalysisViewRequest",
            "RunPackageRequest",
            "OpenFolderRequest",
        ]:
            self.assertIn(schema_name, payload["components"]["schemas"])

        request_refs = {
            "/api/telemetry/event": "#/components/schemas/TelemetryEventRequest",
            "/api/csv/export_job": "#/components/schemas/CsvExportRequest",
            "/api/abf_batch/process_job": "#/components/schemas/AbfBatchProcessRequest",
            "/api/abf/plot": "#/components/schemas/AbfPlotRequest",
            "/api/abf/export_job": "#/components/schemas/AbfExportRequest",
            "/api/abf/export_peaks_job": "#/components/schemas/AbfExportPeaksRequest",
            "/api/echem/photocurrent/detect": "#/components/schemas/EchemPhotocurrentDetectRequest",
            "/api/echem/photovoltage/detect": "#/components/schemas/EchemPhotovoltageDetectRequest",
            "/api/echem/lineshape/plot": "#/components/schemas/LineshapePlotRequest",
            "/api/echem/lineshape/trace_data": "#/components/schemas/LineshapePlotRequest",
            "/api/emg/peak-selection/detect": "#/components/schemas/EmgPeakSelectionDetectRequest",
            "/api/emg/peak-selection/export_job": (
                "#/components/schemas/EmgPeakSelectionGroupedExportRequest"
            ),
            "/api/figure/run_job": "#/components/schemas/FigureRunRequest",
            "/api/fluorescence/make_gif_job": "#/components/schemas/FluorescenceGifRenderRequest",
            "/api/fluorescence/merge_gif_job": "#/components/schemas/FluorescenceGifMergeRequest",
            "/api/fluorescence/gif_roi/analyze_job": "#/components/schemas/FluorescenceGifRoiAnalyzeRequest",
            "/api/fluorescence/gif_roi/kymograph_job": "#/components/schemas/FluorescenceGifRoiKymographRequest",
            "/api/fluorescence/roi/analyze_sequence": "#/components/schemas/FluorescenceRoiAnalyzeSequenceRequest",
            "/api/fluorescence/roi/export_sequence_job": "#/components/schemas/FluorescenceRoiExportSequenceRequest",
            "/api/fluorescence/roi/export_sequence_gif_job": "#/components/schemas/FluorescenceRoiExportSequenceGifRequest",
            "/api/fluorescence/roi/load_stack": "#/components/schemas/FluorescenceRoiLoadStackRequest",
            "/api/fluorescence/stack_export_job": "#/components/schemas/FluorescenceStackExportRequest",
            "/api/fluorescence/stack_export_batch_job": "#/components/schemas/FluorescenceStackExportBatchRequest",
            "/api/fluorescence/3d/volume": "#/components/schemas/Fluorescence3dVolumeRequest",
            "/api/fluorescence/3d/export_volume_job": "#/components/schemas/Fluorescence3dVolumeRequest",
            "/api/fluorescence/3d/rotation_gif_preview": "#/components/schemas/Fluorescence3dRotationGifRequest",
            "/api/fluorescence/3d/export_rotation_gif_job": "#/components/schemas/Fluorescence3dRotationGifRequest",
            "/api/fluorescence/3d/intensity_distribution": "#/components/schemas/Fluorescence3dDistributionRequest",
            "/api/histology/project/scan_tiff": "#/components/schemas/HistologyTiffProjectScanRequest",
            "/api/histology/project/create_from_tiff": "#/components/schemas/HistologyTiffProjectCreateRequest",
            "/api/histology/project/load": "#/components/schemas/HistologyDataProjectLoadRequest",
            "/api/histology/project/rename_entry": "#/components/schemas/HistologyDataProjectRenameEntryRequest",
            "/api/histology/project/image_preview": "#/components/schemas/HistologyDataProjectImagePreviewRequest",
            "/api/histology/project/analysis/save_rois": "#/components/schemas/HistologyDataProjectSaveRoisRequest",
            "/api/histology/project/analysis/run_job": "#/components/schemas/HistologyDataProjectAnalyzeRoisRequest",
            "/api/histology/project/analysis/debug_roi": "#/components/schemas/HistologyDataProjectRoiDebugRequest",
            "/api/histology/file/image_preview": "#/components/schemas/HistologyFileImagePreviewRequest",
            "/api/histology/project/image_region_preview": "#/components/schemas/HistologyDataProjectImageRegionPreviewRequest",
            "/api/histology/file/image_region_preview": "#/components/schemas/HistologyFileImageRegionPreviewRequest",
            "/api/histology/label_preview": "#/components/schemas/HistologyLabelPreviewRequest",
            "/api/histology/file/analysis/run_job": "#/components/schemas/HistologyFileAnalyzeRoisRequest",
            "/api/emg/analysis/plot": "#/components/schemas/EmgAnalysisViewRequest",
            "/api/emg/analysis/process": "#/components/schemas/EmgAnalysisProcessingRequest",
            "/api/emg/analysis/export_channel_job": "#/components/schemas/EmgAnalysisExportChannelRequest",
            "/api/emg/analysis/export_processing_job": "#/components/schemas/EmgAnalysisProcessingRequest",
            "/api/emg/analysis/export_all_job": "#/components/schemas/EmgAnalysisExportAllRequest",
            "/api/emg/analysis/export_queue_job": "#/components/schemas/EmgAnalysisExportQueueRequest",
            "/api/emg/analysis/rename/preview": "#/components/schemas/EmgAnalysisRenamePreviewRequest",
            "/api/emg/analysis/rename/apply_job": "#/components/schemas/EmgAnalysisRenameApplyRequest",
            "/api/fluorescence/lif/preview": "#/components/schemas/LifPreviewRequest",
            "/api/fluorescence/lif/export_manifest": "#/components/schemas/LifExportManifestRequest",
            "/api/fluorescence/lif/export_tiff_job": "#/components/schemas/LifExportTiffRequest",
            "/api/fluorescence/lif/export_tiff_batch_job": "#/components/schemas/LifExportTiffBatchRequest",
            "/api/fluorescence/lif/export_volume3d_job": "#/components/schemas/LifExportVolume3dRequest",
            "/api/preferences/view_save": "#/components/schemas/ViewPreferencesSaveRequest",
            "/api/file_profiles/save": "#/components/schemas/FileProfileSaveRequest",
            "/api/run_history/package_job": "#/components/schemas/RunPackageRequest",
            "/api/system/open_folder": "#/components/schemas/OpenFolderRequest",
        }
        for path, expected_ref in request_refs.items():
            operation = payload["paths"][path]["post"]
            schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            self.assertEqual(schema_ref, expected_ref)
        for removed_path in [
            "/api/histology/ets_project",
            "/api/histology/ets_image_preview",
            "/api/histology/ets_analysis/save_rois",
            "/api/histology/ets_analysis/run",
            "/api/histology/ets_analysis/run_job",
            "/api/histology/browse",
            "/api/histology/preview",
            "/api/histology/rename",
            "/api/histology/rename_job",
            "/api/histology/project/add_paths",
            "/api/histology/analysis/save_rois",
            "/api/histology/analysis/run",
            "/api/histology/analysis/run_job",
        ]:
            self.assertNotIn(removed_path, payload["paths"])

    def test_schema_validation_returns_422(self) -> None:
        response = self.client.post("/api/jobs/list", json={"limit": 9999})
        payload = response.get_json()

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid request payload")

    def test_migrated_preference_schema_validation_returns_422(self) -> None:
        response = self.client.post("/api/preferences/view_get", json={})
        payload = response.get_json()

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid request payload")

    def test_preference_file_migrates_out_of_root(self) -> None:
        from web_api.preferences import preferences_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = root / "web_gui_settings.json"
            legacy_path.write_text('{"version": 1, "global": {}, "views": {}}\n', encoding="utf-8")

            path = preferences_path(root)

            self.assertEqual(path, root / ".dataprocess_cache" / "web_gui_settings.json")
            self.assertFalse(legacy_path.exists())
            self.assertTrue(path.exists())

    def test_migrated_csv_schema_validation_returns_422(self) -> None:
        response = self.client.post("/api/csv/columns", json={})
        payload = response.get_json()

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Invalid request payload")

    def test_telemetry_is_disabled_by_default(self) -> None:
        with mock.patch("web_api.telemetry._telemetry_enabled", return_value=False):
            response = self.client.post(
                "/api/telemetry/event",
                json={"event": "page_open", "view": "index"},
            )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["enabled"])
        self.assertFalse(payload["data"]["recorded"])

    def _wait_for_api_job(self, job_id: str) -> dict:
        for _ in range(80):
            response = self.client.post("/api/jobs/get", json={"job_id": job_id})
            job = _api_data(response.get_json())["job"]
            if job.get("status") in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for API job {job_id}")


if __name__ == "__main__":
    unittest.main()
