from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from polis.store.engine import Database
from polis.store.living_city import (
    _coalesce,
    _external_registration_row,
    _external_session_row,
    _fetch_gateway_events,
    _registered_session_rows,
)


class _GatewayEventDatabase:
    def __init__(self) -> None:
        self.query = ""
        self.params: Sequence[Any] | None = None

    async def fetch(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.query = query
        self.params = params
        return []


def test_external_projection_defaults_apply_to_explicit_nulls() -> None:
    agent_id = f"ag_{'12' * 32}"
    base_payload = {
        "pubkey": "12" * 32,
        "operator": None,
        "declared_model": None,
        "declared_scaffold": None,
        "embodiment": "cohort_matched",
        "admitted_tick": 7,
    }

    missing_declaration = _external_registration_row(
        agent_id,
        {"tick": 7},
        {**base_payload, "declaration": None},
    )
    assert missing_declaration["display_name"] == agent_id
    assert missing_declaration["memory"] == "ours"
    assert missing_declaration["protocol_version"] == 1

    explicit_nulls = _external_registration_row(
        agent_id,
        {"tick": 7},
        {
            **base_payload,
            "declaration": {
                "contact": None,
                "display_name": None,
                "declared_model_version": None,
                "scaffold_notes": None,
                "memory": None,
                "sdk_version": None,
                "protocol_version": None,
            },
        },
    )
    assert explicit_nulls["contact"] == ""
    assert explicit_nulls["display_name"] == agent_id
    assert explicit_nulls["declared_model_version"] == ""
    assert explicit_nulls["scaffold_notes"] == ""
    assert explicit_nulls["memory"] == "ours"
    assert explicit_nulls["sdk_version"] == ""
    assert explicit_nulls["protocol_version"] == 1

    session = _external_session_row(
        agent_id,
        {"tick": 8},
        {
            "session_id": "ses_1",
            "custody": "operator",
            "client": None,
            "expires_unix_ms": 123,
        },
    )
    assert session["client"] == {}
    assert _coalesce(None, 0) == 0
    assert _coalesce(4, 0) == 4


def test_external_session_projection_excludes_unregistered_agents() -> None:
    registered_agent_id = f"ag_{'12' * 32}"
    orphan_agent_id = f"ag_{'34' * 32}"
    sessions = {
        "ses_registered": {"agent_id": registered_agent_id},
        "ses_orphan": {"agent_id": orphan_agent_id},
    }
    external_agents = {registered_agent_id: {"agent_id": registered_agent_id}}

    assert _registered_session_rows(sessions, external_agents) == {
        "ses_registered": {"agent_id": registered_agent_id}
    }


async def test_gateway_event_fetch_is_bounded_by_projection_sequence() -> None:
    db = _GatewayEventDatabase()
    run_id = UUID(int=1)

    assert await _fetch_gateway_events(cast(Database, db), run_id, 42) == []

    assert "seq<=%s" in db.query
    assert db.params is not None
    assert db.params[0] == run_id
    assert db.params[-1] == 42
