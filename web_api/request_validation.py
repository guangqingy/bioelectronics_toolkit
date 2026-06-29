from __future__ import annotations

import functools
import traceback
from collections.abc import Callable
from typing import Annotated, Optional, TypeVar

from flask import request
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from .response import api_error


def _blank_to_none(value):
    """Treat empty/whitespace strings from web form fields as 'unset' (None).

    The browser sends "" for cleared numeric inputs; mapping those to None lets
    Pydantic coerce the rest ("1.5" -> 1.5) and validate types at the request
    boundary, so route/service code no longer needs scattered float_or/int_or.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


# Optional numeric request fields that accept blank web-form values as None and
# otherwise coerce/validate to the declared type at the request boundary.
OptFloat = Annotated[Optional[float], BeforeValidator(_blank_to_none)]
OptInt = Annotated[Optional[int], BeforeValidator(_blank_to_none)]


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


def parse_query_params(model: type[ModelT]) -> ModelT:
    return model.model_validate(dict(request.args))


def validation_error_response(exc: ValidationError):
    return api_error("Invalid request payload", 422, data={"details": exc.errors()})


def request_schema(model: type[ModelT]) -> Callable:
    """Attach a request schema to a Flask endpoint for OpenAPI docs."""

    def decorator(func: Callable) -> Callable:
        _REQUEST_SCHEMAS_BY_ENDPOINT[func.__name__] = model
        return func

    return decorator


def api_endpoint(
    model: type[ModelT],
    *,
    source: str = "json",
    dump: bool = True,
) -> Callable:
    """Parse/validate request data and convert common API exceptions to envelopes."""

    parser = parse_query_params if source == "query" else parse_json_payload

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                payload = parser(model)
                body = payload.model_dump() if dump else payload
                return func(body, *args, **kwargs)
            except ValidationError as exc:
                return validation_error_response(exc)
            except ValueError as exc:
                return api_error(str(exc))
            except Exception:
                return api_error(traceback.format_exc())

        _REQUEST_SCHEMAS_BY_ENDPOINT[func.__name__] = model
        return wrapper

    return decorator


def iter_request_models() -> list[type[RequestModel]]:
    return [_REQUEST_MODELS[name] for name in sorted(_REQUEST_MODELS)]


def request_schema_for_endpoint(endpoint: str) -> type[RequestModel] | None:
    return _REQUEST_SCHEMAS_BY_ENDPOINT.get(endpoint.rsplit(".", 1)[-1])
