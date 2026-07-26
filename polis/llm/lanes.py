from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from polis.config.settings import LLMSettings
from polis.llm.providers.base import Capabilities, CompletionRequest, CompletionResponse, Provider
from polis.llm.providers.openai_compat import OllamaProvider, OpenAICompatProvider
from polis.llm.providers.stub import StubProvider


@dataclass(slots=True)
class Lane:
    name: str
    provider: Provider
    semaphore: asyncio.Semaphore

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        async with self.semaphore:
            return await self.provider.complete(request)


def build_lanes(settings: LLMSettings, *, http: httpx.AsyncClient | None = None) -> dict[str, Lane]:
    if settings.cache.mode == "replay":
        return {}
    result: dict[str, Lane] = {}
    for name, config in sorted(settings.providers.items()):
        if config.kind == "stub":
            provider: Provider = StubProvider()
        else:
            capabilities = Capabilities(
                context_window=128_000,
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
            provider_class = OllamaProvider if config.kind == "ollama" else OpenAICompatProvider
            provider = provider_class(
                name=name,
                model=model,
                base_url=config.base_url or "",
                api_key_env=config.api_key_env,
                capabilities=capabilities,
                client=http,
            )
        result[name] = Lane(
            name=name,
            provider=provider,
            semaphore=asyncio.Semaphore(config.max_concurrency),
        )
    return result
