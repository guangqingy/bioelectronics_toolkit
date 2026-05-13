from __future__ import annotations

from typing import TypeVar

from flask import request
from pydantic import BaseModel, ConfigDict, ValidationError

from .response import api_error


class RequestModel(BaseModel):
    """Base class for JSON request payload schemas."""

    model_config = ConfigDict(extra="forbid")


ModelT = TypeVar("ModelT", bound=RequestModel)


def parse_json_payload(model: type[ModelT]) -> ModelT:
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    return model.model_validate(body)


def validation_error_response(exc: ValidationError):
    return api_error("Invalid request payload", 422, data={"details": exc.errors()})
