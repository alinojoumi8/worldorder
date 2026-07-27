from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from polis.config.settings import CacheSettings, load_settings
from polis.llm.purposes import Purpose
from polis.llm.router import LLMRouter

RUN_ID = UUID("e50f69c2-7b23-56be-a4d9-c6ed76a15332")
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reason"],
    "properties": {
        "action": {"enum": ["IDLE"]},
        "reason": {"type": "string", "maxLength": 80},
    },
}
VARIABLES = {
    "system": "Return one compact JSON object and no other text.",
    "prompt": (
        'Choose the action IDLE. Return {"action":"IDLE","reason":"..."}. '
        "The reason must be at most 80 characters."
    ),
}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def smoke(config: Path) -> dict[str, Any]:
    live_settings = load_settings(config)
    router = LLMRouter(settings=live_settings, run_id=RUN_ID)
    route = live_settings.llm.routing[Purpose.DELIBERATE.value]
    provider_kind = live_settings.llm.providers[route.lane].kind
    await router.start()
    try:
        live = await router.call(
            Purpose.DELIBERATE,
            "ag_live_smoke",
            1,
            VARIABLES,
            SCHEMA,
        )
    finally:
        await router.close()

    replay_settings = live_settings.model_copy(
        update={
            "llm": live_settings.llm.model_copy(
                update={
                    "cache": CacheSettings(
                        mode="replay",
                        path=live_settings.llm.cache.path,
                    )
                }
            )
        }
    )
    replay_router = LLMRouter(settings=replay_settings, run_id=RUN_ID)
    try:
        replay = await replay_router.call(
            Purpose.DELIBERATE,
            "ag_live_smoke",
            1,
            VARIABLES,
            SCHEMA,
        )
    finally:
        await replay_router.close()

    exact = (
        live.call_id == replay.call_id
        and live.text == replay.text
        and live.tokens_in == replay.tokens_in
        and live.tokens_out == replay.tokens_out
        and live.cost_usd == replay.cost_usd
    )
    cost_limit = live_settings.llm.budget.usd_per_run
    max_calls = live_settings.llm.budget.max_calls_per_run
    gates = {
        "real_provider_response": bool(live.text) and live.model_version is not None,
        "real_provider_call": not live.cache_hit,
        "schema_valid": live.parsed_ok,
        "hard_output_token_bound": live.tokens_out <= route.max_tokens,
        "hard_cost_limit": live.cost_usd <= cost_limit,
        "hard_call_limit": max_calls is None or router.budget.cumulative_calls <= max_calls,
        "offline_replay_has_no_lanes": replay_router.lanes == {},
        "offline_replay_cache_hit": replay_router.cache.hits == 1,
        "offline_replay_exact": exact,
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "provider": provider_kind,
        "model": live.model,
        "model_version": live.model_version,
        "call_id": str(live.call_id),
        "parsed_ok": live.parsed_ok,
        "tokens_in": live.tokens_in,
        "tokens_out": live.tokens_out,
        "cost_usd": str(live.cost_usd),
        "hard_cost_limit_usd": str(cost_limit),
        "latency_ms": live.latency_ms,
        "finish_reason": live.error or "stop",
        "offline_replay": {
            "cache_mode": replay.cache_mode,
            "lanes": len(replay_router.lanes),
            "text_sha256_matches": live.text == replay.text,
            "call_id_matches": live.call_id == replay.call_id,
        },
        "gates": gates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded live-provider and cache replay smoke")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/live-minimax-smoke.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/acceptance/live-provider-smoke.json"),
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(smoke(args.config))
    except Exception as exc:
        report = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
