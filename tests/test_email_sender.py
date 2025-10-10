"""
Comprehensive unit tests for Email Sender module.

Tests email formatting, SMTP connection, sending, and error handling.
"""

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from unittest.mock import MagicMock, patch

import pytest

from storj_monitor.email_sender import _send_smtp_email, send_email_notification


@pytest.fixture
def email_config():
    """Mock email configuration."""
    return {
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "use_tls": True,
        "username": "test@example.com",
        "password": "test_password",
    }


@pytest.fixture
def sample_recipients():
    """Sample email recipients."""
    return ["recipient1@example.com", "recipient2@example.com"]


@pytest.fixture
def sample_html_content():
    """Sample HTML email content."""
    return """
    <html>
        <body>
            <h1>Test Alert</h1>
            <p>This is a test email.</p>
        </body>
    </html>
    """


@pytest.mark.asyncio
async def test_send_email_notification_success(sample_recipients, sample_html_content):
    """Test successful email notification sending."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=sample_recipients, subject="Test Subject", html_content=sample_html_content
        )

        # Verify SMTP function was called
        assert mock_smtp.called
        call_args = mock_smtp.call_args

        # Check message structure
        message = call_args[0][0]
        assert isinstance(message, MIMEMultipart)
        assert message["Subject"] == "Test Subject"
        assert message["From"] == "test@example.com"
        assert message["To"] == ", ".join(sample_recipients)


@pytest.mark.asyncio
async def test_send_email_notification_no_recipients(sample_html_content):
    """Test email notification with no recipients."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=[], subject="Test Subject", html_content=sample_html_content
        )

        # Should not call SMTP function with empty recipients
        assert not mock_smtp.called


@pytest.mark.asyncio
async def test_send_email_notification_no_credentials(sample_recipients, sample_html_content):
    """Test email notification without credentials."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", None),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", None),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=sample_recipients, subject="Test Subject", html_content=sample_html_content
        )

        # Should not call SMTP function without credentials
        assert not mock_smtp.called


@pytest.mark.asyncio
async def test_send_email_notification_smtp_error(sample_recipients, sample_html_content):
    """Test email notification with SMTP error."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch(
            "storj_monitor.email_sender._send_smtp_email",
            side_effect=smtplib.SMTPException("SMTP Error"),
        ),
    ):
        # Should not raise exception, but log error
        await send_email_notification(
            recipients=sample_recipients, subject="Test Subject", html_content=sample_html_content
        )


@pytest.mark.asyncio
async def test_email_message_formatting(sample_recipients, sample_html_content):
    """Test email message formatting."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        subject = "Critical Alert: Node Offline"

        await send_email_notification(
            recipients=sample_recipients, subject=subject, html_content=sample_html_content
        )

        message = mock_smtp.call_args[0][0]

        # Verify message headers
        assert message["Subject"] == subject
        assert message["From"] == "test@example.com"
        assert message["To"] == "recipient1@example.com, recipient2@example.com"

        # Verify message has HTML part
        parts = message.get_payload()
        assert len(parts) > 0

        html_part = parts[0]
        assert html_part.get_content_type() == "text/html"


def test_send_smtp_email_with_tls():
    """Test SMTP email sending with TLS."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("smtplib.SMTP") as mock_smtp_class,
    ):
        # Setup mock SMTP server
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        # Create test message
        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "test@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        # Send email
        _send_smtp_email(message, recipients)

        # Verify SMTP operations
        mock_smtp_class.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@example.com", "test_password")
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()


def test_send_smtp_email_with_ssl():
    """Test SMTP email sending with SSL."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 465),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", False),
        patch("smtplib.SMTP_SSL") as mock_smtp_ssl_class,
    ):
        # Setup mock SMTP server
        mock_server = MagicMock()
        mock_smtp_ssl_class.return_value = mock_server

        # Create test message
        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "test@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        # Send email
        _send_smtp_email(message, recipients)

        # Verify SMTP_SSL was used (no starttls)
        mock_smtp_ssl_class.assert_called_once()
        mock_server.starttls.assert_not_called()
        mock_server.login.assert_called_once_with("test@example.com", "test_password")
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()


def test_send_smtp_email_authentication_error():
    """Test SMTP email with authentication error."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "wrong_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("smtplib.SMTP") as mock_smtp_class,
    ):
        # Setup mock to raise authentication error
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, "Authentication failed"
        )
        mock_smtp_class.return_value = mock_server

        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "test@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        # Should raise exception
        with pytest.raises(smtplib.SMTPAuthenticationError):
            _send_smtp_email(message, recipients)


