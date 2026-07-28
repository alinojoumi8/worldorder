from __future__ import annotations

from polis.agents.actions.params.base import ActionParams, AgentId


class BefriendParams(ActionParams):
    target_id: AgentId


class CourtParams(ActionParams):
    target_id: AgentId


class ProposeUnionParams(ActionParams):
    target_id: AgentId


class DissolveUnionParams(ActionParams):
    union_id: str
    reason: str | None = None


class HaveChildIntentParams(ActionParams):
    partner_id: AgentId
