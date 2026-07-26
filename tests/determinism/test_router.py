from pathlib import Path
from uuid import UUID

import pytest

from polis.config.settings import load_settings
from polis.llm.purposes import Purpose
from polis.llm.router import LLMRouter

SCHEMA = {
    "type": "object",
    "required": ["value"],
    "additionalProperties": False,
    "properties": {"value": {"type": "integer", "minimum": 0, "maximum": 100}},
}


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_router_cache_and_repeat_are_deterministic() -> None:
    settings = load_settings(Path("configs/smoke.yaml"))
    run_id = UUID("20000000-0000-0000-0000-000000000005")
    router = LLMRouter(settings=settings, run_id=run_id)
    first = await router.call(
        Purpose.DELIBERATE,
        "ag_0001",
        1,
        {"prompt": "Choose carefully."},
        SCHEMA,
    )
    second = await router.call(
        Purpose.DELIBERATE,
        "ag_0001",
        1,
        {"prompt": "Choose carefully."},
        SCHEMA,
    )
    assert first.text == second.text
    assert first.cache_key == second.cache_key
    assert not first.cache_hit
    assert second.cache_hit
    assert first.call_id == second.call_id
