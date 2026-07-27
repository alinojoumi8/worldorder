from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from polis.llm.providers.base import (
    Capabilities,
    CompletionRequest,
    CompletionResponse,
    HealthReport,
    ProviderPermanent,
    ProviderTimeout,
    ProviderTransient,
)

_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_SAFE_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "GROK_HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


class _OutputLimitExceeded(Exception):
    pass


def _safe_environment(api_key_env: str | None) -> dict[str, str]:
    allowed = set(_SAFE_ENV_KEYS)
    if api_key_env:
        allowed.add(api_key_env)
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


async def _read_limited(
    stream: asyncio.StreamReader | None,
    *,
    limit: int,
) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(_READ_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise _OutputLimitExceeded(f"CLI output exceeded {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=5)
            except TimeoutError:
                killer.kill()
        except OSError:
            process.kill()
    else:
        process.kill()
    if process.returncode is None:
        process.kill()
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _run_cli(
    executable: str,
    args: Sequence[str],
    *,
    stdin: str | None,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    output_limit: int = _MAX_OUTPUT_BYTES,
) -> tuple[str, str]:
    creationflags = (
        int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    )
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            cwd=cwd,
            env=dict(env),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise ProviderPermanent(f"cannot start {executable!r}: {exc}") from exc

    if stdin is not None and process.stdin is not None:
        process.stdin.write(stdin.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

    stdout_task = asyncio.create_task(_read_limited(process.stdout, limit=output_limit))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, limit=output_limit))
    wait_task = asyncio.create_task(process.wait())
    try:
        output = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, wait_task),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        await _terminate_process(process)
        raise ProviderTimeout(f"{executable!r} exceeded {timeout_s:.1f}s") from exc
    except _OutputLimitExceeded as exc:
        await _terminate_process(process)
        raise ProviderPermanent(str(exc)) from exc
    except BaseException:
        await _terminate_process(process)
        raise

    stdout_bytes = output[0]
    stderr_bytes = output[1]
    return_code = output[2]
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if return_code != 0:
        detail = stderr.strip() or stdout.strip() or f"exit code {return_code}"
        raise ProviderTransient(f"{executable!r} failed: {detail[:500]}")
    return stdout, stderr


def _finish_reason(value: str) -> Literal["stop", "length", "content_filter", "error"]:
    normalized = value.casefold()
    if normalized in {"max_tokens", "maxlength", "length"}:
        return "length"
    if normalized in {"content_filter", "contentfilter", "safety"}:
        return "content_filter"
    if normalized in {"endturn", "stop", "stopped"}:
        return "stop"
    return "error"


def parse_codex_jsonl(stdout: str) -> CompletionResponse:
    request_id: str | None = None
    final_message = ""
    error_message = ""
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    for raw_line in stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        if event_type == "thread.started":
            request_id = str(event.get("thread_id", "")) or request_id
        elif event_type == "error":
            error_message = str(event.get("message", "")).strip() or error_message
        elif event_type == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                final_message = str(item.get("text", "")).strip() or final_message
        elif event_type == "turn.completed":
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                input_tokens = int(usage.get("input_tokens", input_tokens))
                cached_input_tokens = int(usage.get("cached_input_tokens", cached_input_tokens))
                output_tokens = int(usage.get("output_tokens", output_tokens))
        elif event_type == "turn.failed":
            error = event.get("error", {})
            if isinstance(error, dict):
                error_message = str(error.get("message", "")).strip() or error_message
    if error_message:
        raise ProviderTransient(error_message)
    if not final_message:
        raise ProviderPermanent("Codex CLI returned no final agent message")
    return CompletionResponse(
        text=final_message,
        tokens_in=input_tokens,
        tokens_out=output_tokens,
        tokens_cached_in=cached_input_tokens,
        model_version=None,
        provider_request_id=request_id,
        latency_ms=0,
        finish_reason="stop",
    )


