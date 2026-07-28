from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from polis.agents.actions.params.media import ClaimParams, PostParams, RepostParams
from polis.config.settings import SocietySettings
from polis.events.kinds import (
    CASCADE_CLOSED,
    FOLLOW_CREATED,
    FOLLOW_ENDED,
    POST_DELETED,
    POST_ENGAGED,
    POST_PUBLISHED,
    REPOST_MADE,
)
from polis.events.log import EventLog
from polis.events.types import Event, NewEvent
from polis.kernel.clock import Clock
from polis.kernel.det import det_id
from polis.society.graph import SocialGraph

EngagementType = Literal["view", "like", "repost", "comment", "report"]


@dataclass(frozen=True, slots=True)
class Post:
    post_id: str
    author_id: str
    tick: int
    text: str
    topic: str | None
    stance_proposition: str | None
    stance_value: float | None
    in_reply_to: str | None
    repost_of: str | None
    root_post_id: str
    claims: tuple[Mapping[str, Any], ...]
    reach: int


@dataclass(frozen=True, slots=True)
class Engagement:
    engagement_id: str
    post_id: str
    agent_id: str
    tick: int
    type: EngagementType


class PlatformRepository(Protocol):
    def put_post(self, post: Post) -> None: ...

    def post(self, post_id: str) -> Post | None: ...

    def posts(self) -> tuple[Post, ...]: ...

    def delete_post(self, post_id: str) -> None: ...

    def put_follow(self, follower_id: str, followee_id: str, tick: int) -> bool: ...

    def end_follow(self, follower_id: str, followee_id: str, tick: int) -> bool: ...

    def followees(self, agent_id: str) -> frozenset[str]: ...

    def followers(self, agent_id: str) -> frozenset[str]: ...

    def add_engagement(self, engagement: Engagement) -> bool: ...

    def engagements(self, post_id: str | None = None) -> tuple[Engagement, ...]: ...

    def engagements_by_agent_author(
        self,
        agent_id: str,
        author_id: str,
    ) -> tuple[Engagement, ...]: ...


class MemoryPlatformRepository:
    def __init__(self) -> None:
        self._posts: dict[str, Post] = {}
        self._deleted: set[str] = set()
        self._follows: dict[tuple[str, str], tuple[int, int | None]] = {}
        self._engagements_by_post: dict[str, list[Engagement]] = defaultdict(list)
        self._engagement_keys: set[tuple[str, str, int, EngagementType]] = set()
        self._engagements_by_agent_author: dict[
            tuple[str, str],
            list[Engagement],
        ] = defaultdict(list)

    def put_post(self, post: Post) -> None:
        self._posts[post.post_id] = post

    def post(self, post_id: str) -> Post | None:
        if post_id in self._deleted:
            return None
        return self._posts.get(post_id)

    def posts(self) -> tuple[Post, ...]:
        return tuple(
            sorted(
                (post for key, post in self._posts.items() if key not in self._deleted),
                key=lambda post: post.post_id,
            )
        )

    def delete_post(self, post_id: str) -> None:
        self._deleted.add(post_id)

    def put_follow(self, follower_id: str, followee_id: str, tick: int) -> bool:
        key = (follower_id, followee_id)
        current = self._follows.get(key)
        if current is not None and current[1] is None:
            return False
        self._follows[key] = (tick, None)
        return True

    def end_follow(self, follower_id: str, followee_id: str, tick: int) -> bool:
        key = (follower_id, followee_id)
        current = self._follows.get(key)
        if current is None or current[1] is not None:
            return False
        self._follows[key] = (current[0], tick)
        return True

    def followees(self, agent_id: str) -> frozenset[str]:
        return frozenset(
            followee
            for (follower, followee), (_, ended) in self._follows.items()
            if follower == agent_id and ended is None
        )

    def followers(self, agent_id: str) -> frozenset[str]:
        return frozenset(
            follower
            for (follower, followee), (_, ended) in self._follows.items()
            if followee == agent_id and ended is None
        )

    def add_engagement(self, engagement: Engagement) -> bool:
        key = (
            engagement.post_id,
            engagement.agent_id,
            engagement.tick,
            engagement.type,
        )
        if key in self._engagement_keys:
            return False
        self._engagement_keys.add(key)
        self._engagements_by_post[engagement.post_id].append(engagement)
        post = self._posts.get(engagement.post_id)
        if post is not None:
            self._engagements_by_agent_author[(engagement.agent_id, post.author_id)].append(
                engagement
            )
        return True

    def engagements(self, post_id: str | None = None) -> tuple[Engagement, ...]:
        rows = (
            [row for post_rows in self._engagements_by_post.values() for row in post_rows]
            if post_id is None
            else list(self._engagements_by_post.get(post_id, ()))
        )
        return tuple(sorted(rows, key=lambda row: (row.tick, row.agent_id, row.engagement_id)))

    def engagements_by_agent_author(
        self,
        agent_id: str,
        author_id: str,
    ) -> tuple[Engagement, ...]:
        return tuple(
            sorted(
                self._engagements_by_agent_author.get((agent_id, author_id), ()),
                key=lambda row: (row.tick, row.post_id, row.engagement_id),
            )
        )


