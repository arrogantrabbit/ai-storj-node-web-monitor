import asyncio
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from storj_monitor.config import (
    EMAIL_PASSWORD,
    EMAIL_SMTP_PORT,
    EMAIL_SMTP_SERVER,
    EMAIL_USE_TLS,
    EMAIL_USERNAME,
)

logger = logging.getLogger(__name__)


async def send_email_notification(recipients: list[str], subject: str, html_content: str):
    if not recipients:
        logger.warning("No email recipients specified. Skipping email notification.")
        return

    if not EMAIL_USERNAME or not EMAIL_PASSWORD:
        logger.error(
            "Email sender credentials (username/password) are not configured. Cannot send email."
        )
        return

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_USERNAME
    message["To"] = ", ".join(recipients)

    part = MIMEText(html_content, "html")
    message.attach(part)

    try:
        await asyncio.to_thread(_send_smtp_email, message, recipients)
        logger.info(f"Successfully sent email to {', '.join(recipients)}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}", exc_info=True)


def _send_smtp_email(message: MIMEMultipart, recipients: list[str]):
    """Synchronous function to send email via SMTP."""
    try:
        context = ssl.create_default_context()
        if EMAIL_USE_TLS:
            server = smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT)
            server.starttls(context=context)
        else:
            server = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, context=context)

        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USERNAME, recipients, message.as_string())
        server.quit()
    except Exception as e:
        logger.error(f"SMTP error: {e}")
        raise