def parse_grok_json(stdout: str, *, configured_model: str) -> CompletionResponse:
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProviderPermanent("Grok CLI returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise ProviderPermanent("Grok CLI response must be a JSON object")
    raw_text = body.get("text", "")
    text = (
        raw_text.strip()
        if isinstance(raw_text, str)
        else json.dumps(raw_text, sort_keys=True, separators=(",", ":"))
    )
    if not text:
        raise ProviderPermanent("Grok CLI returned no final text")
    usage = body.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    uncached_input = int(usage.get("input_tokens", 0))
    cached_input = int(usage.get("cache_read_input_tokens", 0))
    model_version = configured_model
    model_usage = body.get("modelUsage", {})
    if isinstance(model_usage, dict) and len(model_usage) == 1:
        model_version = str(next(iter(model_usage)))
    return CompletionResponse(
        text=text,
        tokens_in=uncached_input + cached_input,
        tokens_out=int(usage.get("output_tokens", 0)),
        tokens_cached_in=cached_input,
        model_version=model_version,
        provider_request_id=str(body.get("requestId", "")) or None,
        latency_ms=0,
        finish_reason=_finish_reason(str(body.get("stopReason", ""))),
    )


class CliProvider:
    def __init__(
        self,
        *,
        kind: Literal["codex_cli", "grok_cli"],
        name: str,
        model: str,
        api_key_env: str | None,
        capabilities: Capabilities,
        executable: str | None = None,
        allow_readonly_agent: bool = False,
        use_default_model: bool = False,
        output_limit: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        self.kind = kind
        self.name = name
        self.model = model
        self.api_key_env = api_key_env
        self.capabilities = capabilities
        default_executable = (
            "codex.cmd" if kind == "codex_cli" and os.name == "nt" else kind.removesuffix("_cli")
        )
        self.executable = executable or default_executable
        self.allow_readonly_agent = allow_readonly_agent
        self.use_default_model = use_default_model
        self.output_limit = output_limit
        self._sandbox = tempfile.TemporaryDirectory(prefix=f"polis-{kind}-")
        self.sandbox_path = Path(self._sandbox.name).resolve()
        self.environment = _safe_environment(api_key_env)
        if kind == "grok_cli":
            self._prepare_grok_home()

    def _prepare_grok_home(self) -> None:
        source_home = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))).expanduser()
        clean_home = self.sandbox_path / "grok-home"
        clean_home.mkdir(parents=True, exist_ok=True)
        agents_skills = (Path.home() / ".agents" / "skills").as_posix()
        (clean_home / "config.toml").write_text(
            "\n".join(
                [
                    "[compat.cursor]",
                    "skills = false",
                    "rules = false",
                    "agents = false",
                    "mcps = false",
                    "hooks = false",
                    "sessions = false",
                    "",
                    "[compat.claude]",
                    "skills = false",
                    "rules = false",
                    "agents = false",
                    "mcps = false",
                    "hooks = false",
                    "sessions = false",
                    "",
                    "[compat.codex]",
                    "sessions = false",
                    "",
                    "[skills]",
                    f"ignore = [{json.dumps(agents_skills)}]",
                    "",
                    "[plugins]",
                    "enabled = []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        source_auth = source_home / "auth.json"
        target_auth = clean_home / "auth.json"
        if source_auth.is_file():
            with suppress(OSError):
                os.link(source_auth, target_auth)
        self.environment["GROK_HOME"] = str(clean_home)
        for vendor in ("CLAUDE", "CURSOR"):
            for surface in ("SKILLS", "RULES", "AGENTS", "MCPS", "HOOKS"):
                self.environment[f"GROK_{vendor}_{surface}_ENABLED"] = "false"
        self.environment["RUST_LOG"] = "off"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if self.kind == "codex_cli":
            return await self._complete_codex(request)
        return await self._complete_grok(request)

    async def _complete_codex(self, request: CompletionRequest) -> CompletionResponse:
        if not self.allow_readonly_agent:
            raise ProviderPermanent("Codex CLI lane requires extra.allow_readonly_agent=true")
        args = [
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(self.sandbox_path),
            "--color",
            "never",
        ]
        if self.model and not self.use_default_model:
            args.extend(["--model", self.model])
        schema_path: Path | None = None
        if request.schema:
            schema_path = self.sandbox_path / f"schema-{request.call_seed}.json"
            schema_path.write_text(
                json.dumps(request.schema, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            args.extend(["--output-schema", str(schema_path)])
        args.append("-")
        prompt = (
            "Do not use tools. Return only the requested final answer.\n\n"
            f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
        )
        started = asyncio.get_running_loop().time()
        try:
            stdout, _ = await _run_cli(
                self.executable,
                args,
                stdin=prompt,
                cwd=self.sandbox_path,
                env=self.environment,
                timeout_s=request.timeout_s,
                output_limit=self.output_limit,
            )
        finally:
            if schema_path is not None:
                schema_path.unlink(missing_ok=True)
        response = parse_codex_jsonl(stdout)
        latency = int((asyncio.get_running_loop().time() - started) * 1000)
        return replace(response, model_version=self.model, latency_ms=latency)

    async def _complete_grok(self, request: CompletionRequest) -> CompletionResponse:
        prompt_path = self.sandbox_path / f"prompt-{request.call_seed}.txt"
        prompt_path.write_text(
            (
                "Return only the requested final answer. Do not use tools.\n\n"
                f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
            ),
            encoding="utf-8",
        )
        args = [
            "--prompt-file",
            str(prompt_path),
            "--output-format",
            "json",
            "--max-turns",
            "1",
            "--no-subagents",
            "--no-memory",
            "--no-plan",
            "--disable-web-search",
            "--tools=",
            "--permission-mode",
            "dontAsk",
            "--cwd",
            str(self.sandbox_path),
            "--verbatim",
        ]
        if self.model and not self.use_default_model:
            args.extend(["--model", self.model])
        if request.schema:
            args.extend(
                [
                    "--json-schema",
                    json.dumps(request.schema, sort_keys=True, separators=(",", ":")),
                ]
            )
        started = asyncio.get_running_loop().time()
        try:
            stdout, _ = await _run_cli(
                self.executable,
                args,
                stdin=None,
                cwd=self.sandbox_path,
                env=self.environment,
                timeout_s=request.timeout_s,
                output_limit=self.output_limit,
            )
        finally:
            prompt_path.unlink(missing_ok=True)
        response = parse_grok_json(stdout, configured_model=self.model)
        latency = int((asyncio.get_running_loop().time() - started) * 1000)
        return replace(response, latency_ms=latency)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        raise ProviderPermanent(f"{self.kind} does not support embeddings")

    async def health(self) -> HealthReport:
        executable = shutil.which(self.executable, path=self.environment.get("PATH"))
        if executable is None:
            return HealthReport(
                False,
                self.name,
                self.model,
                None,
                0,
                f"executable {self.executable!r} is unavailable",
            )
        if self.kind == "codex_cli" and not self.allow_readonly_agent:
            return HealthReport(
                False,
                self.name,
                self.model,
                None,
                0,
                "extra.allow_readonly_agent must be true",
            )
        if self.kind == "grok_cli":
            clean_home = Path(self.environment["GROK_HOME"])
            if not (clean_home / "auth.json").is_file() and not (
                self.api_key_env and self.environment.get(self.api_key_env)
            ):
                return HealthReport(
                    False,
                    self.name,
                    self.model,
                    None,
                    0,
                    "Grok credentials are unavailable",
                )
        return HealthReport(
            True,
            self.name,
            self.model,
            None,
            0,
            f"{self.kind} executable available; isolated working directory active",
        )

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
        self._sandbox.cleanup()


def cli_extra_bool(extra: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = extra.get(key, default)
    return value if isinstance(value, bool) else default


def cli_extra_int(extra: Mapping[str, Any], key: str, default: int) -> int:
    value = extra.get(key, default)
    return value if isinstance(value, int) and value > 0 else default


def cli_extra_str(extra: Mapping[str, Any], key: str) -> str | None:
    value = extra.get(key)
    return value if isinstance(value, str) and value else None
