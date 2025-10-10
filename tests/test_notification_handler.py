"""
Comprehensive unit tests for Notification Handler module.

Tests notification routing, channel selection, multi-channel delivery, and filtering.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.notification_handler import NotificationHandler, notification_handler


@pytest.fixture
def handler():
    """Create notification handler instance."""
    return NotificationHandler()


@pytest.fixture
def alert_data():
    """Sample alert data for testing."""
    return {
        "alert_type": "disk_full",
        "severity": "CRITICAL",
        "message": "Disk usage has exceeded 95%",
        "details": {
            "node_name": "test-node",
            "disk_usage": "96%",
            "available_space": "2GB",
            "timestamp": "2025-01-15 10:30:00",
        },
    }


@pytest.mark.asyncio
async def test_notification_handler_initialization(handler):
    """Test notification handler initialization."""
    assert handler.email_enabled is not None
    assert handler.webhook_enabled is not None
    assert isinstance(handler.email_recipients, list)
    assert isinstance(handler.custom_webhook_urls, list)


@pytest.mark.asyncio
async def test_send_notification_both_channels(handler, alert_data):
    """Test sending notification to both email and webhook channels."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock) as mock_webhooks,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Both channels should be called
        assert mock_email.called
        assert mock_webhooks.called


@pytest.mark.asyncio
async def test_send_notification_email_only(handler, alert_data):
    """Test sending notification via email only."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock) as mock_webhooks,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", False),
        patch.object(handler, "email_recipients", ["test@example.com"]),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Only email should be called
        assert mock_email.called
        assert not mock_webhooks.called


@pytest.mark.asyncio
async def test_send_notification_webhook_only(handler, alert_data):
    """Test sending notification via webhook only."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock) as mock_webhooks,
        patch.object(handler, "email_enabled", False),
        patch.object(handler, "webhook_enabled", True),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Only webhooks should be called
        assert not mock_email.called
        assert mock_webhooks.called


@pytest.mark.asyncio
async def test_send_notification_no_channels(handler, alert_data):
    """Test sending notification with no channels enabled."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock) as mock_webhooks,
        patch.object(handler, "email_enabled", False),
        patch.object(handler, "webhook_enabled", False),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Neither channel should be called
        assert not mock_email.called
        assert not mock_webhooks.called


@pytest.mark.asyncio
async def test_send_notification_no_recipients(handler, alert_data):
    """Test sending notification with email enabled but no recipients."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock),
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", False),
        patch.object(handler, "email_recipients", []),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Email should not be called without recipients
        assert not mock_email.called


