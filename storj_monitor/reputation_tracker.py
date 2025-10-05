"""
Reputation Tracker

Monitors node reputation scores from the Storj node API and stores historical data.
Critical for preventing node suspension and disqualification.
"""

import asyncio
import logging
import datetime
from typing import Dict, Any, List, Optional

from .config import (
    AUDIT_SCORE_WARNING,
    AUDIT_SCORE_CRITICAL,
    SUSPENSION_SCORE_CRITICAL,
    ONLINE_SCORE_WARNING
)

log = logging.getLogger("StorjMonitor.ReputationTracker")


async def track_reputation(
    app: Dict[str, Any],
    node_name: str,
    api_client
) -> Optional[Dict[str, Any]]:
    """
    Poll reputation scores from node API and process them.
    
    Args:
        app: Application context
        node_name: Name of the node
        api_client: StorjNodeAPIClient instance
    
    Returns:
        Dict with reputation data if successful, None otherwise
    """
    try:
        satellites_data = await api_client.get_satellites()
        if not satellites_data:
            return None
        
        reputation_records = []
        alerts = []
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        
        # The API returns a dict with satellite IDs as keys, where each value is a list of satellite info
        # Or it could return a list directly. Handle both cases.
        satellites_list = []
        
        if isinstance(satellites_data, dict):
            # Dict format: flatten all values into a single list
            for value in satellites_data.values():
                if isinstance(value, list):
                    satellites_list.extend(value)
                elif isinstance(value, dict):
                    satellites_list.append(value)
        elif isinstance(satellites_data, list):
            satellites_list = satellites_data
        else:
            log.error(f"[{node_name}] Unexpected satellites data format: {type(satellites_data)}")
            return None
        
        if not satellites_list:
            log.warning(f"[{node_name}] No satellite data found")
            return None
        
        for sat_data in satellites_list:
            if not isinstance(sat_data, dict):
                log.warning(f"[{node_name}] Skipping non-dict satellite entry: {type(sat_data)}")
                continue
            # Extract satellite ID
            sat_id = sat_data.get('id')
            if not sat_id:
                continue
            
            # Extract reputation scores
            audit = sat_data.get('audit', {})
            suspension = sat_data.get('suspension', {})
            online = sat_data.get('online', {})
            
            audit_score = audit.get('score', 1.0) * 100  # Convert to percentage
            suspension_score = suspension.get('score', 1.0) * 100
            online_score = online.get('score', 1.0) * 100
            
            # Check if node is disqualified or suspended
            is_disqualified = sat_data.get('disqualified')
            is_suspended = sat_data.get('suspended')
            
            # Create reputation record
            record = {
                'timestamp': timestamp,
                'node_name': node_name,
                'satellite': sat_id,
                'audit_score': audit_score,
                'suspension_score': suspension_score,
                'online_score': online_score,
                'audit_success_count': audit.get('successCount', 0),
                'audit_total_count': audit.get('totalCount', 0),
                'is_disqualified': is_disqualified,
                'is_suspended': is_suspended
            }
            reputation_records.append(record)
            
            # Check for alert conditions
            satellite_name = _get_satellite_name(sat_id)
            
            if is_disqualified:
                alerts.append({
                    'severity': 'critical',
                    'node_name': node_name,
                    'satellite': satellite_name,
                    'title': f'Node Disqualified on {satellite_name}',
                    'message': f'Node {node_name} has been DISQUALIFIED on {satellite_name}. '
                               f'This is permanent and cannot be reversed.'
                })
            elif is_suspended:
                alerts.append({
                    'severity': 'critical',
                    'node_name': node_name,
                    'satellite': satellite_name,
                    'title': f'Node Suspended on {satellite_name}',
                    'message': f'Node {node_name} is currently SUSPENDED on {satellite_name}. '
                               f'Improve performance immediately to avoid disqualification.'
                })
            else:
                # Check score thresholds
                if audit_score < AUDIT_SCORE_CRITICAL:
                    alerts.append({
                        'severity': 'critical',
                        'node_name': node_name,
                        'satellite': satellite_name,
                        'title': f'Critical Audit Score on {satellite_name}',
                        'message': f'Audit score is {audit_score:.2f}% (threshold: {AUDIT_SCORE_CRITICAL}%). '
                                   f'Risk of disqualification. Check disk health immediately.'
                    })
                elif audit_score < AUDIT_SCORE_WARNING:
                    alerts.append({
                        'severity': 'warning',
                        'node_name': node_name,
                        'satellite': satellite_name,
                        'title': f'Low Audit Score on {satellite_name}',
                        'message': f'Audit score is {audit_score:.2f}% (threshold: {AUDIT_SCORE_WARNING}%). '
                                   f'Monitor closely and check for issues.'
                    })
                
                if suspension_score < SUSPENSION_SCORE_CRITICAL:
                    alerts.append({
                        'severity': 'critical',
                        'node_name': node_name,
                        'satellite': satellite_name,
                        'title': f'Critical Suspension Score on {satellite_name}',
                        'message': f'Suspension score is {suspension_score:.2f}% (threshold: {SUSPENSION_SCORE_CRITICAL}%). '
                                   f'Node may be suspended soon. Improve upload performance.'
                    })
                
                if online_score < ONLINE_SCORE_WARNING:
                    alerts.append({
                        'severity': 'warning',
                        'node_name': node_name,
                        'satellite': satellite_name,
                        'title': f'Low Online Score on {satellite_name}',
                        'message': f'Online score is {online_score:.2f}% (threshold: {ONLINE_SCORE_WARNING}%). '
                                   f'Check network connectivity and uptime.'
                    })
        
        # Store reputation records in database
        if reputation_records:
            from .database import blocking_write_reputation_history
            from .config import DATABASE_FILE
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                app['db_executor'],
                blocking_write_reputation_history,
                DATABASE_FILE,
                reputation_records
            )
        
        # Process alerts
        if alerts:
            log.warning(f"[{node_name}] Generated {len(alerts)} reputation alerts")
            for alert in alerts:
                log.warning(
                    f"[{node_name}] {alert['severity'].upper()}: {alert['title']} - {alert['message']}"
                )
            # Store alerts (will be implemented in Phase 1.4)
            # For now, just return them
        
        return {
            'node_name': node_name,
            'timestamp': timestamp,
            'reputation_records': reputation_records,
            'alerts': alerts
        }
        
    except Exception as e:
        log.error(f"[{node_name}] Error tracking reputation: {e}", exc_info=True)
        return None


