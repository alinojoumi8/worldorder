from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Literal, Protocol

from polis.config.canon import round6
from polis.config.settings import FEED_FEATURE_COUNT, SocietySettings
from polis.events.kinds import FEED_SERVED
from polis.events.log import EventLog
from polis.events.types import NewEvent
from polis.kernel.clock import Clock
from polis.kernel.rng import RngRegistry
from polis.society.graph import SocialGraph
from polis.society.media.platform import Platform, Post
from polis.society.protocols import BeliefChannel

FeedAlgorithm = Literal["chronological", "engagement", "random", "adversarial"]


@dataclass(frozen=True, slots=True)
class Features:
    rec: float
    pop: float
    tie: float
    aff: float
    inf: float
    cong: float
    ext: float
    dis: float
    agr: float
    conf: float
    repeat: float

    def vector(self) -> tuple[float, ...]:
        return (
            1.0,
            self.aff,
            self.tie,
            self.pop,
            self.rec,
            self.inf,
            self.ext,
            self.agr,
            self.dis,
            self.conf,
            self.repeat,
        )


@dataclass(frozen=True, slots=True)
class PostBrief:
    post_id: str
    author_id: str
    tick: int
    text: str
    topic: str | None
    likes: int
    is_repost: bool


@dataclass(slots=True)
class FeedContext:
    platform: Platform
    graph: SocialGraph
    beliefs: BeliefChannel
    model: EngagementModel
    rng: RngRegistry
    clock: Clock
    cfg: SocietySettings
    features: dict[tuple[str, str], Features]
    random_scores: dict[tuple[str, str], float]
    tick: int


class FeedRanker(Protocol):
    @property
    def name(self) -> FeedAlgorithm: ...

    @property
    def uses_out_of_network(self) -> bool: ...

    def pool(self, agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]: ...

    def score(self, agent_id: str, post: Post, f: Features, ctx: FeedContext) -> float: ...


def _window_ticks(ctx: FeedContext) -> int:
    return max(
        1,
        math.ceil(ctx.cfg.feed_window_sim_hours * ctx.clock.profile.ticks_per_sim_day / 24),
    )


def _dedupe(posts: Sequence[Post]) -> tuple[Post, ...]:
    newest: dict[str, Post] = {}
    for post in sorted(posts, key=lambda row: (-row.tick, row.post_id)):
        newest.setdefault(post.root_post_id, post)
    return tuple(sorted(newest.values(), key=lambda row: row.post_id))


def _truncate(posts: Sequence[Post], cap: int) -> tuple[Post, ...]:
    ranked = sorted(
        posts,
        key=lambda post: (
            -post.tick,
            sha256(post.post_id.encode("utf-8")).hexdigest(),
        ),
    )[:cap]
    return tuple(sorted(ranked, key=lambda post: post.post_id))


def _in_network(
    agent_id: str,
    tick: int,
    ctx: FeedContext,
    *,
    exclude_engaged: bool = False,
) -> tuple[Post, ...]:
    followees = ctx.platform.followees(agent_id)
    rows = [
        post
        for post in ctx.platform.posts_in_window(tick, _window_ticks(ctx))
        if post.author_id in followees
        and post.author_id != agent_id
        and (
            not exclude_engaged
            or not any(
                engagement.agent_id == agent_id
                for engagement in ctx.platform.repo.engagements(post.post_id)
            )
        )
    ]
    return _truncate(
        _dedupe(rows),
        ctx.cfg.feed_candidate_cap,
    )


