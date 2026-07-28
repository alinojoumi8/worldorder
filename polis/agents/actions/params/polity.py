from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import StringConstraints

from polis.agents.actions.params.base import ActionParams, AgentId, Cents


class JoinPartyParams(ActionParams):
    party_id: str


class AnnounceCandidacyParams(ActionParams):
    office_id: str


class CampaignParams(ActionParams):
    candidacy_id: str
    spend_cents: Cents = 0


class VoteParams(ActionParams):
    election_id: str
    candidate_id: str


class ProposePolicyParams(ActionParams):
    title: str
    description: str
    platform: Mapping[str, float]


class LobbyParams(ActionParams):
    target_id: str
    policy_id: str
    spend_cents: Cents = 0


class FoundPartyParams(ActionParams):
    name: Annotated[str, StringConstraints(max_length=64)]
    platform: Mapping[str, float]
    founding_member_ids: tuple[AgentId, ...]
