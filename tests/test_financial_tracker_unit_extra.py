import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.financial_tracker import FinancialTracker


def test_calculate_held_percentage_buckets():
    ft = FinancialTracker("node-1")
    # <=3 months
    assert ft.calculate_held_percentage(1) == pytest.approx(
        0.75, rel=1e-6
    )
    assert ft.calculate_held_percentage(3) == pytest.approx(
        0.75, rel=1e-6
    )
    # 4-6
    assert ft.calculate_held_percentage(4) == pytest.approx(
        0.50, rel=1e-6
    )
    assert ft.calculate_held_percentage(6) == pytest.approx(
        0.50, rel=1e-6
    )
    # 7-9
    assert ft.calculate_held_percentage(7) == pytest.approx(
        0.25, rel=1e-6
    )
    assert ft.calculate_held_percentage(9) == pytest.approx(
        0.25, rel=1e-6
    )
    # 10-15
    assert ft.calculate_held_percentage(10) == pytest.approx(
        0.00, rel=1e-6
    )
    assert ft.calculate_held_percentage(15) == pytest.approx(
        0.00, rel=1e-6
    )
    # 16+
    assert ft.calculate_held_percentage(16) == pytest.approx(
        0.00, rel=1e-6
    )
    assert ft.calculate_held_percentage(48) == pytest.approx(
        0.00, rel=1e-6
    )


@pytest.mark.asyncio
async def test_calculate_monthly_earnings_db_fallback_simple(monkeypatch):
    # Arrange
    ft = FinancialTracker("node-1")

    # Ensure we take the DB fallback path (pretend node age is known)
    monkeypatch.setattr(ft, "determine_node_age", AsyncMock(return_value=12))

    # Use a past month (to avoid API current-month branch and extrapolation)
    now = datetime.datetime.now(datetime.timezone.utc)
    past = (now.replace(day=1) - datetime.timedelta(days=35)).strftime("%Y-%m")

    # Prepare side effects for run_in_executor calls in order:
    # 1) _get_satellites_from_db -> ["sat-abc"]
    # 2) _blocking_calculate_from_traffic -> traffic earnings dict
    # 3) _blocking_calculate_storage_earnings -> (storage_bytes_hour, storage_gross, storage_net)
    satellites = ["sat-abc"]
    traffic = {
        "egress_bytes": 1024 * 1024,
        "egress_earnings_gross": 0.002,
        "egress_earnings_net": 0.002,
        "repair_bytes": 0,
        "repair_earnings_gross": 0.0,
        "repair_earnings_net": 0.0,
        "audit_bytes": 0,
        "audit_earnings_gross": 0.0,
        "audit_earnings_net": 0.0,
    }
    storage_tuple = (123456, 0.001, 0.001)

    async def fake_run_in_executor(executor, func, *args, **kwargs):
        # Return based on which function is being requested
        if func.__name__ == "_get_satellites_from_db":
            return satellites
        if func.__name__ == "_blocking_calculate_from_traffic":
            return traffic
        if func.__name__ == "_blocking_calculate_storage_earnings":
            return storage_tuple
        # Default: return None
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)

        # Act
        estimates = await ft.calculate_monthly_earnings("dummy.db", past)

    # Assert
    assert isinstance(estimates, list)
    assert len(estimates) == 1
    est = estimates[0]
    assert est["satellite"] == "sat-abc"
    assert est["period"] == past
    # Totals should sum net parts (traffic + storage)
    assert est["total_earnings_net"] == pytest.approx(traffic["egress_earnings_net"] + storage_tuple[2])


