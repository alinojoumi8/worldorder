"""Typed envelopes and conservative heuristics for untrusted in-world text."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Direction = Literal["inbound", "outbound"]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|prompt|rule)s?\b", re.I),
    ),
    (
        "tool_call",
        re.compile(r'["\']?(tool|function|command)["\']?\s*:\s*["\']', re.I),
    ),
    (
        "secret_request",
        re.compile(r"\b(api[_ -]?key|private[_ -]?key|seed phrase|password|token)\b", re.I),
    ),
    (
        "shell_instruction",
        re.compile(r"\b(curl|wget|powershell|cmd\.exe|/bin/(?:sh|bash))\b", re.I),
    ),
)
_SIM_AWARE = re.compile(
    r"\b(simulation|simulated|language model|large language model|artificial intelligence|"
    r"\bAI\b|system prompt|token budget|game engine)\b",
    re.I,
)
_TAG = re.compile(r"<[^>]*>")
_LINK = re.compile(r"(?:https?://|www\.)\S+", re.I)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_CONTROL = re.compile(r"[*_`>#~]")


@dataclass(frozen=True, slots=True)
class InjectionHit:
    pattern_id: str
    sample_hash: str
    direction: Direction
    channel: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_inbound(text: str, *, source_ref: str, channel: str) -> InjectionHit | None:
    for pattern_id, pattern in _PATTERNS:
        if pattern.search(text):
            return InjectionHit(pattern_id, _digest(f"{source_ref}\x00{text}"), "inbound", channel)
    return None


def scan_outbound(text: str, *, channel: str) -> InjectionHit | None:
    for pattern_id, pattern in _PATTERNS:
        if pattern.search(text):
            return InjectionHit(pattern_id, _digest(text), "outbound", channel)
    return None


def sim_aware_score(text: str) -> float:
    matches = _SIM_AWARE.findall(text)
    return min(1.0, len(matches) / 3)


def _plain_text(text: str, *, max_chars: int) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    bounded_text = text[:max_chars]
    without_controls = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in bounded_text
    )
    without_html = _TAG.sub("", without_controls)
    without_markdown_links = _MARKDOWN_LINK.sub(r"\1", without_html)
    without_links = _LINK.sub("[link removed]", without_markdown_links)
    plain = _MARKDOWN_CONTROL.sub("", without_links)
    return " ".join(plain.split())[:max_chars]


def wrap(
    text: str,
    *,
    channel: str,
    source_ref: str,
    author_id: str,
    tick: int,
    trust_hint: float,
    max_chars: int = 1_000,
) -> Mapping[str, object]:
    if not 0 <= trust_hint <= 1:
        raise ValueError("trust_hint must be between zero and one")
    return {
        "kind": "in_world_text",
        "channel": channel,
        "source_ref": source_ref,
        "author_id": author_id,
        "tick": tick,
        "trust_hint": trust_hint,
        "content_is_untrusted": True,
        "text": _plain_text(text, max_chars=max_chars),
    }