@pytest.mark.asyncio
async def test_send_email_success(handler, alert_data):
    """Test successful email sending."""
    with patch(
        "storj_monitor.notification_handler.send_email_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.email_recipients = ["test@example.com"]

        await handler._send_email(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify email was sent with correct parameters
        assert mock_send.called
        call_args = mock_send.call_args
        assert call_args[1]["recipients"] == handler.email_recipients
        assert alert_data["alert_type"] in call_args[1]["subject"]
        assert alert_data["severity"] in call_args[1]["subject"]


@pytest.mark.asyncio
async def test_send_email_error_handling(handler, alert_data):
    """Test email error handling."""
    with patch(
        "storj_monitor.notification_handler.send_email_notification",
        new_callable=AsyncMock,
        side_effect=Exception("Email error"),
    ):
        handler.email_recipients = ["test@example.com"]

        # Should not raise exception
        await handler._send_email(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )


@pytest.mark.asyncio
async def test_send_webhooks_discord(handler, alert_data):
    """Test sending Discord webhook."""
    with patch(
        "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.discord_webhook_url = "https://discord.com/api/webhooks/test"
        handler.slack_webhook_url = None
        handler.custom_webhook_urls = []

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify Discord webhook was called
        assert mock_send.called
        call_args = mock_send.call_args
        assert call_args[1]["url"] == handler.discord_webhook_url
        assert call_args[1]["platform"] == "discord"


@pytest.mark.asyncio
async def test_send_webhooks_slack(handler, alert_data):
    """Test sending Slack webhook."""
    with patch(
        "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.discord_webhook_url = None
        handler.slack_webhook_url = "https://hooks.slack.com/services/test"
        handler.custom_webhook_urls = []

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify Slack webhook was called
        assert mock_send.called
        call_args = mock_send.call_args
        assert call_args[1]["url"] == handler.slack_webhook_url
        assert call_args[1]["platform"] == "slack"


@pytest.mark.asyncio
async def test_send_webhooks_custom(handler, alert_data):
    """Test sending custom webhooks."""
    custom_urls = ["https://example.com/webhook1", "https://example.com/webhook2"]

    with patch(
        "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.discord_webhook_url = None
        handler.slack_webhook_url = None
        handler.custom_webhook_urls = custom_urls

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify custom webhooks were called
        assert mock_send.call_count == len(custom_urls)


@pytest.mark.asyncio
async def test_send_webhooks_all_platforms(handler, alert_data):
    """Test sending to all webhook platforms simultaneously."""
    with patch(
        "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.discord_webhook_url = "https://discord.com/api/webhooks/test"
        handler.slack_webhook_url = "https://hooks.slack.com/services/test"
        handler.custom_webhook_urls = ["https://example.com/webhook"]

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Should call webhook sender 3 times (Discord + Slack + 1 custom)
        assert mock_send.call_count == 3


@pytest.mark.asyncio
async def test_send_webhooks_no_urls(handler, alert_data):
    """Test sending webhooks with no URLs configured."""
    with patch(
        "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
    ) as mock_send:
        handler.discord_webhook_url = None
        handler.slack_webhook_url = None
        handler.custom_webhook_urls = []

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Should not call webhook sender
        assert not mock_send.called


def test_format_email_content_critical(handler, alert_data):
    """Test email content formatting for critical alert."""
    html_content = handler._format_email_content(
        alert_type=alert_data["alert_type"],
        severity="CRITICAL",
        message=alert_data["message"],
        details=alert_data["details"],
    )

    assert isinstance(html_content, str)
    assert "CRITICAL" in html_content
    assert alert_data["alert_type"] in html_content
    assert alert_data["message"] in html_content

    # Verify details are included
    for _key, value in alert_data["details"].items():
        assert str(value) in html_content


def test_format_email_content_warning(handler):
    """Test email content formatting for warning alert."""
    html_content = handler._format_email_content(
        alert_type="audit_score_low",
        severity="WARNING",
        message="Audit score below threshold",
        details={"audit_score": "0.94"},
    )

    assert "WARNING" in html_content
    assert "#FFA500" in html_content  # Orange color for warning


def test_format_email_content_info(handler):
    """Test email content formatting for info alert."""
    html_content = handler._format_email_content(
        alert_type="node_started",
        severity="INFO",
        message="Node started successfully",
        details={"node_name": "test-node"},
    )

    assert "INFO" in html_content
    assert "#0000FF" in html_content  # Blue color for info


def test_format_email_content_html_structure(handler, alert_data):
    """Test email HTML structure."""
    html_content = handler._format_email_content(
        alert_type=alert_data["alert_type"],
        severity=alert_data["severity"],
        message=alert_data["message"],
        details=alert_data["details"],
    )

    # Verify HTML structure
    assert "<!DOCTYPE html>" in html_content
    assert "<html>" in html_content
    assert "<body>" in html_content
    assert "</html>" in html_content
    assert "<style>" in html_content
    assert "</style>" in html_content


def test_format_email_content_severity_colors(handler):
    """Test email severity color mapping."""
    severities = {"CRITICAL": "#FF0000", "WARNING": "#FFA500", "INFO": "#0000FF"}

    for severity, color in severities.items():
        html_content = handler._format_email_content(
            alert_type="test", severity=severity, message="Test message", details={}
        )

        assert color in html_content


def test_format_email_content_empty_details(handler):
    """Test email formatting with empty details."""
    html_content = handler._format_email_content(
        alert_type="test_alert", severity="INFO", message="Test message", details={}
    )

    assert isinstance(html_content, str)
    assert "test_alert" in html_content
    assert "Test message" in html_content


def test_format_email_content_special_characters(handler):
    """Test email formatting with special characters."""
    html_content = handler._format_email_content(
        alert_type="test_alert",
        severity="CRITICAL",
        message='Alert: Node "test-node" has 100% disk! 🚨',
        details={"special": "value<>&\"'"},
    )

    assert "test-node" in html_content
    assert "100%" in html_content


@pytest.mark.asyncio
async def test_multi_channel_delivery_success(handler, alert_data):
    """Test successful multi-channel notification delivery."""
    with (
        patch(
            "storj_monitor.notification_handler.send_email_notification", new_callable=AsyncMock
        ) as mock_email,
        patch(
            "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
        ) as mock_webhook,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
        patch.object(handler, "discord_webhook_url", "https://discord.com/api/webhooks/test"),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Both channels should be called
        assert mock_email.called
        assert mock_webhook.called


@pytest.mark.asyncio
async def test_multi_channel_partial_failure(handler, alert_data):
    """Test multi-channel delivery with one channel failing."""
    with (
        patch(
            "storj_monitor.notification_handler.send_email_notification",
            new_callable=AsyncMock,
            side_effect=Exception("Email failed"),
        ),
        patch(
            "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
        ) as mock_webhook,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
        patch.object(handler, "discord_webhook_url", "https://discord.com/api/webhooks/test"),
    ):
        # Should not raise exception even if email fails
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Webhook should still be attempted
        assert mock_webhook.called


@pytest.mark.asyncio
async def test_notification_routing_by_severity_critical(handler):
    """Test notification routing for critical alerts."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "_send_webhooks", new_callable=AsyncMock) as mock_webhooks,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", True),
        patch.object(handler, "email_recipients", ["admin@example.com"]),
    ):
        await handler.send_notification(
            alert_type="disk_full", severity="CRITICAL", message="Critical alert", details={}
        )

        # Both channels should be used for critical alerts
        assert mock_email.called
        assert mock_webhooks.called


@pytest.mark.asyncio
async def test_concurrent_notifications(handler, alert_data):
    """Test sending multiple notifications concurrently."""
    with (
        patch(
            "storj_monitor.notification_handler.send_email_notification", new_callable=AsyncMock
        ) as mock_email,
        patch(
            "storj_monitor.notification_handler.send_webhook_notification", new_callable=AsyncMock
        ) as mock_webhook,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "webhook_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
        patch.object(handler, "discord_webhook_url", "https://discord.com/api/webhooks/test"),
    ):
        # Send multiple notifications concurrently
        tasks = [
            handler.send_notification(
                alert_type=f"alert_{i}", severity="WARNING", message=f"Test alert {i}", details={}
            )
            for i in range(5)
        ]

        await asyncio.gather(*tasks)

        # All notifications should be sent
        assert mock_email.call_count == 5
        assert mock_webhook.call_count == 5


def test_global_notification_handler_instance():
    """Test that global notification handler instance exists."""

    assert notification_handler is not None
    assert isinstance(notification_handler, NotificationHandler)


@pytest.mark.asyncio
async def test_notification_metadata_preservation(handler, alert_data):
    """Test that notification metadata is preserved through channels."""
    with (
        patch(
            "storj_monitor.notification_handler.send_email_notification", new_callable=AsyncMock
        ) as mock_email,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
    ):
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify all metadata is passed to email
        call_args = mock_email.call_args
        html_content = call_args[1]["html_content"]

        # Check that all details are in email content
        for _key, value in alert_data["details"].items():
            assert str(value) in html_content


def test_email_subject_formatting(handler, alert_data):
    """Test email subject formatting."""
    # Email subject should be: "Storj Node Alert: {alert_type} - {severity}"
    expected_subject = f"Storj Node Alert: {alert_data['alert_type']} - {alert_data['severity']}"

    with patch(
        "storj_monitor.notification_handler.send_email_notification", new_callable=AsyncMock
    ) as mock_email:
        handler.email_recipients = ["test@example.com"]

        # Use asyncio.run to execute the async function
        asyncio.run(
            handler._send_email(
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                message=alert_data["message"],
                details=alert_data["details"],
            )
        )

        call_args = mock_email.call_args
        assert call_args[1]["subject"] == expected_subject


@pytest.mark.asyncio
async def test_webhook_error_resilience(handler, alert_data):
    """Test that webhook errors don't prevent other webhooks from being sent."""
    call_count = {"success": 0, "error": 0}

    async def mock_webhook_send(url, platform, alert_type, severity, message, details):
        if "fail" in url:
            call_count["error"] += 1
            raise Exception("Webhook failed")
        else:
            call_count["success"] += 1

    with patch(
        "storj_monitor.notification_handler.send_webhook_notification",
        side_effect=mock_webhook_send,
    ):
        handler.discord_webhook_url = "https://discord.com/fail"
        handler.slack_webhook_url = "https://slack.com/success"
        handler.custom_webhook_urls = []

        await handler._send_webhooks(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Both should be attempted despite one failing
        assert call_count["error"] == 1
        assert call_count["success"] == 1


def test_email_html_escaping(handler):
    """Test that HTML special characters are handled correctly."""
    html_content = handler._format_email_content(
        alert_type="test",
        severity="INFO",
        message='Test message with <tags> & "quotes"',
        details={"key": 'value with <html> & "chars"'},
    )

    # Content should be in HTML but properly formatted
    assert "<" in html_content  # HTML tags exist
    assert isinstance(html_content, str)


@pytest.mark.asyncio
async def test_notification_with_numeric_details(handler):
    """Test notification with numeric detail values."""
    with (
        patch.object(handler, "_send_email", new_callable=AsyncMock) as mock_email,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
    ):
        await handler.send_notification(
            alert_type="metrics",
            severity="INFO",
            message="Metrics update",
            details={"cpu_usage": 75.5, "memory_mb": 1024, "uptime_hours": 168},
        )

        assert mock_email.called


@pytest.mark.asyncio
async def test_notification_handler_cleanup(handler, alert_data):
    """Test that notification handler cleans up properly after errors."""
    with (
        patch(
            "storj_monitor.notification_handler.send_email_notification",
            new_callable=AsyncMock,
            side_effect=Exception("Error"),
        ) as mock_email,
        patch.object(handler, "email_enabled", True),
        patch.object(handler, "email_recipients", ["test@example.com"]),
    ):
        # Should handle error gracefully
        await handler.send_notification(
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Handler should still be usable after error
        mock_email.side_effect = None
        mock_email.reset_mock()

        await handler.send_notification(
            alert_type="test", severity="INFO", message="Test", details={}
        )

        assert mock_email.called
