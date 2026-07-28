from __future__ import annotations

import json

from polis.events.kinds import (
    FOLLOW_CREATED,
    FOLLOW_ENDED,
    POST_DELETED,
    POST_ENGAGED,
    POST_PUBLISHED,
    TIE_ENDED,
    TIE_FORMED,
    TIE_TYPE_CHANGED,
    TIE_UPDATED,
)
from polis.events.types import Event
from polis.store.projections.base import ProjectionContext, register_projection


class PostsProjection:
    name = "posts_projection"
    tables: tuple[str, ...] = ("posts", "engagements", "post_viewers")
    handles: frozenset[int] = frozenset({POST_PUBLISHED, POST_DELETED, POST_ENGAGED})

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
        else:
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


posts_projection = PostsProjection()
follows_projection = FollowsProjection()
relationships_projection = RelationshipsProjection()

register_projection(posts_projection)
register_projection(follows_projection)
register_projection(relationships_projection)

__all__ = [
    "FollowsProjection",
    "PostsProjection",
    "RelationshipsProjection",
    "follows_projection",
    "posts_projection",
    "relationships_projection",
]
