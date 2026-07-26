from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from polis.config.canon import canonical_bytes, canonical_json, round6, round_floats, sha256_hex

__all__ = [
    "canonical_bytes",
    "canonical_json",
    "det_id",
    "det_uuid",
    "round6",
    "round_floats",
    "sha256_hex",
    "stable",
    "stable_dict",
]


def stable[T](items: Iterable[T], *, key: Callable[[T], Any]) -> list[T]:
    return sorted(items, key=key)


def stable_dict[K, V](value: Mapping[K, V]) -> list[tuple[K, V]]:
    return sorted(value.items(), key=lambda item: str(item[0]))


def det_uuid(namespace: str, *parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, "|".join((namespace, *(str(part) for part in parts))))


def det_id(prefix: str, namespace: str, *parts: object) -> str:
    return f"{prefix}_{det_uuid(namespace, *parts).hex[:16]}"
