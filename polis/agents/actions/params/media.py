from __future__ import annotations

from polis.agents.actions.params.base import ActionParams, AgentId, ShortText


class PostParams(ActionParams):
    text: ShortText
    media_urls: tuple[str, ...] = ()


class RepostParams(ActionParams):
    post_id: str
    text: ShortText | None = None


class LikeParams(ActionParams):
    post_id: str


class CommentParams(ActionParams):
    post_id: str
    text: ShortText


class FollowParams(ActionParams):
    target_id: AgentId


class UnfollowParams(ActionParams):
    target_id: AgentId


class PublishArticleParams(ActionParams):
    outlet_id: str
    headline: str
    body: str


class RetractParams(ActionParams):
    article_id: str
    reason: str | None = None
