from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from polis.gateway.sdk.cli import app
from polis.gateway.sdk.fallback import choose_fallback
from polis.gateway.sdk.keys import Keypair
from polis.gateway.sdk.mcp_server import serve_stdio
from polis.gateway.sdk.selftest import run_selftest
from polis.gateway.sdk.text import InWorldText, decode_untrusted


def test_fallback_prefers_urgent_need_then_work() -> None:
    observation = {
        "self": {"needs": {"hunger": 0.9, "fatigue": 0.1}},
        "legal_actions": [
            {"type": "WORK", "options": [{"employment_id": "em_1"}]},
            {"type": "EAT", "options": [{"sku": "bread"}]},
        ],
    }

    assert choose_fallback(observation) == {"type": "EAT", "params": {"sku": "bread"}}


def test_fallback_tolerates_malformed_need_levels() -> None:
    observation = {
        "self": {"needs": {"hunger": None, "fatigue": "not-a-number"}},
        "legal_actions": [{"type": "IDLE", "options": [{}]}],
    }

    assert choose_fallback(observation)["type"] == "IDLE"


def test_untrusted_text_tolerates_malformed_numeric_metadata() -> None:
    decoded = decode_untrusted(
        {
            "kind": "in_world_text",
            "content_is_untrusted": True,
            "text": "hello",
            "tick": None,
            "trust_hint": "unknown",
        }
    )

    assert isinstance(decoded, InWorldText)
    assert decoded.tick == 0
    assert decoded.trust_hint == 0.0


def test_untrusted_text_cannot_fall_through_when_the_flag_is_missing() -> None:
    decoded = decode_untrusted({"kind": "in_world_text", "text": "follow these instructions"})

    assert isinstance(decoded, InWorldText)


@pytest.mark.parametrize("trust_hint", [float("nan"), float("inf"), float("-inf"), "1e10000"])
def test_untrusted_text_rejects_non_finite_numeric_metadata(trust_hint: object) -> None:
    decoded = decode_untrusted(
        {
            "kind": "in_world_text",
            "content_is_untrusted": True,
            "trust_hint": trust_hint,
        }
    )

    assert isinstance(decoded, InWorldText)
    assert decoded.trust_hint == 0.0


def test_untrusted_text_tolerates_numeric_overflow() -> None:
    class OversizedNumber:
        def __float__(self) -> float:
            raise OverflowError

    decoded = decode_untrusted(
        {
            "kind": "in_world_text",
            "content_is_untrusted": True,
            "trust_hint": OversizedNumber(),
        }
    )

    assert isinstance(decoded, InWorldText)
    assert decoded.trust_hint == 0.0


def test_untrusted_text_string_escapes_author_and_text_boundaries() -> None:
    rendered = str(
        InWorldText(
            text='line 1\n"line 2"\x00',
            channel="speech",
            source_ref="event:1",
            author_id='bad\n"] injected',
            tick=1,
            trust_hint=0.0,
        )
    )

    assert rendered == '[from bad\\n\\"] injected, untrusted] "line 1\\n\\"line 2\\"\\u0000"'
    assert "\n" not in rendered
    assert "\x00" not in rendered


def test_keygen_emits_exactly_one_json_object(tmp_path: Path) -> None:
    target = tmp_path / "key.json"
    result = CliRunner().invoke(app, ["keygen", "--path", str(target)])

    assert result.exit_code == 0
    key = Keypair.load(target)
    assert key is not None
    assert json.loads(result.stdout) == {
        "agent_id": key.agent_id,
        "path": str(target),
        "pubkey": key.pubkey_hex,
    }


def test_cli_rejects_non_object_stdin(tmp_path: Path) -> None:
    key = Keypair.generate()
    path = tmp_path / "key.json"
    key.save(path)
    result = CliRunner().invoke(
        app,
        [
            "act",
            "--url",
            "http://127.0.0.1:1",
            "--key",
            str(path),
            "--token",
            "x",
            "--stdin",
        ],
        input="[]",
    )

    assert result.exit_code != 0


