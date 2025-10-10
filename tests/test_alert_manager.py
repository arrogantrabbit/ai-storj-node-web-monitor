"""
Comprehensive tests for alert manager module.
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest


def test_alert_manager_imports():
    """Test alert manager imports."""
    from storj_monitor.alert_manager import AlertManager

    assert AlertManager is not None


@pytest.mark.asyncio
async def test_alert_manager_initialization(temp_db, mock_app, analytics_engine, anomaly_detector):
    """Test alert manager initialization."""
    from storj_monitor.alert_manager import AlertManager

    manager = AlertManager(mock_app, analytics_engine, anomaly_detector)

    assert manager.app == mock_app
    assert manager.analytics == analytics_engine
    assert manager.anomaly_detector == anomaly_detector
    assert isinstance(manager.active_alerts, dict)
    assert isinstance(manager.alert_cooldown, dict)
    assert manager.cooldown_minutes == 15


def test_generate_alert_key(alert_manager):
    """Test alert key generation for deduplication."""
    # Basic alert key
    key1 = alert_manager._generate_alert_key("test-node", "audit_score_warning", {})
    assert key1 == "test-node:audit_score_warning"

    # Alert key with satellite
    key2 = alert_manager._generate_alert_key(
        "test-node",
        "audit_score_warning",
        {"satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S"},
    )
    assert (
        key2 == "test-node:audit_score_warning:12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S"
    )

    # Alert key with metric name
    key3 = alert_manager._generate_alert_key(
        "test-node", "latency_spike", {"metric_name": "p99_latency"}
    )
    assert key3 == "test-node:latency_spike:p99_latency"


def test_should_generate_alert_no_cooldown(alert_manager):
    """Test alert generation when no previous alert exists."""
    should_generate = alert_manager._should_generate_alert("new-alert-key")
    assert should_generate is True


def test_should_generate_alert_within_cooldown(alert_manager):
    """Test alert suppression within cooldown period."""
    alert_key = "test-node:audit_score_warning"

    # Set recent alert time
    alert_manager.alert_cooldown[alert_key] = datetime.datetime.now(datetime.timezone.utc)

    should_generate = alert_manager._should_generate_alert(alert_key)
    assert should_generate is False


def test_should_generate_alert_after_cooldown(alert_manager):
    """Test alert generation after cooldown period."""
    alert_key = "test-node:audit_score_warning"

    # Set old alert time (16 minutes ago, beyond 15-minute cooldown)
    alert_manager.alert_cooldown[alert_key] = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(minutes=16)

    should_generate = alert_manager._should_generate_alert(alert_key)
    assert should_generate is True


@pytest.mark.asyncio
async def test_generate_alert_success(alert_manager):
    """Test successful alert generation."""
    with patch("storj_monitor.database.blocking_write_alert", return_value=True):
        with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock()):
            with patch("storj_monitor.notification_handler.notification_handler") as mock_notif:
                mock_notif.send_notification = AsyncMock(return_value=True)

                alert = await alert_manager.generate_alert(
                    "test-node",
                    "test_alert",
                    "warning",
                    "Test Alert",
                    "This is a test alert",
                    {"test_key": "test_value"},
                )

    assert alert is not None
    assert alert["node_name"] == "test-node"
    assert alert["alert_type"] == "test_alert"
    assert alert["severity"] == "warning"
    assert alert["title"] == "Test Alert"
    assert alert["message"] == "This is a test alert"
    assert "test_key" in alert["metadata"]


@pytest.mark.asyncio
async def test_generate_alert_cooldown_suppression(alert_manager):
    """Test alert is suppressed during cooldown."""
    alert_key = "test-node:test_alert"
    alert_manager.alert_cooldown[alert_key] = datetime.datetime.now(datetime.timezone.utc)

    with patch("storj_monitor.database.blocking_write_alert", return_value=True):
        alert = await alert_manager.generate_alert(
            "test-node", "test_alert", "warning", "Test Alert", "This should be suppressed"
        )

    assert alert is None


@pytest.mark.asyncio
async def test_generate_alert_database_failure(alert_manager):
    """Test alert generation when database write fails."""
    with patch("storj_monitor.database.blocking_write_alert", return_value=False):
        alert = await alert_manager.generate_alert(
            "test-node", "test_alert", "warning", "Test Alert", "Database write will fail"
        )

    # Alert should not be created if database write fails
    assert alert is None


@pytest.mark.asyncio
async def test_generate_alert_caching(alert_manager):
    """Test alert is cached after generation."""
    with patch("storj_monitor.database.blocking_write_alert", return_value=True):
        with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock()):
            with patch("storj_monitor.notification_handler.notification_handler") as mock_notif:
                mock_notif.send_notification = AsyncMock(return_value=True)

                await alert_manager.generate_alert(
                    "test-node", "test_alert", "warning", "Test Alert", "Test message"
                )

    alert_key = "test-node:test_alert"
    assert alert_key in alert_manager.active_alerts
    assert alert_key in alert_manager.alert_cooldown


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_critical_audit(alert_manager):
    """Test reputation alert evaluation for critical audit score."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 0.55,  # Below critical threshold (0.6)
            "suspension_score": 1.0,
            "online_score": 1.0,
            "is_disqualified": False,
            "is_suspended": False,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate critical audit score alert
        assert mock_generate.called
        call_args = mock_generate.call_args_list[0][0]
        assert call_args[1] == "audit_score_critical"
        assert call_args[2] == "critical"


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_warning_audit(alert_manager):
    """Test reputation alert evaluation for warning audit score."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 75.0,  # Between warning (85.0) and critical (70.0) - as percentage
            "suspension_score": 100.0,
            "online_score": 100.0,
            "is_disqualified": False,
            "is_suspended": False,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate warning audit score alert
        assert mock_generate.called
        call_args = mock_generate.call_args_list[0][0]
        assert call_args[1] == "audit_score_warning"
        assert call_args[2] == "warning"


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_disqualified(alert_manager):
    """Test reputation alert evaluation for disqualified node."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 0.5,
            "suspension_score": 0.5,
            "online_score": 0.9,
            "is_disqualified": True,
            "is_suspended": False,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate disqualification alert
        calls = [call[0][1] for call in mock_generate.call_args_list]
        assert "node_disqualified" in calls


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_suspended(alert_manager):
    """Test reputation alert evaluation for suspended node."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 0.8,
            "suspension_score": 0.4,
            "online_score": 0.9,
            "is_disqualified": False,
            "is_suspended": True,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate suspension alert
        calls = [call[0][1] for call in mock_generate.call_args_list]
        assert "node_suspended" in calls


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_suspension_risk(alert_manager):
    """Test reputation alert evaluation for suspension risk."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 0.95,
            "suspension_score": 0.55,  # Below critical threshold (0.6)
            "online_score": 0.95,
            "is_disqualified": False,
            "is_suspended": False,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate suspension risk alert
        calls = [call[0][1] for call in mock_generate.call_args_list]
        assert "suspension_risk" in calls


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_low_uptime(alert_manager):
    """Test reputation alert evaluation for low uptime score."""
    reputation_data = [
        {
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "audit_score": 1.0,
            "suspension_score": 1.0,
            "online_score": 0.85,  # Below warning threshold (0.9)
            "is_disqualified": False,
            "is_suspended": False,
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate uptime warning
        calls = [call[0][1] for call in mock_generate.call_args_list]
        assert "uptime_warning" in calls


@pytest.mark.asyncio
async def test_evaluate_reputation_alerts_no_data(alert_manager):
    """Test reputation alert evaluation with no data."""
    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", [])

        # Should not generate any alerts
        assert not mock_generate.called


@pytest.mark.asyncio
async def test_evaluate_storage_alerts_critical(alert_manager):
    """Test storage alert evaluation for critical usage."""
    storage_data = {
        "used_percent": 95.0,  # Above critical threshold (90%)
        "available_bytes": 500000000,
    }

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_storage_alerts("test-node", storage_data)

        # Should generate critical storage alert
        assert mock_generate.called
        call_args = mock_generate.call_args_list[0][0]
        assert call_args[1] == "storage_critical"
        assert call_args[2] == "critical"


@pytest.mark.asyncio
async def test_evaluate_storage_alerts_warning(alert_manager):
    """Test storage alert evaluation for warning usage."""
    storage_data = {
        "used_percent": 85.0,  # Above warning threshold (80%)
        "available_bytes": 1500000000,
    }

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_storage_alerts("test-node", storage_data)

        # Should generate warning storage alert
        assert mock_generate.called
        call_args = mock_generate.call_args_list[0][0]
        assert call_args[1] == "storage_warning"
        assert call_args[2] == "warning"


@pytest.mark.asyncio
async def test_evaluate_storage_alerts_healthy(alert_manager):
    """Test storage alert evaluation for healthy usage."""
    storage_data = {"used_percent": 50.0, "available_bytes": 5000000000}

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_storage_alerts("test-node", storage_data)

        # Should not generate alerts for healthy storage
        assert not mock_generate.called


@pytest.mark.asyncio
async def test_evaluate_latency_alerts_critical(alert_manager):
    """Test latency alert evaluation for critical latency."""
    # Mock config to have known thresholds
    with patch("storj_monitor.config.LATENCY_CRITICAL_MS", 1000):
        latency_data = {
            "p99": 1500.0  # Above critical threshold
        }

        with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
            await alert_manager.evaluate_latency_alerts("test-node", latency_data)

            # Should generate critical latency alert
            assert mock_generate.called
            call_args = mock_generate.call_args_list[0][0]
            assert call_args[1] == "latency_critical"
            assert call_args[2] == "critical"


@pytest.mark.asyncio
async def test_evaluate_latency_alerts_warning(alert_manager):
    """Test latency alert evaluation for warning latency."""
    # Mock config to have known thresholds
    with patch("storj_monitor.config.LATENCY_WARNING_MS", 500):
        with patch("storj_monitor.config.LATENCY_CRITICAL_MS", 1000):
            latency_data = {
                "p99": 700.0  # Above warning threshold
            }

            with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
                await alert_manager.evaluate_latency_alerts("test-node", latency_data)

                # Should generate warning latency alert
                assert mock_generate.called
                call_args = mock_generate.call_args_list[0][0]
                assert call_args[1] == "latency_warning"
                assert call_args[2] == "warning"


@pytest.mark.asyncio
async def test_process_anomalies_warning(alert_manager):
    """Test processing warning-level anomalies."""
    anomalies = [
        {
            "node_name": "test-node",
            "insight_type": "traffic_anomaly",
            "severity": "warning",
            "title": "Traffic Anomaly Detected",
            "description": "Unusual traffic pattern",
            "metadata": {"z_score": 3.5},
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.process_anomalies(anomalies)

        # Should generate alert for warning anomaly
        assert mock_generate.called


@pytest.mark.asyncio
async def test_process_anomalies_critical(alert_manager):
    """Test processing critical anomalies."""
    anomalies = [
        {
            "node_name": "test-node",
            "insight_type": "latency_spike",
            "severity": "critical",
            "title": "Critical Latency Spike",
            "description": "Severe performance degradation",
            "metadata": {"z_score": 5.0},
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.process_anomalies(anomalies)

        # Should generate alert for critical anomaly
        assert mock_generate.called


@pytest.mark.asyncio
async def test_process_anomalies_info_ignored(alert_manager):
    """Test that info-level anomalies are not alerted."""
    anomalies = [
        {
            "node_name": "test-node",
            "insight_type": "bandwidth_spike",
            "severity": "info",
            "title": "Bandwidth Increase",
            "description": "Bandwidth is higher than usual",
            "metadata": {"z_score": 3.2},
        }
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.process_anomalies(anomalies)

        # Should not generate alerts for info anomalies
        assert not mock_generate.called


@pytest.mark.asyncio
async def test_acknowledge_alert_success(alert_manager):
    """Test successful alert acknowledgment."""
    with patch("storj_monitor.database.blocking_acknowledge_alert", return_value=True):
        with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock()):
            success = await alert_manager.acknowledge_alert(123)

    assert success is True


@pytest.mark.asyncio
async def test_acknowledge_alert_failure(alert_manager):
    """Test alert acknowledgment failure."""
    with patch("storj_monitor.database.blocking_acknowledge_alert", return_value=False):
        success = await alert_manager.acknowledge_alert(123)

    assert success is False


@pytest.mark.asyncio
async def test_get_active_alerts_all_nodes(alert_manager):
    """Test retrieving active alerts for all nodes."""
    expected_alerts = [
        {"id": 1, "node_name": "node1", "severity": "warning"},
        {"id": 2, "node_name": "node2", "severity": "critical"},
    ]

    with patch("storj_monitor.database.blocking_get_active_alerts", return_value=expected_alerts):
        alerts = await alert_manager.get_active_alerts()

    assert len(alerts) == 2
    assert alerts == expected_alerts


@pytest.mark.asyncio
async def test_get_active_alerts_filtered(alert_manager):
    """Test retrieving active alerts filtered by node names."""
    expected_alerts = [{"id": 1, "node_name": "test-node", "severity": "warning"}]

    with patch("storj_monitor.database.blocking_get_active_alerts", return_value=expected_alerts):
        alerts = await alert_manager.get_active_alerts(node_names=["test-node"])

    assert len(alerts) == 1
    assert alerts[0]["node_name"] == "test-node"


def test_get_alert_summary_empty(alert_manager):
    """Test alert summary when no active alerts."""
    summary = alert_manager.get_alert_summary()

    assert summary["total"] == 0
    assert summary["critical"] == 0
    assert summary["warning"] == 0
    assert summary["info"] == 0


def test_get_alert_summary_with_alerts(alert_manager):
    """Test alert summary with various alert severities."""
    # Add test alerts to active_alerts
    alert_manager.active_alerts["key1"] = {"severity": "critical"}
    alert_manager.active_alerts["key2"] = {"severity": "critical"}
    alert_manager.active_alerts["key3"] = {"severity": "warning"}
    alert_manager.active_alerts["key4"] = {"severity": "warning"}
    alert_manager.active_alerts["key5"] = {"severity": "warning"}
    alert_manager.active_alerts["key6"] = {"severity": "info"}

    summary = alert_manager.get_alert_summary()

    assert summary["total"] == 6
    assert summary["critical"] == 2
    assert summary["warning"] == 3
    assert summary["info"] == 1


def test_get_alert_summary_unknown_severity(alert_manager):
    """Test alert summary handles unknown severity gracefully."""
    alert_manager.active_alerts["key1"] = {"severity": "unknown"}

    summary = alert_manager.get_alert_summary()

    assert summary["total"] == 1
    # Unknown severity shouldn't increment any counter
    assert summary["critical"] == 0
    assert summary["warning"] == 0
    assert summary["info"] == 0


@pytest.mark.asyncio
async def test_alert_broadcasting(alert_manager):
    """Test that alerts are broadcast via websockets."""
    with (
        patch("storj_monitor.database.blocking_write_alert", return_value=True),
        patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock()) as mock_broadcast,
    ):
        with patch("storj_monitor.notification_handler.notification_handler") as mock_notif:
            mock_notif.send_notification = AsyncMock(return_value=True)

            await alert_manager.generate_alert(
                "test-node", "test_alert", "warning", "Test Alert", "Test message"
            )

    # Verify broadcast was called
    assert mock_broadcast.called


@pytest.mark.asyncio
async def test_alert_notification_sending(alert_manager):
    """Test that alerts trigger notifications."""
    with patch("storj_monitor.database.blocking_write_alert", return_value=True):
        with patch("storj_monitor.websocket_utils.robust_broadcast", new=AsyncMock()):
            with patch("storj_monitor.notification_handler.notification_handler") as mock_notif:
                mock_notif.send_notification = AsyncMock(return_value=True)

                await alert_manager.generate_alert(
                    "test-node", "test_alert", "critical", "Critical Alert", "Critical message"
                )

    # Verify notification was sent
    assert mock_notif.send_notification.called


@pytest.mark.asyncio
async def test_multiple_satellites_alerts(alert_manager):
    """Test alert generation for multiple satellites."""
    reputation_data = [
        {
            "satellite": "sat1",
            "audit_score": 0.55,
            "suspension_score": 1.0,
            "online_score": 1.0,
            "is_disqualified": False,
            "is_suspended": False,
        },
        {
            "satellite": "sat2",
            "audit_score": 0.52,
            "suspension_score": 1.0,
            "online_score": 1.0,
            "is_disqualified": False,
            "is_suspended": False,
        },
    ]

    with patch.object(alert_manager, "generate_alert", new=AsyncMock()) as mock_generate:
        await alert_manager.evaluate_reputation_alerts("test-node", reputation_data)

        # Should generate alerts for both satellites
        assert mock_generate.call_count >= 2
