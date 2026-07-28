from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import WebSocketDisconnect

from polis.gateway.app import (
    _accepted_subprotocol,
    _stream_receiver,
    _websocket_token,
)
from polis.gateway.auth import Session
from polis.gateway.errors import ErrorCode, ProtocolError
from polis.gateway.stream import AgentConnectionLimiter, BoundedFrameBuffer


async def test_slow_reader_loses_old_observations_and_gets_degraded_notice() -> None:
    frames = BoundedFrameBuffer(max_frames=4)
    for tick in range(1, 8):
        await frames.put(
            {
                "type": "observation",
                "tick": tick,
                "observation": {"tick": tick},
            }
        )

    notice = await frames.get()
    retained = [await frames.get() for _ in range(4)]

    assert notice is not None
    assert notice["type"] == "notice"
    assert notice["notice"] == "degraded"
    assert notice["detail"] == {
        "reason": "backpressure",
        "observations_dropped": 3,
        "frames_dropped": 0,
    }
    assert [frame["tick"] for frame in retained if frame is not None] == [4, 5, 6, 7]


async def test_full_buffer_preserves_non_droppable_frames() -> None:
    frames = BoundedFrameBuffer(max_frames=2)
    await frames.put({"type": "action.receipt", "tick": 1})
    await frames.put({"type": "error", "tick": 2})

    await frames.put({"type": "observation", "tick": 3})

    notice = await frames.get()
    retained = [await frames.get(), await frames.get()]
    assert notice is not None
    assert notice["detail"]["observations_dropped"] == 1
    assert [frame["type"] for frame in retained if frame is not None] == [
        "action.receipt",
        "error",
    ]


async def test_non_observation_frames_never_backpressure_the_producer() -> None:
    frames = BoundedFrameBuffer(max_frames=1)
    await frames.put({"type": "lifecycle", "tick": 1})
    terminal_put = asyncio.create_task(frames.put({"type": "terminal", "tick": 2}))

    await asyncio.wait_for(terminal_put, timeout=0.1)
    assert terminal_put.done()
    notice = await frames.get()
    retained = await frames.get()

    assert notice is not None
    assert notice["detail"]["frames_dropped"] == 1
    assert retained is not None
    assert retained["type"] == "terminal"


def test_connection_limiter_caps_each_agent_and_releases_capacity() -> None:
    limiter = AgentConnectionLimiter(2)

    assert limiter.acquire("ag_a")
    assert limiter.acquire("ag_a")
    assert not limiter.acquire("ag_a")
    assert limiter.acquire("ag_b")

    limiter.release("ag_a")
    assert limiter.acquire("ag_a")


def test_websocket_subprotocol_accepts_only_a_one_time_ticket() -> None:
    class FakeWebSocket:
        def __init__(self, protocols: list[str], authorization: str | None = None) -> None:
            self.headers = {"authorization": authorization} if authorization is not None else {}
            self.scope = {"subprotocols": protocols}

    bearer = cast(Any, FakeWebSocket([], "Bearer secret-session-token"))
    assert _websocket_token(bearer) == ("secret-session-token", False)
    assert _accepted_subprotocol(bearer) is None

    ticket_protocol = "polis.v1.ticket.short-lived-ticket"
    ticket = cast(Any, FakeWebSocket([ticket_protocol]))
    assert _websocket_token(ticket) == ("short-lived-ticket", True)
    assert _accepted_subprotocol(ticket) == ticket_protocol

    legacy = cast(Any, FakeWebSocket(["polis.v1.secret-session-token"]))
    with pytest.raises(ProtocolError) as caught:
        _websocket_token(legacy)
    assert caught.value.code is ErrorCode.SESSION_INVALID
    assert _accepted_subprotocol(legacy) is None


async def test_action_receipt_frame_owns_its_type_and_tick_fields() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.frames = [json.dumps({"type": "act", "action": {"type": "NULL_ACTION"}})]

        async def receive_text(self) -> str:
            if not self.frames:
                raise WebSocketDisconnect()
            return self.frames.pop(0)

    class FakeTicks:
        @staticmethod
        def snapshot() -> Any:
            return type("Snapshot", (), {"tick": 9})()

    class FakeTools:
        ticks = FakeTicks()

        @staticmethod
        async def call(name: str, arguments: Any, *, session: Session) -> Any:
            del name, arguments, session
            return {"type": "untrusted", "tick": 7, "accepted": True}

    session = Session(
        "ses_1",
        "ag_aaaaaaaaaaaaaaaa",
        "operator",
        None,
        10_000,
        "ws",
        "test",
        "00" * 32,
    )
    frames = BoundedFrameBuffer()
    await _stream_receiver(
        cast(Any, FakeWebSocket()),
        cast(Any, FakeTools()),
        session,
        frames,
        max_frame_bytes=1_024,
    )

    receipt = await frames.get()
    assert receipt == {
        "type": "action.receipt",
        "tick": 9,
        "accepted": True,
    }


async def test_malformed_action_result_returns_error_and_receiver_continues() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.frames = [
                json.dumps({"type": "act", "action": {"type": "NULL_ACTION"}}),
                json.dumps({"type": "ping"}),
            ]

        async def receive_text(self) -> str:
            if not self.frames:
                raise WebSocketDisconnect()
            return self.frames.pop(0)

        async def close(self, *, code: int, reason: str) -> None:
            del code, reason

    class FakeTicks:
        @staticmethod
        def snapshot() -> Any:
            return type("Snapshot", (), {"tick": 9})()

    class FakeTools:
        ticks = FakeTicks()

        @staticmethod
        async def call(name: str, arguments: Any, *, session: Session) -> Any:
            del name, arguments, session
            return object()

    session = Session(
        "ses_1",
        "ag_aaaaaaaaaaaaaaaa",
        "operator",
        None,
        10_000,
        "ws",
        "test",
        "00" * 32,
    )
    frames = BoundedFrameBuffer()
    await _stream_receiver(
        cast(Any, FakeWebSocket()),
        cast(Any, FakeTools()),
        session,
        frames,
        max_frame_bytes=1_024,
    )

    receipt = await frames.get()
    pong = await frames.get()
    assert receipt is not None
    assert receipt["type"] == "action.receipt"
    assert receipt["accepted"] is False
    assert receipt["error"]["code"] == "GATEWAY_DEGRADED"
    assert pong is not None
    assert pong["type"] == "pong"
    assert pong["tick"] == 9
