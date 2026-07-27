from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import httpx

from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    HealthReport,
    ProviderPermanent,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTransient,
)


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key_env: str | None,
        capabilities: Capabilities,
        client: httpx.AsyncClient | None = None,
        reasoning_split: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.capabilities = capabilities
        self.api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        self.client = client or httpx.AsyncClient(timeout=45)
        self._owns_client = client is None
        self.reasoning_split = reasoning_split

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.sampling.temperature,
            "top_p": request.sampling.top_p,
            "max_tokens": request.sampling.max_tokens,
            "stream": False,
        }
        if request.sampling.seed is not None and self.capabilities.supports_call_seed:
            payload["seed"] = request.sampling.seed
        if self.reasoning_split:
            payload["reasoning_split"] = True
        if request.schema and self.capabilities.structured_output == "schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "polis_response", "schema": request.schema},
            }
        started = asyncio.get_running_loop().time()
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=request.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderTransient(str(exc)) from exc
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after", "1")
            try:
                retry_after_s = max(0.0, float(retry_after))
            except ValueError:
                retry_after_s = 1.0
            raise ProviderRateLimited(
                "provider rate limited",
                retry_after_s=retry_after_s,
            )
        if response.status_code >= 500:
            raise ProviderTransient(f"provider returned {response.status_code}")
        if response.status_code >= 400:
            raise ProviderPermanent(
                f"provider returned {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        usage = body.get("usage", {})
        choice = body["choices"][0]
        latency = int((asyncio.get_running_loop().time() - started) * 1000)
        return CompletionResponse(
            text=str(choice["message"]["content"]),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            tokens_cached_in=int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)),
            model_version=str(body.get("model", self.model)),
            provider_request_id=response.headers.get("x-request-id"),
            latency_ms=latency,
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": list(texts)},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda item: item["index"])
        return [[float(value) for value in row["embedding"]] for row in rows]

    async def health(self) -> HealthReport:
        if not self.api_key:
            return HealthReport(False, self.name, self.model, None, 0, "API key is not configured")
        return HealthReport(True, self.name, self.model, None, 0, "credentials present")

    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal:
        uncached = max(0, tin - tcached)
        cached_price = (
            self.capabilities.price_cached_in_usd_per_mtok
            or self.capabilities.price_in_usd_per_mtok
        )
        return (
            Decimal(uncached) * self.capabilities.price_in_usd_per_mtok
            + Decimal(tcached) * cached_price
            + Decimal(tout) * self.capabilities.price_out_usd_per_mtok
        ) / Decimal(1_000_000)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class OllamaProvider(OpenAICompatProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "options": {
                "temperature": request.sampling.temperature,
                "top_p": request.sampling.top_p,
                "num_predict": request.sampling.max_tokens,
            },
        }
        if request.schema:
            payload["format"] = request.schema
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=request.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderTransient(str(exc)) from exc
        if response.status_code == 429:
            raise ProviderRateLimited("provider rate limited")
        if response.status_code >= 500:
            raise ProviderTransient(f"provider returned {response.status_code}")
        if response.status_code >= 400:
            raise ProviderPermanent(f"provider returned {response.status_code}")
        body: Mapping[str, Any] = response.json()
        text = str(body["message"]["content"])
        return CompletionResponse(
            text=text,
            tokens_in=int(body.get("prompt_eval_count", 0)),
            tokens_out=int(body.get("eval_count", 0)),
            tokens_cached_in=0,
            model_version=str(body.get("model", self.model)),
            provider_request_id=None,
            latency_ms=int(int(body.get("total_duration", 0)) / 1_000_000),
            finish_reason="stop" if body.get("done", True) else "length",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self.client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": list(texts)},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        vectors = [[float(value) for value in vector] for vector in response.json()["embeddings"]]
        expected = self.capabilities.embedding_dim
        if expected is not None and any(len(vector) != expected for vector in vectors):
            raise ProviderPermanent(f"embedding dimension must be {expected}")
        return vectors