@pytest.mark.asyncio
async def test_forecast_payout_past_month_no_extrapolation(monkeypatch):
    ft = FinancialTracker("node-1")

    # Past period string
    now = datetime.datetime.now(datetime.timezone.utc)
    prev_month = (now.replace(day=1) - datetime.timedelta(days=31)).strftime("%Y-%m")

    # Mock calculate_monthly_earnings to return two satellites worth of estimates
    estimates = [
        {
            "total_earnings_net": 1.23,
            "held_amount": 0.11,
            "storage_bytes_hour": 1000,
        },
        {
            "total_earnings_net": 2.34,
            "held_amount": 0.22,
            "storage_bytes_hour": 0,
        },
    ]
    monkeypatch.setattr(ft, "calculate_monthly_earnings", AsyncMock(return_value=estimates))

    # Act
    result = await ft.forecast_payout("dummy.db", prev_month)

    # Assert: no extrapolation for past month, forecast equals sum of current
    assert result["period"] == prev_month
    assert result["forecasted_payout"] == pytest.approx(1.23 + 2.34)
    # Confidence for past month uses time_confidence=1.0 and data_confidence depends on storage presence
    assert 0.7 <= result["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_import_historical_payouts_skips_existing(monkeypatch):
    # Arrange a tracker with available API client (minimal)
    class DummyAPI:
        is_available = True

        async def get_payout_paystubs(self, period: str):
            # Should not be called because we simulate existing data
            return None

    ft = FinancialTracker("node-1", api_client=DummyAPI())

    # Simulate DB already has data for any period (return non-empty list)
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=[{"exists": True}])
        # Act
        await ft.import_historical_payouts("dummy.db", loop=asyncio.get_running_loop(), executor=None)
        # If it runs without error, it's fine; main purpose is to hit the "skip existing period" path


@pytest.mark.asyncio
async def test_calculate_monthly_earnings_current_month_with_api_scale(monkeypatch):
    # Arrange
    ft = FinancialTracker("node-1")

    # Force node age to a known value
    monkeypatch.setattr(ft, "determine_node_age", AsyncMock(return_value=6))

    # Fake API currentMonth totals (in cents)
    ft.api_client = type("C", (), {"is_available": True, "get_estimated_payout": AsyncMock(return_value={
        "currentMonth": {"payout": 1234, "held": 321}  # $12.34 payout, $3.21 held
    })})()

    # Use current month
    now = datetime.datetime.now(datetime.timezone.utc)
    current = now.strftime("%Y-%m")

    # We need to traverse the "API current month" scaling branch:
    # Provide satellites list then per-satellite traffic/storage with some values to produce db_total_net > 0
    satellites = ["sat-1", "sat-2"]
    traffic_1 = {
        "egress_bytes": 1000,
        "egress_earnings_gross": 0.002,
        "egress_earnings_net": 0.002,
        "repair_bytes": 0,
        "repair_earnings_gross": 0.0,
        "repair_earnings_net": 0.0,
        "audit_bytes": 0,
        "audit_earnings_gross": 0.0,
        "audit_earnings_net": 0.0,
    }
    traffic_2 = {
        "egress_bytes": 2000,
        "egress_earnings_gross": 0.004,
        "egress_earnings_net": 0.004,
        "repair_bytes": 0,
        "repair_earnings_gross": 0.0,
        "repair_earnings_net": 0.0,
        "audit_bytes": 0,
        "audit_earnings_gross": 0.0,
        "audit_earnings_net": 0.0,
    }
    storage_1 = (100, 0.001, 0.001)
    storage_2 = (200, 0.002, 0.002)

    calls = []

    async def fake_run_in_executor(executor, func, *args, **kwargs):
        # Record the function name for sanity
        calls.append(func.__name__)
        if func.__name__ == "_blocking_delete_current_month_estimates":
            return True
        if func.__name__ == "_get_satellites_from_db":
            return satellites
        if func.__name__ == "_blocking_calculate_from_traffic":
            return traffic_1 if args[1] == "sat-1" else traffic_2
        if func.__name__ == "_blocking_calculate_storage_earnings":
            return storage_1 if args[1] == "sat-1" else storage_2
        return None

    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(side_effect=fake_run_in_executor)

        # Act
        estimates = await ft.calculate_monthly_earnings("dummy.db", current)

    # Assert
    assert len(estimates) == 2
    # They should contain held_amount distributed evenly and scaled totals (rough checks)
    held_values = [e["held_amount"] for e in estimates]
    assert pytest.approx(held_values[0]) == held_values[1]
    # Ensure function sequence included expected helpers
    assert "_get_satellites_from_db" in calls
    assert calls.count("_blocking_calculate_from_traffic") == 2
    assert calls.count("_blocking_calculate_storage_earnings") == 2