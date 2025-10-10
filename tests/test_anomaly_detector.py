"""
Comprehensive tests for anomaly detector module.
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest


def test_anomaly_detector_imports():
    """Test anomaly detector imports."""
    from storj_monitor.anomaly_detector import AnomalyDetector

    assert AnomalyDetector is not None


@pytest.mark.asyncio
async def test_anomaly_detector_initialization(temp_db, mock_app, analytics_engine):
    """Test anomaly detector initialization."""
    from storj_monitor.anomaly_detector import AnomalyDetector

    detector = AnomalyDetector(mock_app, analytics_engine)

    assert detector.app == mock_app
    assert detector.analytics == analytics_engine
    assert detector.anomaly_threshold == 3.0
    assert len(detector.recent_anomalies) == 0


@pytest.mark.asyncio
async def test_detect_anomalies_spike(anomaly_detector):
    """Test anomaly detection for spike (high Z-score)."""
    # Mock baseline
    baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}

    with patch.object(
        anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
    ):
        # Test value 3.5 standard deviations above mean (135.0)
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 135.0, window_hours=168
        )

    assert anomaly is not None
    assert anomaly["node_name"] == "test-node"
    assert anomaly["metric_name"] == "test_metric"
    assert anomaly["current_value"] == 135.0
    assert anomaly["baseline_mean"] == 100.0
    assert anomaly["z_score"] == pytest.approx(3.5, rel=0.01)
    assert anomaly["anomaly_type"] == "spike"
    assert anomaly["severity"] in ["warning", "critical"]


@pytest.mark.asyncio
async def test_detect_anomalies_drop(anomaly_detector):
    """Test anomaly detection for drop (low Z-score)."""
    baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}

    with patch.object(
        anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
    ):
        # Test value 3.5 standard deviations below mean (65.0)
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 65.0, window_hours=168
        )

    assert anomaly is not None
    assert anomaly["anomaly_type"] == "drop"
    assert anomaly["z_score"] == pytest.approx(-3.5, rel=0.01)


@pytest.mark.asyncio
async def test_detect_anomalies_critical_threshold(anomaly_detector):
    """Test critical anomaly threshold (Z-score >= 4.0)."""
    baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}

    with patch.object(
        anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
    ):
        # Test value 4.5 standard deviations above mean (145.0)
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 145.0, window_hours=168
        )

    assert anomaly is not None
    assert anomaly["severity"] == "critical"
    assert abs(anomaly["z_score"]) >= 4.0


@pytest.mark.asyncio
async def test_detect_anomalies_no_anomaly(anomaly_detector):
    """Test when value is within normal range."""
    baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}

    with patch.object(
        anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
    ):
        # Test value within 2 standard deviations (115.0)
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 115.0, window_hours=168
        )

    assert anomaly is None


@pytest.mark.asyncio
async def test_detect_anomalies_no_baseline(anomaly_detector):
    """Test anomaly detection with no baseline available."""
    with patch.object(anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=None)):
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 150.0, window_hours=168
        )

    assert anomaly is None


@pytest.mark.asyncio
async def test_detect_anomalies_cache(anomaly_detector):
    """Test that detected anomalies are cached."""
    baseline = {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}

    with patch.object(
        anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
    ):
        anomaly = await anomaly_detector.detect_anomalies(
            "test-node", "test_metric", 140.0, window_hours=168
        )

    assert anomaly is not None
    assert len(anomaly_detector.recent_anomalies) == 1
    assert anomaly_detector.recent_anomalies[0] == anomaly


@pytest.mark.asyncio
async def test_detect_traffic_anomalies_low_success_rate(anomaly_detector):
    """Test detection of low success rate anomaly."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Create events with low success rate (30%)
    recent_events = [
        {"status": "success", "timestamp": now - datetime.timedelta(minutes=i)} for i in range(3)
    ]
    recent_events.extend(
        [
            {
                "status": "failed",
                "error_reason": "timeout",
                "timestamp": now - datetime.timedelta(minutes=i + 3),
            }
            for i in range(7)
        ]
    )

    baseline = {
        "mean": 0.95,  # Normal success rate 95%
        "std_dev": 0.02,
        "min": 0.90,
        "max": 1.0,
        "count": 100,
    }

    with (
        patch.object(
            anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
        ),
        patch.object(anomaly_detector.analytics, "calculate_z_score", return_value=-3.5),
    ):
        anomalies = await anomaly_detector.detect_traffic_anomalies("test-node", recent_events)

    assert len(anomalies) > 0

    # Should have traffic anomaly for low success rate
    traffic_anomalies = [a for a in anomalies if a["insight_type"] == "traffic_anomaly"]
    assert len(traffic_anomalies) > 0
    assert "success_rate" in traffic_anomalies[0]["metadata"]


