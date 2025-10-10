"""
Shared fixtures for Storj Node Monitor tests.
"""

import builtins
import contextlib
import datetime
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Create temporary test database with full schema."""
    import storj_monitor.config as config
    import storj_monitor.database as database

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Patch DATABASE_FILE in both config and database modules
    monkeypatch.setattr(config, "DATABASE_FILE", path)
    monkeypatch.setattr(database, "DATABASE_FILE", path)

    try:
        # Initialize database with full schema using init_db()
        database.init_db()

        yield path
    finally:
        # Clean up temp file
        with contextlib.suppress(builtins.BaseException):
            os.unlink(path)


@pytest.fixture
def sample_log_lines():
    """Load sample log data from fixtures."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_logs.txt"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return f.readlines()
    return []


@pytest.fixture
def sample_api_responses():
    """Load sample API response data."""
    import json

    fixture_path = Path(__file__).parent / "fixtures" / "sample_api_responses.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            return json.load(f)
    return {}


@pytest.fixture
async def mock_aiohttp_session():
    """Create mock aiohttp session."""
    session = AsyncMock()

    # Mock successful responses
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"data": "test"})
    mock_response.text = AsyncMock(return_value="test response")

    session.get = AsyncMock(return_value=mock_response)
    session.post = AsyncMock(return_value=mock_response)

    return session


@pytest.fixture
def mock_api_client():
    """Create mock StorjNodeAPIClient."""
    client = Mock()

    # Mock common API methods
    client.get_node_info = AsyncMock(
        return_value={"id": "test-node-id", "wallet": "0x1234567890abcdef", "version": "v1.0.0"}
    )

    client.get_satellites = AsyncMock(
        return_value=[
            {
                "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                "url": "us1.storj.io:7777",
                "disqualified": None,
                "suspended": None,
            }
        ]
    )

    client.get_reputation = AsyncMock(
        return_value={
            "audit": {"score": 1.0, "successCount": 100, "totalCount": 100},
            "suspension": {"score": 1.0},
            "online": {"score": 1.0},
        }
    )

    client.get_storage_summary = AsyncMock(
        return_value={"diskSpace": {"used": 1000000000, "available": 9000000000, "trash": 50000000}}
    )

    return client


@pytest.fixture
def test_config():
    """Test configuration overrides."""
    return {
        "DATABASE_FILE": ":memory:",
        "ENABLE_ANOMALY_DETECTION": True,
        "ALERT_ENABLE_EMAIL": False,
        "ALERT_ENABLE_WEBHOOK": False,
        "STATS_WINDOW_MINUTES": 60,
        "HISTORICAL_HOURS_TO_SHOW": 24,
        "LOG_LEVEL": "DEBUG",
    }


@pytest.fixture
def sample_event():
    """Sample event data for testing."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "ts_unix": datetime.datetime.now(datetime.timezone.utc).timestamp(),
        "action": "GET",
        "status": "success",
        "size": 1024000,
        "piece_id": "test-piece-id",
        "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "remote_ip": "192.168.1.1",
        "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
        "error_reason": None,
        "node_name": "test-node",
        "category": "get",
        "duration_ms": 150,
    }


@pytest.fixture
def sample_reputation_data():
    """Sample reputation data for testing."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "audit_score": 1.0,
        "suspension_score": 1.0,
        "online_score": 1.0,
        "audit_success_count": 100,
        "audit_total_count": 100,
        "is_disqualified": False,
        "is_suspended": False,
    }


@pytest.fixture
def sample_storage_snapshot():
    """Sample storage snapshot for testing."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "total_bytes": 10000000000,
        "used_bytes": 5000000000,
        "available_bytes": 5000000000,
        "trash_bytes": 100000000,
        "used_percent": 50.0,
        "trash_percent": 1.0,
        "available_percent": 50.0,
    }


@pytest.fixture
def sample_alert():
    """Sample alert for testing."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "alert_type": "reputation",
        "severity": "warning",
        "title": "Audit Score Declining",
        "message": "Audit score has dropped below 0.95",
        "metadata": {"audit_score": 0.94},
    }


