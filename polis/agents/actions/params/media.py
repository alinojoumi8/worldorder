from __future__ import annotations

from typing import Any

from polis.agents.actions.params.base import ActionParams, AgentId, ShortText


class PostParams(ActionParams):
    text: ShortText
    media_urls: tuple[str, ...] = ()
    topic: str | None = None
    stance_proposition: str | None = None
    stance_value: float | None = None
    claims: tuple[dict[str, Any], ...] = ()
    in_reply_to: str | None = None


class RepostParams(ActionParams):
    post_id: str | None = None
    text: ShortText | None = None
    repost_of: str | None = None
    comment: ShortText | None = None


class LikeParams(ActionParams):
    post_id: str


class CommentParams(ActionParams):
    post_id: str
    text: ShortText
    claims: tuple[dict[str, Any], ...] = ()


class FollowParams(ActionParams):
    target_id: AgentId | None = None
    followee_id: AgentId | None = None


class UnfollowParams(ActionParams):
    target_id: AgentId | None = None
    followee_id: AgentId | None = None


class PublishArticleParams(ActionParams):
    outlet_id: str
    headline: str
    body: str


class RetractParams(ActionParams):
    article_id: str
    reason: str | None = None
