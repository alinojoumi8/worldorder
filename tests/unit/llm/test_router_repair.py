from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from polis.config.settings import CacheSettings, load_settings
from polis.llm.cache import CompletionCache
from polis.llm.lanes import Lane
from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    HealthReport,
)
from polis.llm.purposes import Purpose
from polis.llm.router import LLMRouter

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}


class RepairProvider:
    name = "stub"
    model = "stub-v1"
    capabilities = Capabilities(
        context_window=1000,
        max_output_tokens=100,
        structured_output="none",
        prefix_caching=False,
        max_concurrency=1,
        rpm_limit=None,
        tpm_limit=None,
        supports_embeddings=False,
        embedding_dim=None,
        billing="free",
        price_in_usd_per_mtok=Decimal(0),
        price_out_usd_per_mtok=Decimal(0),
        price_cached_in_usd_per_mtok=None,
        reports_model_version=True,
        supports_call_seed=False,
    )

    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        text = '{"value":7}' if "Validation error:" in request.user else "not json"
        return CompletionResponse(text, 10, 2, 0, self.model, None, 1, "stop")

    async def embed(self, texts):
        del texts
        return []

    async def health(self) -> HealthReport:
        return HealthReport(True, self.name, self.model, self.model, 0)

    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal:
        del tin, tout, tcached
        return Decimal(0)


@pytest.mark.asyncio
async def test_router_renders_schema_repairs_and_aggregates_usage() -> None:
    base = load_settings(Path("configs/smoke.yaml"))
    route = base.llm.routing[Purpose.DELIBERATE.value].model_copy(update={"structured": "repair"})
    settings = base.model_copy(
        update={
            "llm": base.llm.model_copy(
                update={
                    "routing": {**base.llm.routing, Purpose.DELIBERATE.value: route},
                    "providers": {
                        **base.llm.providers,
                        "stub": base.llm.providers["stub"].model_copy(
                            update={"extra": {"render_schema": True}}
                        ),
                    },
                    "cache": CacheSettings(mode="hybrid"),
                }
            )
        }
    )
    provider = RepairProvider()
    lane = Lane(
        name="stub",
        provider=provider,
        semaphore=asyncio.Semaphore(1),
        max_retries=0,
    )
    router = LLMRouter(
        settings=settings,
        run_id=UUID("20000000-0000-0000-0000-000000000008"),
        lanes={"stub": lane},
        cache=CompletionCache(mode="hybrid"),
    )
    try:
        result = await router.call(
            Purpose.DELIBERATE,
            "ag_0001",
            1,
            {"prompt": "Choose."},
            SCHEMA,
        )
    finally:
        await router.close()

    assert result.parsed == {"value": 7}
    assert result.parsed_ok
    assert result.repair_attempts == 1
    assert result.tokens_in == 20
    assert result.tokens_out == 4
    assert len(provider.requests) == 2
    assert "Required JSON Schema" in provider.requests[0].user
    assert "Validation error:" in provider.requests[1].user
