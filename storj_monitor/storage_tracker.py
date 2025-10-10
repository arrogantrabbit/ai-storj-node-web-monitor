"""
Storage Tracker

Monitors disk capacity and forecasts storage exhaustion to prevent
downtime from full disks.
"""

import asyncio
import datetime
import logging
from typing import Any, Optional

from .config import (
    NODE_API_POLL_INTERVAL,
    STORAGE_CRITICAL_PERCENT,
    STORAGE_FORECAST_CRITICAL_DAYS,
    STORAGE_FORECAST_WARNING_DAYS,
    STORAGE_WARNING_PERCENT,
)

log = logging.getLogger("StorjMonitor.StorageTracker")


async def track_storage(
    app: dict[str, Any], node_name: str, api_client
) -> Optional[dict[str, Any]]:
    """
    Poll storage capacity from node API and process it.

    Args:
        app: Application context
        node_name: Name of the node
        api_client: StorjNodeAPIClient instance

    Returns:
        Dict with storage data if successful, None otherwise
    """
    try:
        # Get dashboard data for overall storage
        dashboard = await api_client.get_dashboard()
        if not dashboard:
            return None

        # Get per-satellite data
        satellites_data = await api_client.get_satellites()
        if not satellites_data:
            satellites_data = {}

        timestamp = datetime.datetime.now(datetime.timezone.utc)

        # Extract capacity data - API returns nested structure
        disk_space = dashboard.get("diskSpace", {})
        if isinstance(disk_space, dict):
            # IMPORTANT: Storj API field naming is confusing!
            # - 'available': Total allocated storage capacity (e.g., 25TB)
            # - 'used': Space used by pieces including trash (e.g., 11.17TB)
            # - 'trash': Space used by trash, subset of 'used' (e.g., 0.26TB)
            # - Free space = available - used (e.g., 25TB - 11.17TB = 13.83TB)

            used_space = disk_space.get("used", 0)
            allocated_capacity = disk_space.get(
                "available", 0
            )  # This is TOTAL allocated, not free!
            trash_space = disk_space.get("trash", 0)

            # Calculate actual free space
            free_space = allocated_capacity - used_space
            total_space = allocated_capacity  # The allocated capacity IS the total

            log.debug(
                f"[{node_name}] API storage: allocated={allocated_capacity / (1024**4):.2f}TB, "
                f"used={used_space / (1024**4):.2f}TB, "
                f"free={free_space / (1024**4):.2f}TB, "
                f"trash={trash_space / (1024**4):.2f}TB"
            )
        else:
            log.error(f"[{node_name}] Unexpected diskSpace format: {type(disk_space)}")
            return None

        # Calculate percentages
        if total_space > 0:
            used_percent = (used_space / total_space) * 100
            trash_percent = (trash_space / total_space) * 100
            free_percent = (free_space / total_space) * 100
        else:
            used_percent = trash_percent = free_percent = 0

        # Create storage snapshot
        snapshot = {
            "timestamp": timestamp,
            "node_name": node_name,
            "total_bytes": total_space,
            "used_bytes": used_space,
            "available_bytes": free_space,  # This is the actual FREE space
            "trash_bytes": trash_space,
            "used_percent": round(used_percent, 2),
            "trash_percent": round(trash_percent, 2),
            "available_percent": round(free_percent, 2),
        }

        # Store in database
        from .config import DATABASE_FILE
        from .database import blocking_write_storage_snapshot

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            app["db_executor"], blocking_write_storage_snapshot, DATABASE_FILE, snapshot
        )

        # Calculate growth rate and forecast
        forecast_data = await calculate_storage_forecast(
            app,
            node_name,
            free_space,  # Pass the actual free space
        )

        # Check for alerts
        alerts = []

        # Check capacity thresholds
        if used_percent >= STORAGE_CRITICAL_PERCENT:
            alerts.append(
                {
                    "severity": "critical",
                    "node_name": node_name,
                    "title": f"Critical Disk Usage on {node_name}",
                    "message": f"Disk is {used_percent:.1f}% full (threshold: {STORAGE_CRITICAL_PERCENT}%). "
                    f"Free space: {_format_bytes(free_space)}. "
                    f"Immediate action required to prevent downtime.",
                }
            )
        elif used_percent >= STORAGE_WARNING_PERCENT:
            alerts.append(
                {
                    "severity": "warning",
                    "node_name": node_name,
                    "title": f"High Disk Usage on {node_name}",
                    "message": f"Disk is {used_percent:.1f}% full (threshold: {STORAGE_WARNING_PERCENT}%). "
                    f"Free space: {_format_bytes(free_space)}. "
                    f"Consider adding capacity soon.",
                }
            )

        # Check forecast
        if forecast_data and forecast_data.get("days_until_full"):
            days = forecast_data["days_until_full"]
            if days <= STORAGE_FORECAST_CRITICAL_DAYS:
                alerts.append(
                    {
                        "severity": "critical",
                        "node_name": node_name,
                        "title": f"Disk Will Be Full Soon on {node_name}",
                        "message": f"At current growth rate, disk will be full in {days:.1f} days. "
                        f"Add capacity immediately to prevent downtime.",
                    }
                )
            elif days <= STORAGE_FORECAST_WARNING_DAYS:
                alerts.append(
                    {
                        "severity": "warning",
                        "node_name": node_name,
                        "title": f"Disk Capacity Warning on {node_name}",
                        "message": f"At current growth rate, disk will be full in {days:.1f} days. "
                        f"Plan capacity expansion.",
                    }
                )

        # Log alerts
        if alerts:
            log.warning(f"[{node_name}] Generated {len(alerts)} storage alerts")
            for alert in alerts:
                log.warning(
                    f"[{node_name}] {alert['severity'].upper()}: {alert['title']} - {alert['message']}"
                )

        return {
            "node_name": node_name,
            "timestamp": timestamp,
            "snapshot": snapshot,
            "forecast": forecast_data,
            "alerts": alerts,
        }

    except Exception as e:
        log.error(f"[{node_name}] Error tracking storage: {e}", exc_info=True)
        return None


