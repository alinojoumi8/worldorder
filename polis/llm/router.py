from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from polis.config.canon import canonical_bytes, canonical_json, sha256_hex
from polis.config.settings import Settings
from polis.llm.budget import Admission, BudgetGuard
from polis.llm.cache import CacheRecord, CompletionCache, cache_key
from polis.llm.lanes import Lane, build_lanes
from polis.llm.providers.base import CompletionRequest, CompletionResponse, SamplingParams
from polis.llm.purposes import PURPOSE_LINE, Purpose
from polis.llm.structured import extract_and_validate


@dataclass(frozen=True, slots=True)
class CallResult:
    call_id: UUID
    purpose: Purpose
    text: str
    parsed: Mapping[str, Any] | None
    parsed_ok: bool
    lane: str
    model: str
    model_version: str | None
    cache_key: str
    cache_hit: bool
    cache_mode: str
    tokens_in: int
    tokens_out: int
    tokens_cached_in: int
    cost_usd: Decimal
    latency_ms: int
    degraded: bool
    budget_line: str
    provider_request_id: str | None
    error: str | None
    repair_attempts: int = 0


@dataclass(frozen=True, slots=True)
class CallRequest:
    purpose: Purpose
    agent_id: str
    tick: int
    variables: Mapping[str, Any]
    schema: Mapping[str, Any] | None = None


