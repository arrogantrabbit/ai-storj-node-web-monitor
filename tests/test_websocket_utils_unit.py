import asyncio
from typing import Any, Dict

import pytest

from storj_monitor.websocket_utils import safe_send_json, robust_broadcast


class DummyWS:
    """
    Minimal async WebSocket-like stub for testing safe_send_json and robust_broadcast.
    """

    def __init__(self, closed: bool = False, raise_exc: BaseException = None):
        self.closed = closed
        self._raise_exc = raise_exc
        self.sent: list[Dict[str, Any]] = []

    async def send_json(self, payload):
        if self._raise_exc:
            raise self._raise_exc
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_safe_send_json_success():
    # Arrange
    ws = DummyWS(closed=False)
    payload = {"type": "ping", "data": 1}

    # Act
    ok = await safe_send_json(ws, payload)

    # Assert
    assert ok is True
    assert ws.sent == [payload]


@pytest.mark.asyncio
async def test_safe_send_json_closed_socket_returns_false():
    # Arrange
    ws = DummyWS(closed=True)
    payload = {"type": "ping", "data": 2}

    # Act
    ok = await safe_send_json(ws, payload)

    # Assert
    assert ok is False
    assert ws.sent == []


@pytest.mark.asyncio
async def test_safe_send_json_connection_reset_error_is_suppressed():
    # Arrange: safe_send_json catches ConnectionResetError and returns False
    ws = DummyWS(closed=False, raise_exc=ConnectionResetError("connection reset"))
    payload = {"type": "ping", "data": 3}

    # Act
    ok = await safe_send_json(ws, payload)

    # Assert
    assert ok is False
    assert ws.sent == []


@pytest.mark.asyncio
async def test_robust_broadcast_filters_by_node_and_aggregate_and_sends_concurrently():
    # Recipients:
    # - ws1: viewing Aggregate -> should receive any node broadcast
    # - ws2: viewing "node-a" -> should receive broadcasts for node-a
    # - ws3: viewing "node-b" -> should NOT receive broadcasts for node-a
    ws1 = DummyWS()
    ws2 = DummyWS()
    ws3 = DummyWS()

    websockets = {
        ws1: {"view": ["Aggregate"]},
        ws2: {"view": ["node-a"]},
        ws3: {"view": ["node-b"]},
    }

    payload = {"type": "stats_update", "node_name": "node-a"}

    # Act
    await robust_broadcast(websockets, payload, node_name="node-a")

    # Assert
    assert ws1.sent == [payload]
    assert ws2.sent == [payload]
    assert ws3.sent == []


@pytest.mark.asyncio
async def test_robust_broadcast_no_recipients_is_noop():
    # No recipients filtered by node name -> should not error
    websockets = {}
    payload = {"type": "noop"}
    # Should complete without raising
    await robust_broadcast(websockets, payload, node_name="any")