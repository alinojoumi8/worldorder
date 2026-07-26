import socket
from pathlib import Path
from uuid import UUID

import pytest

from polis.config.settings import CacheSettings, load_settings
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
    await router.close()


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_router_replays_from_file_cache_without_a_provider(tmp_path) -> None:
    base = load_settings(Path("configs/smoke.yaml"))
    cache_path = f"file://{tmp_path.as_posix()}"
    live_settings = base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={"cache": CacheSettings(mode="live", path=cache_path)}
            )
        }
    )
    run_id = UUID("20000000-0000-0000-0000-000000000006")
    live = LLMRouter(settings=live_settings, run_id=run_id)
    expected = await live.call(
        Purpose.DELIBERATE,
        "ag_0001",
        1,
        {"prompt": "Persist this exact response."},
        SCHEMA,
    )
    await live.close()

    replay_settings = live_settings.model_copy(
        update={
            "llm": live_settings.llm.model_copy(
                update={"cache": CacheSettings(mode="replay", path=cache_path)}
            )
        }
    )
    replay = LLMRouter(settings=replay_settings, run_id=run_id)
    actual = await replay.call(
        Purpose.DELIBERATE,
        "ag_0001",
        1,
        {"prompt": "Persist this exact response."},
        SCHEMA,
    )
    assert replay.lanes == {}
    assert actual.text == expected.text
    assert actual.call_id == expected.call_id
    assert actual.cost_usd == expected.cost_usd
    assert not actual.cache_hit
    assert replay.cache.hits == 1
    await replay.close()


@pytest.mark.determinism
@pytest.mark.asyncio
async def test_stub_router_succeeds_with_network_blocked(monkeypatch, tmp_path) -> None:
    def reject_network(*_args, **_kwargs):
        raise AssertionError("StubProvider attempted network access")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    base = load_settings(Path("configs/smoke.yaml"))
    settings = base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={
                    "cache": CacheSettings(
                        mode="live",
                        path=f"file://{tmp_path.as_posix()}",
                    )
                }
            )
        }
    )
    router = LLMRouter(
        settings=settings,
        run_id=UUID("20000000-0000-0000-0000-000000000007"),
    )
    result = await router.call(
        Purpose.DELIBERATE,
        "ag_0001",
        1,
        {"prompt": "Choose carefully without a network."},
        SCHEMA,
    )
    assert result.text
    await router.close()