@pytest.mark.asyncio
async def test_detect_traffic_anomalies_high_error_rate(anomaly_detector):
    """Test detection of high error rate pattern."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Create events with 40% error rate
    recent_events = []
    for i in range(6):
        recent_events.append(
            {"status": "success", "timestamp": now - datetime.timedelta(minutes=i)}
        )
    for i in range(4):
        recent_events.append(
            {
                "status": "failed",
                "error_reason": "disk_full",
                "timestamp": now - datetime.timedelta(minutes=i + 6),
            }
        )

    with patch.object(anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=None)):
        anomalies = await anomaly_detector.detect_traffic_anomalies("test-node", recent_events)

    # Should detect error pattern
    error_anomalies = [a for a in anomalies if a["insight_type"] == "error_pattern"]
    assert len(error_anomalies) > 0
    assert error_anomalies[0]["metadata"]["error_rate"] > 0.1


@pytest.mark.asyncio
async def test_detect_traffic_anomalies_insufficient_data(anomaly_detector):
    """Test traffic anomaly detection with insufficient data."""
    recent_events = [
        {"status": "success", "timestamp": datetime.datetime.now(datetime.timezone.utc)}
        for _ in range(5)  # Less than 10 events
    ]

    anomalies = await anomaly_detector.detect_traffic_anomalies("test-node", recent_events)

    # Should return empty list with insufficient data
    assert len(anomalies) == 0


@pytest.mark.asyncio
async def test_detect_traffic_anomalies_no_data(anomaly_detector):
    """Test traffic anomaly detection with no data."""
    anomalies = await anomaly_detector.detect_traffic_anomalies("test-node", [])
    assert len(anomalies) == 0


@pytest.mark.asyncio
async def test_detect_latency_anomalies_critical(anomaly_detector, sample_latency_data):
    """Test detection of critical latency."""
    # Mock config to have known thresholds
    with patch("storj_monitor.config.LATENCY_CRITICAL_MS", 1000):
        # Set P99 to critical level
        latency_data = sample_latency_data.copy()
        latency_data["p99"] = 1500.0  # Above critical threshold (1000ms)

        anomalies = await anomaly_detector.detect_latency_anomalies("test-node", latency_data)

        assert len(anomalies) > 0

        # Should have critical latency insight
        critical = [a for a in anomalies if a["insight_type"] == "latency_critical"]
        assert len(critical) > 0
        assert critical[0]["severity"] == "critical"
        assert critical[0]["metadata"]["p99_ms"] == 1500.0


@pytest.mark.asyncio
async def test_detect_latency_anomalies_warning(anomaly_detector, sample_latency_data):
    """Test detection of warning-level latency."""
    # Mock config to have known thresholds
    with patch("storj_monitor.config.LATENCY_WARNING_MS", 500):
        with patch("storj_monitor.config.LATENCY_CRITICAL_MS", 1000):
            # Set P99 to warning level
            latency_data = sample_latency_data.copy()
            latency_data["p99"] = 600.0  # Above warning threshold (500ms)

            anomalies = await anomaly_detector.detect_latency_anomalies("test-node", latency_data)

            assert len(anomalies) > 0

            # Should have warning latency insight
            warnings = [a for a in anomalies if a["insight_type"] == "latency_warning"]
            assert len(warnings) > 0
            assert warnings[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_detect_latency_anomalies_spike(anomaly_detector, sample_latency_data):
    """Test detection of latency spike using Z-score."""
    baseline = {"mean": 150.0, "std_dev": 20.0, "min": 100.0, "max": 200.0, "count": 100}

    # Set P50 to abnormally high value
    latency_data = sample_latency_data.copy()
    latency_data["p50"] = 250.0  # High spike

    with (
        patch.object(
            anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
        ),
        patch.object(anomaly_detector, "detect_anomalies") as mock_detect,
    ):
        mock_detect.return_value = {
            "anomaly_type": "spike",
            "severity": "warning",
            "z_score": 5.0,
            "baseline_mean": 150.0,
            "confidence": 0.9,
        }

        anomalies = await anomaly_detector.detect_latency_anomalies("test-node", latency_data)

    # Should have latency spike insight
    spikes = [a for a in anomalies if a["insight_type"] == "latency_spike"]
    assert len(spikes) > 0


@pytest.mark.asyncio
async def test_detect_latency_anomalies_no_data(anomaly_detector):
    """Test latency anomaly detection with no data."""
    anomalies = await anomaly_detector.detect_latency_anomalies("test-node", {})
    assert len(anomalies) == 0


@pytest.mark.asyncio
async def test_detect_bandwidth_anomalies_egress_spike(anomaly_detector, sample_bandwidth_data):
    """Test detection of egress bandwidth spike."""
    baseline = {"mean": 5.0, "std_dev": 1.0, "min": 3.0, "max": 7.0, "count": 100}

    # Increase egress to abnormal level
    bandwidth_data = sample_bandwidth_data.copy()
    bandwidth_data["avg_egress_mbps"] = 15.0  # 10 std devs above mean

    with (
        patch.object(
            anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
        ),
        patch.object(anomaly_detector, "detect_anomalies") as mock_detect,
    ):
        mock_detect.return_value = {
            "anomaly_type": "spike",
            "severity": "info",
            "z_score": 10.0,
            "baseline_mean": 5.0,
            "confidence": 0.95,
        }

        anomalies = await anomaly_detector.detect_bandwidth_anomalies("test-node", bandwidth_data)

    # Should have bandwidth spike insight
    spikes = [a for a in anomalies if a["insight_type"] == "bandwidth_spike"]
    assert len(spikes) > 0
    assert spikes[0]["severity"] == "info"


@pytest.mark.asyncio
async def test_detect_bandwidth_anomalies_egress_drop(anomaly_detector, sample_bandwidth_data):
    """Test detection of egress bandwidth drop."""
    baseline = {"mean": 5.0, "std_dev": 1.0, "min": 3.0, "max": 7.0, "count": 100}

    # Drop egress to abnormally low level
    bandwidth_data = sample_bandwidth_data.copy()
    bandwidth_data["avg_egress_mbps"] = 1.0

    with (
        patch.object(
            anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
        ),
        patch.object(anomaly_detector, "detect_anomalies") as mock_detect,
    ):
        mock_detect.return_value = {
            "anomaly_type": "drop",
            "severity": "warning",
            "z_score": -4.0,
            "baseline_mean": 5.0,
            "confidence": 0.85,
        }

        anomalies = await anomaly_detector.detect_bandwidth_anomalies("test-node", bandwidth_data)

    # Should have bandwidth drop insight
    drops = [a for a in anomalies if a["insight_type"] == "bandwidth_drop"]
    assert len(drops) > 0
    assert drops[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_detect_bandwidth_anomalies_ingress_drop(anomaly_detector, sample_bandwidth_data):
    """Test detection of ingress bandwidth drop."""
    baseline = {"mean": 2.0, "std_dev": 0.5, "min": 1.0, "max": 3.0, "count": 100}

    # Drop ingress to abnormally low level
    bandwidth_data = sample_bandwidth_data.copy()
    bandwidth_data["avg_ingress_mbps"] = 0.2

    with (
        patch.object(
            anomaly_detector.analytics, "get_baseline", new=AsyncMock(return_value=baseline)
        ),
        patch.object(anomaly_detector, "detect_anomalies") as mock_detect,
    ):
        mock_detect.return_value = {
            "anomaly_type": "drop",
            "severity": "info",
            "z_score": -3.6,
            "baseline_mean": 2.0,
            "confidence": 0.8,
        }

        anomalies = await anomaly_detector.detect_bandwidth_anomalies("test-node", bandwidth_data)

    # Should have upload activity drop insight
    drops = [a for a in anomalies if a["insight_type"] == "upload_activity_drop"]
    assert len(drops) > 0


@pytest.mark.asyncio
async def test_detect_bandwidth_anomalies_no_data(anomaly_detector):
    """Test bandwidth anomaly detection with no data."""
    anomalies = await anomaly_detector.detect_bandwidth_anomalies("test-node", {})
    assert len(anomalies) == 0


@pytest.mark.asyncio
async def test_detect_bandwidth_anomalies_zero_bandwidth(anomaly_detector):
    """Test bandwidth anomaly detection with zero bandwidth."""
    bandwidth_data = {"avg_egress_mbps": 0.0, "avg_ingress_mbps": 0.0}

    anomalies = await anomaly_detector.detect_bandwidth_anomalies("test-node", bandwidth_data)

    # Should handle zero bandwidth gracefully
    assert isinstance(anomalies, list)


def test_get_recent_anomalies_no_filter(anomaly_detector):
    """Test retrieving recent anomalies without filters."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Add some test anomalies
    for i in range(5):
        anomaly_detector.recent_anomalies.append(
            {
                "timestamp": now - datetime.timedelta(minutes=i * 10),
                "node_name": f"node-{i}",
                "metric_name": "test_metric",
                "z_score": 3.5,
            }
        )

    # Get anomalies from last 60 minutes
    recent = anomaly_detector.get_recent_anomalies(minutes=60)

    assert len(recent) == 5