def _get_satellite_name(sat_id: str) -> str:
    """Get friendly satellite name from ID."""
    from .config import SATELLITE_NAMES
    return SATELLITE_NAMES.get(sat_id, sat_id[:12] + '...')


async def reputation_polling_task(app: Dict[str, Any]):
    """
    Background task that periodically polls reputation data from all nodes with API access.
    
    This is a critical monitoring task that helps prevent node suspension/disqualification.
    """
    from .config import NODE_API_POLL_INTERVAL
    
    log.info("Reputation polling task started")
    
    # Wait a bit for initial setup
    await asyncio.sleep(10)
    
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
                
                result = await track_reputation(app, node_name, api_client)
                if result and result.get('alerts'):
                    # Broadcast alerts to websocket clients
                    from .websocket_utils import robust_broadcast
                    from .state import app_state
                    payload = {
                        'type': 'reputation_alerts',
                        'node_name': node_name,
                        'alerts': result['alerts']
                    }
                    await robust_broadcast(app_state['websockets'], payload)
            
            # Wait before next poll
            await asyncio.sleep(NODE_API_POLL_INTERVAL)
            
        except asyncio.CancelledError:
            log.info("Reputation polling task cancelled")
            break
        except Exception:
            log.error("Error in reputation polling task", exc_info=True)
            await asyncio.sleep(60)  # Back off on error


def calculate_reputation_health_score(reputation_data: Dict[str, Any]) -> float:
    """
    Calculate a composite health score (0-100) based on reputation metrics.
    
    Weighting:
    - Audit Score: 40%
    - Suspension Score: 30%
    - Online Score: 30%
    
    Args:
        reputation_data: Dict with audit_score, suspension_score, online_score
    
    Returns:
        Health score between 0 and 100
    """
    audit = reputation_data.get('audit_score', 100)
    suspension = reputation_data.get('suspension_score', 100)
    online = reputation_data.get('online_score', 100)
    
    # Weighted average
    health_score = (audit * 0.4) + (suspension * 0.3) + (online * 0.3)
    
    return round(health_score, 2)


async def get_reputation_summary(
    app: Dict[str, Any],
    node_names: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Get current reputation summary for specified nodes.
    
    Args:
        app: Application context
        node_names: List of node names, or None for all nodes
    
    Returns:
        List of reputation summaries
    """
    from .database import blocking_get_latest_reputation
    from .config import DATABASE_FILE
    
    if node_names is None:
        node_names = list(app.get('api_clients', {}).keys())
    
    loop = asyncio.get_running_loop()
    summaries = await loop.run_in_executor(
        app['db_executor'],
        blocking_get_latest_reputation,
        DATABASE_FILE,
        node_names
    )
    
    # Add health scores
    for summary in summaries:
        summary['health_score'] = calculate_reputation_health_score(summary)
    
    return summaries