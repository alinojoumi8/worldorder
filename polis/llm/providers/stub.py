"""Deterministic mandatory test provider; never performs I/O or reads ambient state."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from itertools import pairwise
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict

from polis.config.canon import canonical_bytes
from polis.config.errors import PolisError
from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    HealthReport,
)


class StubContractError(PolisError):
    """The deterministic stub could not honour its requested schema."""


class StubConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    embedding_dim: int = 768


def stub_key(request: CompletionRequest) -> bytes:
    return hashlib.sha256(
        canonical_bytes(
            {
                "purpose": request.purpose,
                "system": request.system,
                "user": request.user,
                "schema": request.schema,
                "sampling": {
                    "temperature": request.sampling.temperature,
                    "top_p": request.sampling.top_p,
                    "max_tokens": request.sampling.max_tokens,
                    "stop": request.sampling.stop,
                    "seed": request.sampling.seed,
                },
                "call_seed": request.call_seed,
            }
        )
    ).digest()


def legal_actions_from_prompt(prompt: str) -> list[str]:
    match = re.search(r"## What you can do\s*(.*?)(?:\n## |\Z)", prompt, flags=re.DOTALL)
    if not match:
        return []
    actions: list[str] = []
    for value in re.findall(r"\b[A-Z][A-Z_]{2,}\b", match.group(1)):
        if value not in actions:
            actions.append(value)
    return actions


def ids_from_prompt(prompt: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        prefix: [] for prefix in ("ag", "fm", "pl", "st", "bk", "hh", "pt", "ol")
    }
    for prefix, value in re.findall(r"\b(ag|fm|pl|st|bk|hh|pt|ol)_([a-zA-Z0-9_-]+)\b", prompt):
        full = f"{prefix}_{value}"
        if full not in result[prefix]:
            result[prefix].append(full)
    return result


def _pick(values: Sequence[Any], digest: bytes, offset: int) -> Any:
    if not values:
        raise StubContractError("cannot select from an empty enum")
    return values[digest[offset % len(digest)] % len(values)]


def _synthesise(schema: Mapping[str, Any], digest: bytes, prompt: str, path: str = "") -> Any:
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return _pick(list(schema["enum"]), digest, len(path))
    if "oneOf" in schema:
        branches = list(schema["oneOf"])
        if not branches:
            raise StubContractError("cannot select from an empty oneOf")
        if path.endswith("/action"):
            supported = {
                branch.get("properties", {}).get("type", {}).get("const"): branch
                for branch in branches
            }
            actions = [
                action for action in legal_actions_from_prompt(prompt) if action in supported
            ]
            if actions:
                selected = _pick(actions, digest, len(path))
                return _synthesise(supported[selected], digest, prompt, path)
        return _synthesise(branches[0], digest, prompt, path)
    if "anyOf" in schema:
        return _synthesise(schema["anyOf"][0], digest, prompt, path)
    kind = schema.get("type", "object")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        result: dict[str, Any] = {
            key: _synthesise(properties.get(key, {}), digest, prompt, f"{path}/{key}")
            for key in required
        }
        if path.endswith("/action"):
            actions = legal_actions_from_prompt(prompt)
            type_schema = properties.get("type", {})
            if actions and "type" in result and "const" not in type_schema:
                result["type"] = _pick(actions, digest, len(path))
        return result
    if kind == "array":
        size = int(schema.get("minItems", 0))
        return [
            _synthesise(schema.get("items", {}), digest, prompt, f"{path}/{index}")
            for index in range(size)
        ]
    if kind == "integer":
        minimum = int(schema.get("minimum", 0))
        maximum = int(schema.get("maximum", minimum + 100))
        return minimum + digest[len(path) % len(digest)] % (maximum - minimum + 1)
    if kind == "number":
        numeric_minimum = float(schema.get("minimum", 0))
        numeric_maximum = float(schema.get("maximum", 1))
        ratio = digest[len(path) % len(digest)] / 255
        return round(numeric_minimum + ratio * (numeric_maximum - numeric_minimum), 6)
    if kind == "boolean":
        return bool(digest[len(path) % len(digest)] % 2)
    pattern = str(schema.get("pattern", ""))
    prefix_match = re.fullmatch(r"\^([a-z]{2})_\[a-z0-9_\]\{1,(\d+)\}\$", pattern)
    if prefix_match is not None:
        prefix = prefix_match.group(1)
        max_suffix_length = int(prefix_match.group(2))
        prompted = ids_from_prompt(prompt).get(prefix, [])
        candidate_pattern = re.compile(f"^{prefix}_[a-z0-9_]{{1,{max_suffix_length}}}$")
        for candidate in prompted:
            if candidate_pattern.fullmatch(candidate):
                return candidate
        suffix = hashlib.sha256(digest + path.encode()).hexdigest()[: min(max_suffix_length, 12)]
        return f"{prefix}_{suffix}"
    minimum_length = int(schema.get("minLength", 1))
    text = f"stub-{hashlib.sha256(digest + path.encode()).hexdigest()[:16]}"
    return text[: int(schema.get("maxLength", len(text)))].ljust(minimum_length, "x")


def stub_embedding(text: str, dim: int = 768) -> list[float]:
    vector = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    features = tokens + [f"{a}:{b}" for a, b in pairwise(tokens)]
    for feature in features:
        digest = hashlib.sha256(feature.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class StubProvider:
    name = "stub"
    model = "stub-v1"

    def __init__(self, config: StubConfig | None = None) -> None:
        self.config = config or StubConfig()
        self.capabilities = Capabilities(
            context_window=32_768,
            max_output_tokens=4096,
            structured_output="schema",
            prefix_caching=False,
            max_concurrency=64,
            rpm_limit=None,
            tpm_limit=None,
            supports_embeddings=True,
            embedding_dim=self.config.embedding_dim,
            billing="free",
            price_in_usd_per_mtok=Decimal(0),
            price_out_usd_per_mtok=Decimal(0),
            price_cached_in_usd_per_mtok=None,
            reports_model_version=True,
            supports_call_seed=True,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        digest = stub_key(request)
        if request.schema is None:
            value: Any = {
                "message": f"stub-{digest.hex()[:16]}",
                "purpose": request.purpose,
            }
        else:
            value = _synthesise(request.schema, digest, request.user)
            errors = list(Draft202012Validator(request.schema).iter_errors(value))
            if errors:
                raise StubContractError(errors[0].message)
        text = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return CompletionResponse(
            text=text,
            tokens_in=(len(request.system) + len(request.user)) // 4,
            tokens_out=max(1, len(text) // 4),
            tokens_cached_in=0,
            model_version=self.model,
            provider_request_id=digest.hex()[:16],
            latency_ms=0,
            finish_reason="stop",
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [stub_embedding(text, self.config.embedding_dim) for text in texts]

    async def health(self) -> HealthReport:
        return HealthReport(True, self.name, self.model, self.model, 0)

    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal:
        del tin, tout, tcached
        return Decimal(0)
