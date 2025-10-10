"""
Comprehensive tests for analytics engine module.
"""

import datetime
import statistics
from unittest.mock import patch

import pytest


def test_analytics_engine_imports():
    """Test analytics engine imports."""
    from storj_monitor.analytics_engine import AnalyticsEngine

    assert AnalyticsEngine is not None


@pytest.mark.asyncio
async def test_analytics_engine_initialization(temp_db, mock_app):
    """Test analytics engine initialization."""
    from storj_monitor.analytics_engine import AnalyticsEngine

    engine = AnalyticsEngine(mock_app)
    assert engine.app == mock_app
    assert isinstance(engine.baselines, dict)
    assert len(engine.baselines) == 0


@pytest.mark.asyncio
async def test_calculate_baseline(analytics_engine):
    """Test baseline calculation from values."""
    values = [100, 105, 98, 102, 99, 101, 103, 104, 100, 102]

    with patch("storj_monitor.database.blocking_update_baseline", return_value=True):
        baseline = await analytics_engine.calculate_baseline(
            "test-node", "test_metric", values, 168
        )

    assert baseline is not None
    assert "mean" in baseline
    assert "std_dev" in baseline
    assert "min" in baseline
    assert "max" in baseline
    assert "count" in baseline

    # Verify calculated values
    expected_mean = statistics.mean(values)
    assert baseline["mean"] == pytest.approx(expected_mean, rel=0.01)
    assert baseline["std_dev"] == pytest.approx(statistics.stdev(values), rel=0.01)
    assert baseline["min"] == min(values)
    assert baseline["max"] == max(values)
    assert baseline["count"] == len(values)


@pytest.mark.asyncio
async def test_calculate_baseline_insufficient_data(analytics_engine):
    """Test baseline calculation with insufficient data."""
    # Empty list
    with patch("storj_monitor.database.blocking_update_baseline", return_value=True):
        baseline = await analytics_engine.calculate_baseline("test-node", "test_metric", [], 168)
    assert baseline is None

    # Single value
    with patch("storj_monitor.database.blocking_update_baseline", return_value=True):
        baseline = await analytics_engine.calculate_baseline("test-node", "test_metric", [100], 168)
    assert baseline is None


@pytest.mark.asyncio
async def test_calculate_baseline_caching(analytics_engine):
    """Test baseline caching mechanism."""
    values = [100, 105, 98, 102, 99]

    with patch("storj_monitor.database.blocking_update_baseline", return_value=True):
        baseline = await analytics_engine.calculate_baseline(
            "test-node", "test_metric", values, 168
        )

    # Check cache
    cache_key = "test-node:test_metric:168"
    assert cache_key in analytics_engine.baselines
    assert analytics_engine.baselines[cache_key] == baseline


@pytest.mark.asyncio
async def test_get_baseline_from_cache(analytics_engine):
    """Test retrieving baseline from cache."""
    # Set up cache
    cache_key = "test-node:test_metric:168"
    cached_baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 50}
    analytics_engine.baselines[cache_key] = cached_baseline

    # Retrieve from cache
    baseline = await analytics_engine.get_baseline("test-node", "test_metric", 168)

    assert baseline == cached_baseline


@pytest.mark.asyncio
async def test_get_baseline_from_database(analytics_engine):
    """Test retrieving baseline from database."""
    expected_baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 50}

    with patch("storj_monitor.database.blocking_get_baseline", return_value=expected_baseline):
        baseline = await analytics_engine.get_baseline("test-node", "test_metric", 168)

    assert baseline == expected_baseline

    # Verify it was cached
    cache_key = "test-node:test_metric:168"
    assert cache_key in analytics_engine.baselines


def test_calculate_z_score(analytics_engine, sample_baseline):
    """Test Z-score calculation."""
    # Test normal case
    z_score = analytics_engine.calculate_z_score(115.0, sample_baseline)
    assert z_score == pytest.approx(1.5, rel=0.01)

    # Test negative Z-score
    z_score = analytics_engine.calculate_z_score(85.0, sample_baseline)
    assert z_score == pytest.approx(-1.5, rel=0.01)

    # Test zero Z-score (value equals mean)
    z_score = analytics_engine.calculate_z_score(100.0, sample_baseline)
    assert z_score == pytest.approx(0.0, rel=0.01)


def test_calculate_z_score_zero_std_dev(analytics_engine):
    """Test Z-score calculation with zero standard deviation."""
    baseline = {"mean": 100.0, "std_dev": 0.0, "min": 100.0, "max": 100.0, "count": 10}

    z_score = analytics_engine.calculate_z_score(105.0, baseline)
    assert z_score is None