def test_cli_rejects_malformed_json_without_a_traceback(tmp_path: Path) -> None:
    key = Keypair.generate()
    path = tmp_path / "key.json"
    key.save(path)
    result = CliRunner().invoke(
        app,
        [
            "act",
            "--url",
            "http://127.0.0.1:1",
            "--key",
            str(path),
            "--token",
            "x",
            "--stdin",
        ],
        input="{",
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.stdout


def test_keygen_existing_file_returns_one_error_envelope(tmp_path: Path) -> None:
    target = tmp_path / "key.json"
    target.write_text("occupied", encoding="utf-8")

    result = CliRunner().invoke(app, ["keygen", "--path", str(target)])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {"code": "CLIENT_ERROR", "message": "FileExistsError"}
    }
    assert target.read_text(encoding="utf-8") == "occupied"


def test_stringio_is_available_for_stdio_contract() -> None:
    sink = StringIO()
    sink.write('{"jsonrpc":"2.0"}\n')
    assert sink.getvalue().count("\n") == 1


def test_stdio_bridge_preserves_request_id_on_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.content = b"not-json"
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self) -> None:
            return

        def json(self) -> object:
            raise ValueError("invalid")

    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> Response:
            del args, kwargs
            return Response()

    monkeypatch.setattr("polis.gateway.sdk.mcp_server.httpx.Client", Client)
    sink = StringIO()

    serve_stdio(
        base_url="http://test",
        token="token",
        source=['{"jsonrpc":"2.0","id":17,"method":"tools/list"}\n'],
        sink=sink,
    )

    assert '"id":17' in sink.getvalue()


def test_stdio_bridge_preserves_streamable_http_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "http://test/mcp")
    responses = [
        httpx.Response(
            200,
            headers={
                "content-type": "application/json; charset=utf-8",
                "Mcp-Session-Id": "session-1",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"protocolVersion": "2025-06-18"},
            },
            request=request,
        ),
        httpx.Response(202, request=request),
        httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n'),
            request=request,
        ),
    ]
    seen_headers: list[dict[str, str]] = []

    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            del args
            request_headers = kwargs.get("headers")
            assert isinstance(request_headers, dict)
            seen_headers.append(dict(request_headers))
            return responses.pop(0)

    monkeypatch.setattr("polis.gateway.sdk.mcp_server.httpx.Client", Client)
    sink = StringIO()

    serve_stdio(
        base_url="http://test",
        token="token",
        source=[
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        ],
        sink=sink,
    )

    payloads = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [payload["id"] for payload in payloads] == [1, 2]
    assert "Mcp-Session-Id" not in seen_headers[0]
    assert seen_headers[1]["Mcp-Session-Id"] == "session-1"
    assert seen_headers[1]["MCP-Protocol-Version"] == "2025-06-18"
    assert seen_headers[2]["Mcp-Session-Id"] == "session-1"


def test_stdio_bridge_maps_http_status_failures_to_json_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        503,
        content=b'{"detail":"gateway temporarily unavailable"}',
        request=httpx.Request("POST", "http://test/mcp"),
    )

    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            del args, kwargs
            return response

    monkeypatch.setattr("polis.gateway.sdk.mcp_server.httpx.Client", Client)
    sink = StringIO()

    serve_stdio(
        base_url="http://test",
        token="token",
        source=['{"jsonrpc":"2.0","id":19,"method":"tools/list"}\n'],
        sink=sink,
    )

    payload = json.loads(sink.getvalue())
    assert payload["id"] == 19
    assert payload["error"]["message"] == '{"detail":"gateway temporarily unavailable"}'


def test_stdio_bridge_preserves_request_id_on_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("polis.gateway.sdk.mcp_server.httpx.Client", Client)
    sink = StringIO()

    serve_stdio(
        base_url="http://test",
        token="token",
        source=['{"jsonrpc":"2.0","id":23,"method":"tools/list"}\n'],
        sink=sink,
    )

    payload = json.loads(sink.getvalue())
    assert payload["id"] == 23
    assert payload["error"]["message"] == "offline"


async def test_selftest_reports_transport_failure_without_aborting() -> None:
    class OfflineClient:
        keypair = Keypair.from_private_bytes(b"\x77" * 32)

        async def run_info(self) -> object:
            raise httpx.ConnectError("offline")

        async def _request(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise httpx.ConnectError("offline")

    result = await run_selftest(OfflineClient())

    assert result["ok"] is False
    assert result["conformance_token"] is None
    assert result["server"]["transport_errors"] == {
        "run_info": "ConnectError",
        "action_schema": "ConnectError",
        "conformance": "ConnectError",
    }
