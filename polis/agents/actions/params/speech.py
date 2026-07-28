from __future__ import annotations

from typing import Any

from polis.agents.actions.params.base import ActionParams, AgentId, ShortText


class SayParams(ActionParams):
    text: ShortText
    to_id: AgentId | None = None
    addressed_to: tuple[AgentId, ...] = ()
    conversation_id: str | None = None
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: float | None = None
    closing: bool = False
    claims: tuple[dict[str, Any], ...] = ()


class DirectMessageParams(ActionParams):
    recipient_id: AgentId
    text: ShortText
    in_reply_to: str | None = None
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: float | None = None
    claims: tuple[dict[str, Any], ...] = ()


class BroadcastParams(ActionParams):
    text: ShortText
    place_id: str | None = None
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: float | None = None
    claims: tuple[dict[str, Any], ...] = ()
