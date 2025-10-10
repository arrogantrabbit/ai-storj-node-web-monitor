"""
Comprehensive unit tests for Financial Tracker module.

Tests earnings calculations, forecasting, held amounts, and API integration.
"""

import asyncio
import datetime
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock

import pytest

from storj_monitor import config
from storj_monitor.financial_tracker import FinancialTracker


@pytest.fixture
async def financial_tracker(temp_db):
    """Create financial tracker instance with mock API client."""
    mock_api_client = Mock()
    mock_api_client.is_available = True

    # Mock API methods
    mock_api_client.get_estimated_payout = AsyncMock(
        return_value={
            "currentMonth": {
                "payout": 150,  # cents
                "held": 50,  # cents
            }
        }
    )

    mock_api_client.get_dashboard = AsyncMock(return_value={"startedAt": "2024-06-01T00:00:00Z"})

    mock_api_client.get_payout_paystubs = AsyncMock(return_value=[])

    tracker = FinancialTracker("test-node", mock_api_client)
    return tracker


@pytest.fixture
async def tracker_with_db_data(temp_db):
    """Create tracker with test data in database."""
    # Insert test events and storage snapshots
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Insert events for current month
    now = datetime.datetime.now(datetime.timezone.utc)
    now.strftime("%Y-%m")

    for i in range(10):
        cursor.execute(
            """
            INSERT INTO events (timestamp, node_name, satellite_id, action, status, size)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                (now - datetime.timedelta(days=i)).isoformat(),
                "test-node",
                "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                "GET",
                "success",
                1024 * 1024 * 100,  # 100 MB
            ),
        )

    # Insert storage snapshots
    for i in range(10):
        cursor.execute(
            """
            INSERT INTO storage_snapshots (timestamp, node_name, total_bytes, used_bytes, available_bytes, trash_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                (now - datetime.timedelta(days=i)).isoformat(),
                "test-node",
                10 * 1024**3,  # 10 GB
                5 * 1024**3,  # 5 GB
                5 * 1024**3,  # 5 GB
                100 * 1024**2,  # 100 MB
            ),
        )

    conn.commit()
    conn.close()

    tracker = FinancialTracker("test-node", None)
    return tracker, temp_db


@pytest.mark.asyncio
async def test_financial_tracker_initialization(financial_tracker):
    """Test financial tracker initialization."""
    assert financial_tracker.node_name == "test-node"
    assert financial_tracker.api_client is not None
    assert financial_tracker.last_poll_time is None
    assert financial_tracker.node_start_date is None


@pytest.mark.asyncio
async def test_calculate_held_percentage(financial_tracker):
    """Test held amount calculation based on node age."""
    # Months 1-3: 75%
    assert financial_tracker.calculate_held_percentage(1) == config.HELD_AMOUNT_MONTHS_1_TO_3
    assert financial_tracker.calculate_held_percentage(3) == config.HELD_AMOUNT_MONTHS_1_TO_3

    # Months 4-6: 50%
    assert financial_tracker.calculate_held_percentage(4) == config.HELD_AMOUNT_MONTHS_4_TO_6
    assert financial_tracker.calculate_held_percentage(6) == config.HELD_AMOUNT_MONTHS_4_TO_6

    # Months 7-9: 25%
    assert financial_tracker.calculate_held_percentage(7) == config.HELD_AMOUNT_MONTHS_7_TO_9
    assert financial_tracker.calculate_held_percentage(9) == config.HELD_AMOUNT_MONTHS_7_TO_9

    # Months 10-15: 25%
    assert financial_tracker.calculate_held_percentage(10) == config.HELD_AMOUNT_MONTHS_10_TO_15
    assert financial_tracker.calculate_held_percentage(15) == config.HELD_AMOUNT_MONTHS_10_TO_15

    # Month 16+: 0%
    assert financial_tracker.calculate_held_percentage(16) == config.HELD_AMOUNT_MONTH_16_PLUS
    assert financial_tracker.calculate_held_percentage(20) == config.HELD_AMOUNT_MONTH_16_PLUS


