from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from polis.llm.lanes import Lane
from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    HealthReport,
    ProviderRateLimited,
    ProviderTransient,
    SamplingParams,
)
from polis.llm.quota import SlidingWindowQuota


class FlakyProvider:
    name = "flaky"
    model = "flaky-v1"
    capabilities = Capabilities(
        context_window=100,
        max_output_tokens=10,
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

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        del request
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderTransient("retry")
        return CompletionResponse("ok", 1, 1, 0, self.model, None, 1, "stop")

    async def embed(self, texts):
        del texts
        return []

    async def health(self) -> HealthReport:
        return HealthReport(True, self.name, self.model, self.model, 0)

    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal:
        del tin, tout, tcached
        return Decimal(0)


class RateLimitedProvider(FlakyProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        del request
        self.calls += 1
        if self.calls <= self.failures:
            raise ProviderRateLimited("retry", retry_after_s=0)
        return CompletionResponse("ok", 1, 1, 0, self.model, None, 1, "stop")


def request() -> CompletionRequest:
    return CompletionRequest(
        purpose="DELIBERATE",
        system="system",
        user="user",
        schema=None,
        sampling=SamplingParams(0),
        call_seed=7,
        timeout_s=1,
    )


@pytest.mark.asyncio
async def test_lane_retries_transient_failures_deterministically() -> None:
    provider = FlakyProvider(failures=2)
    lane = Lane(
        name="flaky",
        provider=provider,
        semaphore=asyncio.Semaphore(1),
        max_retries=2,
        retry_base_seconds=0,
    )
    response = await lane.complete(request())
    assert response.text == "ok"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_lane_stops_after_retry_limit() -> None:
    provider = FlakyProvider(failures=3)
    lane = Lane(
        name="flaky",
        provider=provider,
        semaphore=asyncio.Semaphore(1),
        max_retries=2,
        retry_base_seconds=0,
    )
    with pytest.raises(ProviderTransient):
        await lane.complete(request())
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_lane_retries_rate_limits_up_to_retry_limit() -> None:
    provider = RateLimitedProvider(failures=2)
    lane = Lane(
        name="rate-limited",
        provider=provider,
        semaphore=asyncio.Semaphore(1),
        max_retries=2,
        retry_base_seconds=0,
    )
    response = await lane.complete(request())
    assert response.text == "ok"
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_lane_run_quota_hard_stops_wire_retries(tmp_path) -> None:
    provider = FlakyProvider(failures=3)
    lane = Lane(
        name="run-limited",
        provider=provider,
        semaphore=asyncio.Semaphore(1),
        max_retries=3,
        retry_base_seconds=0,
        run_quota=SlidingWindowQuota(f"file://{tmp_path / 'quota.sqlite3'}"),
        run_quota_scope="polis-run:test",
        run_quota_limit=2,
        run_quota_window_seconds=3600,
    )
    with pytest.raises(ProviderRateLimited):
        await lane.complete(request())
    assert provider.calls == 2
