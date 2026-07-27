from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from uuid import UUID

import httpx

from polis.config.settings import LLMSettings
from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    Provider,
    ProviderRateLimited,
    ProviderTransient,
)
from polis.llm.providers.cli import CliProvider, cli_extra_bool, cli_extra_int, cli_extra_str
from polis.llm.providers.openai_compat import OllamaProvider, OpenAICompatProvider
from polis.llm.providers.stub import StubProvider
from polis.llm.quota import SlidingWindowQuota


@dataclass(slots=True)
class Lane:
    name: str
    provider: Provider
    semaphore: asyncio.Semaphore
    quota: SlidingWindowQuota | None = None
    quota_scope: str | None = None
    quota_limit: int | None = None
    quota_window_seconds: int = 18_000
    max_retries: int = 2
    retry_base_seconds: float = 0.25
    retry_max_seconds: float = 10.0
    run_quota: SlidingWindowQuota | None = None
    run_quota_scope: str | None = None
    run_quota_limit: int | None = None
    run_quota_window_seconds: int = 604_800

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        async with self.semaphore:
            for attempt in range(self.max_retries + 1):
                if (
                    self.run_quota is not None
                    and self.run_quota_scope is not None
                    and self.run_quota_limit is not None
                ):
                    await self.run_quota.reserve(
                        self.run_quota_scope,
                        limit=self.run_quota_limit,
                        window_seconds=self.run_quota_window_seconds,
                    )
                if (
                    self.quota is not None
                    and self.quota_scope is not None
                    and self.quota_limit is not None
                ):
                    await self.quota.reserve(
                        self.quota_scope,
                        limit=self.quota_limit,
                        window_seconds=self.quota_window_seconds,
                    )
                try:
                    return await self.provider.complete(request)
                except ProviderRateLimited as exc:
                    if attempt >= self.max_retries or exc.retry_after_s > self.retry_max_seconds:
                        raise
                    await asyncio.sleep(
                        max(
                            exc.retry_after_s,
                            self._retry_delay(request.call_seed, attempt),
                        )
                    )
                except ProviderTransient:
                    if attempt >= self.max_retries:
                        raise
                    await asyncio.sleep(self._retry_delay(request.call_seed, attempt))
            raise RuntimeError("provider retry loop exhausted")

    def _retry_delay(self, call_seed: int, attempt: int) -> float:
        maximum: float = min(
            float(self.retry_max_seconds),
            float(self.retry_base_seconds * (2**attempt)),
        )
        digest = hashlib.sha256(f"{call_seed}:{attempt}".encode()).digest()
        fraction: float = int.from_bytes(digest[:8], "big") / (2**64 - 1)
        return maximum * fraction


def build_lanes(
    settings: LLMSettings,
    *,
    http: httpx.AsyncClient | None = None,
    run_id: UUID | None = None,
) -> dict[str, Lane]:
    if settings.cache.mode == "replay":
        return {}
    result: dict[str, Lane] = {}
    for name, config in sorted(settings.providers.items()):
        if config.kind == "stub":
            provider: Provider = StubProvider()
        else:
            capabilities = Capabilities(
                context_window=1_000_000 if config.kind == "minimax" else 128_000,
                max_output_tokens=8192,
                structured_output=config.structured_output,
                prefix_caching=True,
                max_concurrency=config.max_concurrency,
                rpm_limit=config.rpm_limit,
                tpm_limit=config.tpm_limit,
                supports_embeddings=config.kind == "ollama",
                embedding_dim=768 if config.kind == "ollama" else None,
                billing=config.billing,
                price_in_usd_per_mtok=config.price_in_usd_per_mtok,
                price_out_usd_per_mtok=config.price_out_usd_per_mtok,
                price_cached_in_usd_per_mtok=config.price_cached_in_usd_per_mtok,
                reports_model_version=True,
                supports_call_seed=config.kind == "openai_compat",
            )
            route_models = [
                route.model for route in settings.routing.values() if route.lane == name
            ]
            model = route_models[0] if route_models else config.model_version_pin or ""
            if config.kind in {"codex_cli", "grok_cli"}:
                provider = CliProvider(
                    kind=config.kind,
                    name=name,
                    model=model,
                    api_key_env=config.api_key_env,
                    capabilities=capabilities,
                    executable=cli_extra_str(config.extra, "executable"),
                    allow_readonly_agent=cli_extra_bool(config.extra, "allow_readonly_agent"),
                    use_default_model=cli_extra_bool(config.extra, "use_default_model"),
                    output_limit=cli_extra_int(config.extra, "max_output_bytes", 2 * 1024 * 1024),
                )
            else:
                provider_class = OllamaProvider if config.kind == "ollama" else OpenAICompatProvider
                reasoning_split = config.extra.get("reasoning_split", False)
                if not isinstance(reasoning_split, bool):
                    raise ValueError(f"provider {name!r} extra.reasoning_split must be boolean")
                provider = provider_class(
                    name=name,
                    model=model,
                    base_url=config.base_url or "",
                    api_key_env=config.api_key_env,
                    capabilities=capabilities,
                    client=http,
                    reasoning_split=reasoning_split,
                )
        result[name] = Lane(
            name=name,
            provider=provider,
            semaphore=asyncio.Semaphore(config.max_concurrency),
            quota=(
                SlidingWindowQuota(config.quota_path)
                if config.calls_per_window is not None
                else None
            ),
            quota_scope=config.quota_scope or config.api_key_env or name,
            quota_limit=config.calls_per_window,
            quota_window_seconds=config.call_window_seconds,
            max_retries=config.max_retries,
            retry_base_seconds=config.retry_base_seconds,
            retry_max_seconds=config.retry_max_seconds,
            run_quota=(
                SlidingWindowQuota(config.quota_path)
                if run_id is not None and settings.budget.max_calls_per_run is not None
                else None
            ),
            run_quota_scope=f"polis-run:{run_id}" if run_id is not None else None,
            run_quota_limit=settings.budget.max_calls_per_run,
        )
    return result
