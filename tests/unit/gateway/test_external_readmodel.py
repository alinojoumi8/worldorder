from __future__ import annotations

from typing import Any
from uuid import UUID

from polis.store.readmodels.external import market, public_record, recall, remember, whoami

RUN_ID = UUID(int=1)
AGENT_ID = "ag_0000000000000000"


class FakeReader:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []

    async def fetch(
        self, query: str, params: tuple[Any, ...] | list[Any] | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return self.responses.pop(0)


async def test_whoami_returns_only_self_and_protocol_projection() -> None:
    db = FakeReader(
        [
            [
                {
                    "agent_id": AGENT_ID,
                    "born_tick": 1,
                    "age_years": 30.75,
                    "place_id": "pl_1",
                    "home_place_id": "pl_home",
                    "household_id": "hh_1",
                    "generation": 0,
                    "criminal_record": 0,
                    "state": {"wealth_cents": 100, "skills": {"labour": 0.4}},
                    "display_name": "Nikos",
                    "deadlines_missed": 2,
                    "consecutive_misses": 1,
                    "strikes": 0,
                    "protocol_version": 1,
                    "driver": "operator",
                    "next_nonce": 9,
                }
            ]
        ]
    )

    result = await whoami(db, RUN_ID, AGENT_ID, 10)

    assert result["identity"]["agent_id"] == AGENT_ID
    assert result["identity"]["age_years"] == 30.75
    assert result["standing"]["wealth_cents"] == 100
    assert result["protocol"]["next_nonce"] == 9
    assert "events" not in db.calls[0][0].casefold()


async def test_recall_is_scoped_to_the_callers_own_memories() -> None:
    db = FakeReader(
        [
            [
                {
                    "memory_id": "me_1",
                    "tick": 3,
                    "type": "plan",
                    "text": "repay the loan",
                    "importance": 0.8,
                    "score": 1.1,
                    "parent_memory_ids": [],
                }
            ]
        ]
    )

    result = await recall(
        db,
        RUN_ID,
        AGENT_ID,
        "loan",
        k=4,
        mtype=None,
        since_tick=None,
        at_tick=10,
    )

    assert result["memories"][0]["memory_id"] == "me_1"
    assert db.calls[0][1][0] == 10
    assert db.calls[0][1][2] == AGENT_ID
    assert "%s-last_accessed_tick" in db.calls[0][0]


async def test_remember_clamps_importance_and_drops_unheld_citations() -> None:
    db = FakeReader([[{"memory_id": "me_1"}]])

    result = await remember(
        db,
        RUN_ID,
        AGENT_ID,
        {
            "text": "A short note",
            "importance": 1.0,
            "supported_by": ["me_1", "me_secret"],
        },
    )

    assert result["importance_assigned"] < 1
    assert result["supported_by"] == ["me_1"]
    assert result["citations_dropped"] == ["me_secret"]


async def test_market_reads_only_the_public_market_view() -> None:
    db = FakeReader(
        [
            [
                {
                    "run_id": RUN_ID,
                    "symbol": "ACME",
                    "trader_id": "ag_private",
                    "side": "buy",
                    "price_cents": 100,
                    "qty": 5,
                    "orders_n": 2,
                    "as_of_tick": 7,
                }
            ]
        ]
    )

    result = await market(db, RUN_ID, AGENT_ID, ["ACME"], [], 3)

    assert result["quotes"][0]["bid_depth"][0]["qty"] == 5
    assert "v_market_visible" in db.calls[0][0]
    assert "trader_id" not in str(result)
    assert "ag_private" not in str(result)


async def test_history_reads_only_the_public_record_view() -> None:
    db = FakeReader(
        [
            [
                {
                    "record_id": "po_1",
                    "tick": 4,
                    "kind": "post",
                    "title": "work",
                    "body": "Hiring now",
                    "actor_id": AGENT_ID,
                }
            ]
        ]
    )

    result = await public_record(
        db,
        RUN_ID,
        AGENT_ID,
        "Hiring",
        kinds=["post"],
        since_tick=None,
        limit=10,
    )

    assert result["records"][0]["source_ref"] == "po_1"
    assert "v_public_record" in db.calls[0][0]
    assert " events " not in db.calls[0][0].casefold()
