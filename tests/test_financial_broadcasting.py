import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.state import app_state


class DummyLoop:
    async def run_in_executor(self, executor, fn, *args):
        # Execute the blocking function synchronously for tests
        return fn(*args)


@pytest.mark.asyncio
async def test_broadcast_earnings_update_current_only(monkeypatch):
    # Import target after fixtures are ready
    from storj_monitor.financial_tracker import broadcast_earnings_update

    # Prepare minimal app with one node and a dummy tracker
    app = {
        "nodes": {"node-1": {}},
        "financial_trackers": {},
        "db_executor": object(),  # not used because we patch run_in_executor
    }

    class DummyTracker:
        async def forecast_payout(self, db_path, period, loop, executor):
            return {
                "period": period or "2025-10",
                "forecasted_payout": 1.23,
                "confidence": 0.9,
            }

    app["financial_trackers"]["node-1"] = DummyTracker()

    # Ensure app_state websockets exists
    app_state["websockets"] = {}

    # Patch loop to inline run_in_executor
    monkeypatch.setattr("storj_monitor.financial_tracker.asyncio.get_running_loop", lambda: DummyLoop())

    # Patch the DB reader to return two earnings rows for the node
    fake_rows = [
        {
            "node_name": "node-1",
            "satellite": "12ab34cd",
            "egress_earnings_net": 0.50,
            "storage_earnings_net": 0.25,
            "repair_earnings_net": 0.10,
            "audit_earnings_net": 0.05,
            "total_earnings_net": 0.90,
            "total_earnings_gross": 1.00,
            "held_amount": 0.10,
        },
        {
            "node_name": "node-1",
            "satellite": "aggregate",
            "egress_earnings_net": 0.50,
            "storage_earnings_net": 0.25,
            "repair_earnings_net": 0.10,
            "audit_earnings_net": 0.05,
            "total_earnings_net": 0.90,
            "total_earnings_gross": 1.00,
            "held_amount": 0.10,
        },
    ]
    monkeypatch.setattr(
        "storj_monitor.financial_tracker.blocking_get_latest_earnings",
        lambda db_file, node_names, period: list(fake_rows),
    )

    # Patch broadcast so we can assert it was awaited (patch where it's defined)
    with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock(return_value=True)) as rb:
        await broadcast_earnings_update(app, loop=None, current_period_only=True)

    # Ensure broadcast called once
    assert rb.await_count >= 1

    # Ensure cache populated with aggregate and node-specific entries
    # smoke check: cache updated; keys asserted below
    assert any(isinstance(k, tuple) and k[0] in ("Aggregate", "node-1") for k in app_state.get("earnings_cache", {}).keys())


@pytest.mark.asyncio
async def test_broadcast_earnings_update_all_periods(monkeypatch):
    from storj_monitor.financial_tracker import broadcast_earnings_update

    app = {
        "nodes": {"node-2": {}},
        "financial_trackers": {},
        "db_executor": object(),
    }

    class DummyTracker:
        async def forecast_payout(self, db_path, period, loop, executor):
            return {
                "period": period or "2025-10",
                "forecasted_payout": 5.55,
                "confidence": 0.66,
            }

    app["financial_trackers"]["node-2"] = DummyTracker()
    app_state["websockets"] = {}

    monkeypatch.setattr("storj_monitor.financial_tracker.asyncio.get_running_loop", lambda: DummyLoop())

    fake_rows = [
        {
            "node_name": "node-2",
            "satellite": "sat-XYZ",
            "egress_earnings_net": 1.0,
            "storage_earnings_net": 2.0,
            "repair_earnings_net": 0.0,
            "audit_earnings_net": 0.0,
            "total_earnings_net": 3.0,
            "total_earnings_gross": 3.3,
            "held_amount": 0.3,
        },
    ]
    monkeypatch.setattr(
        "storj_monitor.financial_tracker.blocking_get_latest_earnings",
        lambda db_file, node_names, period: list(fake_rows),
    )

    with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock(return_value=True)) as rb:
        await broadcast_earnings_update(app, loop=None, current_period_only=False)

    assert rb.await_count >= 1


@pytest.mark.asyncio
async def test_determine_node_age_earliest_earning_date(monkeypatch):
    from storj_monitor.financial_tracker import FinancialTracker

    tracker = FinancialTracker("node-age-test")

    # Provide DummyLoop to return earliest earning date
    def loop_factory():
        class _DL:
            async def run_in_executor(self, executor, fn, *args):
                if fn.__name__ == "_blocking_get_earliest_earning_date":
                    return "2024-01-01T00:00:00+00:00"
                return None
        return _DL()

    monkeypatch.setattr("storj_monitor.financial_tracker.asyncio.get_running_loop", loop_factory)

    months = await tracker.determine_node_age("dummy.db", executor=None)
    assert months >= 1
    assert tracker.node_start_date is not None


@pytest.mark.asyncio
async def test_determine_node_age_api_fallback(monkeypatch):
    from storj_monitor.financial_tracker import FinancialTracker

    class DummyAPI:
        is_available = True

        async def get_dashboard(self):
            return {"startedAt": "2024-05-01T00:00:00+00:00"}

    tracker = FinancialTracker("node-age-test", api_client=DummyAPI())

    # First run_in_executor returns None to simulate no earnings history
    def loop_factory():
        class _DL:
            async def run_in_executor(self, executor, fn, *args):
                return None
        return _DL()

    monkeypatch.setattr("storj_monitor.financial_tracker.asyncio.get_running_loop", loop_factory)

    months = await tracker.determine_node_age("dummy.db", executor=None)
    assert months >= 1
    assert tracker.node_start_date is not None


@pytest.mark.asyncio
async def test_determine_node_age_db_fallback(monkeypatch):
    from storj_monitor.financial_tracker import FinancialTracker

    tracker = FinancialTracker("node-age-test", api_client=None)

    # First call returns None (no earnings history), second returns 10 (DB fallback)
    class _DL:
        def __init__(self):
            self.calls = 0

        async def run_in_executor(self, executor, fn, *args):
            self.calls += 1
            if fn.__name__ == "_blocking_get_earliest_earning_date":
                return None
            if fn.__name__ == "_blocking_determine_node_age_from_db":
                return 10
            return None

    monkeypatch.setattr("storj_monitor.financial_tracker.asyncio.get_running_loop", lambda: _DL())

    months = await tracker.determine_node_age("dummy.db", executor=None)
    assert months == 10