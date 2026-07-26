from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

from polis.config.canon import canonical_bytes, sha256_hex
from polis.config.errors import PolisError
from polis.events.kinds import spec


class PayloadSchemaError(PolisError):
    def __init__(self, kind: int, path: str, message: str) -> None:
        super().__init__(f"kind {kind} payload at {path or '<root>'}: {message}")
        self.kind = kind
        self.path = path
        self.message = message


def _walk(value: Any, path: str = "") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PayloadSchemaError(-1, path, "non-finite float")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PayloadSchemaError(-1, path, "mapping keys must be strings")
            _walk(child, f"{path}/{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, child in enumerate(value):
            _walk(child, f"{path}/{index}")
        return
    forbidden = type(value).__name__
    if isinstance(value, (Decimal, set, bytes, UUID)):
        raise PayloadSchemaError(-1, path, f"{forbidden} is not JSON-safe")
    raise PayloadSchemaError(-1, path, f"unsupported JSON value {forbidden}")


def assert_json_safe(payload: Mapping[str, Any]) -> None:
    _walk(payload)


def validate_payload(kind: int, payload: Mapping[str, Any]) -> None:
    try:
        assert_json_safe(payload)
    except PayloadSchemaError as exc:
        raise PayloadSchemaError(kind, exc.path, exc.message) from exc
    errors = sorted(Draft202012Validator(spec(kind).schema).iter_errors(payload), key=str)
    if errors:
        error = errors[0]
        path = "/" + "/".join(str(item) for item in error.absolute_path)
        raise PayloadSchemaError(kind, path, error.message)


def validator_for(kind: int) -> Callable[[Mapping[str, Any]], None]:
    return lambda payload: validate_payload(kind, payload)


def schema_hash(kind: int) -> str:
    return sha256_hex(canonical_bytes(spec(kind).schema))
