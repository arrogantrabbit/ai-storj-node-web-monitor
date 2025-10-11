import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.alert_manager import AlertManager
from storj_monitor.reputation_tracker import get_reputation_summary


@pytest.mark.asyncio
async def test_alert_manager_generate_and_cooldown_and_summary():
    # Arrange
    app = {"db_executor": object()}
    am = AlertManager(app, analytics_engine=None, anomaly_detector=None)

    with (
        patch("storj_monitor.database.blocking_write_alert", return_value=True),
        patch("asyncio.get_running_loop") as mock_loop,
        patch("storj_monitor.notification_handler.notification_handler.send_notification", new_callable=AsyncMock) as mock_notify,
        patch("storj_monitor.websocket_utils.robust_broadcast", new_callable=AsyncMock) as mock_broadcast,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=True)

        # Act: first alert should be created
        alert = await am.generate_alert(
            node_name="node-x",
            alert_type="unit_test",
            severity="warning",
            title="Test Title",
            message="Test Message",
            metadata={"satellite": "sat-1"},
        )

        # Assert: alert created and notifications attempted
        assert alert is not None
        assert alert["node_name"] == "node-x"
        assert alert["severity"] == "warning"
        assert "unit_test" in alert["alert_type"]

        # Summary should reflect 1 warning
        summary = am.get_alert_summary()
        assert summary["total"] == 1
        assert summary["warning"] == 1

        # Act: trigger same alert immediately -> cooldown suppresses duplicate
        suppressed = await am.generate_alert(
            node_name="node-x",
            alert_type="unit_test",
            severity="warning",
            title="Test Title",
            message="Test Message",
            metadata={"satellite": "sat-1"},
        )

        # Assert: suppressed due to cooldown
        assert suppressed is None

        # Ensure broadcast and notifications were invoked at least once
        assert mock_broadcast.await_count >= 1
        assert mock_notify.await_count >= 1


@pytest.mark.asyncio
async def test_get_reputation_summary_adds_health_score_when_no_node_names():
    # Arrange: When node_names is None, function uses app['api_clients'] keys
    app = {"db_executor": object(), "api_clients": {"node-a": object()}}

    # Simulate DB returning one summary dict
    fake_summary = [
        {"audit_score": 90.0, "suspension_score": 80.0, "online_score": 70.0}
    ]
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=fake_summary)

        # Act
        summaries = await get_reputation_summary(app)

    # Assert: health_score added and equals weighted average: 0.4*90 + 0.3*80 + 0.3*70 = 81.0
    assert isinstance(summaries, list) and len(summaries) == 1
    assert summaries[0]["health_score"] == 81.0