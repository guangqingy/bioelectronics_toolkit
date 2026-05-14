from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from flask import request
from pydantic import BaseModel, ConfigDict, ValidationError

from .response import api_error


class RequestModel(BaseModel):
    """Base class for JSON request payload schemas."""

    model_config = ConfigDict(extra="forbid")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__name__ != "RequestModel":
            _REQUEST_MODELS[cls.__name__] = cls


ModelT = TypeVar("ModelT", bound=RequestModel)
_REQUEST_MODELS: dict[str, type[RequestModel]] = {}
_REQUEST_SCHEMAS_BY_ENDPOINT: dict[str, type[RequestModel]] = {}


def parse_json_payload(model: type[ModelT]) -> ModelT:
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    return model.model_validate(body)


def validation_error_response(exc: ValidationError):
    return api_error("Invalid request payload", 422, data={"details": exc.errors()})


def request_schema(model: type[ModelT]) -> Callable:
    """Attach a request schema to a Flask endpoint for OpenAPI docs."""

    def decorator(func: Callable) -> Callable:
        _REQUEST_SCHEMAS_BY_ENDPOINT[func.__name__] = model
        return func

    return decorator


def iter_request_models() -> list[type[RequestModel]]:
    return [_REQUEST_MODELS[name] for name in sorted(_REQUEST_MODELS)]


def request_schema_for_endpoint(endpoint: str) -> type[RequestModel] | None:
    return _REQUEST_SCHEMAS_BY_ENDPOINT.get(endpoint.rsplit(".", 1)[-1])
