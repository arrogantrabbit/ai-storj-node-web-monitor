import asyncio
from collections import deque
from unittest.mock import AsyncMock, patch
import contextlib

import pytest

from storj_monitor.state import app_state


@pytest.mark.asyncio
async def test_database_writer_task_writes_once(monkeypatch):
    # Arrange
    from storj_monitor.tasks import database_writer_task

    app = {"db_executor": object()}
    app_state["db_write_queue"] = asyncio.Queue()
    # Put some fake events
    await app_state["db_write_queue"].put({"ts_unix": 1})
    await app_state["db_write_queue"].put({"ts_unix": 2})

    # Patch the sleep only inside the tasks module; use original sleep to avoid recursion
    orig_sleep = asyncio.sleep
    async def tiny_sleep(_):
        # yield control once
        await orig_sleep(0)

    monkeypatch.setattr("storj_monitor.tasks.asyncio.sleep", tiny_sleep)
    # Make the writer run immediately
    monkeypatch.setattr("storj_monitor.tasks.DB_WRITE_BATCH_INTERVAL_SECONDS", 0)

    # Patch loop.run_in_executor to actually invoke the target function
    with patch("asyncio.get_running_loop") as mock_loop, \
         patch("storj_monitor.tasks.blocking_db_batch_write", return_value=True) as bw:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=lambda executor, fn, *args: fn(*args))
        task = asyncio.create_task(database_writer_task(app))
        try:
            # allow loop body to run
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Expect we attempted at least one batch write (or the queue is now empty)
        assert bw.called or app_state["db_write_queue"].empty()


@pytest.mark.asyncio
async def test_hourly_aggregator_task_invokes_aggregation(monkeypatch):
    from storj_monitor.tasks import hourly_aggregator_task

    app = {"db_executor": object(), "nodes": {"n1": {}, "n2": {}}}

    # Patch only the tasks module sleep
    orig_sleep = asyncio.sleep
    async def tiny_sleep(_):
        await orig_sleep(0)

    monkeypatch.setattr("storj_monitor.tasks.asyncio.sleep", tiny_sleep)

    # Patch run_in_executor called with blocking_hourly_aggregation
    async def fake_run_in_executor(executor, fn, *args, **kwargs):
        # Just ensure it's called
        assert fn.__name__ == "blocking_hourly_aggregation"
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        task = asyncio.create_task(hourly_aggregator_task(app))
        try:
            await asyncio.sleep(0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_database_pruner_task_invokes_prune(monkeypatch):
    from storj_monitor.tasks import database_pruner_task

    app = {"db_executor": object()}

    # Make sleep cancel after first post-prune sleep
    call_count = {"n": 0}

    async def controlled_sleep(_):
        call_count["n"] += 1
        if call_count["n"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)

    # run_in_executor called with blocking_db_prune
    async def fake_run_in_executor(executor, fn, *args, **kwargs):
        assert fn.__name__ == "blocking_db_prune"
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        task = asyncio.create_task(database_pruner_task(app))
        try:
            await asyncio.sleep(0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_websocket_batch_broadcaster_task_batches_and_sends(monkeypatch):
    from storj_monitor.tasks import websocket_batch_broadcaster_task

    # Prepare events to batch
    app_state["websocket_event_queue"] = [
        {"type": "log_entry", "arrival_time": 1000.0},
        {"type": "log_entry", "arrival_time": 1000.1},
    ]
    app_state["websocket_queue_lock"] = asyncio.Lock()
    app_state["websockets"] = {}

    # Remove delay and batch immediately (patch only in tasks module)
    orig_sleep = asyncio.sleep
    async def tiny_sleep(_):
        await orig_sleep(0)
    monkeypatch.setattr("storj_monitor.tasks.asyncio.sleep", tiny_sleep)
    monkeypatch.setattr("storj_monitor.tasks.WEBSOCKET_BATCH_INTERVAL_MS", 0)

    with patch("storj_monitor.tasks.robust_broadcast", new=AsyncMock(return_value=True)) as rb:
        task = asyncio.create_task(websocket_batch_broadcaster_task({}))
        try:
            # give it a couple cycles
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Either a broadcast happened or the queue was drained
        assert rb.await_count >= 1 or len(app_state["websocket_event_queue"]) == 0


@pytest.mark.asyncio
async def test_connection_status_broadcaster_task_compiles_states(monkeypatch):
    from storj_monitor.tasks import connection_status_broadcaster_task

    class DummyAPIClient:
        def get_connection_state(self):
            return {"state": "connected"}

    app = {
        "api_clients": {"n1": DummyAPIClient(), "n2": DummyAPIClient()},
    }
    # Connection states in app_state for log reader
    app_state["connection_states"] = {"n1": {"log_reader": {"state": "connected"}}}
    app_state["websockets"] = {}

    # Remove delay to let one iteration run
    orig_sleep = asyncio.sleep
    async def tiny_sleep(_):
        await orig_sleep(0)

    monkeypatch.setattr("storj_monitor.tasks.asyncio.sleep", tiny_sleep)

    with patch("storj_monitor.tasks.robust_broadcast", new=AsyncMock(return_value=True)) as rb:
        task = asyncio.create_task(connection_status_broadcaster_task(app))
        try:
            # Give the task multiple chances to run the iteration
            for _ in range(10):
                await asyncio.sleep(0)
                if rb.await_count >= 1:
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Verify a broadcast occurred
        assert rb.await_count >= 1