class LLMRouter:
    def __init__(
        self,
        *,
        settings: Settings,
        run_id: UUID,
        lanes: Mapping[str, Lane] | None = None,
        cache: CompletionCache | None = None,
        budget: BudgetGuard | None = None,
    ) -> None:
        self.settings = settings
        self.run_id = run_id
        self.lanes = dict(lanes if lanes is not None else build_lanes(settings.llm, run_id=run_id))
        self.cache = cache or CompletionCache(
            mode=settings.llm.cache.mode,
            l0_entries=settings.llm.cache.l0_entries,
            verify_render=settings.llm.cache.verify_render,
            path=settings.llm.cache.path,
            namespace=str(run_id),
            schema_version=settings.llm.cache.schema_version,
            strict_version=settings.llm.cache.strict_version,
        )
        self.budget = budget or BudgetGuard(settings.llm.budget)

    async def start(self) -> None:
        failures = []
        for name, lane in sorted(self.lanes.items()):
            report = await lane.provider.health()
            if not report.ok:
                failures.append(f"{name}: {report.detail}")
        if failures:
            raise RuntimeError("LLM lane health check failed: " + "; ".join(failures))

    def _seed(self, purpose: Purpose, agent_id: str, tick: int) -> int:
        digest = hashlib.sha256(
            canonical_bytes(
                {
                    "run_id": str(self.run_id),
                    "master_seed": self.settings.run.seed,
                    "purpose": purpose.value,
                    "agent_id": agent_id,
                    "tick": tick,
                }
            )
        ).digest()
        return int.from_bytes(digest[:8], "big")

    async def call(
        self,
        purpose: Purpose,
        agent_id: str,
        tick: int,
        variables: Mapping[str, Any],
        schema: Mapping[str, Any] | None = None,
    ) -> CallResult:
        route = self.settings.llm.routing[purpose.value]
        provider_config = self.settings.llm.providers[route.lane]
        rendered_variables = dict(variables)
        schema_instruction = ""
        if schema is not None and route.structured == "repair":
            schema_instruction = (
                "No deep analysis is needed. Answer directly.\n"
                "## Required JSON Schema\n"
                f"{canonical_json(schema)}\n"
                "Return one JSON object matching this schema and no other text."
            )
        if schema_instruction and provider_config.extra.get("render_schema", False):
            prompt = str(rendered_variables.get("prompt", canonical_json(rendered_variables)))
            rendered_variables["prompt"] = f"{prompt}\n\n{schema_instruction}"
        attempts = [
            await self._call_once(
                purpose,
                agent_id,
                tick,
                rendered_variables,
                schema,
            )
        ]
        if schema is None or route.structured != "repair" or attempts[-1].parsed_ok:
            return attempts[-1]
        for repair_attempt in range(1, 3):
            error = attempts[-1].error or "response did not match the required schema"
            repaired_variables = {
                **rendered_variables,
                "prompt": (
                    f"{rendered_variables['prompt']}\n\n"
                    f"{schema_instruction}\n"
                    f"Validation error: {error}\n"
                    "Return a corrected JSON object only."
                ),
                "repair_attempt": repair_attempt,
            }
            attempts.append(
                await self._call_once(
                    purpose,
                    agent_id,
                    tick,
                    repaired_variables,
                    schema,
                )
            )
            if attempts[-1].parsed_ok:
                return self._aggregate_attempts(attempts)
        exhausted = self._aggregate_attempts(attempts)
        return dataclass_replace(
            exhausted,
            degraded=True,
            error=f"schema_repair_exhausted: {attempts[-1].error}",
        )

    async def _call_once(
        self,
        purpose: Purpose,
        agent_id: str,
        tick: int,
        variables: Mapping[str, Any],
        schema: Mapping[str, Any] | None,
    ) -> CallResult:
        route = self.settings.llm.routing[purpose.value]
        lane = self.lanes.get(route.lane)
        provider_config = self.settings.llm.providers[route.lane]
        provider_name = lane.provider.name if lane is not None else route.lane
        model = route.model
        system = str(variables.get("system", "You are a POLIS agent."))
        user = str(variables.get("prompt", canonical_json(variables)))
        rendered_hash = sha256_hex((system + "\x1f" + user).encode())
        sampling = SamplingParams(
            route.temperature,
            max_tokens=route.max_tokens,
            seed=self._seed(purpose, agent_id, tick),
        )
        key = cache_key(
            provider=provider_name,
            model=model,
            model_version=(
                lane.provider.model
                if lane is not None
                else provider_config.model_version_pin or route.model
            ),
            prompt_template_hash=sha256_hex(route.template.encode()),
            prompt_variables=variables,
            sampling=sampling,
            schema_hash=sha256_hex(canonical_bytes(schema)) if schema else None,
            call_seed=sampling.seed or 0,
        )
        call_id = uuid5(NAMESPACE_URL, f"polis:{self.run_id}:{key}")
        cached = await self.cache.get(key, rendered_hash=rendered_hash)
        if cached is not None:
            return self._result(
                call_id,
                purpose,
                route.lane,
                model,
                key,
                cached.response,
                schema,
                cache_hit=self.cache.reported_hit(key),
                cost=cached.cost_usd,
            )
        if lane is None:
            raise RuntimeError(f"provider lane {route.lane!r} unavailable in replay mode")
        self.budget.begin_tick(tick)
        line = PURPOSE_LINE[purpose]
        estimate_in = (len(system) + len(user)) // 4
        estimate_usd = lane.provider.price(estimate_in, route.max_tokens)
        admission = self.budget.admit(line, estimate_in, route.max_tokens, estimate_usd)
        if admission != Admission.PERMIT:
            empty = CompletionResponse(
                "",
                0,
                0,
                0,
                lane.provider.model,
                None,
                0,
                "error",
            )
            result = self._result(
                call_id,
                purpose,
                route.lane,
                model,
                key,
                empty,
                schema,
                cache_hit=False,
            )
            return dataclass_replace(result, degraded=True, error=admission.value)
        request = CompletionRequest(
            purpose=purpose.value,
            system=system,
            user=user,
            schema=schema,
            sampling=sampling,
            call_seed=sampling.seed or 0,
            timeout_s=self.settings.llm.providers[route.lane].timeout_s,
        )
        response = await lane.complete(request)
        cost = lane.provider.price(
            response.tokens_in,
            response.tokens_out,
            response.tokens_cached_in,
        )
        self.budget.charge(
            line,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            usd=cost,
        )
        await self.cache.put(CacheRecord(key, rendered_hash, response, cost))
        return self._result(
            call_id,
            purpose,
            route.lane,
            model,
            key,
            response,
            schema,
            cache_hit=False,
            cost=cost,
        )

    @staticmethod
    def _aggregate_attempts(attempts: Sequence[CallResult]) -> CallResult:
        final = attempts[-1]
        return dataclass_replace(
            final,
            tokens_in=sum(attempt.tokens_in for attempt in attempts),
            tokens_out=sum(attempt.tokens_out for attempt in attempts),
            tokens_cached_in=sum(attempt.tokens_cached_in for attempt in attempts),
            cost_usd=sum(
                (attempt.cost_usd for attempt in attempts),
                Decimal(0),
            ),
            latency_ms=sum(attempt.latency_ms for attempt in attempts),
            repair_attempts=len(attempts) - 1,
        )

    def _result(
        self,
        call_id: UUID,
        purpose: Purpose,
        lane: str,
        model: str,
        key: str,
        response: CompletionResponse,
        schema: Mapping[str, Any] | None,
        *,
        cache_hit: bool,
        cost: Decimal = Decimal(0),
    ) -> CallResult:
        parsed: Mapping[str, Any] | None = None
        error: str | None = None
        if schema is not None and response.text:
            parsed, error = extract_and_validate(response.text, schema)
        return CallResult(
            call_id=call_id,
            purpose=purpose,
            text=response.text,
            parsed=parsed,
            parsed_ok=schema is None or parsed is not None,
            lane=lane,
            model=model,
            model_version=response.model_version,
            cache_key=key,
            cache_hit=cache_hit,
            cache_mode=self.cache.mode,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            tokens_cached_in=response.tokens_cached_in,
            cost_usd=cost,
            latency_ms=response.latency_ms,
            degraded=False,
            budget_line=PURPOSE_LINE[purpose],
            provider_request_id=response.provider_request_id,
            error=error,
        )

    async def gather(self, requests: Sequence[CallRequest]) -> list[CallResult]:
        return list(
            await asyncio.gather(
                *(
                    self.call(
                        request.purpose,
                        request.agent_id,
                        request.tick,
                        request.variables,
                        request.schema,
                    )
                    for request in requests
                )
            )
        )

    async def embed(
        self, texts: Sequence[str], *, tick: int, owner_id: str = ""
    ) -> list[list[float]]:
        del tick, owner_id
        route = self.settings.llm.routing[Purpose.EMBED.value]
        return await self.lanes[route.lane].provider.embed(texts)

    def model_manifest(self) -> dict[str, dict[str, str | None]]:
        return {
            purpose.value: {
                "lane": route.lane,
                "model": route.model,
                "provider_kind": self.settings.llm.providers[route.lane].kind,
                "model_version_pin": self.settings.llm.providers[route.lane].model_version_pin,
            }
            for purpose in Purpose
            if (route := self.settings.llm.routing.get(purpose.value)) is not None
        }

    async def close(self) -> None:
        for lane in self.lanes.values():
            close = getattr(lane.provider, "close", None)
            if close is not None:
                await close()
        await self.cache.close()


def dataclass_replace(result: CallResult, **changes: Any) -> CallResult:
    from dataclasses import replace

    return replace(result, **changes)