@pytest.fixture
def sample_earnings_estimate():
    """Sample earnings estimate for testing."""
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "period": "2025-01",
        "egress_bytes": 1000000000,
        "egress_earnings_gross": 2.00,
        "egress_earnings_net": 1.50,
        "storage_bytes_hour": 100000000000,
        "storage_earnings_gross": 1.50,
        "storage_earnings_net": 1.13,
        "repair_bytes": 50000000,
        "repair_earnings_gross": 0.50,
        "repair_earnings_net": 0.38,
        "audit_bytes": 10000000,
        "audit_earnings_gross": 0.10,
        "audit_earnings_net": 0.08,
        "total_earnings_gross": 4.10,
        "total_earnings_net": 3.09,
        "held_amount": 1.01,
        "node_age_months": 6,
        "held_percentage": 25.0,
    }


@pytest.fixture
def mock_geoip_reader():
    """Mock GeoIP2 reader for testing."""
    reader = Mock()

    # Mock city response
    city_response = Mock()
    city_response.country.iso_code = "US"
    city_response.country.name = "United States"
    city_response.location.latitude = 37.7749
    city_response.location.longitude = -122.4194

    reader.city = Mock(return_value=city_response)

    return reader


@pytest.fixture
def nodes_config():
    """Sample nodes configuration for testing."""
    return {
        "test-node": {
            "log_file": "/var/log/storj/test-node.log",
            "api_url": "http://localhost:14002",
            "enabled": True,
        },
        "test-node-2": {
            "log_file": "/var/log/storj/test-node-2.log",
            "api_url": "http://localhost:14003",
            "enabled": True,
        },
    }


@pytest.fixture
def mock_email_sender():
    """Mock email sender for testing."""
    sender = Mock()
    sender.send_alert_email = AsyncMock(return_value=True)
    return sender


@pytest.fixture
def mock_webhook_sender():
    """Mock webhook sender for testing."""
    sender = Mock()
    sender.send_alert_webhook = AsyncMock(return_value=True)
    return sender


@pytest.fixture
def mock_app():
    """Mock app instance for analytics modules."""
    from concurrent.futures import ThreadPoolExecutor

    app = {
        "db_executor": ThreadPoolExecutor(max_workers=1),
        "nodes": {"test-node": {"name": "test-node", "enabled": True}},
    }

    yield app

    # Cleanup
    app["db_executor"].shutdown(wait=False)


@pytest.fixture
async def analytics_engine(temp_db, mock_app):
    """Create analytics engine instance."""
    from storj_monitor.analytics_engine import AnalyticsEngine

    return AnalyticsEngine(mock_app)


@pytest.fixture
async def anomaly_detector(temp_db, mock_app, analytics_engine):
    """Create anomaly detector instance."""
    from storj_monitor.anomaly_detector import AnomalyDetector

    return AnomalyDetector(mock_app, analytics_engine)


@pytest.fixture
async def alert_manager(temp_db, mock_app, analytics_engine, anomaly_detector):
    """Create alert manager instance."""
    from storj_monitor.alert_manager import AlertManager

    return AlertManager(mock_app, analytics_engine, anomaly_detector)


@pytest.fixture
def sample_baseline():
    """Sample baseline statistics."""
    return {"mean": 100.0, "std_dev": 10.0, "min": 80.0, "max": 120.0, "count": 100}


@pytest.fixture
def sample_time_series():
    """Sample time series data for testing."""
    import datetime

    base_time = datetime.datetime.now(datetime.timezone.utc)

    return [(base_time - datetime.timedelta(hours=i), 100.0 + i * 2) for i in range(24, 0, -1)]


@pytest.fixture
def sample_latency_data():
    """Sample latency data for testing."""
    return {
        "p50": 150.0,
        "p75": 200.0,
        "p95": 300.0,
        "p99": 450.0,
        "avg": 180.0,
        "min": 50.0,
        "max": 500.0,
    }


@pytest.fixture
def sample_bandwidth_data():
    """Sample bandwidth data for testing."""
    return {
        "avg_egress_mbps": 5.0,
        "avg_ingress_mbps": 2.0,
        "peak_egress_mbps": 10.0,
        "peak_ingress_mbps": 4.0,
        "total_egress_bytes": 1000000000,
        "total_ingress_bytes": 500000000,
    }