def _value(params: object, name: str, default: Any = None) -> Any:
    if isinstance(params, Mapping):
        return params.get(name, default)
    return getattr(params, name, default)


class Platform:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        repo: PlatformRepository,
        graph: SocialGraph,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.repo = repo
        self.graph = graph
        self.cfg = cfg
        self.cascades = CascadeTracker(log=log, clock=clock, platform=self, cfg=cfg)
        self._post_ordinal: dict[tuple[int, str], int] = defaultdict(int)
        self._reach: dict[str, set[str]] = defaultdict(set)
        self._impressions: dict[str, int] = defaultdict(int)
        for engagement in self.repo.engagements():
            if engagement.type == "view" and self.repo.post(engagement.post_id) is not None:
                self._reach[engagement.post_id].add(engagement.agent_id)
                self._impressions[engagement.post_id] += 1

    def _emit(
        self,
        kind: int,
        payload: Mapping[str, object],
        tick: int,
        *,
        actor_id: str | None = None,
        subjects: Sequence[str] = (),
        cause_seq: int | None = None,
    ) -> Event:
        return self.log.stage(
            NewEvent(
                kind,
                payload,
                actor_id=actor_id,
                subject_ids=tuple(subjects),
                cause_seq=cause_seq,
            ),
            tick=tick,
            sim_time=self.clock.sim_time_at(tick),
        )

    def _new_post_id(self, author_id: str, tick: int) -> str:
        key = (tick, author_id)
        ordinal = self._post_ordinal[key]
        self._post_ordinal[key] += 1
        return det_id("po", "society.post", author_id, tick, ordinal)

    def publish(
        self,
        author_id: str,
        params: PostParams,
        tick: int,
        cause_seq: int | None,
    ) -> tuple[Post, Sequence[Event]]:
        post_id = self._new_post_id(author_id, tick)
        in_reply_to = _value(params, "in_reply_to")
        parent = self.repo.post(str(in_reply_to)) if in_reply_to else None
        root_id = parent.root_post_id if parent is not None else post_id
        claims = tuple(
            claim.model_dump(mode="json") if isinstance(claim, ClaimParams) else claim
            for claim in _value(params, "claims", ())
        )
        post = Post(
            post_id=post_id,
            author_id=author_id,
            tick=tick,
            text=str(_value(params, "text", "")),
            topic=_value(params, "topic"),
            stance_proposition=_value(params, "stance_proposition"),
            stance_value=_value(params, "stance_value"),
            in_reply_to=in_reply_to,
            repost_of=None,
            root_post_id=root_id,
            claims=claims,
            reach=0,
        )
        self.repo.put_post(post)
        event = self._emit(
            POST_PUBLISHED,
            {
                "post_id": post.post_id,
                "author_id": author_id,
                "text": post.text,
                "topic": post.topic,
                "stance_proposition": post.stance_proposition,
                "stance_value": post.stance_value,
                "in_reply_to": post.in_reply_to,
                "repost_of": None,
                "root_post_id": post.root_post_id,
                "claims": list(post.claims),
                "follower_count_at_post": self.follower_count(author_id),
            },
            tick,
            actor_id=author_id,
            subjects=(author_id,),
            cause_seq=cause_seq,
        )
        self.cascades.note(post, tick)
        return post, (event,)

    def repost(
        self,
        author_id: str,
        params: RepostParams,
        tick: int,
    ) -> tuple[Post, Sequence[Event]]:
        source_id = _value(params, "repost_of") or _value(params, "post_id")
        source = self.repo.post(str(source_id))
        if source is None:
            raise KeyError(f"unknown post: {source_id}")
        post_id = self._new_post_id(author_id, tick)
        comment = _value(params, "comment")
        if comment is None:
            comment = _value(params, "text", "")
        post = Post(
            post_id,
            author_id,
            tick,
            str(comment or ""),
            source.topic,
            source.stance_proposition,
            source.stance_value,
            None,
            source.post_id,
            source.root_post_id,
            (),
            0,
        )
        self.repo.put_post(post)
        depth = self.cascades.depth_for(source.post_id) + 1
        published = self._emit(
            POST_PUBLISHED,
            {
                "post_id": post.post_id,
                "author_id": author_id,
                "text": post.text,
                "topic": post.topic,
                "stance_proposition": post.stance_proposition,
                "stance_value": post.stance_value,
                "in_reply_to": None,
                "repost_of": source.post_id,
                "root_post_id": post.root_post_id,
                "claims": [],
                "follower_count_at_post": self.follower_count(author_id),
            },
            tick,
            actor_id=author_id,
            subjects=(author_id, source.author_id),
        )
        root_post = self.repo.post(post.root_post_id)
        reposted = self._emit(
            REPOST_MADE,
            {
                "post_id": post.post_id,
                "repost_of": source.post_id,
                "root_post_id": post.root_post_id,
                "author_id": author_id,
                "original_author_id": (
                    root_post.author_id if root_post is not None else source.author_id
                ),
                "cascade_depth": depth,
                "comment": post.text or None,
            },
            tick,
            actor_id=author_id,
            subjects=(author_id, source.author_id),
            cause_seq=published.seq,
        )
        engagement = self.engage(author_id, source.post_id, "repost", tick)
        self.cascades.note(post, tick)
        return post, (
            published,
            reposted,
            *((engagement,) if engagement is not None else ()),
        )

    def engage(
        self,
        agent_id: str,
        post_id: str,
        type: EngagementType,
        tick: int,
    ) -> Event | None:
        post = self.repo.post(post_id)
        if post is None:
            return None
        row = Engagement(
            det_id("en", "society.engagement", agent_id, post_id, tick, type),
            post_id,
            agent_id,
            tick,
            type,
        )
        if not self.repo.add_engagement(row):
            return None
        if type == "view":
            self._reach[post_id].add(agent_id)
            self._impressions[post_id] += 1
            if post.reach != len(self._reach[post_id]):
                self.repo.put_post(replace(post, reach=len(self._reach[post_id])))
        return self._emit(
            POST_ENGAGED,
            {
                "post_id": post_id,
                "agent_id": agent_id,
                "type": type,
                "author_id": post.author_id,
            },
            tick,
            actor_id=agent_id,
            subjects=(agent_id, post.author_id),
        )

    def follow(
        self,
        follower_id: str,
        followee_id: str,
        context: str,
        tick: int,
    ) -> Event | None:
        if follower_id == followee_id or not self.repo.put_follow(follower_id, followee_id, tick):
            return None
        return self._emit(
            FOLLOW_CREATED,
            {
                "follower_id": follower_id,
                "followee_id": followee_id,
                "context": context,
            },
            tick,
            actor_id=follower_id,
            subjects=(follower_id, followee_id),
        )

    def unfollow(
        self,
        follower_id: str,
        followee_id: str,
        reason: str,
        tick: int,
    ) -> Event | None:
        if not self.repo.end_follow(follower_id, followee_id, tick):
            return None
        return self._emit(
            FOLLOW_ENDED,
            {
                "follower_id": follower_id,
                "followee_id": followee_id,
                "reason": reason,
            },
            tick,
            actor_id=follower_id,
            subjects=(follower_id, followee_id),
        )

    def followees(self, agent_id: str) -> frozenset[str]:
        return self.repo.followees(agent_id)

    def follower_count(self, agent_id: str) -> int:
        return len(self.repo.followers(agent_id))

    def reach(self, post_id: str) -> int:
        return len(self._reach.get(post_id, ()))

    def impressions(self, post_id: str) -> int:
        return self._impressions.get(post_id, 0)

    def posts_in_window(self, tick: int, window_ticks: int) -> tuple[Post, ...]:
        return tuple(post for post in self.repo.posts() if tick - window_ticks < post.tick <= tick)

    def delete(self, post_id: str, reason: str, tick: int) -> Event:
        post = self.repo.post(post_id)
        if post is None:
            raise KeyError(f"unknown post: {post_id}")
        self.repo.delete_post(post_id)
        self._reach.pop(post_id, None)
        self._impressions.pop(post_id, None)
        return self._emit(
            POST_DELETED,
            {"post_id": post_id, "author_id": post.author_id, "reason": reason},
            tick,
            actor_id=post.author_id,
            subjects=(post.author_id,),
        )