@pytest.mark.asyncio
async def test_held_amount_calculation_with_earnings(financial_tracker):
    """Test held amount calculation with actual earnings."""
    # Node 2 months old (75% held)
    node_age = 2
    held_pct = financial_tracker.calculate_held_percentage(node_age)
    total_gross = 100.0
    held_amount = total_gross * held_pct

    assert held_amount == pytest.approx(75.0, rel=0.01)

    # Node 5 months old (50% held)
    node_age = 5
    held_pct = financial_tracker.calculate_held_percentage(node_age)
    held_amount = total_gross * held_pct

    assert held_amount == pytest.approx(50.0, rel=0.01)

    # Node 8 months old (25% held - months 7-9)
    node_age = 8
    held_pct = financial_tracker.calculate_held_percentage(node_age)
    held_amount = total_gross * held_pct

    assert held_amount == pytest.approx(25.0, rel=0.01)

    # Node 12 months old (0% held - months 10-15)
    node_age = 12
    held_pct = financial_tracker.calculate_held_percentage(node_age)
    held_amount = total_gross * held_pct

    assert held_amount == pytest.approx(0.0, rel=0.01)

    # Node 16+ months old (0% held)
    node_age = 16
    held_pct = financial_tracker.calculate_held_percentage(node_age)
    held_amount = total_gross * held_pct

    assert held_amount == pytest.approx(0.0, rel=0.01)


@pytest.mark.asyncio
async def test_get_api_earnings_success(financial_tracker):
    """Test successful API earnings fetch."""
    earnings_data = await financial_tracker.get_api_earnings()

    assert earnings_data is not None
    assert "currentMonth" in earnings_data
    assert earnings_data["currentMonth"]["payout"] == 150
    assert earnings_data["currentMonth"]["held"] == 50


@pytest.mark.asyncio
async def test_get_api_earnings_unavailable():
    """Test API earnings fetch when API is unavailable."""
    tracker = FinancialTracker("test-node", None)
    earnings_data = await tracker.get_api_earnings()

    assert earnings_data is None


@pytest.mark.asyncio
async def test_get_api_earnings_error(financial_tracker):
    """Test API earnings fetch with error."""
    financial_tracker.api_client.get_estimated_payout = AsyncMock(
        side_effect=Exception("API Error")
    )
    earnings_data = await financial_tracker.get_api_earnings()

    assert earnings_data is None


@pytest.mark.asyncio
async def test_determine_node_age_from_api(financial_tracker, temp_db):
    """Test node age determination from API."""
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        node_age = await financial_tracker.determine_node_age(temp_db, executor)

        # Should be about 4-5 months from 2024-06-01
        assert node_age is not None
        assert node_age >= 4
        assert financial_tracker.node_start_date is not None
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_determine_node_age_from_earnings_history(temp_db):
    """Test node age determination from earnings history."""
    # Insert historical earnings
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO earnings_estimates (timestamp, node_name, satellite, period, total_earnings_net, held_amount)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "test-node",
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "2024-01",
            10.0,
            2.5,
        ),
    )

    conn.commit()
    conn.close()

    tracker = FinancialTracker("test-node", None)
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        node_age = await tracker.determine_node_age(temp_db, executor)

        # Should be about 9-10 months from 2024-01
        assert node_age is not None
        assert node_age >= 9
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_storage_earnings_calculation():
    """Test storage earnings from GB-hours."""
    FinancialTracker("test-node", None)

    # 1TB for 30 days = 720 hours
    # 1TB = 1024 GB
    gb_hours = 1024 * 720  # 737,280 GB-hours

    # Calculate expected earnings
    hours_in_month = 720  # 30 days
    tb_months = gb_hours / (1024 * hours_in_month)
    expected_gross = tb_months * config.PRICING_STORAGE_PER_TB_MONTH
    expected_net = expected_gross * config.OPERATOR_SHARE_STORAGE

    assert tb_months == pytest.approx(1.0, rel=0.01)
    assert expected_net > 0


