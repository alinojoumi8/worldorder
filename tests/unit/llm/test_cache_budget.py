from decimal import Decimal

import pytest

from polis.config.settings import LLMBudgetLine, LLMBudgetSettings
from polis.llm.budget import Admission, BudgetGuard
from polis.llm.cache import CacheMissInReplay, CacheRecord, CompletionCache, cache_key
from polis.llm.providers.base import CompletionResponse, SamplingParams


def test_cache_key_is_canonical_and_sensitive() -> None:
    base = dict(
        provider="stub",
        model="stub-v1",
        model_version="v1",
        prompt_template_hash="template",
        prompt_variables={"b": 2, "a": 1},
        sampling=SamplingParams(0.3, seed=1),
        schema_hash="schema",
        call_seed=9,
    )
    first = cache_key(**base)
    reordered = cache_key(**{**base, "prompt_variables": {"a": 1, "b": 2}})
    changed = cache_key(**{**base, "call_seed": 10})
    assert first == reordered
    assert first != changed


@pytest.mark.asyncio
async def test_replay_cache_miss_is_structural() -> None:
    cache = CompletionCache(mode="replay")
    with pytest.raises(CacheMissInReplay):
        await cache.get("missing", rendered_hash="rendered")


@pytest.mark.asyncio
async def test_file_cache_survives_for_offline_replay(tmp_path) -> None:
    path = f"file://{tmp_path.as_posix()}"
    response = CompletionResponse("{}", 10, 2, 0, "v1", "request", 4, "stop")
    live = CompletionCache(mode="live", path=path, namespace="run")
    await live.put(CacheRecord("key", "rendered", response))
    await live.close()

    replay = CompletionCache(mode="replay", path=path, namespace="run")
    restored = await replay.get("key", rendered_hash="rendered")
    assert restored is not None
    assert restored.response == response
    assert not replay.reported_hit("key")
    assert await replay.get("key", rendered_hash="rendered") == restored
    assert replay.reported_hit("key")
    await replay.close()


@pytest.mark.asyncio
async def test_hybrid_cache_reuses_persistent_completion(tmp_path) -> None:
    path = f"file://{tmp_path.as_posix()}"
    response = CompletionResponse("{}", 10, 2, 0, "v1", "request", 4, "stop")
    first = CompletionCache(mode="hybrid", path=path, namespace="run")
    await first.put(CacheRecord("key", "rendered", response))
    await first.close()

    resumed = CompletionCache(mode="hybrid", path=path, namespace="run")
    restored = await resumed.get("key", rendered_hash="rendered")
    assert restored is not None
    assert restored.response == response
    assert resumed.reported_hit("key")
    await resumed.close()


def test_budget_ladder_degrades_then_halts() -> None:
    settings = LLMBudgetSettings(
        lines={"cognition": LLMBudgetLine(calls_per_tick=1, tokens_per_tick=10)},
        usd_per_run=Decimal("1"),
        usd_halt_multiple=Decimal("1.2"),
    )
    budget = BudgetGuard(settings)
    budget.begin_tick(1)
    assert budget.admit("cognition", 5, 5, Decimal(0)) == Admission.PERMIT
    budget.charge("cognition", tokens_in=5, tokens_out=5, usd=Decimal(0))
    assert budget.admit("cognition", 1, 1, Decimal(0)) == Admission.DEGRADE
    budget.cumulative_usd = Decimal("1.3")
    assert budget.admit("cognition", 0, 0, Decimal(0)) == Admission.HALT


def test_budget_hard_stops_at_run_call_limit() -> None:
    settings = LLMBudgetSettings(
        lines={"cognition": LLMBudgetLine(calls_per_tick=3, tokens_per_tick=100)},
        max_calls_per_run=2,
    )
    budget = BudgetGuard(settings)
    budget.begin_tick(1)
    assert budget.admit("cognition", 1, 1, Decimal(0)) == Admission.PERMIT
    assert budget.admit("cognition", 1, 1, Decimal(0)) == Admission.PERMIT
    assert budget.admit("cognition", 1, 1, Decimal(0)) == Admission.HALT
    assert budget.cumulative_calls == 2
    assert budget.binding_constraint == "run.calls_halt"