def _citywide(agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
    return _truncate(
        _dedupe(
            [
                post
                for post in ctx.platform.posts_in_window(tick, _window_ticks(ctx))
                if post.author_id != agent_id
            ]
        ),
        ctx.cfg.feed_candidate_cap,
    )


def _mixed_pool(agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
    in_network = list(_in_network(agent_id, tick, ctx))
    in_ids = {post.post_id for post in in_network}
    out = [post for post in _citywide(agent_id, tick, ctx) if post.post_id not in in_ids]
    stream = ctx.rng.get("feed.pool", agent_id, tick)
    stream.shuffle(out)
    remaining = max(0, ctx.cfg.feed_candidate_cap - len(in_network))
    combined = in_network + out[:remaining]
    return tuple(sorted(combined, key=lambda post: post.post_id))


class ChronologicalRanker:
    name: Final[FeedAlgorithm] = "chronological"
    uses_out_of_network: Final = False

    def pool(self, agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
        return _in_network(agent_id, tick, ctx, exclude_engaged=True)

    def score(self, agent_id: str, post: Post, f: Features, ctx: FeedContext) -> float:
        del agent_id, f, ctx
        return float(post.tick)


class EngagementRanker:
    name: Final[FeedAlgorithm] = "engagement"
    uses_out_of_network: Final = True

    def __init__(self, model: EngagementModel) -> None:
        self.model = model

    def pool(self, agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
        return _mixed_pool(agent_id, tick, ctx)

    def score(self, agent_id: str, post: Post, f: Features, ctx: FeedContext) -> float:
        del agent_id, post
        score = self.model.predict(f)
        return score if f.repeat == 0 else score * ctx.cfg.repeat_penalty


class RandomRanker:
    name: Final[FeedAlgorithm] = "random"
    uses_out_of_network: Final = True

    def pool(self, agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
        return _citywide(agent_id, tick, ctx)[: ctx.cfg.feed_candidate_cap]

    def score(self, agent_id: str, post: Post, f: Features, ctx: FeedContext) -> float:
        score = ctx.random_scores[(agent_id, post.post_id)]
        return score if f.repeat == 0 else score * ctx.cfg.repeat_penalty


class AdversarialRanker:
    name: Final[FeedAlgorithm] = "adversarial"
    uses_out_of_network: Final = True

    def __init__(self, beliefs: BeliefChannel, model: EngagementModel) -> None:
        self.beliefs = beliefs
        self.model = model

    def pool(self, agent_id: str, tick: int, ctx: FeedContext) -> tuple[Post, ...]:
        return _mixed_pool(agent_id, tick, ctx)

    def score(self, agent_id: str, post: Post, f: Features, ctx: FeedContext) -> float:
        proposition = post.stance_proposition
        target = post.stance_value
        if proposition is None or target is None:
            return -math.inf
        before = self.beliefs.value(agent_id, proposition)
        mean = self.beliefs.population_mean(proposition)
        delta = self.beliefs.predict_delta(agent_id, proposition, target, post.author_id, "media")
        dispersion_delta = abs(before + delta - mean) - abs(before - mean)
        if dispersion_delta <= 0:
            return -math.inf
        score = float(self.model.predict(f) ** ctx.cfg.feed.adversarial_gamma * dispersion_delta)
        return score if f.repeat == 0 else score * ctx.cfg.repeat_penalty


type RankerFactory = Callable[[BeliefChannel, EngagementModel], FeedRanker]


def _chronological_ranker(beliefs: BeliefChannel, model: EngagementModel) -> FeedRanker:
    del beliefs, model
    return ChronologicalRanker()


def _engagement_ranker(beliefs: BeliefChannel, model: EngagementModel) -> FeedRanker:
    del beliefs
    return EngagementRanker(model)


def _random_ranker(beliefs: BeliefChannel, model: EngagementModel) -> FeedRanker:
    del beliefs, model
    return RandomRanker()


def _adversarial_ranker(beliefs: BeliefChannel, model: EngagementModel) -> FeedRanker:
    return AdversarialRanker(beliefs, model)


RANKERS: Final[Mapping[FeedAlgorithm, RankerFactory]] = {
    "chronological": _chronological_ranker,
    "engagement": _engagement_ranker,
    "random": _random_ranker,
    "adversarial": _adversarial_ranker,
}


class EngagementModel:
    def __init__(
        self,
        beta: Sequence[float] | None = None,
        *,
        eta: float = 0.05,
        passes: int = 20,
        n0: int = 5_000,
        beta_prior: Sequence[float] | None = None,
    ) -> None:
        prior = tuple(beta_prior or (0.0,) * FEED_FEATURE_COUNT)
        if len(prior) != FEED_FEATURE_COUNT:
            raise ValueError(f"beta_prior must contain {FEED_FEATURE_COUNT} entries")
        current = tuple(beta or prior)
        if len(current) != FEED_FEATURE_COUNT:
            raise ValueError(f"beta must contain {FEED_FEATURE_COUNT} entries")
        self.beta = tuple(round6(value) for value in current)
        self.n_observations = 0
        self.eta = eta
        self.passes = passes
        self.n0 = n0
        self.beta_prior = prior

    def predict(self, f: Features) -> float:
        value = sum(
            coefficient * feature
            for coefficient, feature in zip(self.beta, f.vector(), strict=True)
        )
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exp_value = math.exp(value)
        return exp_value / (1.0 + exp_value)

    def refit(
        self,
        impressions: Sequence[tuple[str, str, Features, bool]],
        tick: int,
    ) -> tuple[float, ...]:
        del tick
        rows = sorted(impressions, key=lambda row: (row[0], row[1]))
        if not rows:
            return self.beta
        fitted = list(self.beta)
        for _ in range(self.passes):
            gradient = [0.0] * len(fitted)
            for _, _, features, engaged in rows:
                vector = features.vector()
                raw = sum(
                    coefficient * value for coefficient, value in zip(fitted, vector, strict=True)
                )
                prediction = (
                    1.0 / (1.0 + math.exp(-raw))
                    if raw >= 0
                    else math.exp(raw) / (1.0 + math.exp(raw))
                )
                error = prediction - float(engaged)
                for index, value in enumerate(vector):
                    gradient[index] += error * value
            fitted = [
                round6(coefficient - self.eta * gradient[index] / len(rows))
                for index, coefficient in enumerate(fitted)
            ]
        self.n_observations += len(rows)
        denominator = self.n0 + self.n_observations
        self.beta = tuple(
            round6(
                (self.n0 * self.beta_prior[index] + self.n_observations * fitted[index])
                / denominator
            )
            for index in range(FEED_FEATURE_COUNT)
        )
        return self.beta

    def dump(self) -> Mapping[str, object]:
        return {
            "beta": list(self.beta),
            "n_observations": self.n_observations,
            "eta": self.eta,
            "passes": self.passes,
            "n0": self.n0,
            "beta_prior": list(self.beta_prior),
        }

    def load(self, state: Mapping[str, object]) -> None:
        raw_beta = state["beta"]
        if not isinstance(raw_beta, Sequence):
            raise ValueError("checkpoint beta must be a sequence")
        beta = tuple(float(value) for value in raw_beta)
        if len(beta) != FEED_FEATURE_COUNT:
            raise ValueError(f"checkpoint beta must contain {FEED_FEATURE_COUNT} entries")
        self.beta = tuple(round6(value) for value in beta)
        raw_count = state["n_observations"]
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0:
            raise ValueError("checkpoint n_observations must be a non-negative integer")
        self.n_observations = raw_count
        raw_eta = state["eta"]
        if not isinstance(raw_eta, (int, float)) or isinstance(raw_eta, bool) or raw_eta <= 0:
            raise ValueError("checkpoint eta must be positive")
        raw_passes = state["passes"]
        if not isinstance(raw_passes, int) or isinstance(raw_passes, bool) or raw_passes < 1:
            raise ValueError("checkpoint passes must be a positive integer")
        raw_n0 = state["n0"]
        if not isinstance(raw_n0, int) or isinstance(raw_n0, bool) or raw_n0 < 0:
            raise ValueError("checkpoint n0 must be a non-negative integer")
        raw_prior = state["beta_prior"]
        if not isinstance(raw_prior, Sequence):
            raise ValueError("checkpoint beta_prior must be a sequence")
        prior = tuple(round6(float(value)) for value in raw_prior)
        if len(prior) != FEED_FEATURE_COUNT:
            raise ValueError(f"checkpoint beta_prior must contain {FEED_FEATURE_COUNT} entries")
        self.eta = float(raw_eta)
        self.passes = raw_passes
        self.n0 = raw_n0
        self.beta_prior = prior


class FeedService:
    def __init__(
        self,
        *,
        algorithm: FeedAlgorithm,
        platform: Platform,
        graph: SocialGraph,
        beliefs: BeliefChannel,
        model: EngagementModel,
        rng: RngRegistry,
        clock: Clock,
        log: EventLog,
        cfg: SocietySettings,
    ) -> None:
        self.algorithm = algorithm
        self.platform = platform
        self.graph = graph
        self.beliefs = beliefs
        self.model = model
        self.rng = rng
        self.clock = clock
        self.log = log
        self.cfg = cfg
        self._impression_features: list[tuple[int, str, str, Features]] = []
        self.ranker = RANKERS[algorithm](beliefs, model)

    def _features(self, agent_id: str, post: Post, tick: int) -> Features:
        half_life = max(
            1.0,
            self.cfg.feed.recency_halflife_sim_hours * self.clock.profile.ticks_per_sim_day / 24,
        )
        rec = math.exp(-math.log(2) * max(0, tick - post.tick) / half_life)
        engagement_rows = self.platform.repo.engagements(post.post_id)
        pop = min(
            1.0,
            math.log1p(len(engagement_rows)) / math.log1p(self.cfg.feed.pop_norm),
        )
        tie = self.graph.strength(agent_id, post.author_id)
        past = self.platform.repo.engagements_by_agent_author(agent_id, post.author_id)
        engaged = sum(row.type in {"like", "repost", "comment"} for row in past)
        rate = 0.0 if not past else engaged / len(past)
        affinity = 0.5 * tie + 0.5 * rate
        influence = min(
            1.0,
            math.log1p(self.platform.follower_count(post.author_id))
            / math.log1p(self.cfg.feed.follower_norm),
        )
        proposition = post.stance_proposition
        stance = post.stance_value
        belief = 0.0 if proposition is None else self.beliefs.value(agent_id, proposition)
        congruence = 0.0 if stance is None else min(1.0, max(-1.0, belief * stance))
        confidence = 0.5 if proposition is None else self.beliefs.confidence(agent_id, proposition)
        repeated = any(row.agent_id == agent_id and row.type == "view" for row in engagement_rows)
        return Features(
            rec=rec,
            pop=pop,
            tie=tie,
            aff=affinity,
            inf=influence,
            cong=congruence,
            ext=0.0 if stance is None else abs(stance),
            dis=max(0.0, -congruence),
            agr=max(0.0, congruence),
            conf=confidence,
            repeat=1.0 if repeated else 0.0,
        )

    def _rank(
        self, agent_id: str, tick: int
    ) -> tuple[tuple[Post, ...], tuple[float, ...], tuple[Features, ...], int]:
        if self.cfg.feed_slice <= 0:
            return (), (), (), 0
        context = FeedContext(
            self.platform,
            self.graph,
            self.beliefs,
            self.model,
            self.rng,
            self.clock,
            self.cfg,
            {},
            {},
            tick,
        )
        pool = tuple(
            sorted(
                self.ranker.pool(agent_id, tick, context),
                key=lambda post: post.post_id,
            )
        )
        scored: list[tuple[float, Post]] = []
        if self.algorithm == "random":
            stream = self.rng.get("feed.random", agent_id, tick)
            context.random_scores.update(
                {(agent_id, post.post_id): stream.random() for post in pool}
            )
        for post in pool:
            features = self._features(agent_id, post, tick)
            context.features[(agent_id, post.post_id)] = features
            score = self.ranker.score(agent_id, post, features, context)
            if math.isfinite(score):
                scored.append((score, post))
        if self.algorithm == "chronological":
            scored.sort(key=lambda item: (-item[1].tick, item[1].post_id))
        else:
            tie_rng = self.rng.get("feed.tiebreak", agent_id, tick)
            tie_break = {
                post.post_id: tie_rng.random()
                for _, post in sorted(scored, key=lambda item: item[1].post_id)
            }
            scored.sort(
                key=lambda item: (
                    -item[0],
                    tie_break[item[1].post_id],
                    item[1].post_id,
                )
            )

        followees = self.platform.followees(agent_id)
        out_limit = math.floor(self.cfg.feed_out_of_network_quota * self.cfg.feed_slice)
        chosen: list[tuple[float, Post]] = []
        out_count = 0
        for item in scored:
            is_out = item[1].author_id not in followees
            if (
                self.algorithm in {"engagement", "adversarial"}
                and is_out
                and out_count >= out_limit
            ):
                continue
            chosen.append(item)
            out_count += int(is_out)
            if len(chosen) >= self.cfg.feed_slice:
                break
        posts = tuple(item[1] for item in chosen)
        return (
            posts,
            tuple(item[0] for item in chosen),
            tuple(context.features[(agent_id, post.post_id)] for post in posts),
            len(pool),
        )

    def build(self, agent_id: str, tick: int) -> tuple[tuple[Post, ...], tuple[float, ...]]:
        posts, scores, _, _ = self._rank(agent_id, tick)
        return posts, scores

    def build_all(self, agent_ids: Sequence[str], tick: int) -> Mapping[str, tuple[PostBrief, ...]]:
        result: dict[str, tuple[PostBrief, ...]] = {}
        ordered = tuple(sorted(set(agent_ids)))
        ranked = {agent_id: self._rank(agent_id, tick) for agent_id in ordered}
        for agent_id in ordered:
            posts, scores, features, candidate_pool_size = ranked[agent_id]
            followees = self.platform.followees(agent_id)
            for post, feature in zip(posts, features, strict=True):
                self.platform.engage(agent_id, post.post_id, "view", tick)
                self._impression_features.append((tick, agent_id, post.post_id, feature))
            self.log.stage(
                NewEvent(
                    FEED_SERVED,
                    {
                        "agent_id": agent_id,
                        "algorithm": self.algorithm,
                        "post_ids": [post.post_id for post in posts],
                        "scores": list(scores),
                        "candidate_pool_size": candidate_pool_size,
                        "out_of_network_count": sum(
                            post.author_id not in followees for post in posts
                        ),
                        "cross_cutting_count": sum(feature.dis > 0 for feature in features),
                        "mean_extremity": (
                            0.0
                            if not features
                            else sum(feature.ext for feature in features) / len(features)
                        ),
                    },
                    actor_id=agent_id,
                    subject_ids=(agent_id,),
                ),
                tick=tick,
                sim_time=self.clock.sim_time_at(tick),
            )
            result[agent_id] = tuple(
                PostBrief(
                    post.post_id,
                    post.author_id,
                    post.tick,
                    post.text,
                    post.topic,
                    sum(row.type == "like" for row in self.platform.repo.engagements(post.post_id)),
                    post.repost_of is not None,
                )
                for post in posts
            )
        return result

    def impressions_for_refit(self, sim_day: int) -> Sequence[tuple[str, str, Features, bool]]:
        rows: list[tuple[str, str, Features, bool]] = []
        for tick, agent_id, post_id, feature in self._impression_features:
            if self.clock.sim_day(tick) != sim_day:
                continue
            engaged = any(
                row.agent_id == agent_id
                and row.post_id == post_id
                and row.type in {"like", "repost", "comment"}
                for row in self.platform.repo.engagements(post_id)
            )
            rows.append((agent_id, post_id, feature, engaged))
        self._impression_features = [
            row for row in self._impression_features if self.clock.sim_day(row[0]) > sim_day
        ]
        return tuple(sorted(rows, key=lambda row: (row[0], row[1])))


__all__ = [
    "RANKERS",
    "AdversarialRanker",
    "ChronologicalRanker",
    "EngagementModel",
    "EngagementRanker",
    "Features",
    "FeedAlgorithm",
    "FeedContext",
    "FeedRanker",
    "FeedService",
    "PostBrief",
    "RandomRanker",
]
