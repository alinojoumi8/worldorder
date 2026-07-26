import json
import math

import pytest

from polis.llm.providers.base import CompletionRequest, SamplingParams
from polis.llm.providers.stub import (
    StubProvider,
    legal_actions_from_prompt,
    stub_embedding,
)

ACTION_SCHEMA = {
    "type": "object",
    "required": ["action", "confidence"],
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "object",
            "required": ["type", "params"],
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["MOVE", "REST", "STUDY"]},
                "params": {"type": "object", "additionalProperties": True},
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def request(seed: int = 4) -> CompletionRequest:
    return CompletionRequest(
        purpose="DELIBERATE",
        system="Return an action.",
        user="## What you can do\n- MOVE\n- STUDY\n\nPlaces: pl_001",
        schema=ACTION_SCHEMA,
        sampling=SamplingParams(temperature=0.8, seed=seed),
        call_seed=seed,
        timeout_s=1,
    )


@pytest.mark.asyncio
async def test_stub_is_pure_and_schema_valid() -> None:
    provider = StubProvider()
    first = await provider.complete(request())
    second = await provider.complete(request())
    assert first == second
    assert first.latency_ms == 0
    value = json.loads(first.text)
    assert value["action"]["type"] in {"MOVE", "STUDY"}
    assert 0 <= value["confidence"] <= 1


def test_legal_action_parser_is_scoped() -> None:
    assert legal_actions_from_prompt("## What you can do\nMOVE · REST\n## State\nX") == [
        "MOVE",
        "REST",
    ]
    assert legal_actions_from_prompt("MOVE") == []


def test_stub_embeddings_are_deterministic_and_semantic() -> None:
    base = stub_embedding("school learning education")
    related = stub_embedding("school learning education books")
    unrelated = stub_embedding("rain transit market")

    def cosine(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))

    assert len(base) == 768
    assert math.isclose(math.sqrt(cosine(base, base)), 1.0)
    assert base == stub_embedding("school learning education")
    assert cosine(base, related) > cosine(base, unrelated)
