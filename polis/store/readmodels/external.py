"""The complete database read surface available to external citizens.

No gateway handler receives a database object directly. All database-backed responses pass
through one of the five functions exported here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID


class _Reader(Protocol):
    async def fetch(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]: ...


async def whoami(db: _Reader, run_id: UUID, agent_id: str, tick: int) -> Mapping[str, Any]:
    rows = await db.fetch(
        """
        SELECT a.agent_id,a.born_tick,a.age_years,a.place_id,a.home_place_id,
               a.household_id,a.generation,a.criminal_record,a.state,
               x.display_name,x.deadlines_missed,x.consecutive_misses,x.strikes,
               x.protocol_version,c.driver,
               COALESCE(n.last_nonce,-1) + 1 AS next_nonce
        FROM agents a
        JOIN v_agent_control c
          ON c.run_id=a.run_id AND c.agent_id=a.agent_id
        LEFT JOIN external_agents x
          ON x.run_id=a.run_id AND x.agent_id=a.agent_id
        LEFT JOIN external_nonces n
          ON n.run_id=a.run_id AND n.agent_id=a.agent_id
        WHERE a.run_id=%s AND a.agent_id=%s AND a.died_tick IS NULL
        """,
        (run_id, agent_id),
    )
    if not rows:
        raise LookupError("not visible")
    row = rows[0]
    state = dict(row.get("state") or {})
    identity = {
        "agent_id": agent_id,
        "display_name": row.get("display_name") or state.get("display_name"),
        "age_years": float(row["age_years"]),
        "generation": int(row.get("generation") or 0),
        "household_id": row.get("household_id"),
        "home_place_id": row.get("home_place_id") or row["place_id"],
        "born_at_tick": int(row["born_tick"]),
    }
    standing_fields = (
        "employment_status",
        "employer_id",
        "occupation",
        "wage_cents",
        "wealth_cents",
        "skills",
        "education_level",
        "reputation",
        "health",
        "offices_held",
        "firms_controlled",
        "open_orders",
        "loans",
        "goals",
        "identity_summary",
    )
    standing = {field: state.get(field) for field in standing_fields}
    standing["criminal_record"] = int(row.get("criminal_record") or 0)
    return {
        "identity": identity,
        "standing": standing,
        "protocol": {
            "tick": tick,
            "driver": row["driver"],
            "next_nonce": int(row["next_nonce"]),
            "deadlines_missed": int(row.get("deadlines_missed") or 0),
            "consecutive_misses": int(row.get("consecutive_misses") or 0),
            "strikes": int(row.get("strikes") or 0),
            "protocol_version": int(row.get("protocol_version") or 1),
        },
    }


async def recall(
    db: _Reader,
    run_id: UUID,
    agent_id: str,
    query: str,
    *,
    k: int,
    mtype: str | None,
    since_tick: int | None,
    at_tick: int,
) -> Mapping[str, Any]:
    if not 1 <= k <= 24:
        raise ValueError("k must be between 1 and 24")
    clauses = ["run_id=%s", "agent_id=%s", "archived=FALSE"]
    params: list[Any] = [at_tick, run_id, agent_id]
    if mtype is not None:
        clauses.append("type=%s")
        params.append(mtype)
    if since_tick is not None:
        clauses.append("tick>=%s")
        params.append(since_tick)
    if query.strip():
        clauses.append("text ILIKE %s")
        params.append(f"%{query.strip()}%")
    params.append(k + 1)
    rows = await db.fetch(
        """
        SELECT memory_id,tick,type,text,importance,parent_memory_ids,
               (importance + 1.0 / (1.0 + GREATEST(0,%s-last_accessed_tick))) AS score
        FROM memories
        WHERE """
        + " AND ".join(clauses)
        + " ORDER BY score DESC,tick DESC,memory_id LIMIT %s",
        params,
    )
    memories = [
        {
            "memory_id": row["memory_id"],
            "tick": int(row["tick"]),
            "type": row["type"],
            "text": row["text"],
            "importance": float(row["importance"]),
            "score": float(row["score"]),
            "parent_memory_ids": list(row.get("parent_memory_ids") or ()),
        }
        for row in rows[:k]
    ]
    return {"truncated": len(rows) > k, "memories": memories}


async def remember(
    db: _Reader,
    run_id: UUID,
    agent_id: str,
    body: Mapping[str, Any],
) -> Mapping[str, Any]:
    text = body.get("text")
    if not isinstance(text, str) or not text or len(text) > 1_000:
        raise ValueError("memory text must contain 1 to 1000 characters")
    memory_type = body.get("type", "reflection")
    if memory_type not in {"observation", "reflection", "plan", "semantic"}:
        raise ValueError("unknown memory type")
    declared = body.get("importance", 0.5)
    if isinstance(declared, bool) or not isinstance(declared, int | float):
        raise ValueError("importance must be numeric")
    if not 0 <= float(declared) <= 1:
        raise ValueError("importance must be between zero and one")
    supported = body.get("supported_by", ())
    if not isinstance(supported, Sequence) or isinstance(supported, str | bytes):
        raise ValueError("supported_by must be an array")
    requested_ids = tuple(dict.fromkeys(str(value) for value in supported[:12]))
    held_ids: set[str] = set()
    if requested_ids:
        rows = await db.fetch(
            """
            SELECT memory_id FROM memories
            WHERE run_id=%s AND agent_id=%s AND archived=FALSE
              AND memory_id=ANY(%s)
            """,
            (run_id, agent_id, list(requested_ids)),
        )
        held_ids = {str(row["memory_id"]) for row in rows}
    assigned = min(float(declared), _importance_score(text) + 0.15)
    subject_ids = body.get("subject_ids", ())
    if not isinstance(subject_ids, Sequence) or isinstance(subject_ids, str | bytes):
        raise ValueError("subject_ids must be an array")
    return {
        "agent_id": agent_id,
        "type": memory_type,
        "text": text,
        "importance_requested": float(declared),
        "importance_assigned": round(assigned, 6),
        "subject_ids": list(dict.fromkeys(str(value) for value in subject_ids[:8])),
        "supported_by": [value for value in requested_ids if value in held_ids],
        "citations_dropped": [value for value in requested_ids if value not in held_ids],
    }


async def market(
    db: _Reader,
    run_id: UUID,
    agent_id: str,
    symbols: Sequence[str],
    skus: Sequence[str],
    depth: int,
) -> Mapping[str, Any]:
    del agent_id
    if not 1 <= depth <= 5:
        raise ValueError("depth must be between 1 and 5")
    clean_symbols = tuple(dict.fromkeys(symbols[:12]))
    rows = (
        await db.fetch(
            """
            SELECT run_id,symbol,side,price_cents,qty,orders_n,as_of_tick
            FROM (
                SELECT v.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY run_id,symbol,side
                           ORDER BY
                               CASE WHEN side='buy' THEN -price_cents ELSE price_cents END,
                               price_cents
                       ) AS depth_rank
                FROM v_market_visible v
                WHERE run_id=%s AND symbol=ANY(%s)
            ) ranked
            WHERE depth_rank<=%s
            ORDER BY symbol,side,depth_rank
            """,
            (run_id, list(clean_symbols), depth),
        )
        if clean_symbols
        else []
    )
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        quote = quotes.setdefault(
            symbol,
            {"symbol": symbol, "bid_depth": [], "ask_depth": []},
        )
        side = "bid_depth" if row["side"] == "buy" else "ask_depth"
        quote[side].append(
            {
                "price_cents": int(row["price_cents"]),
                "qty": int(row["qty"]),
                "orders_n": int(row["orders_n"]),
            }
        )
        quote["tick"] = int(row["as_of_tick"])
    return {
        "quotes": [quotes[symbol] for symbol in sorted(quotes)],
        "goods": [{"sku": sku, "available": False} for sku in dict.fromkeys(skus[:12])],
    }


async def public_record(
    db: _Reader,
    run_id: UUID,
    agent_id: str,
    query: str,
    *,
    kinds: Sequence[str],
    since_tick: int | None,
    limit: int,
) -> Mapping[str, Any]:
    del agent_id
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    clauses = ["run_id=%s"]
    params: list[Any] = [run_id]
    if query.strip():
        clauses.append("(title ILIKE %s OR body ILIKE %s)")
        pattern = f"%{query.strip()}%"
        params.extend((pattern, pattern))
    if kinds:
        clauses.append("kind=ANY(%s)")
        params.append(list(dict.fromkeys(kinds)))
    if since_tick is not None:
        clauses.append("tick>=%s")
        params.append(since_tick)
    params.append(limit + 1)
    rows = await db.fetch(
        """
        SELECT record_id,tick,kind,title,body,actor_id
        FROM v_public_record WHERE """
        + " AND ".join(clauses)
        + " ORDER BY tick DESC,record_id LIMIT %s",
        params,
    )
    records = [
        {
            "source_ref": row["record_id"],
            "kind": row["kind"],
            "tick": int(row["tick"]),
            "author_id": row.get("actor_id"),
            "title": row["title"],
            "text": row["body"],
        }
        for row in rows[:limit]
    ]
    return {"truncated": len(rows) > limit, "records": records}


def _importance_score(text: str) -> float:
    words = text.split()
    length_signal = min(1.0, math.log1p(len(words)) / math.log(101))
    durable_signal = (
        1.0
        if any(
            token in text.casefold()
            for token in ("plan", "promise", "owe", "deadline", "remember", "conclusion")
        )
        else 0.0
    )
    return min(1.0, 0.15 + 0.55 * length_signal + 0.30 * durable_signal)


__all__ = ["market", "public_record", "recall", "remember", "whoami"]
