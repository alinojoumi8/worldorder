from __future__ import annotations

import json

from polis.events.kinds import (
    ARTICLE_DISTRIBUTED,
    ARTICLE_PUBLISHED,
    ARTICLE_RETRACTED,
    BELIEF_DRIFT_APPLIED,
    BELIEF_PRIORS_SET,
    BELIEF_UPDATED,
    CLAIM_CHECKED,
    FOLLOW_CREATED,
    FOLLOW_ENDED,
    OUTLET_CLOSED,
    OUTLET_FOUNDED,
    OUTLET_REVENUE_BOOKED,
    POST_DELETED,
    POST_ENGAGED,
    POST_PUBLISHED,
    TIE_ENDED,
    TIE_FORMED,
    TIE_TYPE_CHANGED,
    TIE_UPDATED,
)
from polis.events.types import Event
from polis.society.beliefs import proposition_spec
from polis.store.projections.base import ProjectionContext, register_projection


class PostsProjection:
    name = "posts_projection"
    tables: tuple[str, ...] = ("posts", "engagements", "post_viewers")
    handles: frozenset[int] = frozenset({POST_PUBLISHED, POST_DELETED, POST_ENGAGED, CLAIM_CHECKED})

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == POST_PUBLISHED:
            await ctx.conn.execute(
                """
                INSERT INTO posts(
                    run_id,post_id,author_id,tick,text,topic,stance_proposition,
                    stance_value,in_reply_to,repost_of,root_post_id,claims,reach
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,0)
                ON CONFLICT(run_id,post_id) DO UPDATE SET
                    text=EXCLUDED.text, topic=EXCLUDED.topic,
                    stance_proposition=EXCLUDED.stance_proposition,
                    stance_value=EXCLUDED.stance_value,
                    in_reply_to=EXCLUDED.in_reply_to,
                    repost_of=EXCLUDED.repost_of,
                    root_post_id=EXCLUDED.root_post_id,
                    claims=EXCLUDED.claims, deleted_tick=NULL
                """,
                (
                    ctx.run_id,
                    payload["post_id"],
                    payload["author_id"],
                    event.tick,
                    payload["text"],
                    payload["topic"],
                    payload["stance_proposition"],
                    payload["stance_value"],
                    payload["in_reply_to"],
                    payload["repost_of"],
                    payload["root_post_id"],
                    json.dumps(payload["claims"], sort_keys=True),
                ),
            )
        elif event.kind == POST_DELETED:
            await ctx.conn.execute(
                "UPDATE posts SET deleted_tick=%s WHERE run_id=%s AND post_id=%s",
                (event.tick, ctx.run_id, payload["post_id"]),
            )
        elif event.kind == POST_ENGAGED:
            await ctx.conn.execute(
                """
                INSERT INTO engagements(
                    run_id,engagement_id,post_id,agent_id,tick,type
                ) VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (
                    ctx.run_id,
                    event.seq,
                    payload["post_id"],
                    payload["agent_id"],
                    event.tick,
                    payload["type"],
                ),
            )
            if payload["type"] == "view":
                await ctx.conn.execute(
                    """
                    WITH inserted AS (
                        INSERT INTO post_viewers(run_id,post_id,agent_id)
                        VALUES(%s,%s,%s)
                        ON CONFLICT DO NOTHING
                        RETURNING 1
                    )
                    UPDATE posts SET reach=reach+1
                    WHERE run_id=%s AND post_id=%s
                      AND EXISTS (SELECT 1 FROM inserted)
                    """,
                    (
                        ctx.run_id,
                        payload["post_id"],
                        payload["agent_id"],
                        ctx.run_id,
                        payload["post_id"],
                    ),
                )
        elif (
            event.kind == CLAIM_CHECKED
            and payload["subject_kind"] == "post"
            and payload["score"] is not None
        ):
            await ctx.conn.execute(
                """
                UPDATE posts SET
                    truthfulness=(
                        COALESCE(truthfulness,0)*truthfulness_n + %s
                    )/(truthfulness_n+1),
                    truthfulness_n=truthfulness_n+1
                WHERE run_id=%s AND post_id=%s
                """,
                (payload["score"], ctx.run_id, payload["subject_id"]),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM post_viewers WHERE run_id=%s", (ctx.run_id,))
        await ctx.conn.execute("DELETE FROM engagements WHERE run_id=%s", (ctx.run_id,))
        await ctx.conn.execute("DELETE FROM posts WHERE run_id=%s", (ctx.run_id,))


class FollowsProjection:
    name = "follows_projection"
    tables: tuple[str, ...] = ("follows",)
    handles: frozenset[int] = frozenset({FOLLOW_CREATED, FOLLOW_ENDED})

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == FOLLOW_CREATED:
            await ctx.conn.execute(
                """
                INSERT INTO follows(
                    run_id,follower_id,followee_id,started_tick,ended_tick,context
                ) VALUES(%s,%s,%s,%s,NULL,%s)
                ON CONFLICT(run_id,follower_id,followee_id) DO UPDATE SET
                    started_tick=EXCLUDED.started_tick, ended_tick=NULL,
                    context=EXCLUDED.context
                """,
                (
                    ctx.run_id,
                    payload["follower_id"],
                    payload["followee_id"],
                    event.tick,
                    payload["context"],
                ),
            )
        elif event.kind == OUTLET_REVENUE_BOOKED:
            await ctx.conn.execute(
                """
                UPDATE follows SET ended_tick=%s
                WHERE run_id=%s AND follower_id=%s AND followee_id=%s
                """,
                (
                    event.tick,
                    ctx.run_id,
                    payload["follower_id"],
                    payload["followee_id"],
                ),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM follows WHERE run_id=%s", (ctx.run_id,))


class RelationshipsProjection:
    name = "relationships_projection"
    tables: tuple[str, ...] = ("relationships",)
    handles: frozenset[int] = frozenset({TIE_FORMED, TIE_UPDATED, TIE_ENDED, TIE_TYPE_CHANGED})

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == TIE_FORMED:
            await ctx.conn.execute(
                """
                INSERT INTO relationships(
                    run_id,a_id,b_id,type,strength,valence,trust,formed_tick,
                    ended_tick,last_interaction_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)
                ON CONFLICT(run_id,a_id,b_id,type) DO UPDATE SET
                    strength=EXCLUDED.strength,valence=EXCLUDED.valence,
                    trust=EXCLUDED.trust,formed_tick=EXCLUDED.formed_tick,
                    ended_tick=NULL,last_interaction_tick=EXCLUDED.last_interaction_tick
                """,
                (
                    ctx.run_id,
                    payload["a_id"],
                    payload["b_id"],
                    payload["type"],
                    payload["strength"],
                    payload["valence"],
                    payload["trust"],
                    event.tick,
                    event.tick,
                ),
            )
        elif event.kind == TIE_UPDATED:
            await ctx.conn.execute(
                """
                UPDATE relationships SET
                    strength=GREATEST(0,LEAST(1,strength+%s)),
                    valence=GREATEST(-1,LEAST(1,valence+%s)),
                    trust=GREATEST(0,LEAST(1,trust+%s)),
                    last_interaction_tick=COALESCE(%s,last_interaction_tick)
                WHERE run_id=%s AND a_id=%s AND b_id=%s AND type=%s
                """,
                (
                    payload["d_strength"],
                    payload["d_valence"],
                    payload["d_trust"],
                    event.tick if payload["drivers"] else None,
                    ctx.run_id,
                    payload["a_id"],
                    payload["b_id"],
                    payload["type"],
                ),
            )
        elif event.kind == TIE_ENDED:
            await ctx.conn.execute(
                """
                UPDATE relationships SET ended_tick=%s,strength=%s
                WHERE run_id=%s AND a_id=%s AND b_id=%s AND type=%s
                """,
                (
                    event.tick,
                    payload["final_strength"],
                    ctx.run_id,
                    payload["a_id"],
                    payload["b_id"],
                    payload["type"],
                ),
            )
        else:
            await ctx.conn.execute(
                """
                UPDATE relationships SET ended_tick=%s
                WHERE run_id=%s AND a_id=%s AND b_id=%s AND type=%s
                """,
                (
                    event.tick,
                    ctx.run_id,
                    payload["a_id"],
                    payload["b_id"],
                    payload["from_type"],
                ),
            )
            await ctx.conn.execute(
                """
                INSERT INTO relationships(
                    run_id,a_id,b_id,type,strength,valence,trust,formed_tick,
                    ended_tick,last_interaction_tick
                )
                SELECT run_id,a_id,b_id,%s,strength,valence,trust,%s,NULL,%s
                FROM relationships
                WHERE run_id=%s AND a_id=%s AND b_id=%s AND type=%s
                ON CONFLICT(run_id,a_id,b_id,type) DO UPDATE SET
                    strength=EXCLUDED.strength,valence=EXCLUDED.valence,
                    trust=EXCLUDED.trust,formed_tick=EXCLUDED.formed_tick,
                    ended_tick=NULL,last_interaction_tick=EXCLUDED.last_interaction_tick
                """,
                (
                    payload["to_type"],
                    event.tick,
                    event.tick,
                    ctx.run_id,
                    payload["a_id"],
                    payload["b_id"],
                    payload["from_type"],
                ),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM relationships WHERE run_id=%s", (ctx.run_id,))


class OutletsProjection:
    name = "outlets_projection"
    tables: tuple[str, ...] = ("outlets",)
    handles: frozenset[int] = frozenset({OUTLET_FOUNDED, OUTLET_REVENUE_BOOKED, OUTLET_CLOSED})

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == OUTLET_FOUNDED:
            await ctx.conn.execute(
                """
                INSERT INTO outlets(
                    run_id,outlet_id,name,firm_id,slant,rigour,reach,founded_tick,closed_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,0,%s,NULL)
                ON CONFLICT(run_id,outlet_id) DO UPDATE SET
                    firm_id=EXCLUDED.firm_id,slant=EXCLUDED.slant,
                    rigour=EXCLUDED.rigour,closed_tick=NULL
                """,
                (
                    ctx.run_id,
                    payload["outlet_id"],
                    payload.get("name", str(payload["outlet_id"])),
                    payload["firm_id"],
                    payload["slant"],
                    payload["rigour"],
                    event.tick,
                ),
            )
        elif event.kind == OUTLET_CLOSED:
            await ctx.conn.execute(
                """
                UPDATE outlets SET closed_tick=%s,reach=%s
                WHERE run_id=%s AND outlet_id=%s
                """,
                (
                    event.tick,
                    payload["final_reach"],
                    ctx.run_id,
                    payload["outlet_id"],
                ),
            )
        else:
            await ctx.conn.execute(
                """
                UPDATE outlets SET reach=GREATEST(reach,%s)
                WHERE run_id=%s AND outlet_id=%s
                """,
                (
                    payload["impressions"],
                    ctx.run_id,
                    payload["outlet_id"],
                ),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM outlets WHERE run_id=%s", (ctx.run_id,))


class ArticlesProjection:
    name = "articles_projection"
    tables: tuple[str, ...] = ("articles",)
    handles: frozenset[int] = frozenset(
        {ARTICLE_PUBLISHED, ARTICLE_DISTRIBUTED, ARTICLE_RETRACTED, CLAIM_CHECKED}
    )

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == ARTICLE_PUBLISHED:
            await ctx.conn.execute(
                """
                INSERT INTO articles(
                    run_id,article_id,outlet_id,reporter_id,tick,headline,body,
                    source_event_seqs,claims,accuracy,accuracy_n,slant_applied,
                    stance_proposition,stance_value,reach,retracted_tick
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NULL,0,%s,%s,%s,0,NULL)
                ON CONFLICT(run_id,article_id) DO UPDATE SET
                    headline=EXCLUDED.headline,body=EXCLUDED.body,
                    source_event_seqs=EXCLUDED.source_event_seqs,
                    claims=EXCLUDED.claims,retracted_tick=NULL
                """,
                (
                    ctx.run_id,
                    payload["article_id"],
                    payload["outlet_id"],
                    payload["reporter_id"],
                    event.tick,
                    payload["headline"],
                    payload["body"],
                    list(payload["source_event_seqs"]),
                    json.dumps(payload["claims"], sort_keys=True),
                    payload["slant_applied"],
                    payload.get("stance_proposition"),
                    payload.get("stance_value"),
                ),
            )
        elif event.kind == ARTICLE_DISTRIBUTED:
            await ctx.conn.execute(
                """
                UPDATE articles SET reach=GREATEST(reach,%s)
                WHERE run_id=%s AND article_id=%s
                """,
                (payload["reach"], ctx.run_id, payload["article_id"]),
            )
        elif event.kind == ARTICLE_RETRACTED and payload["article_id"] is not None:
            await ctx.conn.execute(
                """
                UPDATE articles SET retracted_tick=%s
                WHERE run_id=%s AND article_id=%s
                """,
                (event.tick, ctx.run_id, payload["article_id"]),
            )
        elif (
            event.kind == CLAIM_CHECKED
            and payload["subject_kind"] == "article"
            and payload["score"] is not None
        ):
            await ctx.conn.execute(
                """
                UPDATE articles SET
                    accuracy=(COALESCE(accuracy,0)*accuracy_n + %s)/(accuracy_n+1),
                    accuracy_n=accuracy_n+1
                WHERE run_id=%s AND article_id=%s
                """,
                (payload["score"], ctx.run_id, payload["subject_id"]),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM articles WHERE run_id=%s", (ctx.run_id,))


class BeliefsProjection:
    name = "beliefs_projection"
    tables: tuple[str, ...] = ("beliefs",)
    handles: frozenset[int] = frozenset({BELIEF_UPDATED, BELIEF_DRIFT_APPLIED, BELIEF_PRIORS_SET})

    async def _upsert(
        self,
        ctx: ProjectionContext,
        event: Event,
        *,
        agent_id: str,
        proposition: str,
        value: float,
        confidence: float,
        source: str,
        source_ref: str | None,
    ) -> None:
        await ctx.conn.execute(
            """
            INSERT INTO beliefs(
                run_id,agent_id,proposition,value,confidence,updated_tick,
                source,source_ref,source_seq
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(run_id,agent_id,proposition) DO UPDATE SET
                value=EXCLUDED.value,confidence=EXCLUDED.confidence,
                updated_tick=EXCLUDED.updated_tick,source=EXCLUDED.source,
                source_ref=EXCLUDED.source_ref,source_seq=EXCLUDED.source_seq
            """,
            (
                ctx.run_id,
                agent_id,
                proposition,
                value,
                confidence,
                event.tick,
                source,
                source_ref,
                event.seq,
            ),
        )

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == BELIEF_UPDATED:
            await self._upsert(
                ctx,
                event,
                agent_id=str(payload["agent_id"]),
                proposition=str(payload["proposition"]),
                value=float(payload["new_value"]),
                confidence=float(payload["new_confidence"]),
                source=str(payload["channel"]),
                source_ref=(None if payload["source_ref"] is None else str(payload["source_ref"])),
            )
        elif event.kind == BELIEF_PRIORS_SET:
            for row in payload["propositions"]:
                await self._upsert(
                    ctx,
                    event,
                    agent_id=str(payload["agent_id"]),
                    proposition=str(row["proposition"]),
                    value=float(row["value"]),
                    confidence=float(row["confidence"]),
                    source="inherited",
                    source_ref=str(payload["source"]),
                )
        else:
            for row in payload["updates"]:
                proposition = str(row["proposition"])
                spec = proposition_spec(proposition)
                if spec is None:
                    raise ValueError(f"unknown proposition in belief drift: {proposition}")
                d_value = float(row["d_value"])
                d_confidence = float(row["d_confidence"])
                await ctx.conn.execute(
                    """
                    INSERT INTO beliefs(
                        run_id,agent_id,proposition,value,confidence,updated_tick,
                        source,source_ref,source_seq
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,NULL,%s)
                    ON CONFLICT(run_id,agent_id,proposition) DO UPDATE SET
                        value=GREATEST(%s,LEAST(%s,beliefs.value+%s)),
                        confidence=GREATEST(0,LEAST(1,beliefs.confidence+%s)),
                        updated_tick=EXCLUDED.updated_tick,
                        source=EXCLUDED.source,
                        source_ref=EXCLUDED.source_ref,
                        source_seq=EXCLUDED.source_seq
                    """,
                    (
                        ctx.run_id,
                        payload["agent_id"],
                        proposition,
                        max(spec.lo, min(spec.hi, spec.default_value + d_value)),
                        max(0.0, min(1.0, spec.default_confidence + d_confidence)),
                        event.tick,
                        payload["channel"],
                        event.seq,
                        spec.lo,
                        spec.hi,
                        d_value,
                        d_confidence,
                    ),
                )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM beliefs WHERE run_id=%s", (ctx.run_id,))


posts_projection = PostsProjection()
follows_projection = FollowsProjection()
relationships_projection = RelationshipsProjection()
outlets_projection = OutletsProjection()
articles_projection = ArticlesProjection()
beliefs_projection = BeliefsProjection()

register_projection(posts_projection)
register_projection(follows_projection)
register_projection(relationships_projection)
register_projection(outlets_projection)
register_projection(articles_projection)
register_projection(beliefs_projection)

__all__ = [
    "ArticlesProjection",
    "BeliefsProjection",
    "FollowsProjection",
    "OutletsProjection",
    "PostsProjection",
    "RelationshipsProjection",
    "articles_projection",
    "beliefs_projection",
    "follows_projection",
    "outlets_projection",
    "posts_projection",
    "relationships_projection",
]
