from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from polis.config.settings import load_settings
from polis.run_identity import build_run_identity
from polis.store.engine import Database
from polis.store.operations import _stored_replay_metadata


class _Database:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def fetch(self, query: str, params: tuple[object, ...]) -> list[dict[str, Any]]:
        del query, params
        return [self.row]


@pytest.mark.asyncio
async def test_stored_replay_metadata_normalizes_null_hashes() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    identity = build_run_identity(settings, code_git_sha="a" * 40)
    db = _Database(
        {
            "terminal_hash": None,
            "completion_cache_manifest_hash": None,
            "event_count": 0,
            "run_started_payload": identity.event_payload(),
        }
    )

    terminal_hash, event_count, cache_hash, stored_identity = await _stored_replay_metadata(
        cast(Database, db),
        UUID(int=0),
    )

    assert (terminal_hash, event_count, cache_hash) == ("", 0, "")
    assert stored_identity == identity
