from __future__ import annotations

from typing import Annotated, Any

from pydantic import ConfigDict, Field, model_validator

from polis.agents.actions.params.base import ActionParams, AgentId, ShortText

StanceValue = Annotated[float, Field(ge=-1.0, le=1.0)]


class ClaimReference(ActionParams):
    entity_id: str
    predicate: str
    value: Any
    as_of_tick: int = Field(ge=0)


class ClaimParams(ActionParams):
    claim_id: str
    text: ShortText
    refers_to: ClaimReference
    sourced_to_event_seqs: tuple[int, ...] = ()


class PostParams(ActionParams):
    text: ShortText
    media_urls: tuple[str, ...] = ()
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: StanceValue | None = None
    claims: tuple[ClaimParams, ...] = ()
    in_reply_to: str | None = None


class RepostParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["repost_of"],
                    "properties": {"repost_of": {"type": "string"}},
                },
                {
                    "required": ["post_id"],
                    "properties": {"post_id": {"type": "string"}},
                },
            ]
        }
    )

    post_id: str | None = None
    text: ShortText | None = None
    repost_of: str | None = None
    comment: ShortText | None = None

    @model_validator(mode="after")
    def require_source(self) -> RepostParams:
        if (self.repost_of is None) == (self.post_id is None):
            raise ValueError("repost requires exactly one of repost_of or post_id")
        return self


class LikeParams(ActionParams):
    post_id: str


class CommentParams(ActionParams):
    post_id: str
    text: ShortText
    claims: tuple[ClaimParams, ...] = ()


class FollowParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["followee_id"],
                    "properties": {
                        "followee_id": {
                            "type": "string",
                            "pattern": r"^ag_[a-z0-9_]{1,32}$",
                        }
                    },
                },
                {
                    "required": ["target_id"],
                    "properties": {
                        "target_id": {
                            "type": "string",
                            "pattern": r"^ag_[a-z0-9_]{1,32}$",
                        }
                    },
                },
            ]
        }
    )

    target_id: AgentId | None = None
    followee_id: AgentId | None = None

    @model_validator(mode="after")
    def require_target(self) -> FollowParams:
        if (self.followee_id is None) == (self.target_id is None):
            raise ValueError("follow requires exactly one of followee_id or target_id")
        return self


class UnfollowParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["followee_id"],
                    "properties": {
                        "followee_id": {
                            "type": "string",
                            "pattern": r"^ag_[a-z0-9_]{1,32}$",
                        }
                    },
                },
                {
                    "required": ["target_id"],
                    "properties": {
                        "target_id": {
                            "type": "string",
                            "pattern": r"^ag_[a-z0-9_]{1,32}$",
                        }
                    },
                },
            ]
        }
    )

    target_id: AgentId | None = None
    followee_id: AgentId | None = None

    @model_validator(mode="after")
    def require_target(self) -> UnfollowParams:
        if (self.followee_id is None) == (self.target_id is None):
            raise ValueError("unfollow requires exactly one of followee_id or target_id")
        return self


class PublishArticleParams(ActionParams):
    outlet_id: str
    headline: str
    body: str
    source_event_seqs: tuple[int, ...] = ()
    claims: tuple[ClaimParams, ...] = ()


class RetractParams(ActionParams):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["article_id"],
                    "properties": {"article_id": {"type": "string"}},
                    "not": {"required": ["post_id"]},
                },
                {
                    "required": ["post_id"],
                    "properties": {"post_id": {"type": "string"}},
                    "not": {"required": ["article_id"]},
                },
            ]
        }
    )

    article_id: str | None = None
    post_id: str | None = None
    reason: str | None = None
    correction_text: ShortText | None = None

    @model_validator(mode="after")
    def require_subject(self) -> RetractParams:
        if (self.article_id is None) == (self.post_id is None):
            raise ValueError("retract requires exactly one of article_id or post_id")
        return self
