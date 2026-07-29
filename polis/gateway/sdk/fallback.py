"""A deterministic last-resort choice for operator-side outages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _level(needs: Mapping[str, Any], key: str) -> float:
    try:
        return float(needs.get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def choose_fallback(observation: Mapping[str, Any]) -> Mapping[str, Any]:
    legal = observation.get("legal_actions", ())
    if not isinstance(legal, Sequence):
        return {"type": "NULL_ACTION", "params": {"reason": "no legal action list"}}
    by_type = {
        str(item.get("type")): item
        for item in legal
        if isinstance(item, Mapping) and isinstance(item.get("type"), str)
    }
    self_view = observation.get("self", {})
    needs = self_view.get("needs", {}) if isinstance(self_view, Mapping) else {}
    priorities = []
    if isinstance(needs, Mapping):
        if _level(needs, "hunger") >= 0.7:
            priorities.append("EAT")
        if _level(needs, "fatigue") >= 0.7:
            priorities.append("SLEEP")
    priorities.extend(("WORK", "STUDY", "IDLE", "NULL_ACTION"))
    for action_type in priorities:
        candidate = by_type.get(action_type)
        if candidate is None:
            continue
        options = candidate.get("options", ())
        params: Mapping[str, Any] = {}
        if isinstance(options, Sequence) and options and isinstance(options[0], Mapping):
            params = dict(options[0])
        return {"type": action_type, "params": params}
    return {"type": "NULL_ACTION", "params": {"reason": "fallback"}}