def test_send_smtp_email_connection_error():
    """Test SMTP email with connection error."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "invalid.smtp.server"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("smtplib.SMTP", side_effect=smtplib.SMTPConnectError(421, "Cannot connect")),
    ):
        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "test@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        # Should raise exception
        with pytest.raises(smtplib.SMTPConnectError):
            _send_smtp_email(message, recipients)


@pytest.mark.asyncio
async def test_html_email_content_validation(sample_recipients):
    """Test HTML email content validation."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                .alert { color: red; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="alert">
                <h1>Critical Alert</h1>
                <p>Node has gone offline!</p>
                <ul>
                    <li>Timestamp: 2025-01-15 10:30:00</li>
                    <li>Node: test-node</li>
                    <li>Severity: CRITICAL</li>
                </ul>
            </div>
        </body>
        </html>
        """

        await send_email_notification(
            recipients=sample_recipients, subject="Critical Alert", html_content=html_content
        )

        message = mock_smtp.call_args[0][0]
        html_part = message.get_payload()[0]

        # Verify HTML content is preserved
        payload = html_part.get_payload(decode=True).decode("utf-8")
        assert "Critical Alert" in payload
        assert "Node has gone offline!" in payload


@pytest.mark.asyncio
async def test_multiple_recipients(sample_html_content):
    """Test sending to multiple recipients."""
    recipients = ["admin1@example.com", "admin2@example.com", "admin3@example.com"]

    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=recipients, subject="Test", html_content=sample_html_content
        )

        message = mock_smtp.call_args[0][0]
        sent_recipients = mock_smtp.call_args[0][1]

        # Verify all recipients in To header
        assert message["To"] == ", ".join(recipients)

        # Verify all recipients passed to sendmail
        assert sent_recipients == recipients


@pytest.mark.asyncio
async def test_special_characters_in_subject(sample_recipients, sample_html_content):
    """Test email with special characters in subject."""
    special_subject = 'Alert: Node "test-node" has 100% disk usage! 🚨'

    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=sample_recipients, subject=special_subject, html_content=sample_html_content
        )

        message = mock_smtp.call_args[0][0]
        assert message["Subject"] == special_subject


@pytest.mark.asyncio
async def test_email_with_metadata_formatting():
    """Test email formatting with metadata details."""
    recipients = ["admin@example.com"]

    details = {
        "node_name": "test-node",
        "alert_type": "disk_full",
        "severity": "CRITICAL",
        "disk_usage": "95%",
        "timestamp": "2025-01-15 10:30:00",
    }

    html_content = f"""
    <html>
        <body>
            <h2>Alert Details</h2>
            {"".join(f"<p><strong>{k}:</strong> {v}</p>" for k, v in details.items())}
        </body>
    </html>
    """

    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=recipients, subject="Alert", html_content=html_content
        )

        message = mock_smtp.call_args[0][0]
        html_part = message.get_payload()[0]
        payload = html_part.get_payload(decode=True).decode("utf-8")

        # Verify all metadata is in email
        for _key, value in details.items():
            assert str(value) in payload


def test_smtp_email_with_server_timeout():
    """Test SMTP email with server timeout."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("smtplib.SMTP") as mock_smtp_class,
    ):
        mock_server = MagicMock()
        mock_server.sendmail.side_effect = smtplib.SMTPServerDisconnected("Connection lost")
        mock_smtp_class.return_value = mock_server

        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "test@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        with pytest.raises(smtplib.SMTPServerDisconnected):
            _send_smtp_email(message, recipients)


@pytest.mark.asyncio
async def test_concurrent_email_sending(sample_html_content):
    """Test sending multiple emails concurrently."""
    recipients_list = [["admin1@example.com"], ["admin2@example.com"], ["admin3@example.com"]]

    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        # Send emails concurrently
        tasks = [
            send_email_notification(
                recipients=recipients, subject=f"Test {i}", html_content=sample_html_content
            )
            for i, recipients in enumerate(recipients_list)
        ]

        await asyncio.gather(*tasks)

        # Verify all emails were sent
        assert mock_smtp.call_count == len(recipients_list)


@pytest.mark.asyncio
async def test_email_with_empty_subject(sample_recipients, sample_html_content):
    """Test email with empty subject."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=sample_recipients, subject="", html_content=sample_html_content
        )

        message = mock_smtp.call_args[0][0]
        assert message["Subject"] == ""


@pytest.mark.asyncio
async def test_email_with_long_content(sample_recipients):
    """Test email with very long HTML content."""
    # Generate long HTML content
    long_content = (
        """
    <html>
        <body>
            <h1>Detailed Report</h1>
    """
        + "\n".join([f"<p>Line {i}: This is test content</p>" for i in range(1000)])
        + """
        </body>
    </html>
    """
    )

    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "test@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender._send_smtp_email") as mock_smtp,
    ):
        await send_email_notification(
            recipients=sample_recipients, subject="Long Report", html_content=long_content
        )

        # Should handle long content without issues
        assert mock_smtp.called


def test_smtp_sendmail_parameters():
    """Test that sendmail is called with correct parameters."""
    with (
        patch("storj_monitor.email_sender.EMAIL_USERNAME", "sender@example.com"),
        patch("storj_monitor.email_sender.EMAIL_PASSWORD", "test_password"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_SERVER", "smtp.example.com"),
        patch("storj_monitor.email_sender.EMAIL_SMTP_PORT", 587),
        patch("storj_monitor.email_sender.EMAIL_USE_TLS", True),
        patch("smtplib.SMTP") as mock_smtp_class,
    ):
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        message = MIMEMultipart()
        message["Subject"] = "Test"
        message["From"] = "sender@example.com"
        message["To"] = "recipient@example.com"

        recipients = ["recipient@example.com"]

        _send_smtp_email(message, recipients)

        # Verify sendmail parameters
        call_args = mock_server.sendmail.call_args
        assert call_args[0][0] == "sender@example.com"  # from_addr
        assert call_args[0][1] == recipients  # to_addrs
        assert isinstance(call_args[0][2], str)  # message string
