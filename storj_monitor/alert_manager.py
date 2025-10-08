"""
Alert Manager for Storj Node Monitor - Phase 4
Manages alert generation, evaluation, and notification.
"""

import asyncio
import logging
import datetime
from typing import Dict, List, Any, Optional

log = logging.getLogger("StorjMonitor.AlertManager")


class AlertManager:
    """
    Central alert management system that evaluates conditions and generates alerts.
    """
    
    def __init__(self, app, analytics_engine, anomaly_detector):
        self.app = app
        self.analytics = analytics_engine
        self.anomaly_detector = anomaly_detector
        self.active_alerts = {}  # {alert_key: alert_dict}
        self.alert_cooldown = {}  # {alert_key: last_alert_time}
        self.cooldown_minutes = 15  # Minimum time between duplicate alerts
        
    def _generate_alert_key(self, node_name: str, alert_type: str, metadata: Dict) -> str:
        """Generate a unique key for alert deduplication."""
        # Include relevant metadata in key for granular deduplication
        key_parts = [node_name, alert_type]
        
        if 'satellite' in metadata:
            key_parts.append(metadata['satellite'])
        if 'metric_name' in metadata:
            key_parts.append(metadata['metric_name'])
        
        return ':'.join(key_parts)
    
    def _should_generate_alert(self, alert_key: str) -> bool:
        """Check if enough time has passed since last alert of this type."""
        if alert_key not in self.alert_cooldown:
            return True
        
        last_time = self.alert_cooldown[alert_key]
        elapsed = datetime.datetime.now(datetime.timezone.utc) - last_time
        
        return elapsed.total_seconds() >= (self.cooldown_minutes * 60)
    
    async def generate_alert(
        self,
        node_name: str,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        metadata: Dict = None
    ) -> Optional[Dict[str, Any]]:
        """
        Generate and record an alert.
        
        Args:
            node_name: Name of the node
            alert_type: Type of alert
            severity: 'info', 'warning', or 'critical'
            title: Alert title
            message: Alert message
            metadata: Additional metadata
        
        Returns:
            Alert dict if created, None if suppressed
        """
        metadata = metadata or {}
        alert_key = self._generate_alert_key(node_name, alert_type, metadata)
        
        # Check cooldown
        if not self._should_generate_alert(alert_key):
            return None
        
        try:
            alert = {
                'timestamp': datetime.datetime.now(datetime.timezone.utc),
                'node_name': node_name,
                'alert_type': alert_type,
                'severity': severity,
                'title': title,
                'message': message,
                'metadata': metadata
            }
            
            # Write to database
            loop = asyncio.get_running_loop()
            from .config import DATABASE_FILE
            from .database import blocking_write_alert
            
            success = await loop.run_in_executor(
                self.app['db_executor'],
                blocking_write_alert,
                DATABASE_FILE,
                alert
            )
            
            if success:
                # Update cooldown
                self.alert_cooldown[alert_key] = alert['timestamp']
                
                # Add to active alerts
                self.active_alerts[alert_key] = alert
                
                # Broadcast to websockets
                from .websocket_utils import robust_broadcast
                from .state import app_state
                from .notification_handler import notification_handler
                
                await robust_broadcast(
                    app_state['websockets'],
                    {
                        'type': 'new_alert',
                        'alert': {
                            'timestamp': alert['timestamp'].isoformat(),
                            'node_name': alert['node_name'],
                            'alert_type': alert['alert_type'],
                            'severity': alert['severity'],
                            'title': alert['title'],
                            'message': alert['message'],
                            'metadata': alert['metadata']
                        }
                    },
                    node_name=node_name
                )

                # Send notifications via configured channels
                await notification_handler.send_notification(
                    alert_type=alert['alert_type'],
                    severity=alert['severity'],
                    message=alert['message'],
                    details={
                        'node_name': alert['node_name'],
                        'title': alert['title'],
                        **alert['metadata']
                    }
                )
                
                log.info(f"Generated {severity} alert for {node_name}: {title}")
                return alert
            
        except Exception:
            log.error(f"Failed to generate alert for {node_name}:", exc_info=True)
        
        return None
    
    async def evaluate_reputation_alerts(
        self,
        node_name: str,
        reputation_data: List[Dict[str, Any]]
    ):
        """Evaluate reputation data and generate alerts if needed."""
        if not reputation_data:
            return
        
        try:
            from .config import (
                AUDIT_SCORE_WARNING,
                AUDIT_SCORE_CRITICAL,
                SUSPENSION_SCORE_CRITICAL,
                ONLINE_SCORE_WARNING
            )
            
            for sat_data in reputation_data:
                satellite = sat_data['satellite']
                audit_score = sat_data.get('audit_score')
                suspension_score = sat_data.get('suspension_score')
                online_score = sat_data.get('online_score')
                is_disqualified = sat_data.get('is_disqualified', 0)
                is_suspended = sat_data.get('is_suspended', 0)
                
                # Critical: Node disqualified
                if is_disqualified:
                    await self.generate_alert(
                        node_name,
                        'node_disqualified',
                        'critical',
                        f'Node Disqualified on {satellite}',
                        f'Node has been disqualified from {satellite}. This is permanent.',
                        {'satellite': satellite}
                    )
                
                # Critical: Node suspended
                if is_suspended:
                    await self.generate_alert(
                        node_name,
                        'node_suspended',
                        'critical',
                        f'Node Suspended on {satellite}',
                        f'Node has been suspended on {satellite}. Review and fix issues immediately.',
                        {'satellite': satellite}
                    )
                
                # Check audit score
                if audit_score is not None:
                    if audit_score < AUDIT_SCORE_CRITICAL:
                        await self.generate_alert(
                            node_name,
                            'audit_score_critical',
                            'critical',
                            f'Critical Audit Score: {audit_score:.2f}%',
                            f'Audit score on {satellite} is critically low. Risk of disqualification.',
                            {'satellite': satellite, 'score': audit_score}
                        )
                    elif audit_score < AUDIT_SCORE_WARNING:
                        await self.generate_alert(
                            node_name,
                            'audit_score_warning',
                            'warning',
                            f'Low Audit Score: {audit_score:.2f}%',
                            f'Audit score on {satellite} is below threshold. Monitor closely.',
                            {'satellite': satellite, 'score': audit_score}
                        )
                
                # Check suspension score
                if suspension_score is not None and suspension_score < SUSPENSION_SCORE_CRITICAL:
                    await self.generate_alert(
                        node_name,
                        'suspension_risk',
                        'critical',
                        f'Suspension Risk: {suspension_score:.2f}%',
                        f'Suspension score on {satellite} is critically low. Node may be suspended soon.',
                        {'satellite': satellite, 'score': suspension_score}
                    )
                
                # Check online score
                if online_score is not None and online_score < ONLINE_SCORE_WARNING:
                    await self.generate_alert(
                        node_name,
                        'uptime_warning',
                        'warning',
                        f'Low Uptime Score: {online_score:.2f}%',
                        f'Online score on {satellite} indicates connectivity issues.',
                        {'satellite': satellite, 'score': online_score}
                    )
            
        except Exception:
            log.error(f"Failed to evaluate reputation alerts for {node_name}:", exc_info=True)
    
    async def evaluate_storage_alerts(
        self,
        node_name: str,
        storage_data: Dict[str, Any]
    ):
        """Evaluate storage data and generate alerts if needed."""
        if not storage_data:
            return
        
        try:
            from .config import (
                STORAGE_WARNING_PERCENT,
                STORAGE_CRITICAL_PERCENT
            )
            
            used_percent = storage_data.get('used_percent', 0)
            
            if used_percent >= STORAGE_CRITICAL_PERCENT:
                await self.generate_alert(
                    node_name,
                    'storage_critical',
                    'critical',
                    f'Storage Critical: {used_percent:.1f}% Full',
                    'Storage is critically full. Add capacity immediately to avoid service interruption.',
                    {'used_percent': used_percent}
                )
            elif used_percent >= STORAGE_WARNING_PERCENT:
                await self.generate_alert(
                    node_name,
                    'storage_warning',
                    'warning',
                    f'Storage Warning: {used_percent:.1f}% Full',
                    'Storage is approaching capacity. Consider adding more disk space.',
                    {'used_percent': used_percent}
                )
            
        except Exception:
            log.error(f"Failed to evaluate storage alerts for {node_name}:", exc_info=True)
    
    async def evaluate_latency_alerts(
        self,
        node_name: str,
        latency_data: Dict[str, Any]
    ):
        """Evaluate latency data and generate alerts if needed."""
        if not latency_data:
            return
        
        try:
            from .config import LATENCY_WARNING_MS, LATENCY_CRITICAL_MS
            
            p99 = latency_data.get('p99')
            
            if p99:
                if p99 >= LATENCY_CRITICAL_MS:
                    await self.generate_alert(
                        node_name,
                        'latency_critical',
                        'critical',
                        f'Critical Latency: {p99:.0f}ms',
                        'P99 latency is critically high. Check system resources and network.',
                        {'p99_ms': p99}
                    )
                elif p99 >= LATENCY_WARNING_MS:
                    await self.generate_alert(
                        node_name,
                        'latency_warning',
                        'warning',
                        f'High Latency: {p99:.0f}ms',
                        'P99 latency is elevated. Monitor for performance issues.',
                        {'p99_ms': p99}
                    )
            
        except Exception:
            log.error(f"Failed to evaluate latency alerts for {node_name}:", exc_info=True)
    
    async def process_anomalies(
        self,
        anomalies: List[Dict[str, Any]]
    ):
        """
        Process detected anomalies and generate alerts for significant ones.
        
        Args:
            anomalies: List of detected anomalies
        """
        for anomaly in anomalies:
            try:
                # Only generate alerts for warning/critical anomalies
                severity = anomaly.get('severity', 'info')
                
                if severity in ['warning', 'critical']:
                    await self.generate_alert(
                        anomaly['node_name'],
                        anomaly.get('insight_type', 'anomaly'),
                        severity,
                        anomaly.get('title', 'Anomaly Detected'),
                        anomaly.get('description', ''),
                        anomaly.get('metadata', {})
                    )
                
            except Exception:
                log.error("Failed to process anomaly:", exc_info=True)
    
    async def acknowledge_alert(self, alert_id: int) -> bool:
        """
        Acknowledge an alert.
        
        Args:
            alert_id: Database ID of the alert
        
        Returns:
            True if successful
        """
        try:
            loop = asyncio.get_running_loop()
            from .config import DATABASE_FILE
            from .database import blocking_acknowledge_alert
            
            success = await loop.run_in_executor(
                self.app['db_executor'],
                blocking_acknowledge_alert,
                DATABASE_FILE,
                alert_id
            )
            
            if success:
                log.info(f"Alert {alert_id} acknowledged")
                
                # Broadcast acknowledgment
                from .websocket_utils import robust_broadcast
                from .state import app_state
                
                await robust_broadcast(
                    app_state['websockets'],
                    {
                        'type': 'alert_acknowledged',
                        'alert_id': alert_id
                    }
                )
            
            return success
            
        except Exception:
            log.error(f"Failed to acknowledge alert {alert_id}:", exc_info=True)
            return False
    
    async def get_active_alerts(
        self,
        node_names: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get currently active alerts.
        
        Args:
            node_names: Optional list of node names to filter
        
        Returns:
            List of active alerts
        """
        try:
            loop = asyncio.get_running_loop()
            from .config import DATABASE_FILE
            from .database import blocking_get_active_alerts
            
            alerts = await loop.run_in_executor(
                self.app['db_executor'],
                blocking_get_active_alerts,
                DATABASE_FILE,
                node_names
            )
            
            return alerts
            
        except Exception:
            log.error("Failed to get active alerts:", exc_info=True)
            return []
    
    def get_alert_summary(self) -> Dict[str, int]:
        """Get summary of alert counts by severity."""
        summary = {
            'critical': 0,
            'warning': 0,
            'info': 0,
            'total': len(self.active_alerts)
        }
        
        for alert in self.active_alerts.values():
            severity = alert.get('severity', 'info')
            if severity in summary:
                summary[severity] += 1
        
        return summary


# Background task to periodically evaluate alerts
async def alert_evaluation_task(app):
    """
    Periodically evaluate conditions and generate alerts.
    Runs every 5 minutes.
    """
    log.info("Alert evaluation task started")
    
    # Wait for system initialization
    await asyncio.sleep(30)
    
    # Initialize components
    from .analytics_engine import AnalyticsEngine
    from .anomaly_detector import AnomalyDetector
    
    analytics = AnalyticsEngine(app)
    anomaly_detector = AnomalyDetector(app, analytics)
    alert_manager = AlertManager(app, analytics, anomaly_detector)
    
    # Store in app for access by other modules
    app['analytics_engine'] = analytics
    app['anomaly_detector'] = anomaly_detector
    app['alert_manager'] = alert_manager
    
    while True:
        try:
            await asyncio.sleep(300)  # Run every 5 minutes
            
            log.info("[ALERT_EVAL] Starting alert evaluation cycle")
            
            from .state import app_state
            from .config import DATABASE_FILE
            from .database import (
                blocking_get_latest_reputation,
                blocking_get_latest_storage
            )
            
            # Get all node names
            node_names = list(app['nodes'].keys())
            
            if not node_names:
                continue
            
            # Evaluate each node
            for node_name in node_names:
                try:
                    loop = asyncio.get_running_loop()
                    
                    # Get latest reputation data
                    reputation_data = await loop.run_in_executor(
                        app['db_executor'],
                        blocking_get_latest_reputation,
                        DATABASE_FILE,
                        [node_name]
                    )
                    
                    if reputation_data:
                        await alert_manager.evaluate_reputation_alerts(
                            node_name,
                            reputation_data
                        )
                        
                        # Analyze reputation health
                        insights = await analytics.analyze_reputation_health(
                            node_name,
                            reputation_data
                        )
                        
                        # Write insights to database
                        if insights:
                            from .database import blocking_write_insight
                            for insight in insights:
                                await loop.run_in_executor(
                                    app['db_executor'],
                                    blocking_write_insight,
                                    DATABASE_FILE,
                                    insight
                                )
                    
                    # Get latest storage data
                    storage_data_list = await loop.run_in_executor(
                        app['db_executor'],
                        blocking_get_latest_storage,
                        DATABASE_FILE,
                        [node_name]
                    )
                    
                    if storage_data_list:
                        storage_data = storage_data_list[0]
                        
                        await alert_manager.evaluate_storage_alerts(
                            node_name,
                            storage_data
                        )
                        
                        # Get storage history for trend analysis
                        from .database import blocking_get_storage_history
                        storage_history = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_storage_history,
                            DATABASE_FILE,
                            node_name,
                            7  # 7 days
                        )
                        
                        if storage_history:
                            insights = await analytics.analyze_storage_health(
                                node_name,
                                storage_history
                            )
                            
                            # Write insights
                            if insights:
                                from .database import blocking_write_insight
                                for insight in insights:
                                    await loop.run_in_executor(
                                        app['db_executor'],
                                        blocking_write_insight,
                                        DATABASE_FILE,
                                        insight
                                    )
                    
                    # Analyze recent traffic for anomalies
                    if node_name in app_state['nodes']:
                        recent_events = list(app_state['nodes'][node_name]['live_events'])
                        
                        if recent_events:
                            traffic_anomalies = await anomaly_detector.detect_traffic_anomalies(
                                node_name,
                                recent_events
                            )
                            
                            if traffic_anomalies:
                                await alert_manager.process_anomalies(traffic_anomalies)
                    
                except Exception:
                    log.error(f"Failed to evaluate alerts for node {node_name}:", exc_info=True)
            
            log.info("[ALERT_EVAL] Alert evaluation cycle complete")
            
        except asyncio.CancelledError:
            log.info("Alert evaluation task cancelled")
            break
        except Exception:
            log.error("Error in alert evaluation task:", exc_info=True)
            await asyncio.sleep(60)  # Back off on error