class CascadeTracker:
    def __init__(
        self,
        *,
        log: EventLog,
        clock: Clock,
        platform: Platform,
        cfg: SocietySettings,
    ) -> None:
        self.log = log
        self.clock = clock
        self.platform = platform
        self.cfg = cfg
        self._parents: dict[str, str | None] = {}
        self._roots: dict[str, str] = {}
        self._authors: dict[str, str] = {}
        self._ticks: dict[str, int] = {}
        self._last_tick: dict[str, int] = {}
        self._closed: set[str] = set()

    def note(self, post: Post, tick: int) -> None:
        if post.in_reply_to is not None and post.repost_of is None:
            return
        parent = post.repost_of
        if parent is not None and parent not in self._roots:
            parent = post.root_post_id
        self._parents[post.post_id] = parent
        self._roots[post.post_id] = post.root_post_id
        self._authors[post.post_id] = post.author_id
        self._ticks[post.post_id] = post.tick
        self._last_tick[post.root_post_id] = tick

    def depth_for(self, post_id: str) -> int:
        depth = 0
        current = self._parents.get(post_id)
        while current is not None:
            depth += 1
            current = self._parents.get(current)
        return depth

    def close_due(self, tick: int) -> Sequence[Event]:
        events: list[Event] = []
        for root_id, last_tick in sorted(self._last_tick.items()):
            if root_id in self._closed or tick - last_tick < self.cfg.cascade_idle_ticks:
                continue
            nodes = sorted(node for node, root in self._roots.items() if root == root_id)
            children: dict[str, list[str]] = defaultdict(list)
            for node in nodes:
                parent = self._parents[node]
                if parent is not None:
                    children[parent].append(node)
            depth = max((self.depth_for(node) for node in nodes), default=0)
            breadth = max((len(value) for value in children.values()), default=0)
            views = [
                row
                for node in nodes
                for row in self.platform.repo.engagements(node)
                if row.type == "view"
            ]
            payload = {
                "root_post_id": root_id,
                "size": len(nodes),
                "depth": depth,
                "breadth": breadth,
                "structural_virality": self.structural_virality(root_id),
                "reach": len({row.agent_id for row in views}),
                "impressions": len(views),
                "unique_reposters": len({self._authors[node] for node in nodes if node != root_id}),
                "lifetime_ticks": max((self._ticks[node] for node in nodes), default=tick)
                - min((self._ticks[node] for node in nodes), default=tick),
            }
            events.append(
                self.log.stage(
                    NewEvent(CASCADE_CLOSED, payload),
                    tick=tick,
                    sim_time=self.clock.sim_time_at(tick),
                )
            )
            self._closed.add(root_id)
        return tuple(events)

    def structural_virality(self, root_post_id: str) -> float:
        nodes = sorted(node for node, root in self._roots.items() if root == root_post_id)
        if len(nodes) < 2:
            return 0.0
        adjacent: dict[str, set[str]] = defaultdict(set)
        for node in nodes:
            parent = self._parents[node]
            if parent is not None:
                adjacent[node].add(parent)
                adjacent[parent].add(node)
        total = 0
        pairs = 0
        for index, source in enumerate(nodes):
            distance = {source: 0}
            queue = deque([source])
            while queue:
                current = queue.popleft()
                for neighbour in sorted(adjacent[current]):
                    if neighbour not in distance:
                        distance[neighbour] = distance[current] + 1
                        queue.append(neighbour)
            for target in nodes[index + 1 :]:
                if target in distance:
                    total += distance[target]
                    pairs += 1
        return 0.0 if pairs == 0 else total / pairs


__all__ = [
    "CascadeTracker",
    "Engagement",
    "EngagementType",
    "MemoryPlatformRepository",
    "Platform",
    "PlatformRepository",
    "Post",
]
