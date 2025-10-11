import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest


class DummyLoop:
    async def run_in_executor(self, executor, fn, *args):
        # Execute the "blocking" function inline to avoid threads in tests
        return fn(*args)


@pytest.mark.asyncio
async def test_start_and_cleanup_background_tasks_minimal(monkeypatch):
    # Import targets
    from storj_monitor import tasks as tasks_mod
    from storj_monitor.tasks import start_background_tasks, cleanup_background_tasks

    # Minimal app config with both network and file node types
    app = {
        "nodes": {
            "node-net": {"type": "network", "host": "127.0.0.1", "port": 65000},
            "node-file": {"type": "file", "path": "/tmp/does-not-exist.log"},
        }
    }

    # Patch GeoIP reader to a dummy object (avoid filesystem access)
    class DummyReader:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def close(self):
            self.closed = True

    # Patch GeoIP reader class at its import location
    monkeypatch.setattr("geoip2.database.Reader", DummyReader, raising=False)

    # Patch DB init and blocking calls to no-ops (patch at source modules imported inside function)
    monkeypatch.setattr("storj_monitor.db_utils.init_connection_pool", lambda *a, **k: None)
    monkeypatch.setattr("storj_monitor.database.blocking_backfill_hourly_stats", lambda *a, **k: None)
    monkeypatch.setattr("storj_monitor.database.load_initial_state_from_db", lambda *a, **k: {})

    # Avoid real log reader thread and network log reader
    def noop_blocking_log_reader(*args, **kwargs):
        return None

    async def noop_network_log_reader_task(*args, **kwargs):
        # Yield once so the task can start
        await asyncio.sleep(0)

    monkeypatch.setattr("storj_monitor.log_processor.blocking_log_reader", noop_blocking_log_reader)
    monkeypatch.setattr("storj_monitor.log_processor.network_log_reader_task", noop_network_log_reader_task)

    # Avoid API client setup (return no endpoint)
    async def no_endpoint(_cfg):
        return None

    async def noop_setup_api_client(*args, **kwargs):
        return None

    monkeypatch.setattr("storj_monitor.storj_api_client.auto_discover_api_endpoint", no_endpoint)
    monkeypatch.setattr("storj_monitor.storj_api_client.setup_api_client", noop_setup_api_client)

    # Make long-running background tasks immediately yield and be cancellable
    async def short_coro(*args, **kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr("storj_monitor.alert_manager.alert_evaluation_task", short_coro)
    monkeypatch.setattr("storj_monitor.financial_tracker.financial_polling_task", short_coro)

    # Ensure run_in_executor executes inline (no real threads required)
    monkeypatch.setattr("storj_monitor.tasks.asyncio.get_running_loop", lambda: DummyLoop())

    # Start background tasks
    await start_background_tasks(app)

    # Validate basic invariants
    assert "tasks" in app
    assert isinstance(app["tasks"], list)
    assert len(app["tasks"]) >= 1  # startup should create several tasks

    # Immediately cleanup; all tasks should be cancelled and resources closed
    await cleanup_background_tasks(app)

    # All tasks should be completed or cancelled after cleanup
    for t in app.get("tasks", []):
        # task objects could be finished (done) or explicitly cancelled
        assert t.cancelled() or t.done()

    # GeoIP reader should be closed by cleanup
    assert "geoip_reader" in app
    assert getattr(app["geoip_reader"], "closed", False) is True