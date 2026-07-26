from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from polis.config.errors import ConfigError


def canonical_json(obj: Any) -> str:
    try:
        return json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"value is not canonical JSON: {exc}") from exc


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def round6(value: float) -> float:
    return round(value, 6)


def round_floats[T](obj: T, dp: int = 6) -> T:
    if isinstance(obj, float):
        return cast(T, round(obj, dp))
    if isinstance(obj, Mapping):
        return cast(T, {key: round_floats(value, dp) for key, value in obj.items()})
    if isinstance(obj, tuple):
        return cast(T, tuple(round_floats(value, dp) for value in obj))
    if isinstance(obj, list):
        return cast(T, [round_floats(value, dp) for value in obj])
    return obj
