import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.websocket_utils import safe_send_json
from storj_monitor.reputation_tracker import track_reputation


class BoomWS:
    """WebSocket stub that raises a generic exception on send_json to hit unexpected exception path."""
    closed = False

    async def send_json(self, payload):
        raise ValueError("unexpected send failure")


@pytest.mark.asyncio
async def test_safe_send_json_unexpected_exception_returns_false():
    ws = BoomWS()
    ok = await safe_send_json(ws, {"type": "test"})
    assert ok is False


@pytest.mark.asyncio
async def test_track_reputation_disqualified_generates_critical_alert():
    class DummyAPI:
        async def get_satellites(self):
            # Disqualified should produce a critical alert
            return [
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhxxxxx",
                    "audit": {"score": 1.0, "successCount": 100, "totalCount": 100},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                    "disqualified": True,
                    "suspended": None,
                }
            ]

    app = {"db_executor": object()}
    with patch("storj_monitor.database.blocking_write_reputation_history", return_value=True):
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            result = await track_reputation(app, "node-x", DummyAPI())

    assert result is not None
    assert any(a.get("severity") == "critical" and "Disqualified" in a.get("title", "") for a in result["alerts"])


@pytest.mark.asyncio
async def test_track_reputation_invalid_format_returns_none():
    class DummyAPI:
        async def get_satellites(self):
            # Invalid type (neither list nor dict)
            return 12345

    app = {"db_executor": object()}
    result = await track_reputation(app, "node-y", DummyAPI())
    assert result is None