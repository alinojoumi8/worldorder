"""Local stdio bridge to the remote Streamable HTTP MCP endpoint."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from typing import Any, TextIO

import httpx


def _sse_data(body: str) -> tuple[str, ...]:
    events: list[str] = []
    data_lines: list[str] = []
    for line in body.splitlines():
        if not line:
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines.clear()
            continue
        if line.startswith("data:"):
            value = line.removeprefix("data:")
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        events.append("\n".join(data_lines))
    return tuple(events)


def _response_payloads(response: httpx.Response) -> tuple[Any, ...]:
    if not response.content:
        return ()
    media_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type == "application/json":
        return (response.json(),)
    if media_type == "text/event-stream":
        return tuple(json.loads(data) for data in _sse_data(response.text) if data)
    raise ValueError(f"unsupported MCP response content type: {media_type or 'missing'}")


def _error_detail(exc: ValueError | httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.content:
        response_detail = exc.response.text[:2_000].strip()
        if response_detail:
            return response_detail
    return str(exc) or type(exc).__name__


def serve_stdio(
    *,
    base_url: str,
    token: str,
    source: Iterable[str] = sys.stdin,
    sink: TextIO = sys.stdout,
) -> None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as client:
        for line in source:
            if not line.strip():
                continue
            request_id: Any = None
            try:
                request: Any = json.loads(line)
                if isinstance(request, dict):
                    request_id = request.get("id")
                response = client.post("/mcp", headers=headers, json=request)
                response.raise_for_status()
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    headers["Mcp-Session-Id"] = session_id
                payloads = _response_payloads(response)
                if (
                    isinstance(request, dict)
                    and request.get("method") == "initialize"
                    and payloads
                    and isinstance(payloads[0], dict)
                ):
                    result = payloads[0].get("result")
                    if isinstance(result, dict):
                        protocol_version = result.get("protocolVersion")
                        if isinstance(protocol_version, str):
                            headers["MCP-Protocol-Version"] = protocol_version
            except (ValueError, httpx.HTTPError) as exc:
                payloads = (
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": _error_detail(exc)},
                    },
                )
            for payload in payloads:
                sink.write(json.dumps(payload, separators=(",", ":")) + "\n")
                sink.flush()


def main() -> None:
    base_url = os.environ.get("POLIS_GATEWAY_URL")
    token = os.environ.get("POLIS_SESSION_TOKEN")
    if not base_url or not token:
        raise SystemExit("POLIS_GATEWAY_URL and POLIS_SESSION_TOKEN are required")
    serve_stdio(base_url=base_url, token=token)
