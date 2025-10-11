import asyncio
import datetime
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.financial_tracker import FinancialTracker, broadcast_earnings_update


@pytest.mark.asyncio
async def test_determine_node_age_from_earnings_history(monkeypatch):
    ft = FinancialTracker("node-x")
    # Simulate earliest earnings period 2024-01 -> returns an ISO start date string
    earliest_iso = "2024-01-01T00:00:00+00:00"

    async def fake_run_in_executor(executor, fn, *a, **kw):
        # First branch: _blocking_get_earliest_earning_date
        return earliest_iso

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        months = await ft.determine_node_age("dummy.db", executor=None)

    assert isinstance(months, int)
    assert months >= 1
    assert ft.node_start_date is not None


@pytest.mark.asyncio
async def test_determine_node_age_via_api(monkeypatch):
    # No earliest earnings history -> None
    ft = FinancialTracker("node-x")
    ft.api_client = type(
        "C",
        (),
        {
            "is_available": True,
            "get_dashboard": AsyncMock(return_value={"startedAt": "2024-07-01T00:00:00Z"}),
        },
    )()

    # First run_in_executor call: return None to simulate no earnings history.
    # Second branch won't call run_in_executor until DB fallback.
    calls = {"n": 0}

    async def fake_run_in_executor(executor, fn, *a, **kw):
        calls["n"] += 1
        # First call is for _blocking_get_earliest_earning_date -> None
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        months = await ft.determine_node_age("dummy.db", executor=None)

    assert isinstance(months, int) and months >= 1
    assert ft.node_start_date is not None
    assert calls["n"] == 1  # Only the earliest-earnings attempt happened before API path


@pytest.mark.asyncio
async def test_determine_node_age_fallback_db(monkeypatch):
    ft = FinancialTracker("node-x")

    # First run_in_executor: earliest earnings -> None
    # Final fallback: _blocking_determine_node_age_from_db -> return 10 months
    async def fake_run_in_executor(executor, fn, *a, **kw):
        if fn.__name__ == "_blocking_get_earliest_earning_date":
            return None
        if fn.__name__ == "_blocking_determine_node_age_from_db":
            return 10
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        val = await ft.determine_node_age("dummy.db", executor=None)

    assert val == 10


@pytest.mark.asyncio
async def test_track_earnings_writes_estimates(monkeypatch):
    ft = FinancialTracker("node-a")
    now = datetime.datetime.now(datetime.timezone.utc)
    period = now.strftime("%Y-%m")

    # Force calculate_monthly_earnings to return one estimate
    estimate = {
        "timestamp": now,
        "node_name": "node-a",
        "satellite": "aggregate",
        "period": period,
        "egress_bytes": 0,
        "egress_earnings_gross": 0,
        "egress_earnings_net": 0,
        "storage_bytes_hour": 0,
        "storage_earnings_gross": 0,
        "storage_earnings_net": 0,
        "repair_bytes": 0,
        "repair_earnings_gross": 0,
        "repair_earnings_net": 0,
        "audit_bytes": 0,
        "audit_earnings_gross": 0,
        "audit_earnings_net": 0,
        "total_earnings_gross": 0.11,
        "total_earnings_net": 0.11,
        "held_amount": 0.02,
        "node_age_months": 16,
        "held_percentage": 0.0,
        "is_finalized": False,
    }

    async def fake_calc(db_path, per, loop, executor):
        return [estimate]

    monkeypatch.setattr(ft, "calculate_monthly_earnings", fake_calc)

    # run_in_executor for write
    async def fake_run_in_executor(executor, fn, *a, **kw):
        return True

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        await ft.track_earnings("dummy.db", loop=asyncio.get_running_loop(), executor=None)

    assert ft.last_poll_time is not None


@pytest.mark.asyncio
async def test_import_historical_payouts_with_paystubs(monkeypatch):
    class DummyAPI:
        is_available = True

        async def get_payout_paystubs(self, period: str):
            # Return minimal list with one paystub
            return [{"satelliteId": "sat-xyz", "paid": 12345, "held": 2345}]  # micro-dollars

    ft = FinancialTracker("node-hist", api_client=DummyAPI())

    # Sequence:
    # blocking_get_earnings_estimates -> [] (no existing)
    # blocking_write_earnings_estimate -> True
    seq = iter(
        [
            [],  # existing data (None time limit)
            True,  # write for first month encountered
        ]
    )

    async def fake_run_in_executor(executor, fn, *a, **kw):
        try:
            return next(seq)
        except StopIteration:
            # For the rest of months we say "already exists" to move quickly
            return [{}]

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)
        # Should run without raising and write at least once
        await ft.import_historical_payouts("dummy.db", loop=asyncio.get_running_loop(), executor=None)


@pytest.mark.asyncio
async def test_broadcast_earnings_update_current_only(monkeypatch):
    # Prepare app with nodes and a tracker per node
    app = {"nodes": {"n1": {"enabled": True}}, "db_executor": object(), "financial_trackers": {}}
    # Create a dummy tracker that returns a forecast
    t = FinancialTracker("n1")
    async def fake_forecast(db, period, loop, executor):
        return {"forecasted_payout": 1.23, "confidence": 0.9}

    t.forecast_payout = fake_forecast
    app["financial_trackers"]["n1"] = t

    # Return one earnings record from DB
    record = {
        "node_name": "n1",
        "satellite": "121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6",
        "total_earnings_net": 0.55,
        "total_earnings_gross": 0.60,
        "held_amount": 0.05,
        "egress_earnings_net": 0.2,
        "storage_earnings_net": 0.2,
        "repair_earnings_net": 0.1,
        "audit_earnings_net": 0.05,
    }

    async def fake_run_in_executor(executor, fn, *a, **kw):
        # blocking_get_latest_earnings -> return [record]
        return [record]

    with (
        patch("asyncio.get_running_loop") as mock_loop,
        patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock(return_value=True)) as rb,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)

        await broadcast_earnings_update(app, loop=asyncio.get_running_loop(), current_period_only=True)

        assert rb.await_count >= 1