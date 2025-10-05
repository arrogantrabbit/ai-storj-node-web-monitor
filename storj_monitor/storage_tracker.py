"""
Storage Tracker

Monitors disk capacity and forecasts storage exhaustion to prevent
downtime from full disks.
"""

import asyncio
import logging
import datetime
from typing import Dict, Any, Optional

from .config import (
    NODE_API_POLL_INTERVAL,
    STORAGE_WARNING_PERCENT,
    STORAGE_CRITICAL_PERCENT,
    STORAGE_FORECAST_WARNING_DAYS,
    STORAGE_FORECAST_CRITICAL_DAYS
)

log = logging.getLogger("StorjMonitor.StorageTracker")


async def track_storage(
    app: Dict[str, Any],
    node_name: str,
    api_client
) -> Optional[Dict[str, Any]]:
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
        disk_space = dashboard.get('diskSpace', {})
        if isinstance(disk_space, dict):
            used_space = disk_space.get('used', 0)
            available_space = disk_space.get('available', 0)
            trash_space = disk_space.get('trash', 0)
            total_space = used_space + available_space
        else:
            log.error(f"[{node_name}] Unexpected diskSpace format: {type(disk_space)}")
            return None
        
        # Calculate percentages
        if total_space > 0:
            used_percent = (used_space / total_space) * 100
            trash_percent = (trash_space / total_space) * 100
            available_percent = (available_space / total_space) * 100
        else:
            used_percent = trash_percent = available_percent = 0
        
        # Create storage snapshot
        snapshot = {
            'timestamp': timestamp,
            'node_name': node_name,
            'total_bytes': total_space,
            'used_bytes': used_space,
            'available_bytes': available_space,
            'trash_bytes': trash_space,
            'used_percent': round(used_percent, 2),
            'trash_percent': round(trash_percent, 2),
            'available_percent': round(available_percent, 2)
        }
        
        # Store in database
        from .database import blocking_write_storage_snapshot
        from .config import DATABASE_FILE
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            app['db_executor'],
            blocking_write_storage_snapshot,
            DATABASE_FILE,
            snapshot
        )
        
        # Calculate growth rate and forecast
        forecast_data = await calculate_storage_forecast(
            app,
            node_name,
            available_space
        )
        
        # Check for alerts
        alerts = []
        
        # Check capacity thresholds
        if used_percent >= STORAGE_CRITICAL_PERCENT:
            alerts.append({
                'severity': 'critical',
                'node_name': node_name,
                'title': f'Critical Disk Usage on {node_name}',
                'message': f'Disk is {used_percent:.1f}% full (threshold: {STORAGE_CRITICAL_PERCENT}%). '
                           f'Free space: {_format_bytes(available_space)}. '
                           f'Immediate action required to prevent downtime.'
            })
        elif used_percent >= STORAGE_WARNING_PERCENT:
            alerts.append({
                'severity': 'warning',
                'node_name': node_name,
                'title': f'High Disk Usage on {node_name}',
                'message': f'Disk is {used_percent:.1f}% full (threshold: {STORAGE_WARNING_PERCENT}%). '
                           f'Free space: {_format_bytes(available_space)}. '
                           f'Consider adding capacity soon.'
            })
        
        # Check forecast
        if forecast_data and forecast_data.get('days_until_full'):
            days = forecast_data['days_until_full']
            if days <= STORAGE_FORECAST_CRITICAL_DAYS:
                alerts.append({
                    'severity': 'critical',
                    'node_name': node_name,
                    'title': f'Disk Will Be Full Soon on {node_name}',
                    'message': f'At current growth rate, disk will be full in {days:.1f} days. '
                               f'Add capacity immediately to prevent downtime.'
                })
            elif days <= STORAGE_FORECAST_WARNING_DAYS:
                alerts.append({
                    'severity': 'warning',
                    'node_name': node_name,
                    'title': f'Disk Capacity Warning on {node_name}',
                    'message': f'At current growth rate, disk will be full in {days:.1f} days. '
                               f'Plan capacity expansion.'
                })
        
        # Log alerts
        if alerts:
            log.warning(f"[{node_name}] Generated {len(alerts)} storage alerts")
            for alert in alerts:
                log.warning(
                    f"[{node_name}] {alert['severity'].upper()}: {alert['title']} - {alert['message']}"
                )
        
        return {
            'node_name': node_name,
            'timestamp': timestamp,
            'snapshot': snapshot,
            'forecast': forecast_data,
            'alerts': alerts
        }
        
    except Exception as e:
        log.error(f"[{node_name}] Error tracking storage: {e}", exc_info=True)
        return None


