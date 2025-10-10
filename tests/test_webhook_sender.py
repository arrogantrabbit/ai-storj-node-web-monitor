"""
Comprehensive unit tests for Webhook Sender module.

Tests Discord, Slack, and custom webhook formatting, sending, and error handling.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from storj_monitor.webhook_sender import (
    _format_custom_webhook,
    _format_discord_webhook,
    _format_slack_webhook,
    send_webhook_notification,
)


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


@pytest.fixture
def mock_aiohttp_session():
    """Create mock aiohttp session for webhook testing."""
    session = AsyncMock()

    # Mock successful response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.raise_for_status = Mock()

    # Mock post method
    session.post = AsyncMock(return_value=mock_response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return session


def test_format_discord_webhook_critical(alert_data):
    """Test Discord webhook formatting for critical alert."""
    payload = _format_discord_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    assert "username" in payload
    assert payload["username"] == "Storj Node Monitor"
    assert "embeds" in payload
    assert len(payload["embeds"]) == 1

    embed = payload["embeds"][0]
    assert embed["title"] == f"Storj Node Alert: {alert_data['alert_type']}"
    assert embed["description"] == alert_data["message"]
    assert embed["color"] == 16711680  # Red for CRITICAL
    assert "fields" in embed
    assert len(embed["fields"]) == len(alert_data["details"])


def test_format_discord_webhook_warning():
    """Test Discord webhook formatting for warning alert."""
    payload = _format_discord_webhook(
        "audit_score_low",
        "WARNING",
        "Audit score has dropped below 0.95",
        {"audit_score": "0.94", "satellite": "us1.storj.io"},
    )

    embed = payload["embeds"][0]
    assert embed["color"] == 16776960  # Yellow for WARNING


def test_format_discord_webhook_info():
    """Test Discord webhook formatting for info alert."""
    payload = _format_discord_webhook(
        "node_started", "INFO", "Node has started successfully", {"node_name": "test-node"}
    )

    embed = payload["embeds"][0]
    assert embed["color"] == 255  # Blue for INFO


def test_format_discord_webhook_unknown_severity():
    """Test Discord webhook formatting with unknown severity."""
    payload = _format_discord_webhook("unknown_alert", "UNKNOWN", "Unknown alert type", {})

    embed = payload["embeds"][0]
    assert embed["color"] == 0  # Black for unknown


def test_format_discord_webhook_fields(alert_data):
    """Test Discord webhook field formatting."""
    payload = _format_discord_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    embed = payload["embeds"][0]
    fields = embed["fields"]

    # Verify all details are in fields
    for key, value in alert_data["details"].items():
        field_found = False
        for field in fields:
            if field["name"] == key and field["value"] == str(value):
                field_found = True
                assert field["inline"] is True
                break
        assert field_found, f"Field {key} not found in embed"


def test_format_slack_webhook_critical(alert_data):
    """Test Slack webhook formatting for critical alert."""
    payload = _format_slack_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    assert "attachments" in payload
    assert len(payload["attachments"]) == 1

    attachment = payload["attachments"][0]
    assert attachment["color"] == "#FF0000"  # Red for CRITICAL
    assert attachment["pretext"] == "Storj Node Alert: *CRITICAL*"
    assert attachment["title"] == alert_data["alert_type"]
    assert attachment["text"] == alert_data["message"]
    assert "fields" in attachment


def test_format_slack_webhook_warning():
    """Test Slack webhook formatting for warning alert."""
    payload = _format_slack_webhook("test_alert", "WARNING", "Test warning message", {})

    attachment = payload["attachments"][0]
    assert attachment["color"] == "#FFA500"  # Orange for WARNING


def test_format_slack_webhook_info():
    """Test Slack webhook formatting for info alert."""
    payload = _format_slack_webhook("test_alert", "INFO", "Test info message", {})

    attachment = payload["attachments"][0]
    assert attachment["color"] == "#0000FF"  # Blue for INFO


def test_format_slack_webhook_fields(alert_data):
    """Test Slack webhook field formatting."""
    payload = _format_slack_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    attachment = payload["attachments"][0]
    fields = attachment["fields"]

    # Verify all details are in fields
    for key, value in alert_data["details"].items():
        field_found = False
        for field in fields:
            if field["title"] == key and field["value"] == str(value):
                field_found = True
                assert field["short"] is True
                break
        assert field_found, f"Field {key} not found in attachment"


def test_format_slack_webhook_fallback(alert_data):
    """Test Slack webhook fallback text."""
    payload = _format_slack_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    attachment = payload["attachments"][0]
    expected_fallback = f"Storj Node Alert: {alert_data['alert_type']} - {alert_data['severity']} - {alert_data['message']}"
    assert attachment["fallback"] == expected_fallback


def test_format_custom_webhook(alert_data):
    """Test custom webhook formatting."""
    payload = _format_custom_webhook(
        alert_data["alert_type"],
        alert_data["severity"],
        alert_data["message"],
        alert_data["details"],
    )

    assert payload["alert_type"] == alert_data["alert_type"]
    assert payload["severity"] == alert_data["severity"]
    assert payload["message"] == alert_data["message"]
    assert payload["details"] == alert_data["details"]
    assert "timestamp" in payload


def test_format_custom_webhook_minimal():
    """Test custom webhook with minimal data."""
    payload = _format_custom_webhook("test", "INFO", "Test message", {})

    assert payload["alert_type"] == "test"
    assert payload["severity"] == "INFO"
    assert payload["message"] == "Test message"
    assert payload["details"] == {}


@pytest.mark.asyncio
async def test_send_discord_webhook_success(alert_data, mock_aiohttp_session):
    """Test successful Discord webhook sending."""
    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Verify post was called
        assert mock_aiohttp_session.post.called


@pytest.mark.asyncio
async def test_send_slack_webhook_success(alert_data, mock_aiohttp_session):
    """Test successful Slack webhook sending."""
    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
        await send_webhook_notification(
            url="https://hooks.slack.com/services/test",
            platform="slack",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        assert mock_aiohttp_session.post.called


@pytest.mark.asyncio
async def test_send_custom_webhook_success(alert_data, mock_aiohttp_session):
    """Test successful custom webhook sending."""
    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
        await send_webhook_notification(
            url="https://example.com/webhook",
            platform="custom",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        assert mock_aiohttp_session.post.called


@pytest.mark.asyncio
async def test_send_webhook_no_url(alert_data):
    """Test webhook sending with no URL."""
    with patch("aiohttp.ClientSession") as mock_session:
        await send_webhook_notification(
            url="",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Should not create session when URL is empty
        assert not mock_session.called


@pytest.mark.asyncio
async def test_send_webhook_http_error(alert_data):
    """Test webhook sending with HTTP error."""
    mock_session = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 400
    mock_response.raise_for_status = Mock(side_effect=aiohttp.ClientError("Bad Request"))

    mock_session.post = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        # Should not raise exception, but log error
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )


@pytest.mark.asyncio
async def test_send_webhook_network_error(alert_data):
    """Test webhook sending with network error."""
    mock_session = AsyncMock()
    mock_session.post = AsyncMock(side_effect=aiohttp.ClientConnectionError("Connection failed"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        # Should not raise exception, but log error
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )


@pytest.mark.asyncio
async def test_send_webhook_timeout(alert_data):
    """Test webhook sending with timeout."""
    mock_session = AsyncMock()
    mock_session.post = AsyncMock(side_effect=asyncio.TimeoutError("Request timeout"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        # Should not raise exception, but log error
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )


@pytest.mark.asyncio
async def test_concurrent_webhook_delivery(alert_data, mock_aiohttp_session):
    """Test concurrent webhook delivery to multiple platforms."""
    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
        tasks = [
            send_webhook_notification(
                url="https://discord.com/api/webhooks/test",
                platform="discord",
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                message=alert_data["message"],
                details=alert_data["details"],
            ),
            send_webhook_notification(
                url="https://hooks.slack.com/services/test",
                platform="slack",
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                message=alert_data["message"],
                details=alert_data["details"],
            ),
            send_webhook_notification(
                url="https://example.com/webhook",
                platform="custom",
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                message=alert_data["message"],
                details=alert_data["details"],
            ),
        ]

        await asyncio.gather(*tasks)

        # Verify all webhooks were sent
        assert mock_aiohttp_session.post.call_count == 3


@pytest.mark.asyncio
async def test_webhook_payload_validation(alert_data, mock_aiohttp_session):
    """Test that webhook payloads are correctly formatted."""
    with patch("aiohttp.ClientSession", return_value=mock_aiohttp_session):
        # Discord
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        discord_call = mock_aiohttp_session.post.call_args
        discord_payload = discord_call[1]["json"]
        assert "embeds" in discord_payload

        # Reset mock
        mock_aiohttp_session.post.reset_mock()

        # Slack
        await send_webhook_notification(
            url="https://hooks.slack.com/services/test",
            platform="slack",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        slack_call = mock_aiohttp_session.post.call_args
        slack_payload = slack_call[1]["json"]
        assert "attachments" in slack_payload


def test_discord_webhook_empty_details():
    """Test Discord webhook with empty details."""
    payload = _format_discord_webhook("test_alert", "INFO", "Test message", {})

    embed = payload["embeds"][0]
    assert "fields" in embed
    assert len(embed["fields"]) == 0


def test_slack_webhook_empty_details():
    """Test Slack webhook with empty details."""
    payload = _format_slack_webhook("test_alert", "INFO", "Test message", {})

    attachment = payload["attachments"][0]
    assert "fields" in attachment
    assert len(attachment["fields"]) == 0


def test_discord_webhook_special_characters():
    """Test Discord webhook with special characters in message."""
    payload = _format_discord_webhook(
        "test_alert",
        "CRITICAL",
        'Alert: Node "test-node" has 100% disk! 🚨',
        {"special": "value<>&\"'"},
    )

    embed = payload["embeds"][0]
    assert 'Alert: Node "test-node" has 100% disk! 🚨' in embed["description"]


def test_slack_webhook_special_characters():
    """Test Slack webhook with special characters in message."""
    payload = _format_slack_webhook(
        "test_alert",
        "CRITICAL",
        'Alert: Node "test-node" has 100% disk! 🚨',
        {"special": "value<>&\"'"},
    )

    attachment = payload["attachments"][0]
    assert 'Alert: Node "test-node" has 100% disk! 🚨' in attachment["text"]


def test_discord_webhook_numeric_details():
    """Test Discord webhook with numeric values in details."""
    payload = _format_discord_webhook(
        "metrics",
        "INFO",
        "Performance metrics",
        {"cpu_usage": 75.5, "memory_mb": 1024, "uptime_hours": 168},
    )

    embed = payload["embeds"][0]
    fields = embed["fields"]

    # Verify numeric values are converted to strings
    assert all(isinstance(field["value"], str) for field in fields)


def test_slack_webhook_numeric_details():
    """Test Slack webhook with numeric values in details."""
    payload = _format_slack_webhook(
        "metrics",
        "INFO",
        "Performance metrics",
        {"cpu_usage": 75.5, "memory_mb": 1024, "uptime_hours": 168},
    )

    attachment = payload["attachments"][0]
    fields = attachment["fields"]

    # Verify numeric values are converted to strings
    assert all(isinstance(field["value"], str) for field in fields)


@pytest.mark.asyncio
async def test_webhook_url_validation():
    """Test webhook sending with various URL formats."""
    test_urls = [
        "https://discord.com/api/webhooks/123/token",
        "https://hooks.slack.com/services/T/B/token",
        "http://localhost:8080/webhook",
        "https://example.com/api/v1/webhooks/alert",
    ]

    mock_session = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.raise_for_status = Mock()

    mock_session.post = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        for url in test_urls:
            await send_webhook_notification(
                url=url,
                platform="custom",
                alert_type="test",
                severity="INFO",
                message="Test message",
                details={},
            )

        # All URLs should have been called
        assert mock_session.post.call_count == len(test_urls)


@pytest.mark.asyncio
async def test_webhook_retry_behavior(alert_data):
    """Test webhook behavior when retries might be needed."""
    mock_session = AsyncMock()

    # First call fails, but we don't retry in current implementation
    mock_response_fail = AsyncMock()
    mock_response_fail.status = 500
    mock_response_fail.raise_for_status = Mock(side_effect=aiohttp.ClientError("Server Error"))

    mock_session.post = AsyncMock(return_value=mock_response_fail)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        await send_webhook_notification(
            url="https://discord.com/api/webhooks/test",
            platform="discord",
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            message=alert_data["message"],
            details=alert_data["details"],
        )

        # Should only call once (no retry logic)
        assert mock_session.post.call_count == 1


def test_discord_webhook_avatar_url():
    """Test Discord webhook includes avatar URL."""
    payload = _format_discord_webhook("test", "INFO", "Test", {})

    assert "avatar_url" in payload
    assert isinstance(payload["avatar_url"], str)


def test_discord_webhook_username():
    """Test Discord webhook includes username."""
    payload = _format_discord_webhook("test", "INFO", "Test", {})

    assert "username" in payload
    assert payload["username"] == "Storj Node Monitor"


def test_custom_webhook_structure():
    """Test custom webhook has all required fields."""
    payload = _format_custom_webhook(
        "test_alert", "WARNING", "Test warning", {"key1": "value1", "key2": "value2"}
    )

    # Verify all required fields are present
    required_fields = ["alert_type", "severity", "message", "details", "timestamp"]
    for field in required_fields:
        assert field in payload


@pytest.mark.asyncio
async def test_webhook_error_handling_preserves_execution(alert_data):
    """Test that webhook errors don't crash the application."""
    mock_session = AsyncMock()
    mock_session.post = AsyncMock(side_effect=Exception("Unexpected error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        # Should handle exception gracefully
        try:
            await send_webhook_notification(
                url="https://discord.com/api/webhooks/test",
                platform="discord",
                alert_type=alert_data["alert_type"],
                severity=alert_data["severity"],
                message=alert_data["message"],
                details=alert_data["details"],
            )
            # Should not raise exception
            assert True
        except Exception:
            pytest.fail("Webhook error should be caught and logged")


def test_webhook_formatting_consistency():
    """Test that webhook formatters produce consistent output."""
    test_cases = [
        ("CRITICAL", "disk_full", "Disk is full"),
        ("WARNING", "high_cpu", "CPU usage high"),
        ("INFO", "node_started", "Node started"),
    ]

    for severity, alert_type, message in test_cases:
        discord_payload = _format_discord_webhook(alert_type, severity, message, {})
        slack_payload = _format_slack_webhook(alert_type, severity, message, {})
        custom_payload = _format_custom_webhook(alert_type, severity, message, {})

        # All should have non-empty payloads
        assert discord_payload
        assert slack_payload
        assert custom_payload

        # Discord should have embeds
        assert "embeds" in discord_payload
        assert len(discord_payload["embeds"]) > 0

        # Slack should have attachments
        assert "attachments" in slack_payload
        assert len(slack_payload["attachments"]) > 0

        # Custom should have basic fields
        assert "alert_type" in custom_payload
        assert "severity" in custom_payload
        assert "message" in custom_payload
