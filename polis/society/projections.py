from __future__ import annotations

import json

from polis.events.kinds import (
    ABSTAINED,
    ARTICLE_DISTRIBUTED,
    ARTICLE_PUBLISHED,
    ARTICLE_RETRACTED,
    BELIEF_DRIFT_APPLIED,
    BELIEF_PRIORS_SET,
    BELIEF_UPDATED,
    CAMPAIGN_SPEND,
    CANDIDACY_ANNOUNCED,
    CASE_SETTLED,
    CLAIM_CHECKED,
    COUNSEL_RETAINED,
    CRIME_COMMITTED,
    CRIME_DETECTED,
    CRIME_REPORTED,
    ELECTION_CALLED,
    ELECTION_RESOLVED,
    EVIDENCE_ADMITTED,
    FOLLOW_CREATED,
    FOLLOW_ENDED,
    JUDGMENT_RENDERED,
    MIGRATION_IN,
    OUTLET_CLOSED,
    OUTLET_FOUNDED,
    OUTLET_REVENUE_BOOKED,
    PARTY_DISSOLVED,
    PARTY_FOUNDED,
    PARTY_PLATFORM_CHANGED,
    POLICY_ENACTED,
    POLICY_REPEALED,
    POST_DELETED,
    POST_ENGAGED,
    POST_PUBLISHED,
    SUIT_FILED,
    TIE_ENDED,
    TIE_FORMED,
    TIE_TYPE_CHANGED,
    TIE_UPDATED,
    VOTE_CAST,
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
    handles: frozenset[int] = frozenset(
        {BELIEF_UPDATED, BELIEF_DRIFT_APPLIED, BELIEF_PRIORS_SET, MIGRATION_IN}
    )

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
        elif event.kind in {BELIEF_PRIORS_SET, MIGRATION_IN}:
            rows = (
                payload["propositions"]
                if event.kind == BELIEF_PRIORS_SET
                else payload["belief_priors"]
            )
            for row in rows:
                proposition = str(row["proposition"])
                if proposition.startswith("fact.") or proposition_spec(proposition) is None:
                    raise ValueError(f"invalid inherited proposition: {proposition}")
                await self._upsert(
                    ctx,
                    event,
                    agent_id=str(payload["agent_id"]),
                    proposition=proposition,
                    value=float(row["value"]),
                    confidence=float(row["confidence"]),
                    source=("inherited" if event.kind == BELIEF_PRIORS_SET else "migration"),
                    source_ref=str(
                        payload["source"]
                        if event.kind == BELIEF_PRIORS_SET
                        else payload["cohort_id"]
                    ),
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


class PolityProjection:
    name = "polity_projection"
    tables: tuple[str, ...] = (
        "parties",
        "elections",
        "candidacies",
        "votes",
        "policies",
    )
    handles: frozenset[int] = frozenset(
        {
            PARTY_FOUNDED,
            PARTY_PLATFORM_CHANGED,
            PARTY_DISSOLVED,
            ELECTION_CALLED,
            ELECTION_RESOLVED,
            CANDIDACY_ANNOUNCED,
            CAMPAIGN_SPEND,
            VOTE_CAST,
            ABSTAINED,
            POLICY_ENACTED,
            POLICY_REPEALED,
        }
    )

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == PARTY_FOUNDED:
            await ctx.conn.execute(
                """
                INSERT INTO parties(
                    run_id,party_id,name,platform,founded_tick,dissolved_tick
                ) VALUES(%s,%s,%s,%s::jsonb,%s,NULL)
                ON CONFLICT(run_id,party_id) DO UPDATE SET
                    name=EXCLUDED.name,platform=EXCLUDED.platform,
                    founded_tick=EXCLUDED.founded_tick,dissolved_tick=NULL
                """,
                (
                    ctx.run_id,
                    payload["party_id"],
                    payload["name"],
                    json.dumps(payload["platform"], sort_keys=True),
                    event.tick,
                ),
            )
        elif event.kind == PARTY_PLATFORM_CHANGED:
            changes = {str(row["proposition"]): float(row["new"]) for row in payload["changes"]}
            await ctx.conn.execute(
                """
                UPDATE parties SET platform=platform || %s::jsonb
                WHERE run_id=%s AND party_id=%s
                """,
                (
                    json.dumps(changes, sort_keys=True),
                    ctx.run_id,
                    payload["party_id"],
                ),
            )
        elif event.kind == PARTY_DISSOLVED:
            await ctx.conn.execute(
                """
                UPDATE parties SET dissolved_tick=%s
                WHERE run_id=%s AND party_id=%s
                """,
                (event.tick, ctx.run_id, payload["party_id"]),
            )
        elif event.kind == ELECTION_CALLED:
            await ctx.conn.execute(
                """
                INSERT INTO elections(
                    run_id,election_id,office,seats,called_tick,voting_tick,
                    campaign_ends_tick,electorate_size,method
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(run_id,election_id) DO UPDATE SET
                    called_tick=EXCLUDED.called_tick,
                    voting_tick=EXCLUDED.voting_tick,
                    campaign_ends_tick=EXCLUDED.campaign_ends_tick,
                    electorate_size=EXCLUDED.electorate_size,
                    turnout=NULL,winner_id=NULL,winner_ids='[]',
                    margin=NULL,diagnostics='{}'
                """,
                (
                    ctx.run_id,
                    payload["election_id"],
                    payload["office"],
                    payload["seats"],
                    payload["called_tick"],
                    payload["voting_tick"],
                    payload["campaign_ends_tick"],
                    payload["electorate_size"],
                    payload["method"],
                ),
            )
        elif event.kind == ELECTION_RESOLVED:
            winners = list(payload["winner_ids"])
            diagnostics = {
                key: payload[key]
                for key in (
                    "rounds",
                    "n_deliberate",
                    "n_reflex",
                    "fitted_omega",
                    "holdout_accuracy",
                    "first_election_prior",
                )
                if key in payload
            }
            await ctx.conn.execute(
                """
                UPDATE elections SET
                    turnout=%s,winner_id=%s,winner_ids=%s::jsonb,
                    margin=%s,diagnostics=%s::jsonb
                WHERE run_id=%s AND election_id=%s
                """,
                (
                    payload["turnout"],
                    winners[0] if winners else None,
                    json.dumps(winners),
                    payload["margin"],
                    json.dumps(diagnostics, sort_keys=True),
                    ctx.run_id,
                    payload["election_id"],
                ),
            )
            for candidacy_id, votes in sorted(payload["tallies"].items()):
                await ctx.conn.execute(
                    """
                    UPDATE candidacies SET votes=%s
                    WHERE run_id=%s AND candidacy_id=%s
                    """,
                    (votes, ctx.run_id, candidacy_id),
                )
        elif event.kind == CANDIDACY_ANNOUNCED:
            await ctx.conn.execute(
                """
                INSERT INTO candidacies(
                    run_id,candidacy_id,election_id,agent_id,party_id,platform
                ) VALUES(%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT(run_id,candidacy_id) DO UPDATE SET
                    party_id=EXCLUDED.party_id,platform=EXCLUDED.platform
                """,
                (
                    ctx.run_id,
                    payload["candidacy_id"],
                    payload["election_id"],
                    payload["agent_id"],
                    payload["party_id"],
                    json.dumps(payload["platform"], sort_keys=True),
                ),
            )
        elif event.kind == CAMPAIGN_SPEND:
            await ctx.conn.execute(
                """
                UPDATE candidacies SET spend_cents=spend_cents+%s
                WHERE run_id=%s AND candidacy_id=%s
                """,
                (payload["amount_cents"], ctx.run_id, payload["candidacy_id"]),
            )
        elif event.kind in {VOTE_CAST, ABSTAINED}:
            voter_id = payload.get("voter_id", payload.get("agent_id"))
            await ctx.conn.execute(
                """
                INSERT INTO votes(
                    run_id,election_id,voter_id,candidacy_id,tick,
                    ranking,approvals,origin,utility
                ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)
                ON CONFLICT(run_id,election_id,voter_id) DO UPDATE SET
                    candidacy_id=EXCLUDED.candidacy_id,tick=EXCLUDED.tick,
                    ranking=EXCLUDED.ranking,approvals=EXCLUDED.approvals,
                    origin=EXCLUDED.origin,utility=EXCLUDED.utility
                """,
                (
                    ctx.run_id,
                    payload["election_id"],
                    voter_id,
                    payload.get("candidacy_id"),
                    event.tick,
                    json.dumps(payload.get("ranking", [])),
                    json.dumps(payload.get("approvals", [])),
                    payload["origin"],
                    json.dumps(payload["utility"], sort_keys=True),
                ),
            )
        elif event.kind == POLICY_ENACTED:
            await ctx.conn.execute(
                """
                INSERT INTO policies(
                    run_id,policy_id,parameter,old_value,new_value,enacted_tick,
                    effective_tick,enacted_by,vote_margin,proposal_seq
                ) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT(run_id,policy_id) DO UPDATE SET
                    old_value=EXCLUDED.old_value,new_value=EXCLUDED.new_value,
                    effective_tick=EXCLUDED.effective_tick,
                    enacted_by=EXCLUDED.enacted_by,
                    vote_margin=EXCLUDED.vote_margin,
                    proposal_seq=EXCLUDED.proposal_seq
                """,
                (
                    ctx.run_id,
                    payload["policy_id"],
                    payload["parameter"],
                    json.dumps(payload["old_value"], sort_keys=True),
                    json.dumps(payload["new_value"], sort_keys=True),
                    event.tick,
                    payload["effective_tick"],
                    payload["enacted_by"],
                    payload["vote_margin"],
                    payload["proposal_seq"],
                ),
            )
        elif event.kind == POLICY_REPEALED:
            policy_id = payload.get("repealed_policy_id") or payload["policy_id"]
            await ctx.conn.execute(
                """
                UPDATE policies SET repealed_tick=%s
                WHERE run_id=%s AND policy_id=%s
                """,
                (event.tick, ctx.run_id, policy_id),
            )

    async def truncate(self, ctx: ProjectionContext) -> None:
        for table in ("votes", "candidacies", "elections", "parties", "policies"):
            await ctx.conn.execute(f"DELETE FROM {table} WHERE run_id=%s", (ctx.run_id,))


class LawProjection:
    name = "law_projection"
    tables: tuple[str, ...] = ("crimes", "court_cases")
    handles: frozenset[int] = frozenset(
        {
            CRIME_COMMITTED,
            CRIME_DETECTED,
            CRIME_REPORTED,
            SUIT_FILED,
            COUNSEL_RETAINED,
            EVIDENCE_ADMITTED,
            CASE_SETTLED,
            JUDGMENT_RENDERED,
        }
    )

    async def apply(self, ctx: ProjectionContext, event: Event) -> None:
        payload = event.payload
        if event.kind == CRIME_COMMITTED:
            await ctx.conn.execute(
                """
                INSERT INTO crimes(
                    run_id,crime_id,tick,type,perpetrator_id,victim_id,
                    amount_cents,place_id,district_id,source_action_id,
                    concealment,path,detected
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
                ON CONFLICT(run_id,crime_id) DO UPDATE SET
                    amount_cents=EXCLUDED.amount_cents,
                    place_id=EXCLUDED.place_id,
                    district_id=EXCLUDED.district_id,
                    concealment=EXCLUDED.concealment,
                    path=EXCLUDED.path
                """,
                (
                    ctx.run_id,
                    payload["crime_id"],
                    event.tick,
                    payload["type"],
                    payload["perpetrator_id"],
                    payload["victim_id"],
                    payload["amount_cents"],
                    payload["place_id"],
                    payload["district_id"],
                    payload["source_action_id"],
                    payload["concealment"],
                    payload.get("path", "explicit"),
                ),
            )
        elif event.kind == CRIME_DETECTED:
            await ctx.conn.execute(
                """
                UPDATE crimes SET detected=TRUE,detected_tick=%s
                WHERE run_id=%s AND crime_id=%s
                """,
                (event.tick, ctx.run_id, payload["crime_id"]),
            )
        elif event.kind == CRIME_REPORTED:
            await ctx.conn.execute(
                """
                UPDATE crimes SET reported_by=%s
                WHERE run_id=%s AND crime_id=%s
                """,
                (payload["reporter_id"], ctx.run_id, payload["crime_id"]),
            )
        elif event.kind == SUIT_FILED:
            await ctx.conn.execute(
                """
                INSERT INTO court_cases(
                    run_id,case_id,type,plaintiff_id,defendant_id,crime_id,
                    cause_of_action,claim_cents,filed_tick,evidence_event_seqs
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(run_id,case_id) DO UPDATE SET
                    plaintiff_id=EXCLUDED.plaintiff_id,
                    defendant_id=EXCLUDED.defendant_id,
                    crime_id=EXCLUDED.crime_id,
                    cause_of_action=EXCLUDED.cause_of_action,
                    claim_cents=EXCLUDED.claim_cents,
                    evidence_event_seqs=EXCLUDED.evidence_event_seqs,
                    status='open',resolved_tick=NULL
                """,
                (
                    ctx.run_id,
                    payload["case_id"],
                    payload["type"],
                    payload["plaintiff_id"],
                    payload["defendant_id"],
                    payload["crime_id"],
                    payload["cause_of_action"],
                    payload["claim_cents"],
                    event.tick,
                    list(payload.get("evidence_event_seqs", ())),
                ),
            )
        elif event.kind == COUNSEL_RETAINED:
            column = (
                "defence_counsel_id" if payload["side"] == "defence" else "plaintiff_counsel_id"
            )
            await ctx.conn.execute(
                f"""
                UPDATE court_cases SET {column}=%s
                WHERE run_id=%s AND case_id=%s
                """,
                (payload["counsel_id"], ctx.run_id, payload["case_id"]),
            )
        elif event.kind == EVIDENCE_ADMITTED:
            await ctx.conn.execute(
                """
                UPDATE court_cases SET admitted_event_seqs=%s
                WHERE run_id=%s AND case_id=%s
                """,
                (list(payload["admitted_seqs"]), ctx.run_id, payload["case_id"]),
            )
        elif event.kind == CASE_SETTLED:
            await ctx.conn.execute(
                """
                UPDATE court_cases SET status='settled',resolved_tick=%s
                WHERE run_id=%s AND case_id=%s
                """,
                (event.tick, ctx.run_id, payload["case_id"]),
            )
        elif event.kind == JUDGMENT_RENDERED:
            await ctx.conn.execute(
                """
                UPDATE court_cases SET
                    status='resolved',resolved_tick=%s,judge_id=%s,verdict=%s,
                    penalty_cents=%s,sentence_ticks=%s,damages_cents=%s,
                    restitution_cents=%s
                WHERE run_id=%s AND case_id=%s
                """,
                (
                    event.tick,
                    payload["judge_id"],
                    payload["verdict"],
                    payload["fine_cents"],
                    payload["sentence_ticks"],
                    payload["damages_cents"],
                    payload["restitution_cents"],
                    ctx.run_id,
                    payload["case_id"],
                ),
            )
            if payload["verdict"] == "guilty":
                await ctx.conn.execute(
                    """
                    UPDATE agents SET criminal_record=criminal_record+1
                    WHERE run_id=%s AND agent_id=(
                        SELECT defendant_id FROM court_cases
                        WHERE run_id=%s AND case_id=%s
                    )
                    """,
                    (ctx.run_id, ctx.run_id, payload["case_id"]),
                )

    async def truncate(self, ctx: ProjectionContext) -> None:
        await ctx.conn.execute("DELETE FROM court_cases WHERE run_id=%s", (ctx.run_id,))
        await ctx.conn.execute("DELETE FROM crimes WHERE run_id=%s", (ctx.run_id,))


posts_projection = PostsProjection()
follows_projection = FollowsProjection()
relationships_projection = RelationshipsProjection()
outlets_projection = OutletsProjection()
articles_projection = ArticlesProjection()
beliefs_projection = BeliefsProjection()
polity_projection = PolityProjection()
law_projection = LawProjection()

register_projection(posts_projection)
register_projection(follows_projection)
register_projection(relationships_projection)
register_projection(outlets_projection)
register_projection(articles_projection)
register_projection(beliefs_projection)
register_projection(polity_projection)
register_projection(law_projection)

__all__ = [
    "ArticlesProjection",
    "BeliefsProjection",
    "FollowsProjection",
    "LawProjection",
    "OutletsProjection",
    "PolityProjection",
    "PostsProjection",
    "RelationshipsProjection",
    "articles_projection",
    "beliefs_projection",
    "follows_projection",
    "law_projection",
    "outlets_projection",
    "polity_projection",
    "posts_projection",
    "relationships_projection",
]