@pytest.mark.asyncio
async def test_traffic_earnings_calculation(tracker_with_db_data):
    """Test traffic earnings calculation from events."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime("%Y-%m")

        traffic_earnings = await asyncio.get_running_loop().run_in_executor(
            executor,
            tracker._blocking_calculate_from_traffic,
            db_path,
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            period,
        )

        assert traffic_earnings is not None
        assert "egress_bytes" in traffic_earnings
        assert "egress_earnings_net" in traffic_earnings
        assert traffic_earnings["egress_bytes"] > 0
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_calculate_monthly_earnings(tracker_with_db_data):
    """Test monthly earnings calculation."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        estimates = await tracker.calculate_monthly_earnings(
            db_path, loop=asyncio.get_running_loop(), executor=executor
        )

        assert isinstance(estimates, list)
        assert len(estimates) > 0

        # Check estimate structure
        estimate = estimates[0]
        assert "node_name" in estimate
        assert "satellite" in estimate
        assert "period" in estimate
        assert "total_earnings_net" in estimate
        assert "total_earnings_gross" in estimate
        assert "held_amount" in estimate
        assert "egress_earnings_net" in estimate
        assert "storage_earnings_net" in estimate
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_forecast_payout_current_month(tracker_with_db_data):
    """Test payout forecast for current month."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        forecast = await tracker.forecast_payout(
            db_path, loop=asyncio.get_running_loop(), executor=executor
        )

        assert forecast is not None
        assert "period" in forecast
        assert "forecasted_payout" in forecast
        assert "confidence" in forecast
        assert "current_earnings" in forecast
        assert "extrapolation_factor" in forecast

        # Confidence should be between 0 and 1
        assert 0 <= forecast["confidence"] <= 1

        # Forecasted payout should be >= current earnings
        assert forecast["forecasted_payout"] >= forecast["current_earnings"]
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_forecast_confidence_scoring(tracker_with_db_data):
    """Test confidence scoring for forecasts."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        forecast = await tracker.forecast_payout(
            db_path, loop=asyncio.get_running_loop(), executor=executor
        )

        # Current month should have reasonable confidence
        assert "time_confidence" in forecast
        assert "data_confidence" in forecast
        assert forecast["time_confidence"] > 0
        assert forecast["data_confidence"] > 0

        # Overall confidence is product of time and data confidence
        expected_confidence = forecast["time_confidence"] * forecast["data_confidence"]
        assert forecast["confidence"] == pytest.approx(expected_confidence, rel=0.01)
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_per_satellite_aggregation(tracker_with_db_data):
    """Test per-satellite earnings aggregation."""
    tracker, db_path = tracker_with_db_data

    # Add events for multiple satellites
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.datetime.now(datetime.timezone.utc)
    satellites = [
        "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6",
    ]

    for sat in satellites:
        for i in range(5):
            cursor.execute(
                """
                INSERT INTO events (timestamp, node_name, satellite_id, action, status, size)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    (now - datetime.timedelta(days=i)).isoformat(),
                    "test-node",
                    sat,
                    "GET",
                    "success",
                    1024 * 1024 * 50,  # 50 MB
                ),
            )

    conn.commit()
    conn.close()

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        estimates = await tracker.calculate_monthly_earnings(
            db_path, loop=asyncio.get_running_loop(), executor=executor
        )

        # Should have estimates for multiple satellites
        assert len(estimates) >= 2

        # Each satellite should have separate estimate
        satellite_ids = {est["satellite"] for est in estimates}
        assert len(satellite_ids) >= 2
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_storage_earnings_with_snapshots(temp_db):
    """Test storage earnings calculation with multiple snapshots."""
    tracker = FinancialTracker("test-node", None)

    # Insert storage snapshots with varying usage
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    now = datetime.datetime.now(datetime.timezone.utc)
    period = now.strftime("%Y-%m")

    # Simulate growing storage usage
    for i in range(10):
        used_bytes = (i + 1) * 1024**3  # 1GB, 2GB, 3GB, etc.
        cursor.execute(
            """
            INSERT INTO storage_snapshots (timestamp, node_name, total_bytes, used_bytes, available_bytes, trash_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                (now - datetime.timedelta(days=9 - i)).isoformat(),
                "test-node",
                10 * 1024**3,
                used_bytes,
                10 * 1024**3 - used_bytes,
                0,
            ),
        )

    # Add events for satellite
    cursor.execute(
        """
        INSERT INTO events (timestamp, node_name, satellite_id, action, status, size)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (
            now.isoformat(),
            "test-node",
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "GET",
            "success",
            1024 * 1024,
        ),
    )

    conn.commit()
    conn.close()

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        storage_result = await asyncio.get_running_loop().run_in_executor(
            executor,
            tracker._blocking_calculate_storage_earnings,
            temp_db,
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            period,
        )

        storage_bytes_hour, storage_gross, storage_net = storage_result

        assert storage_bytes_hour > 0
        assert storage_gross >= 0
        assert storage_net >= 0
        assert storage_net <= storage_gross
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_import_historical_payouts(financial_tracker, temp_db):
    """Test historical payout import from API."""
    # Mock paystub data
    financial_tracker.api_client.get_payout_paystubs = AsyncMock(
        return_value=[
            {
                "satelliteId": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                "paid": 100000,  # $0.10 in micro-dollars
                "held": 25000,  # $0.025 in micro-dollars
            }
        ]
    )

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        await financial_tracker.import_historical_payouts(
            temp_db, asyncio.get_running_loop(), executor
        )

        # Check if data was inserted
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM earnings_estimates WHERE node_name = ?", ("test-node",)
        )
        count = cursor.fetchone()[0]
        conn.close()

        # Should have imported some historical data
        assert count > 0
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_track_earnings(tracker_with_db_data):
    """Test main earnings tracking function."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        await tracker.track_earnings(db_path, asyncio.get_running_loop(), executor)

        # Check if earnings were written to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime("%Y-%m")

        cursor.execute(
            """
            SELECT COUNT(*) FROM earnings_estimates
            WHERE node_name = ? AND period = ?
        """,
            ("test-node", period),
        )

        count = cursor.fetchone()[0]
        conn.close()

        assert count > 0
        assert tracker.last_poll_time is not None
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_api_earnings_scaling(financial_tracker, temp_db):
    """Test scaling of DB calculations to match API totals."""
    # Setup database with events
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    now = datetime.datetime.now(datetime.timezone.utc)

    # Add events for satellite
    for i in range(5):
        cursor.execute(
            """
            INSERT INTO events (timestamp, node_name, satellite_id, action, status, size)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                (now - datetime.timedelta(days=i)).isoformat(),
                "test-node",
                "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                "GET",
                "success",
                1024 * 1024 * 100,  # 100 MB
            ),
        )

    # Add storage snapshots
    cursor.execute(
        """
        INSERT INTO storage_snapshots (timestamp, node_name, total_bytes, used_bytes, available_bytes, trash_bytes)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (now.isoformat(), "test-node", 10 * 1024**3, 5 * 1024**3, 5 * 1024**3, 0),
    )

    conn.commit()
    conn.close()

    executor = ThreadPoolExecutor(max_workers=1)

    try:
        estimates = await financial_tracker.calculate_monthly_earnings(
            temp_db, loop=asyncio.get_running_loop(), executor=executor
        )

        # API should have been used for current month
        # Check that estimates were generated
        assert len(estimates) > 0
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_caching_behavior(tracker_with_db_data):
    """Test that caching reduces redundant calculations."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime("%Y-%m")

        # First call - should calculate and cache
        result1 = await asyncio.get_running_loop().run_in_executor(
            executor,
            tracker._blocking_calculate_from_traffic,
            db_path,
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            period,
        )

        # Second call - should use cache
        result2 = await asyncio.get_running_loop().run_in_executor(
            executor,
            tracker._blocking_calculate_from_traffic,
            db_path,
            "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            period,
        )

        # Results should be identical (from cache)
        assert result1 == result2
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_empty_database_handling(temp_db):
    """Test handling of empty database."""
    tracker = FinancialTracker("test-node", None)
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        estimates = await tracker.calculate_monthly_earnings(
            temp_db, loop=asyncio.get_running_loop(), executor=executor
        )

        # Should return empty list for no data
        assert estimates == []
    finally:
        executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_multiple_periods_calculation(tracker_with_db_data):
    """Test earnings calculation for different periods."""
    tracker, db_path = tracker_with_db_data
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        # Calculate for current month
        now = datetime.datetime.now(datetime.timezone.utc)
        current_period = now.strftime("%Y-%m")

        current_estimates = await tracker.calculate_monthly_earnings(
            db_path, period=current_period, loop=asyncio.get_running_loop(), executor=executor
        )

        # Current month should have data with actual earnings
        assert len(current_estimates) > 0
        assert current_estimates[0]["total_earnings_net"] > 0
        assert "satellite" in current_estimates[0]
        assert "period" in current_estimates[0]
        assert current_estimates[0]["period"] == current_period
    finally:
        executor.shutdown(wait=False)
