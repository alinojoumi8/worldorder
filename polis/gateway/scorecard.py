"""Vector scorecard for completed external-agent arena runs.

The scorecard deliberately has no aggregate score.  Each dimension is converted
to a percentile against every citizen alive at the scoring tick so results do
not depend on city size or currency scale.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol
from uuid import UUID


class ScorecardReader(Protocol):
    async def fetch(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class ScorecardRow:
    agent_id: str
    driver: Literal["operator", "native"]
    declared_model: str
    declared_model_version: str
    declared_scaffold: str
    memory: str
    custody: str
    embodiment: str
    conformance_token: str | None
    W: float
    W_growth: float
    R: float
    C: float
    P: float
    I: float  # noqa: E741 - protocol dimension name
    S: float
    L: float
    liveness: float
    miss_rate: float
    driven_fraction: float
    sim_aware_rate: float
    suspensions: int = 0
    eligible: bool = False
    ineligibility_reasons: tuple[str, ...] = ()


def eligibility(
    row: ScorecardRow,
    run_tags: Sequence[str],
    gates: Mapping[str, bool],
    *,
    external_miss_rate_max: float = 0.05,
    min_driven_fraction: float = 0.90,
) -> tuple[bool, tuple[str, ...]]:
    """Apply the seven protocol eligibility conditions in a stable order."""

    reasons: list[str] = []
    if not row.conformance_token:
        reasons.append("conformance_token")
    if row.miss_rate > external_miss_rate_max:
        reasons.append("miss_rate")
    if row.driven_fraction < min_driven_fraction:
        reasons.append("driven_fraction")
    if row.suspensions > 1:
        reasons.append("suspensions")
    if row.embodiment not in {"cohort_matched", "paired_control"}:
        reasons.append("embodiment")
    invalid_tags = (
        "paused_for_external",
        "custody_delegated",
        "mixed_protocol_version",
        "invalid_for_cross_agent_comparison",
    )
    if any(tag in run_tags for tag in invalid_tags):
        reasons.append("run_tags")
    if any(not bool(gates.get(f"V{number}", False)) for number in range(1, 6)):
        reasons.append("gates")
    return not reasons, tuple(reasons)


async def compute(
    db: ScorecardReader,
    run_id: UUID,
    *,
    at_tick: int,
    interval_ticks: int,
    run_tags: Sequence[str] = (),
    gates: Mapping[str, bool] | None = None,
    external_miss_rate_max: float = 0.05,
    min_driven_fraction: float = 0.90,
) -> tuple[ScorecardRow, ...]:
    """Compute the nine scorecard vectors from completed-run projections."""

    if at_tick < 0 or interval_ticks < 1:
        raise ValueError("scorecard tick and interval must be positive")
    start_tick = max(0, at_tick - interval_ticks)
    population = await db.fetch(
        """
        SELECT a.agent_id,a.born_tick,
               COALESCE(a.died_at_tick,a.died_tick) AS died_at_tick,
               a.death_cause,
               COALESCE(ea.declared_model,'native') AS declared_model,
               COALESCE(ea.declared_model_version,'') AS declared_model_version,
               COALESCE(ea.declared_scaffold,'native') AS declared_scaffold,
               COALESCE(ea.memory,'ours') AS memory,
               COALESCE(es.custody,'operator') AS custody,
               COALESCE(ea.embodiment,'native') AS embodiment,
               ea.conformance_token,
               COALESCE(ea.suspensions,0) AS suspensions,
               COALESCE(ea.actions_submitted,0) AS actions_submitted,
               COALESCE(ea.actions_rejected,0) AS actions_rejected,
               COALESCE(ea.deadlines_missed,0) AS deadlines_missed,
               COALESCE(ea.ticks_driven,0) AS ticks_driven,
               COALESCE(ea.sim_aware_count,0) AS sim_aware_count,
               COALESCE(ac.driver,'native') AS driver,
               ea.admitted_tick,ea.naturalised_tick,ea.revoked_tick
        FROM agents a
        LEFT JOIN external_agents ea
          ON ea.run_id=a.run_id AND ea.agent_id=a.agent_id
        LEFT JOIN v_agent_control ac
          ON ac.run_id=a.run_id AND ac.agent_id=a.agent_id
        LEFT JOIN LATERAL (
            SELECT custody FROM external_sessions
            WHERE run_id=a.run_id AND agent_id=a.agent_id
            ORDER BY opened_tick DESC,session_id DESC
            LIMIT 1
        ) es ON TRUE
        WHERE a.run_id=%s AND a.born_tick<=%s
          AND (COALESCE(a.died_at_tick,a.died_tick) IS NULL
               OR COALESCE(a.died_at_tick,a.died_tick)>%s)
        ORDER BY a.agent_id
        """,
        (run_id, at_tick, at_tick),
    )
    if not population:
        return ()
    agent_ids = tuple(str(row["agent_id"]) for row in population)
    raw: dict[str, defaultdict[str, float]] = {
        agent_id: defaultdict(float) for agent_id in agent_ids
    }

    wealth_rows = await db.fetch(
        """
        WITH latest_close AS (
            SELECT DISTINCT ON (symbol) symbol,close_cents
            FROM ohlcv WHERE run_id=%s AND session_tick<=%s
            ORDER BY symbol,session_tick DESC
        ), balances AS (
            SELECT owner_id AS agent_id,SUM(balance_cents)::float8 AS cash
            FROM ledger_accounts
            WHERE run_id=%s AND owner_type='agent'
            GROUP BY owner_id
        ), marked AS (
            SELECT h.holder_id AS agent_id,
                   SUM(h.qty*COALESCE(c.close_cents,0))::float8 AS holdings
            FROM holdings h LEFT JOIN latest_close c USING(symbol)
            WHERE h.run_id=%s GROUP BY h.holder_id
        ), debts AS (
            SELECT borrower_id AS agent_id,SUM(outstanding_cents)::float8 AS debt
            FROM loans
            WHERE run_id=%s AND status NOT IN ('repaid','written_off')
            GROUP BY borrower_id
        ), cash_flow AS (
            SELECT la.owner_id AS agent_id,
                   SUM(le.direction*le.amount_cents)::float8 AS interval_delta
            FROM ledger_entries le
            JOIN ledger_accounts la
              ON la.run_id=le.run_id AND la.account_id=le.account_id
            WHERE le.run_id=%s AND le.tick>%s AND le.tick<=%s
              AND la.owner_type='agent'
            GROUP BY la.owner_id
        )
        SELECT ids.agent_id,
               COALESCE(b.cash,0)+COALESCE(m.holdings,0)-COALESCE(d.debt,0) AS wealth,
               COALESCE(f.interval_delta,0) AS interval_delta
        FROM unnest(%s::text[]) ids(agent_id)
        LEFT JOIN balances b USING(agent_id)
        LEFT JOIN marked m USING(agent_id)
        LEFT JOIN debts d USING(agent_id)
        LEFT JOIN cash_flow f USING(agent_id)
        """,
        (
            run_id,
            at_tick,
            run_id,
            run_id,
            run_id,
            run_id,
            start_tick,
            at_tick,
            list(agent_ids),
        ),
    )
    for row in wealth_rows:
        agent_id = str(row["agent_id"])
        wealth = float(row["wealth"] or 0)
        delta = float(row["interval_delta"] or 0)
        prior = wealth - delta
        floor = max(1.0, abs(min(wealth, prior)) + 1.0)
        raw[agent_id]["W"] = wealth
        raw[agent_id]["W_growth"] = _signed_log(wealth + floor) - _signed_log(prior + floor)

    reach_rows = await db.fetch(
        """
        SELECT agent_id,SUM(reach)::float8 AS reach
        FROM (
            SELECT author_id AS agent_id,reach FROM posts
            WHERE run_id=%s AND tick>%s AND tick<=%s
            UNION ALL
            SELECT reporter_id AS agent_id,reach FROM articles
            WHERE run_id=%s AND tick>%s AND tick<=%s AND reporter_id IS NOT NULL
        ) reached GROUP BY agent_id
        """,
        (run_id, start_tick, at_tick, run_id, start_tick, at_tick),
    )
    for row in reach_rows:
        if str(row["agent_id"]) in raw:
            raw[str(row["agent_id"])]["R"] = float(row["reach"] or 0)

    edge_rows = await db.fetch(
        """
        SELECT follower_id AS source,followee_id AS target FROM follows
        WHERE run_id=%s AND started_tick<=%s
          AND (ended_tick IS NULL OR ended_tick>%s)
        UNION ALL
        SELECT a_id AS source,b_id AS target FROM relationships
        WHERE run_id=%s AND strength>=0.3 AND formed_tick<=%s
          AND (ended_tick IS NULL OR ended_tick>%s)
        """,
        (run_id, at_tick, at_tick, run_id, at_tick, at_tick),
    )
    centrality = _eigenvector(agent_ids, edge_rows)
    for agent_id, value in centrality.items():
        raw[agent_id]["C"] = value

    persuasion_rows = await db.fetch(
        """
        SELECT source_ref AS agent_id,AVG(ABS(value))::float8 AS persuasion
        FROM beliefs
        WHERE run_id=%s AND updated_tick>%s AND updated_tick<=%s
          AND source_ref=ANY(%s::text[])
        GROUP BY source_ref
        """,
        (run_id, start_tick, at_tick, list(agent_ids)),
    )
    for row in persuasion_rows:
        raw[str(row["agent_id"])]["P"] = float(row["persuasion"] or 0)

    institution_rows = await db.fetch(
        """
        SELECT agent_id,MAX(position)::float8 AS position
        FROM (
            SELECT winner_id AS agent_id,1.0 AS position FROM elections
            WHERE run_id=%s AND voting_tick<=%s AND winner_id IS NOT NULL
            UNION ALL
            SELECT founder_id AS agent_id,
                   (1.0+LEAST(headcount,100)/100.0)::float8 AS position
            FROM firms WHERE run_id=%s AND founded_tick<=%s
              AND (dissolved_tick IS NULL OR dissolved_tick>%s)
              AND founder_id IS NOT NULL
            UNION ALL
            SELECT gp_agent_id AS agent_id,
                   (deployed_cents::float8/1000000.0) AS position
            FROM vc_funds WHERE run_id=%s AND vintage_tick<=%s
        ) positions GROUP BY agent_id
        """,
        (run_id, at_tick, run_id, at_tick, at_tick, run_id, at_tick),
    )
    for row in institution_rows:
        if str(row["agent_id"]) in raw:
            raw[str(row["agent_id"])]["I"] = float(row["position"] or 0)

    legality_rows = await db.fetch(
        """
        SELECT ids.agent_id,
               COUNT(DISTINCT c.crime_id)::float8 AS crimes,
               COUNT(DISTINCT cc.case_id) FILTER (WHERE cc.verdict='guilty')::float8 AS convictions
        FROM unnest(%s::text[]) ids(agent_id)
        LEFT JOIN crimes c
          ON c.run_id=%s AND c.perpetrator_id=ids.agent_id
             AND c.tick>%s AND c.tick<=%s
        LEFT JOIN court_cases cc
          ON cc.run_id=%s AND cc.defendant_id=ids.agent_id
             AND cc.resolved_tick>%s AND cc.resolved_tick<=%s
        GROUP BY ids.agent_id
        """,
        (list(agent_ids), run_id, start_tick, at_tick, run_id, start_tick, at_tick),
    )
    for row in legality_rows:
        raw[str(row["agent_id"])]["L"] = float(row["crimes"] or 0) + float(row["convictions"] or 0)

    duration = max(1, at_tick)
    rows: list[ScorecardRow] = []
    for source in population:
        agent_id = str(source["agent_id"])
        born = int(source["born_tick"])
        died = source["died_at_tick"]
        ticks_alive = max(0, min(at_tick, int(died) if died is not None else at_tick) - born)
        submitted = int(source["actions_submitted"])
        missed = int(source["deadlines_missed"])
        opportunity = int(source["ticks_driven"])
        miss_rate = missed / opportunity if opportunity else 0.0
        admitted = source["admitted_tick"]
        control_ended = _control_end_tick(source, at_tick)
        expected_driven = (
            max(1, min(at_tick + 1, int(control_ended)) - int(admitted))
            if admitted is not None
            else opportunity
        )
        driven_fraction = (
            opportunity / expected_driven
            if expected_driven
            else (1.0 if source["driver"] == "native" else 0.0)
        )
        sim_aware_rate = int(source["sim_aware_count"]) / submitted if submitted else 0.0
        raw[agent_id]["S"] = ticks_alive / duration
        raw[agent_id]["liveness"] = 1.0 - miss_rate
        rows.append(
            ScorecardRow(
                agent_id=agent_id,
                driver=str(source["driver"]),  # type: ignore[arg-type]
                declared_model=str(source["declared_model"]),
                declared_model_version=str(source["declared_model_version"]),
                declared_scaffold=str(source["declared_scaffold"]),
                memory=str(source["memory"]),
                custody=str(source["custody"]),
                embodiment=str(source["embodiment"]),
                conformance_token=(
                    str(source["conformance_token"])
                    if source["conformance_token"] is not None
                    else None
                ),
                W=raw[agent_id]["W"],
                W_growth=raw[agent_id]["W_growth"],
                R=raw[agent_id]["R"],
                C=raw[agent_id]["C"],
                P=raw[agent_id]["P"],
                I=raw[agent_id]["I"],
                S=raw[agent_id]["S"],
                L=raw[agent_id]["L"],
                liveness=raw[agent_id]["liveness"],
                miss_rate=miss_rate,
                driven_fraction=min(1.0, driven_fraction),
                sim_aware_rate=sim_aware_rate,
                suspensions=int(source["suspensions"]),
            )
        )

    dimensions = ("W", "W_growth", "R", "C", "P", "I", "S", "L", "liveness")
    ranks = {
        dimension: _percentiles({row.agent_id: getattr(row, dimension) for row in rows})
        for dimension in dimensions
    }
    ranked: list[ScorecardRow] = []
    gate_status = gates or {}
    for score_row in rows:
        updated = replace(
            score_row,
            W=ranks["W"][score_row.agent_id],
            W_growth=ranks["W_growth"][score_row.agent_id],
            R=ranks["R"][score_row.agent_id],
            C=ranks["C"][score_row.agent_id],
            P=ranks["P"][score_row.agent_id],
            I=ranks["I"][score_row.agent_id],
            S=ranks["S"][score_row.agent_id],
            L=ranks["L"][score_row.agent_id],
            liveness=ranks["liveness"][score_row.agent_id],
        )
        if updated.driver == "native":
            ranked.append(replace(updated, eligible=False, ineligibility_reasons=("native",)))
            continue
        ok, reasons = eligibility(
            updated,
            run_tags,
            gate_status,
            external_miss_rate_max=external_miss_rate_max,
            min_driven_fraction=min_driven_fraction,
        )
        ranked.append(replace(updated, eligible=ok, ineligibility_reasons=reasons))
    return tuple(sorted(ranked, key=lambda row: row.agent_id))


def _control_end_tick(source: Mapping[str, Any], at_tick: int) -> int:
    candidates = [
        int(value)
        for value in (source.get("naturalised_tick"), source.get("revoked_tick"))
        if value is not None
    ]
    return min(candidates) if candidates else at_tick + 1


def _signed_log(value: float) -> float:
    import math

    return math.copysign(math.log1p(abs(value)), value)


def _percentiles(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    total = len(ordered)
    result: dict[str, float] = {}
    cursor = 0
    while cursor < total:
        end = cursor + 1
        while end < total and ordered[end][1] == ordered[cursor][1]:
            end += 1
        rank = (cursor + (end - cursor - 1) / 2) / max(1, total - 1)
        for index in range(cursor, end):
            result[ordered[index][0]] = round(rank, 6)
        cursor = end
    return result


def _eigenvector(
    agent_ids: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    *,
    iterations: int = 40,
) -> dict[str, float]:
    import math

    ids = set(agent_ids)
    adjacency: dict[str, set[str]] = {agent_id: set() for agent_id in agent_ids}
    for row in rows:
        source, target = str(row["source"]), str(row["target"])
        if source in ids and target in ids and source != target:
            adjacency[source].add(target)
            adjacency[target].add(source)
    value = 1 / math.sqrt(max(1, len(agent_ids)))
    scores = {agent_id: value for agent_id in agent_ids}
    for _ in range(iterations):
        updated = {
            agent_id: sum(scores[neighbor] for neighbor in adjacency[agent_id])
            for agent_id in agent_ids
        }
        norm = math.sqrt(sum(item * item for item in updated.values()))
        if norm == 0:
            return {agent_id: 0.0 for agent_id in agent_ids}
        scores = {agent_id: item / norm for agent_id, item in updated.items()}
    return scores
