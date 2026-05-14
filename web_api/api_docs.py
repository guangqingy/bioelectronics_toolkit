from __future__ import annotations

from flask import Response, jsonify, redirect
from pydantic import BaseModel

from .request_validation import iter_request_models, request_schema_for_endpoint


def _openapi_path(rule: str) -> str:
    return rule.replace("<", "{").replace(">", "}")


def _schema_components() -> dict[str, dict]:
    models: list[type[BaseModel]] = iter_request_models()
    return {model.__name__: model.model_json_schema() for model in models}


def build_openapi_spec(app) -> dict:
    paths: dict[str, dict] = {}
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: item.rule):
        if not rule.rule.startswith("/api/"):
            continue
        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
        if not methods:
            continue
        item = paths.setdefault(_openapi_path(rule.rule), {})
        for method in methods:
            operation = {
                "operationId": rule.endpoint,
                "responses": {
                    "200": {"description": "DataProcess API envelope"},
                    "400": {"description": "Request error"},
                    "422": {"description": "Validation error"},
                    "500": {"description": "Internal error"},
                },
            }
            schema = request_schema_for_endpoint(rule.endpoint)
            if schema is not None and method.upper() in {"POST", "PUT", "PATCH"}:
                operation["requestBody"] = {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{schema.__name__}"}
                        }
                    },
                }
            item[method.lower()] = operation

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "bioelectronics_toolkit Web API",
            "version": getattr(app, "config", {}).get("APP_VERSION", "0.6.0"),
        },
        "paths": paths,
        "components": {"schemas": _schema_components()},
    }


def _docs_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DataProcess API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({ url: '/api/openapi.json', dom_id: '#swagger-ui' });
  </script>
  <noscript><p>Open <a href="/api/openapi.json">/api/openapi.json</a>.</p></noscript>
</body>
</html>
"""


def register_api_docs_routes(app, _ctx) -> None:
    @app.route("/api/openapi.json")
    def api_openapi_json():
        return jsonify(build_openapi_spec(app))

    @app.route("/docs")
    def api_docs():
        return Response(_docs_html(), mimetype="text/html")

    @app.route("/api/docs")
    def api_docs_alias():
        return redirect("/docs")
