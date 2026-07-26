from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from polis.config.errors import PolisError

StructuredMode = Literal["schema", "json_mode", "none"]
Billing = Literal["token", "gpu_time", "free"]


@dataclass(frozen=True, slots=True)
class Capabilities:
    context_window: int
    max_output_tokens: int
    structured_output: StructuredMode
    prefix_caching: bool
    max_concurrency: int
    rpm_limit: int | None
    tpm_limit: int | None
    supports_embeddings: bool
    embedding_dim: int | None
    billing: Billing
    price_in_usd_per_mtok: Decimal
    price_out_usd_per_mtok: Decimal
    price_cached_in_usd_per_mtok: Decimal | None
    reports_model_version: bool
    supports_call_seed: bool


@dataclass(frozen=True, slots=True)
class SamplingParams:
    temperature: float
    top_p: float = 1.0
    max_tokens: int = 512
    stop: tuple[str, ...] = ()
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    purpose: str
    system: str
    user: str
    schema: Mapping[str, Any] | None
    sampling: SamplingParams
    call_seed: int
    timeout_s: float


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str
    tokens_in: int
    tokens_out: int
    tokens_cached_in: int
    model_version: str | None
    provider_request_id: str | None
    latency_ms: int
    finish_reason: Literal["stop", "length", "content_filter", "error"]


@dataclass(frozen=True, slots=True)
class HealthReport:
    ok: bool
    lane: str
    model: str
    model_version: str | None
    latency_ms: int
    detail: str = ""


class Provider(Protocol):
    name: str
    model: str
    capabilities: Capabilities

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def health(self) -> HealthReport: ...

    def price(self, tin: int, tout: int, tcached: int = 0) -> Decimal: ...


class ProviderError(PolisError):
    """Provider request failed."""


class ProviderTransient(ProviderError):
    """Retryable provider error."""


class ProviderPermanent(ProviderError):
    """Non-retryable provider error."""


class ProviderRateLimited(ProviderTransient):
    def __init__(self, message: str, retry_after_s: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderTimeout(ProviderTransient):
    """Provider deadline expired."""
