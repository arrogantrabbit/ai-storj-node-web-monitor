import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


async def send_webhook_notification(
    url: str, platform: str, alert_type: str, severity: str, message: str, details: dict[str, Any]
):
    if not url:
        logger.warning(
            f"No webhook URL provided for platform {platform}. Skipping webhook notification."
        )
        return

    payload = {}
    if platform == "discord":
        payload = _format_discord_webhook(alert_type, severity, message, details)
    elif platform == "slack":
        payload = _format_slack_webhook(alert_type, severity, message, details)
    else:  # custom webhook
        payload = _format_custom_webhook(alert_type, severity, message, details)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                response.raise_for_status()
                logger.info(
                    f"Successfully sent {platform} webhook notification for {alert_type} ({severity})"
                )
    except aiohttp.ClientError as e:
        logger.error(f"Failed to send {platform} webhook notification to {url}: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending {platform} webhook: {e}")


def _format_discord_webhook(
    alert_type: str, severity: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    color_map = {
        "CRITICAL": 16711680,  # Red
        "WARNING": 16776960,  # Yellow
        "INFO": 255,  # Blue
    }
    severity_color = color_map.get(severity.upper(), 0)  # Default to black

    fields = []
    for k, v in details.items():
        fields.append({"name": k, "value": str(v), "inline": True})

    embed = {
        "title": f"Storj Node Alert: {alert_type}",
        "description": message,
        "color": severity_color,
        "fields": fields,
        "timestamp": time.time(),  # Unix timestamp
    }

    return {
        "username": "Storj Node Monitor",
        "avatar_url": "https://storj.io/images/logo.png",  # Placeholder
        "embeds": [embed],
    }


def _format_slack_webhook(
    alert_type: str, severity: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    color_map = {"CRITICAL": "#FF0000", "WARNING": "#FFA500", "INFO": "#0000FF"}
    severity_color = color_map.get(severity.upper(), "#000000")

    fields = []
    for k, v in details.items():
        fields.append({"title": k, "value": str(v), "short": True})

    attachment = {
        "fallback": f"Storj Node Alert: {alert_type} - {severity} - {message}",
        "color": severity_color,
        "pretext": f"Storj Node Alert: *{severity.upper()}*",
        "title": alert_type,
        "text": message,
        "fields": fields,
        "ts": time.time(),  # Unix timestamp
    }

    return {"attachments": [attachment]}


def _format_custom_webhook(
    alert_type: str, severity: str, message: str, details: dict[str, Any]
) -> dict[str, Any]:
    return {
        "alert_type": alert_type,
        "severity": severity,
        "message": message,
        "details": details,
        "timestamp": time.time(),  # Unix timestamp
    }