async def calculate_storage_forecast(
    app: dict[str, Any], node_name: str, current_available: int
) -> Optional[dict[str, Any]]:
    """
    Calculate storage growth rates and forecast when disk will be full.

    Uses linear regression on recent snapshots with multiple time windows
    (1 day, 7 days, 30 days) for better insight into recent vs long-term trends.

    Args:
        app: Application context
        node_name: Name of the node
        current_available: Current available space in bytes

    Returns:
        Dict with growth rates for multiple time windows and days until full
    """
    try:
        from .config import DATABASE_FILE
        from .database import blocking_get_storage_history

        loop = asyncio.get_running_loop()

        # Get 30 days of history (maximum window we need)
        history_30d = await loop.run_in_executor(
            app["db_executor"], blocking_get_storage_history, DATABASE_FILE, node_name, 30
        )

        if len(history_30d) < 2:
            # Not enough data for forecast
            log.info(
                f"[{node_name}] Insufficient storage history for growth rate calculation: {len(history_30d)} records (need 2+)"
            )
            return None

        # Filter out records with None used_bytes (from log-based snapshots)
        valid_history_30d = [h for h in history_30d if h.get("used_bytes") is not None]

        if len(valid_history_30d) < 2:
            # Not enough valid data for forecast
            log.warning(
                f"[{node_name}] Cannot calculate growth rate: {len(valid_history_30d)} records with used_bytes out of {len(history_30d)} total. "
                f"Growth rate requires API-based storage data (not log-based). Enable storage polling via NODE_API_URL config."
            )
            return None

        # Calculate growth rates for multiple time windows
        time_windows = [1, 7, 30]  # days
        growth_rates = {}

        for days in time_windows:
            # Filter history to the specified window
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            valid_history = [
                h
                for h in valid_history_30d
                if datetime.datetime.fromisoformat(h["timestamp"]) >= cutoff
            ]

            if len(valid_history) < 2:
                # Not enough data for this window
                growth_rates[f"{days}d"] = {
                    "growth_rate_bytes_per_day": None,
                    "growth_rate_gb_per_day": None,
                    "days_until_full": None,
                    "data_points": len(valid_history),
                }
                continue

            # Calculate linear regression for this window
            timestamps = [
                (datetime.datetime.fromisoformat(h["timestamp"]).timestamp() / 86400)
                for h in valid_history
            ]  # Convert to days
            used_bytes = [h["used_bytes"] for h in valid_history]

            n = len(timestamps)
            sum_x = sum(timestamps)
            sum_y = sum(used_bytes)
            sum_xy = sum(x * y for x, y in zip(timestamps, used_bytes))
            sum_x2 = sum(x * x for x in timestamps)

            # Calculate slope (bytes per day)
            denominator = (n * sum_x2) - (sum_x * sum_x)
            if denominator == 0:
                growth_rates[f"{days}d"] = {
                    "growth_rate_bytes_per_day": None,
                    "growth_rate_gb_per_day": None,
                    "days_until_full": None,
                    "data_points": n,
                }
                continue

            slope = ((n * sum_xy) - (sum_x * sum_y)) / denominator

            # Calculate days until full for this growth rate
            days_until_full = None if slope <= 0 else current_available / slope

            growth_rates[f"{days}d"] = {
                "growth_rate_bytes_per_day": round(slope) if slope > 0 else 0,
                "growth_rate_gb_per_day": round(slope / (1024**3), 2) if slope > 0 else 0,
                "days_until_full": round(days_until_full, 1) if days_until_full else None,
                "data_points": n,
            }

        # Use 7-day window as the primary forecast for alerts
        primary_rate = growth_rates.get("7d", {})

        return {
            "growth_rate_bytes_per_day": primary_rate.get("growth_rate_bytes_per_day"),
            "growth_rate_gb_per_day": primary_rate.get("growth_rate_gb_per_day"),
            "days_until_full": primary_rate.get("days_until_full"),
            "data_points": primary_rate.get("data_points", 0),
            "growth_rates": growth_rates,  # Include all time windows
        }

    except Exception as e:
        log.error(f"[{node_name}] Error calculating storage forecast: {e}", exc_info=True)
        return None


def _format_bytes(bytes_value: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


async def storage_polling_task(app: dict[str, Any]):
    """
    Background task that periodically polls storage data from all nodes with API access.
    """
    log.info("Storage polling task started")

    # Wait a bit for initial setup
    await asyncio.sleep(15)

    while True:
        try:
            api_clients = app.get("api_clients", {})

            if not api_clients:
                # No API clients available, wait and retry
                await asyncio.sleep(NODE_API_POLL_INTERVAL)
                continue

            for node_name, api_client in api_clients.items():
                if not api_client.is_available:
                    continue

                result = await track_storage(app, node_name, api_client)
                if result and result.get("alerts"):
                    # Broadcast alerts to websocket clients
                    from .state import app_state
                    from .websocket_utils import robust_broadcast

                    payload = {
                        "type": "storage_alerts",
                        "node_name": node_name,
                        "alerts": result["alerts"],
                    }
                    await robust_broadcast(app_state["websockets"], payload)

            # Wait before next poll
            await asyncio.sleep(NODE_API_POLL_INTERVAL)

        except asyncio.CancelledError:
            log.info("Storage polling task cancelled")
            break
        except Exception:
            log.error("Error in storage polling task", exc_info=True)
            await asyncio.sleep(60)  # Back off on error
