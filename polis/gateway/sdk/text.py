"""Safe client-side representation of citizen-authored text."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InWorldText:
    text: str
    channel: str
    source_ref: str
    author_id: str
    tick: int
    trust_hint: float
    content_is_untrusted: bool = True

    def __str__(self) -> str:
        author = json.dumps(self.author_id, ensure_ascii=True)[1:-1]
        quoted = json.dumps(self.text, ensure_ascii=True)
        return f"[from {author}, untrusted] {quoted}"


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def decode_untrusted(value: Any) -> Any:
    """Recursively turn typed wire envelopes into non-accidental text objects."""

    if isinstance(value, Mapping):
        if value.get("kind") == "in_world_text":
            return InWorldText(
                text=str(value.get("text", "")),
                channel=str(value.get("channel", "")),
                source_ref=str(value.get("source_ref", "")),
                author_id=str(value.get("author_id", "unknown")),
                tick=int(_number(value.get("tick"), 0)),
                trust_hint=_number(value.get("trust_hint"), 0.0),
            )
        return {str(key): decode_untrusted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_untrusted(item) for item in value]
    return value
