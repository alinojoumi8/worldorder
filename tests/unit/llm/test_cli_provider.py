from __future__ import annotations

import json
from decimal import Decimal

import pytest

from polis.llm.providers import cli
from polis.llm.providers.base import Capabilities, CompletionRequest, SamplingParams
from polis.llm.providers.cli import CliProvider, parse_codex_jsonl, parse_grok_json


def capabilities() -> Capabilities:
    return Capabilities(
        context_window=128_000,
        max_output_tokens=8192,
        structured_output="schema",
        prefix_caching=True,
        max_concurrency=1,
        rpm_limit=None,
        tpm_limit=None,
        supports_embeddings=False,
        embedding_dim=None,
        billing="token",
        price_in_usd_per_mtok=Decimal("1"),
        price_out_usd_per_mtok=Decimal("2"),
        price_cached_in_usd_per_mtok=Decimal("0.1"),
        reports_model_version=True,
        supports_call_seed=False,
    )


def request() -> CompletionRequest:
    return CompletionRequest(
        purpose="DELIBERATE",
        system="Stay in character.",
        user="Choose one action.",
        schema={
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        },
        sampling=SamplingParams(temperature=0, max_tokens=32),
        call_seed=42,
        timeout_s=5,
    )


def test_parse_codex_jsonl_uses_final_message_and_usage() -> None:
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
            (
                '{"type":"turn.completed","usage":{"input_tokens":12,'
                '"cached_input_tokens":3,"output_tokens":4}}'
            ),
        ]
    )
    response = parse_codex_jsonl(stdout)
    assert response.text == "final"
    assert response.provider_request_id == "thread-1"
    assert response.tokens_in == 12
    assert response.tokens_cached_in == 3
    assert response.tokens_out == 4


def test_parse_grok_json_counts_cached_input_and_model() -> None:
    response = parse_grok_json(
        json.dumps(
            {
                "text": '{"action":"WAIT"}',
                "stopReason": "EndTurn",
                "requestId": "request-1",
                "usage": {
                    "input_tokens": 20,
                    "cache_read_input_tokens": 5,
                    "output_tokens": 7,
                },
                "modelUsage": {"grok-test": {"modelCalls": 1}},
            }
        ),
        configured_model="configured",
    )
    assert response.text == '{"action":"WAIT"}'
    assert response.tokens_in == 25
    assert response.tokens_cached_in == 5
    assert response.model_version == "grok-test"
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_codex_provider_is_ephemeral_read_only_and_secret_minimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "must-not-leak")
    observed: dict[str, object] = {}

    async def fake_run(
        executable: str,
        args: list[str],
        *,
        stdin: str | None,
        cwd,
        env,
        timeout_s: float,
        output_limit: int,
    ) -> tuple[str, str]:
        observed.update(
            executable=executable,
            args=args,
            stdin=stdin,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            output_limit=output_limit,
        )
        return (
            "\n".join(
                [
                    '{"type":"thread.started","thread_id":"thread-1"}',
                    (
                        '{"type":"item.completed","item":{"type":"agent_message",'
                        '"text":"{\\"action\\":\\"WAIT\\"}"}}'
                    ),
                    (
                        '{"type":"turn.completed","usage":{"input_tokens":10,'
                        '"cached_input_tokens":2,"output_tokens":3}}'
                    ),
                ]
            ),
            "",
        )

    monkeypatch.setattr(cli, "_run_cli", fake_run)
    provider = CliProvider(
        kind="codex_cli",
        name="codex",
        model="codex-test",
        api_key_env=None,
        capabilities=capabilities(),
        allow_readonly_agent=True,
        use_default_model=True,
    )
    try:
        response = await provider.complete(request())
    finally:
        await provider.close()

    args = observed["args"]
    assert isinstance(args, list)
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert args[args.index("--sandbox") :][:2] == ["--sandbox", "read-only"]
    assert "--model" not in args
    assert "MINIMAX_API_KEY" not in observed["env"]
    assert response.text == '{"action":"WAIT"}'
    assert response.latency_ms >= 0


@pytest.mark.asyncio
async def test_grok_provider_disables_tools_and_uses_clean_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_run(
        executable: str,
        args: list[str],
        *,
        stdin: str | None,
        cwd,
        env,
        timeout_s: float,
        output_limit: int,
    ) -> tuple[str, str]:
        observed.update(args=args, stdin=stdin, cwd=cwd, env=env)
        return (
            json.dumps(
                {
                    "text": '{"action":"WAIT"}',
                    "stopReason": "EndTurn",
                    "requestId": "request-1",
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 2,
                        "output_tokens": 3,
                    },
                    "modelUsage": {"grok-test": {"modelCalls": 1}},
                }
            ),
            "",
        )

    monkeypatch.setattr(cli, "_run_cli", fake_run)
    provider = CliProvider(
        kind="grok_cli",
        name="grok",
        model="grok-test",
        api_key_env=None,
        capabilities=capabilities(),
    )
    try:
        response = await provider.complete(request())
        clean_home = provider.environment["GROK_HOME"]
    finally:
        await provider.close()

    args = observed["args"]
    assert isinstance(args, list)
    assert "--tools=" in args
    assert "--no-subagents" in args
    assert "--disable-web-search" in args
    assert observed["stdin"] is None
    assert clean_home != str(provider.sandbox_path)
    assert response.text == '{"action":"WAIT"}'
