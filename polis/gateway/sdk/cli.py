"""JSON-in/JSON-out command line client."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
import typer

from polis.gateway.sdk.client import AgentClient, ProtocolResponseError
from polis.gateway.sdk.keys import Keypair
from polis.gateway.sdk.mcp_server import serve_stdio

app = typer.Typer(no_args_is_help=True, help="POLIS external-citizen client")


def _output(value: Any) -> None:
    typer.echo(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def _read_json(path: Path | None, *, stdin: bool) -> dict[str, Any]:
    if stdin:
        raw = sys.stdin.read()
    elif path is not None:
        raw = path.read_text(encoding="utf-8")
    else:
        raise typer.BadParameter("provide --input or --stdin")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"input is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter("input must be one JSON object")
    return value


def _key(path: Path) -> Keypair:
    loaded = Keypair.load(path)
    if loaded is None:
        raise typer.BadParameter(f"key file does not exist: {path}")
    return loaded


async def _client(url: str, key_path: Path, token: str | None = None) -> AgentClient:
    return AgentClient(url, _key(key_path), token=token)


@app.command()
def keygen(path: Annotated[Path, typer.Option()] = Path("polis-agent-key.json")) -> None:
    try:
        key = Keypair.generate().save(path)
    except OSError as exc:
        _output({"error": {"code": "CLIENT_ERROR", "message": type(exc).__name__}})
        raise typer.Exit(1) from exc
    _output({"agent_id": key.agent_id, "pubkey": key.pubkey_hex, "path": str(path)})


@app.command()
def register(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
    input_: Annotated[Path | None, typer.Option("--input", exists=True)] = None,
    stdin: Annotated[bool, typer.Option("--stdin")] = False,
) -> None:
    declaration = _read_json(input_, stdin=stdin)

    async def operation() -> MappingLike:
        client = await _client(url, key)
        async with client:
            return dict(await client.register(declaration))

    _run(operation)


@app.command()
def session(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
    run_id: Annotated[UUID, typer.Option()],
    ttl_s: Annotated[int, typer.Option()] = 3_600,
) -> None:
    async def operation() -> MappingLike:
        client = await _client(url, key)
        async with client:
            return dict(await client.open_session(run_id=run_id, ttl_s=ttl_s))

    _run(operation)


@app.command()
def observe(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
    token: Annotated[str, typer.Option(envvar="POLIS_SESSION_TOKEN")],
) -> None:
    async def operation() -> MappingLike:
        client = await _client(url, key, token)
        async with client:
            return dict(await client.observe())

    _run(operation)


@app.command()
def wait(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
    token: Annotated[str, typer.Option(envvar="POLIS_SESSION_TOKEN")],
    after_tick: Annotated[int, typer.Option()] = 0,
    timeout_ms: Annotated[int, typer.Option()] = 30_000,
) -> None:
    async def operation() -> MappingLike:
        client = await _client(url, key, token)
        async with client:
            return dict(
                await client.wait_for_tick(
                    after_tick=after_tick,
                    timeout_ms=timeout_ms,
                )
            )

    _run(operation)


@app.command()
def mcp(
    url: Annotated[str, typer.Option()],
    token: Annotated[str, typer.Option(envvar="POLIS_SESSION_TOKEN")],
) -> None:
    serve_stdio(base_url=url, token=token)


@app.command()
def act(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
    token: Annotated[str, typer.Option(envvar="POLIS_SESSION_TOKEN")],
    input_: Annotated[Path | None, typer.Option("--input", exists=True)] = None,
    stdin: Annotated[bool, typer.Option("--stdin")] = False,
) -> None:
    value = _read_json(input_, stdin=stdin)

    async def operation() -> MappingLike:
        client = await _client(url, key, token)
        async with client:
            return dict(
                await client.act(
                    str(value.get("type", "")),
                    value.get("params", {}),
                    reasoning=value.get("reasoning"),
                    speech=value.get("speech"),
                    extras=value.get("extras"),
                )
            )

    _run(operation, action=True)


@app.command()
def selftest(
    url: Annotated[str, typer.Option()],
    key: Annotated[Path, typer.Option(exists=True)],
) -> None:
    async def operation() -> MappingLike:
        client = await _client(url, key)
        async with client:
            return dict(await client.selftest())

    _run(operation)


MappingLike = dict[str, Any]


def _run(
    operation: Any,
    *,
    action: bool = False,
) -> None:
    try:
        _output(asyncio.run(operation()))
    except ProtocolResponseError as exc:
        _output(exc.payload)
        action_rejection = action and exc.code not in {
            "SESSION_INVALID",
            "BAD_SIGNATURE",
            "REVOKED",
            "SUSPENDED",
            "TRANSPORT_ERROR",
            "TRANSPORT_INVALID_RESPONSE",
        }
        if not action_rejection:
            raise typer.Exit(1) from exc
    except (httpx.HTTPError, OSError, ValueError) as exc:
        _output({"error": {"code": "CLIENT_ERROR", "message": type(exc).__name__}})
        raise typer.Exit(1) from exc


def main() -> None:
    app()


if __name__ == "__main__":
    main()
