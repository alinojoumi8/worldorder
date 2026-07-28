from __future__ import annotations

from polis.agents.actions.params.base import ActionParams, AgentId, ShortText


class SayParams(ActionParams):
    text: ShortText
    to_id: AgentId | None = None


class DirectMessageParams(ActionParams):
    recipient_id: AgentId
    text: ShortText


class BroadcastParams(ActionParams):
    text: ShortText
