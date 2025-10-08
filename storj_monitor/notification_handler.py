import asyncio
import logging
from typing import List, Dict, Any

from storj_monitor.config import (
    ENABLE_EMAIL_NOTIFICATIONS, EMAIL_TO_ADDRESSES,
    ENABLE_WEBHOOK_NOTIFICATIONS, WEBHOOK_DISCORD_URL, WEBHOOK_SLACK_URL, WEBHOOK_CUSTOM_URLS
)
from storj_monitor.email_sender import send_email_notification
from storj_monitor.webhook_sender import send_webhook_notification

logger = logging.getLogger(__name__)

class NotificationHandler:
    def __init__(self):
        self.email_enabled = ENABLE_EMAIL_NOTIFICATIONS
        self.webhook_enabled = ENABLE_WEBHOOK_NOTIFICATIONS
        self.email_recipients = EMAIL_TO_ADDRESSES
        self.discord_webhook_url = WEBHOOK_DISCORD_URL
        self.slack_webhook_url = WEBHOOK_SLACK_URL
        self.custom_webhook_urls = WEBHOOK_CUSTOM_URLS

    async def send_notification(self, alert_type: str, severity: str, message: str, details: Dict[str, Any]):
        logger.info(f"Dispatching notification: {alert_type} - {severity} - {message}")

        tasks = []
        if self.email_enabled and self.email_recipients:
            tasks.append(self._send_email(alert_type, severity, message, details))
        
        if self.webhook_enabled:
            tasks.append(self._send_webhooks(alert_type, severity, message, details))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning("No notification channels enabled or configured.")

    async def _send_email(self, alert_type: str, severity: str, message: str, details: Dict[str, Any]):
        try:
            await send_email_notification(
                recipients=self.email_recipients,
                subject=f"Storj Node Alert: {alert_type} - {severity}",
                html_content=self._format_email_content(alert_type, severity, message, details)
            )
            logger.info(f"Email notification sent for {alert_type} ({severity})")
        except Exception as e:
            logger.error(f"Failed to send email notification for {alert_type}: {e}")

    async def _send_webhooks(self, alert_type: str, severity: str, message: str, details: Dict[str, Any]):
        webhook_tasks = []
        if self.discord_webhook_url:
            webhook_tasks.append(send_webhook_notification(
                url=self.discord_webhook_url,
                platform="discord",
                alert_type=alert_type,
                severity=severity,
                message=message,
                details=details
            ))
        if self.slack_webhook_url:
            webhook_tasks.append(send_webhook_notification(
                url=self.slack_webhook_url,
                platform="slack",
                alert_type=alert_type,
                severity=severity,
                message=message,
                details=details
            ))
        for url in self.custom_webhook_urls:
            webhook_tasks.append(send_webhook_notification(
                url=url,
                platform="custom",
                alert_type=alert_type,
                severity=severity,
                message=message,
                details=details
            ))
        
        if webhook_tasks:
            await asyncio.gather(*webhook_tasks, return_exceptions=True)
            logger.info(f"Webhook notifications dispatched for {alert_type} ({severity})")
        else:
            logger.warning("No webhook URLs configured.")

    def _format_email_content(self, alert_type: str, severity: str, message: str, details: Dict[str, Any]) -> str:
        # Basic HTML formatting for email
        color_map = {
            "CRITICAL": "#FF0000",
            "WARNING": "#FFA500",
            "INFO": "#0000FF"
        }
        severity_color = color_map.get(severity.upper(), "#000000")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
                .container {{ background-color: #f9f9f9; border: 1px solid #ddd; padding: 20px; border-radius: 8px; }}
                .header {{ background-color: {severity_color}; color: white; padding: 10px 20px; border-radius: 5px 5px 0 0; margin: -20px -20px 20px -20px; }}
                h2 {{ margin: 0; }}
                p {{ line-height: 1.6; }}
                .details {{ background-color: #eee; padding: 15px; border-radius: 5px; margin-top: 20px; }}
                .details strong {{ display: block; margin-bottom: 5px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Storj Node Alert: {severity.upper()}</h2>
                </div>
                <p><strong>Alert Type:</strong> {alert_type}</p>
                <p><strong>Message:</strong> {message}</p>
                <div class="details">
                    <strong>Details:</strong>
                    {"".join(f"<p><strong>{k}:</strong> {v}</p>" for k, v in details.items())}
                </div>
                <p>This notification was sent by your Storj Node Monitor.</p>
            </div>
        </body>
        </html>
        """
        return html

notification_handler = NotificationHandler()