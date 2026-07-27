from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from polis.llm.providers.base import Capabilities, CompletionRequest, SamplingParams
from polis.llm.providers.openai_compat import OpenAICompatProvider


def capabilities() -> Capabilities:
    return Capabilities(
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


@pytest.mark.asyncio
async def test_reasoning_split_is_sent_as_provider_specific_body_field() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "MiniMax-M3",
                "choices": [
                    {
                        "message": {"content": '{"value":7}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = OpenAICompatProvider(
            name="reasoning",
            model="MiniMax-M3",
            base_url="https://example.invalid/v1",
            api_key_env=None,
            capabilities=capabilities(),
            client=client,
            reasoning_split=True,
        )
        response = await provider.complete(
            CompletionRequest(
                purpose="DELIBERATE",
                system="system",
                user="user",
                schema=None,
                sampling=SamplingParams(temperature=0.5, max_tokens=32),
                call_seed=7,
                timeout_s=1,
            )
        )

    assert seen["reasoning_split"] is True
    assert response.text == '{"value":7}'
