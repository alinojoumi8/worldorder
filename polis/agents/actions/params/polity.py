from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import StringConstraints

from polis.agents.actions.params.base import ActionParams, AgentId, Cents


class JoinPartyParams(ActionParams):
    party_id: str


class AnnounceCandidacyParams(ActionParams):
    election_id: str | None = None
    office_id: str | None = None
    party_id: str | None = None
    platform: Mapping[str, float] = {}


class CampaignParams(ActionParams):
    candidacy_id: str
    spend_cents: Cents = 0
    amount_cents: Cents = 0
    channel: Literal["ads", "rally", "canvass"] = "canvass"
    target_id: str | None = None
    place_id: str | None = None


class VoteParams(ActionParams):
    election_id: str
    candidate_id: str | None = None
    candidacy_id: str | None = None
    ranking: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()


class ProposePolicyParams(ActionParams):
    parameter: str | None = None
    proposed_value: Any = None
    rationale: str = ""
    cosigners: tuple[AgentId, ...] = ()
    title: str = ""
    description: str = ""
    platform: Mapping[str, float] = {}


class LobbyParams(ActionParams):
    target_id: str
    policy_id: str
    spend_cents: Cents = 0


class FoundPartyParams(ActionParams):
    name: Annotated[str, StringConstraints(max_length=64)]
    platform: Mapping[str, float]
    founding_member_ids: tuple[AgentId, ...]
