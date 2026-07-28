from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, StringConstraints, model_validator

from polis.agents.actions.params.base import ActionParams, AgentId, Cents


class JoinPartyParams(ActionParams):
    party_id: str


class AnnounceCandidacyParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["election_id"],
                    "properties": {"election_id": {"type": "string"}},
                },
                {
                    "required": ["office_id"],
                    "properties": {"office_id": {"type": "string"}},
                },
            ]
        }
    )

    election_id: str | None = None
    office_id: str | None = None
    party_id: str | None = None
    platform: Mapping[str, float] = {}

    @model_validator(mode="after")
    def require_election(self) -> AnnounceCandidacyParams:
        if self.election_id is None and self.office_id is None:
            raise ValueError("candidacy requires election_id or office_id")
        return self


class CampaignParams(ActionParams):
    candidacy_id: str
    spend_cents: Cents = 0
    amount_cents: Cents = 0
    channel: Literal["ads", "rally", "canvass"] = "canvass"
    target_id: str | None = None
    place_id: str | None = None


class VoteParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["election_id", "candidate_id"],
                    "properties": {
                        "election_id": {"type": "string"},
                        "candidate_id": {"type": "string"},
                    },
                },
                {
                    "required": ["election_id", "candidacy_id"],
                    "properties": {
                        "election_id": {"type": "string"},
                        "candidacy_id": {"type": "string"},
                    },
                },
                {
                    "required": ["election_id", "ranking"],
                    "properties": {
                        "election_id": {"type": "string"},
                        "ranking": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                },
                {
                    "required": ["election_id", "approvals"],
                    "properties": {
                        "election_id": {"type": "string"},
                        "approvals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                },
            ]
        }
    )

    election_id: str
    candidate_id: str | None = None
    candidacy_id: str | None = None
    ranking: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_selection(self) -> VoteParams:
        if (
            self.candidate_id is None
            and self.candidacy_id is None
            and not self.ranking
            and not self.approvals
        ):
            raise ValueError("vote requires a candidate, ranking, or approval")
        return self


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