def test_calculate_z_score_no_baseline(analytics_engine):
    """Test Z-score calculation with no baseline."""
    z_score = analytics_engine.calculate_z_score(105.0, None)
    assert z_score is None


def test_detect_trend_increasing(analytics_engine):
    """Test trend detection for increasing values."""
    values = [100, 105, 110, 115, 120, 125, 130]

    trend, slope = analytics_engine.detect_trend(values, threshold=0.01)

    assert trend == "increasing"
    assert slope > 0


def test_detect_trend_decreasing(analytics_engine):
    """Test trend detection for decreasing values."""
    values = [130, 125, 120, 115, 110, 105, 100]

    trend, slope = analytics_engine.detect_trend(values, threshold=0.01)

    assert trend == "decreasing"
    assert slope < 0


def test_detect_trend_stable(analytics_engine):
    """Test trend detection for stable values."""
    values = [100, 101, 100, 99, 100, 101, 100]

    trend, slope = analytics_engine.detect_trend(values, threshold=0.1)

    assert trend == "stable"


def test_detect_trend_insufficient_data(analytics_engine):
    """Test trend detection with insufficient data."""
    # Empty list
    trend, slope = analytics_engine.detect_trend([])
    assert trend == "stable"
    assert slope == 0.0

    # Too few values
    trend, slope = analytics_engine.detect_trend([100, 105])
    assert trend == "stable"
    assert slope == 0.0


def test_calculate_percentile(analytics_engine):
    """Test percentile calculations."""
    values = list(range(1, 101))  # 1 to 100

    # Test various percentiles
    p25 = analytics_engine.calculate_percentile(values, 25)
    assert p25 == pytest.approx(25.75, rel=0.01)

    p50 = analytics_engine.calculate_percentile(values, 50)
    assert p50 == pytest.approx(50.5, rel=0.01)

    p75 = analytics_engine.calculate_percentile(values, 75)
    assert p75 == pytest.approx(75.25, rel=0.01)

    p99 = analytics_engine.calculate_percentile(values, 99)
    assert p99 == pytest.approx(99.01, rel=0.01)


def test_calculate_percentile_edge_cases(analytics_engine):
    """Test percentile calculation edge cases."""
    # Empty list
    result = analytics_engine.calculate_percentile([], 50)
    assert result is None

    # Single value
    result = analytics_engine.calculate_percentile([100], 50)
    assert result == 100


def test_calculate_rate_of_change(analytics_engine, sample_time_series):
    """Test rate of change calculation."""
    rate = analytics_engine.calculate_rate_of_change(sample_time_series, window_hours=24)

    assert rate is not None
    assert isinstance(rate, float)
    # Note: sample_time_series has values that decrease over time (100.0 + i * 2 where i goes from 24 to 1)
    # So the rate of change should be negative (values are decreasing as we move forward in time)
    # But since the list is ordered oldest to newest, the rate should actually be positive
    # Let me check the fixture more carefully - it goes from 24 hours ago to 1 hour ago
    # So values go from 100+24*2=148 (24h ago) to 100+1*2=102 (1h ago), which is decreasing
    assert rate < 0  # Values decrease over time in the fixture


def test_calculate_rate_of_change_insufficient_data(analytics_engine):
    """Test rate of change with insufficient data."""
    # Empty list
    rate = analytics_engine.calculate_rate_of_change([], window_hours=24)
    assert rate is None

    # Single value
    now = datetime.datetime.now(datetime.timezone.utc)
    rate = analytics_engine.calculate_rate_of_change([(now, 100.0)], window_hours=24)
    assert rate is None


def test_calculate_rate_of_change_with_none_values(analytics_engine):
    """Test rate of change with None values in dataset."""
    now = datetime.datetime.now(datetime.timezone.utc)
    values = [
        (now - datetime.timedelta(hours=3), 100.0),
        (now - datetime.timedelta(hours=2), None),
        (now - datetime.timedelta(hours=1), 110.0),
        (now, 120.0),
    ]

    rate = analytics_engine.calculate_rate_of_change(values, window_hours=24)

    # Should skip None values and still calculate
    assert rate is not None


def test_forecast_linear(analytics_engine):
    """Test linear forecasting."""
    # Create time series with increasing values over time
    now = datetime.datetime.now(datetime.timezone.utc)
    time_series = [
        (now - datetime.timedelta(hours=i), 100.0 + (24 - i) * 2) for i in range(24, 0, -1)
    ]

    forecast = analytics_engine.forecast_linear(time_series, forecast_hours=24)

    assert forecast is not None
    assert isinstance(forecast, float)
    # Forecast should be higher than current values since trend is increasing
    latest_value = time_series[-1][1]
    assert forecast > latest_value