async def calculate_storage_forecast(
    app: Dict[str, Any],
    node_name: str,
    current_available: int
) -> Optional[Dict[str, Any]]:
    """
    Calculate storage growth rate and forecast when disk will be full.
    
    Uses linear regression on recent snapshots.
    
    Args:
        app: Application context
        node_name: Name of the node
        current_available: Current available space in bytes
    
    Returns:
        Dict with growth rate and days until full
    """
    try:
        from .database import blocking_get_storage_history
        from .config import DATABASE_FILE
        
        loop = asyncio.get_running_loop()
        
        # Get last 7 days of history for growth rate calculation
        history = await loop.run_in_executor(
            app['db_executor'],
            blocking_get_storage_history,
            DATABASE_FILE,
            node_name,
            7  # days parameter (positional, not keyword)
        )
        
        if len(history) < 2:
            # Not enough data for forecast
            return None
        
        # Calculate growth rate using linear regression
        # Filter out records with None used_bytes (from log-based snapshots)
        valid_history = [h for h in history if h.get('used_bytes') is not None]
        
        if len(valid_history) < 2:
            # Not enough valid data for forecast (log-based data doesn't have used_bytes)
            return None
        
        timestamps = [(datetime.datetime.fromisoformat(h['timestamp']).timestamp() / 86400)
                     for h in valid_history]  # Convert to days
        used_bytes = [h['used_bytes'] for h in valid_history]
        
        # Simple linear regression
        n = len(timestamps)
        sum_x = sum(timestamps)
        sum_y = sum(used_bytes)
        sum_xy = sum(x * y for x, y in zip(timestamps, used_bytes))
        sum_x2 = sum(x * x for x in timestamps)
        
        # Calculate slope (bytes per day)
        denominator = (n * sum_x2) - (sum_x * sum_x)
        if denominator == 0:
            return None
        
        slope = ((n * sum_xy) - (sum_x * sum_y)) / denominator
        
        # Calculate days until full
        if slope <= 0:
            # Disk usage is stable or decreasing
            days_until_full = None
        else:
            days_until_full = current_available / slope
        
        return {
            'growth_rate_bytes_per_day': round(slope),
            'growth_rate_gb_per_day': round(slope / (1024**3), 2),
            'days_until_full': round(days_until_full, 1) if days_until_full else None,
            'data_points': n
        }
        
    except Exception as e:
        log.error(f"[{node_name}] Error calculating storage forecast: {e}", exc_info=True)
        return None


def _format_bytes(bytes_value: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


async def storage_polling_task(app: Dict[str, Any]):
    """
    Background task that periodically polls storage data from all nodes with API access.
    """
    log.info("Storage polling task started")
    
    # Wait a bit for initial setup
    await asyncio.sleep(15)
    
    while True:
        try:
            api_clients = app.get('api_clients', {})
            
            if not api_clients:
                # No API clients available, wait and retry
                await asyncio.sleep(NODE_API_POLL_INTERVAL)
                continue
            
            for node_name, api_client in api_clients.items():
                if not api_client.is_available:
                    continue
                
                result = await track_storage(app, node_name, api_client)
                if result and result.get('alerts'):
                    # Broadcast alerts to websocket clients
                    from .websocket_utils import robust_broadcast
                    from .state import app_state
                    payload = {
                        'type': 'storage_alerts',
                        'node_name': node_name,
                        'alerts': result['alerts']
                    }
                    await robust_broadcast(app_state['websockets'], payload)
            
            # Wait before next poll
            await asyncio.sleep(NODE_API_POLL_INTERVAL)
            
        except asyncio.CancelledError:
            log.info("Storage polling task cancelled")
            break
        except Exception:
            log.error("Error in storage polling task", exc_info=True)
            await asyncio.sleep(60)  # Back off on error