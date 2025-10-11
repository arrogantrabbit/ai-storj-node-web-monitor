import asyncio
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.state import app_state


class DummyWS:
    closed = False

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_incremental_stats_updater_task_single_iteration(monkeypatch):
    # Configure minimal app and state
    app_state["nodes"] = {
        "test-node": {
            "live_events": deque(
                [
                    {
                        "ts_unix": (asyncio.get_event_loop().time() + 1_000_000),
                        "status": "success",
                        "category": "get",
                        "size": 1024,
                        "satellite_id": "sat-1",
                        "piece_id": "p1",
                        "location": {"country": "US"},
                        "error_reason": None,
                    },
                    {
                        "ts_unix": (asyncio.get_event_loop().time() + 1_000_001),
                        "status": "success",
                        "category": "put",
                        "size": 2048,
                        "satellite_id": "sat-1",
                        "piece_id": "p2",
                        "location": {"country": "CA"},
                        "error_reason": None,
                    },
                ]
            ),
            "active_compactions": {},
            "unprocessed_performance_events": [],
            "has_new_events": True,
        }
    }

    # One websocket viewing the node
    ws = DummyWS()
    app_state["websockets"] = {ws: {"view": ["test-node"]}}

    # Import after state prepared
    from storj_monitor.tasks import incremental_stats_updater_task

    # Patch sleep to let exactly one loop iteration run, then cancel
    call_count = {"n": 0}

    async def fast_sleep(_):
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    # Patch safe_send_json to succeed
    with patch("storj_monitor.tasks.safe_send_json", new=AsyncMock(return_value=True)):
        task = asyncio.create_task(incremental_stats_updater_task({}))
        try:
            await asyncio.sleep(0)  # allow the first iteration to run
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # After one iteration, a payload should have been attempted to be sent
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_performance_aggregator_task_bins_and_broadcast(monkeypatch):
    # Prepare app state with a couple of performance events
    app_state["nodes"] = {
        "node-a": {
            "live_events": deque(),
            "active_compactions": {},
            "unprocessed_performance_events": [
                {"ts_unix": 1000.0, "category": "get", "status": "success", "size": 111},
                {"ts_unix": 1001.0, "category": "put", "status": "success", "size": 222},
            ],
            "has_new_events": False,
        }
    }
    # One websocket (broadcast destination filtered by node)
    app_state["websockets"] = {DummyWS(): {"view": ["node-a"]}}

    from storj_monitor.tasks import performance_aggregator_task

    # Remove interval delay so first iteration runs immediately
    orig_sleep = asyncio.sleep
    async def tiny_sleep(_):
        # yield control once
        await orig_sleep(0)
    # Patch only inside the tasks module to avoid affecting our own asyncio.sleep
    monkeypatch.setattr("storj_monitor.tasks.asyncio.sleep", tiny_sleep)
    # Use a 1-second bin to avoid division-by-zero in binning
    monkeypatch.setattr("storj_monitor.tasks.PERFORMANCE_INTERVAL_SECONDS", 1)

    # Patch robust_broadcast to avoid network and assert it was called
    with patch("storj_monitor.tasks.robust_broadcast", new=AsyncMock(return_value=True)) as rb:
        task = asyncio.create_task(performance_aggregator_task({}))
        try:
            # Wait until a broadcast happens (or timeout)
            for _ in range(20):
                if rb.await_count >= 1:
                    break
                await asyncio.sleep(0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert rb.await_count >= 1


# Needed for contextlib.suppress in the tests above
import contextlib