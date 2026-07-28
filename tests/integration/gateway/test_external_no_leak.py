from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from polis.store.readmodels.external import market, public_record, recall, remember, whoami

RUN_ID = UUID(int=91)
AGENT_ID = "ag_aaaaaaaaaaaaaaaa"
SECRETS = (
    "PRIVATE_MEMORY_7f8e",
    "OTHER_WEALTH_42d1",
    "SEALED_CASE_99ab",
    "FIRM_PRODUCTIVITY_c301",
)


class PlantedSecretReader:
    async def fetch(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        del params
        if "FROM agents a" in query:
            return [
                {
                    "agent_id": AGENT_ID,
                    "born_tick": 0,
                    "age_years": 31,
                    "place_id": "pl_public",
                    "home_place_id": "pl_home",
                    "household_id": "hh_1",
                    "generation": 0,
                    "criminal_record": 0,
                    "display_name": "Ada",
                    "deadlines_missed": 0,
                    "consecutive_misses": 0,
                    "strikes": 0,
                    "protocol_version": 1,
                    "driver": "operator",
                    "next_nonce": 3,
                    "state": {
                        "wealth_cents": 100,
                        "private_memory": SECRETS[0],
                        "other_agent_wealth": SECRETS[1],
                    },
                    "sealed_case_notes": SECRETS[2],
                    "firm_productivity": SECRETS[3],
                }
            ]
        if "SELECT memory_id FROM memories" in query:
            return [{"memory_id": "me_held", "private_embedding": SECRETS[0]}]
        if "FROM memories" in query:
            return [
                {
                    "memory_id": "me_held",
                    "tick": 2,
                    "type": "observation",
                    "text": "I saw a public square.",
                    "importance": 0.5,
                    "score": 0.75,
                    "parent_memory_ids": [],
                    "other_agent_wealth": SECRETS[1],
                }
            ]
        if "FROM v_market_visible" in query:
            return [
                {
                    "symbol": "ACME",
                    "side": "buy",
                    "price_cents": 10,
                    "qty": 2,
                    "orders_n": 1,
                    "as_of_tick": 4,
                    "productivity": SECRETS[3],
                }
            ]
        if "FROM v_public_record" in query:
            return [
                {
                    "record_id": "pr_1",
                    "tick": 3,
                    "kind": "news",
                    "title": "Public notice",
                    "body": "A public hearing was scheduled.",
                    "actor_id": None,
                    "sealed_case": SECRETS[2],
                }
            ]
        return []


async def test_external_read_surface_never_serialises_planted_forbidden_columns() -> None:
    reader = PlantedSecretReader()
    responses = (
        await whoami(reader, RUN_ID, AGENT_ID, 5),
        await recall(
            reader,
            RUN_ID,
            AGENT_ID,
            "square",
            k=4,
            mtype=None,
            since_tick=None,
            at_tick=5,
        ),
        await remember(
            reader,
            RUN_ID,
            AGENT_ID,
            {
                "text": "Remember the public hearing.",
                "supported_by": ["me_held"],
            },
        ),
        await market(reader, RUN_ID, AGENT_ID, ("ACME",), (), 3),
        await public_record(
            reader,
            RUN_ID,
            AGENT_ID,
            "hearing",
            kinds=("news",),
            since_tick=None,
            limit=10,
        ),
    )

    encoded = json.dumps(responses, sort_keys=True)
    for secret in SECRETS:
        assert secret not in encoded