def test_get_recent_anomalies_with_node_filter(anomaly_detector):
    """Test retrieving recent anomalies with node filter."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Add anomalies for different nodes
    for i in range(3):
        anomaly_detector.recent_anomalies.append(
            {
                "timestamp": now - datetime.timedelta(minutes=i * 5),
                "node_name": "test-node",
                "metric_name": "test_metric",
                "z_score": 3.5,
            }
        )

    for i in range(2):
        anomaly_detector.recent_anomalies.append(
            {
                "timestamp": now - datetime.timedelta(minutes=i * 5),
                "node_name": "other-node",
                "metric_name": "test_metric",
                "z_score": 3.5,
            }
        )

    # Get anomalies for specific node
    recent = anomaly_detector.get_recent_anomalies(node_name="test-node", minutes=60)

    assert len(recent) == 3
    assert all(a["node_name"] == "test-node" for a in recent)


def test_get_recent_anomalies_time_window(anomaly_detector):
    """Test retrieving recent anomalies respects time window."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Add anomalies at different times
    anomaly_detector.recent_anomalies.append(
        {
            "timestamp": now - datetime.timedelta(minutes=10),
            "node_name": "test-node",
            "metric_name": "test_metric",
            "z_score": 3.5,
        }
    )

    anomaly_detector.recent_anomalies.append(
        {
            "timestamp": now - datetime.timedelta(minutes=90),  # Outside window
            "node_name": "test-node",
            "metric_name": "test_metric",
            "z_score": 3.5,
        }
    )

    # Get anomalies from last 60 minutes
    recent = anomaly_detector.get_recent_anomalies(minutes=60)

    assert len(recent) == 1


def test_get_recent_anomalies_empty(anomaly_detector):
    """Test retrieving recent anomalies when cache is empty."""
    recent = anomaly_detector.get_recent_anomalies(minutes=60)
    assert len(recent) == 0


def test_anomaly_cache_max_size(anomaly_detector):
    """Test that anomaly cache respects max size."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Add more than max cache size (100)
    for i in range(150):
        anomaly_detector.recent_anomalies.append(
            {
                "timestamp": now - datetime.timedelta(minutes=i),
                "node_name": "test-node",
                "metric_name": "test_metric",
                "z_score": 3.5,
            }
        )

    # Cache should be limited to 100 items
    assert len(anomaly_detector.recent_anomalies) == 100


def test_anomaly_detector_threshold_configuration(mock_app, analytics_engine):
    """Test anomaly detector threshold can be configured."""
    from storj_monitor.anomaly_detector import AnomalyDetector

    detector = AnomalyDetector(mock_app, analytics_engine)

    # Verify default threshold
    assert detector.anomaly_threshold == 3.0

    # Change threshold
    detector.anomaly_threshold = 2.5
    assert detector.anomaly_threshold == 2.5
