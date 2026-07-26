from decimal import Decimal

import pytest

from polis.config.settings import LLMBudgetLine, LLMBudgetSettings
from polis.llm.budget import Admission, BudgetGuard
from polis.llm.cache import CacheMissInReplay, CompletionCache, cache_key
from polis.llm.providers.base import SamplingParams


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