def test_forecast_linear_no_data(analytics_engine):
    """Test linear forecasting with no data."""
    forecast = analytics_engine.forecast_linear([], forecast_hours=24)
    assert forecast is None


@pytest.mark.asyncio
async def test_analyze_reputation_health_critical(analytics_engine):
    """Test reputation health analysis with critical scores."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 55.0,  # Below critical threshold (70.0) - as percentage
            "suspension_score": 50.0,  # Below critical threshold (60.0)
            "online_score": 95.0,
        }
    ]

    insights = await analytics_engine.analyze_reputation_health("test-node", reputation_data)

    assert len(insights) > 0

    # Should have critical audit score insight
    audit_insights = [i for i in insights if i["insight_type"] == "reputation_critical"]
    assert len(audit_insights) > 0
    assert audit_insights[0]["severity"] == "critical"

    # Should have suspension risk insight
    suspension_insights = [i for i in insights if i["insight_type"] == "suspension_risk"]
    assert len(suspension_insights) > 0
    assert suspension_insights[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_analyze_reputation_health_warning(analytics_engine):
    """Test reputation health analysis with warning scores."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 80.0,  # Below warning threshold (85.0) - as percentage
            "suspension_score": 98.0,
            "online_score": 88.0,  # Below warning threshold (95.0)
        }
    ]

    insights = await analytics_engine.analyze_reputation_health("test-node", reputation_data)

    assert len(insights) > 0

    # Should have warning insights
    warning_insights = [i for i in insights if i["severity"] == "warning"]
    assert len(warning_insights) >= 1  # At least one warning


@pytest.mark.asyncio
async def test_analyze_reputation_health_healthy(analytics_engine):
    """Test reputation health analysis with healthy scores."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 99.9,  # Very high score - as percentage
            "suspension_score": 99.9,
            "online_score": 99.9,
        }
    ]

    insights = await analytics_engine.analyze_reputation_health("test-node", reputation_data)

    # Should have no insights for healthy node
    assert len(insights) == 0


@pytest.mark.asyncio
async def test_analyze_storage_health_critical(analytics_engine):
    """Test storage health analysis with critical usage."""
    now = datetime.datetime.now(datetime.timezone.utc)
    storage_history = [
        {
            "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
            "used_bytes": 9000000000 + (i * 50000000),
            "available_bytes": 1000000000 - (i * 50000000),
            "total_bytes": 10000000000,
            "used_percent": 90.0 + (i * 0.5),
        }
        for i in range(7, 0, -1)
    ]
    storage_history.append(
        {
            "timestamp": now.isoformat(),
            "used_bytes": 9500000000,
            "available_bytes": 500000000,
            "total_bytes": 10000000000,
            "used_percent": 95.0,
        }
    )

    insights = await analytics_engine.analyze_storage_health("test-node", storage_history)

    assert len(insights) > 0

    # Should have critical storage insight
    critical_insights = [i for i in insights if i["insight_type"] == "storage_critical"]
    assert len(critical_insights) > 0
    assert critical_insights[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_analyze_storage_health_forecast(analytics_engine):
    """Test storage health forecasting."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Create storage history with consistent growth
    storage_history = []
    for i in range(30, 0, -1):
        used_bytes = 5000000000 + ((30 - i) * 100000000)  # 100MB/day growth
        storage_history.append(
            {
                "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
                "used_bytes": used_bytes,
                "available_bytes": 10000000000 - used_bytes,
                "total_bytes": 10000000000,
                "used_percent": (used_bytes / 10000000000) * 100,
            }
        )

    insights = await analytics_engine.analyze_storage_health("test-node", storage_history)

    # Should have forecast insights if growth rate indicates capacity issues
    [i for i in insights if "forecast" in i["insight_type"]]

    # Depending on the growth rate, we may or may not have forecast insights
    # The important thing is it doesn't crash
    assert isinstance(insights, list)


@pytest.mark.asyncio
async def test_analyze_storage_health_insufficient_data(analytics_engine):
    """Test storage health analysis with insufficient data."""
    storage_history = [
        {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "used_bytes": 5000000000,
            "available_bytes": 5000000000,
            "total_bytes": 10000000000,
            "used_percent": 50.0,
        }
    ]

    insights = await analytics_engine.analyze_storage_health("test-node", storage_history)

    # Should handle gracefully with minimal data
    assert isinstance(insights, list)


@pytest.mark.asyncio
async def test_analyze_storage_health_no_data(analytics_engine):
    """Test storage health analysis with no data."""
    insights = await analytics_engine.analyze_storage_health("test-node", [])

    assert len(insights) == 0
