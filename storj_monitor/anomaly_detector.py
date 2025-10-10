"""
Anomaly Detection for Storj Node Monitor - Phase 4
Detects unusual patterns and behaviors in node operations.
"""

import datetime
import logging
from collections import deque
from typing import Any, Optional

log = logging.getLogger("StorjMonitor.AnomalyDetector")


class AnomalyDetector:
    """
    Anomaly detector using statistical methods (Z-score, IQR, etc.)
    """

    def __init__(self, app, analytics_engine):
        self.app = app
        self.analytics = analytics_engine
        self.anomaly_threshold = 3.0  # Z-score threshold
        self.recent_anomalies = deque(maxlen=100)  # Cache recent anomalies

    async def detect_anomalies(
        self, node_name: str, metric_name: str, current_value: float, window_hours: int = 168
    ) -> Optional[dict[str, Any]]:
        """
        Detect if a metric value is anomalous compared to baseline.

        Args:
            node_name: Name of the node
            metric_name: Name of the metric
            current_value: Current metric value
            window_hours: Baseline window (default: 7 days)

        Returns:
            Anomaly dict if detected, None otherwise
        """
        try:
            # Get baseline statistics
            baseline = await self.analytics.get_baseline(node_name, metric_name, window_hours)

            if not baseline:
                # Not enough data for baseline
                return None

            # Calculate Z-score
            z_score = self.analytics.calculate_z_score(current_value, baseline)

            if z_score is None:
                return None

            # Check if anomalous
            if abs(z_score) >= self.anomaly_threshold:
                anomaly_type = "spike" if z_score > 0 else "drop"
                severity = "critical" if abs(z_score) >= 4.0 else "warning"

                anomaly = {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc),
                    "node_name": node_name,
                    "metric_name": metric_name,
                    "current_value": current_value,
                    "baseline_mean": baseline["mean"],
                    "z_score": z_score,
                    "anomaly_type": anomaly_type,
                    "severity": severity,
                    "confidence": min(abs(z_score) / 5.0, 1.0),  # Scale confidence
                }

                # Add to recent anomalies cache
                self.recent_anomalies.append(anomaly)

                log.info(
                    f"Anomaly detected for {node_name}:{metric_name} - "
                    f"value={current_value:.2f}, z-score={z_score:.2f}"
                )

                return anomaly

            return None

        except Exception:
            log.error(f"Failed to detect anomalies for {metric_name}:", exc_info=True)
            return None

    async def detect_traffic_anomalies(
        self, node_name: str, recent_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Detect anomalies in traffic patterns.

        Args:
            node_name: Name of the node
            recent_events: Recent event data

        Returns:
            List of detected anomalies
        """
        anomalies = []

        if not recent_events or len(recent_events) < 10:
            return anomalies

        try:
            # Calculate recent metrics
            success_count = sum(1 for e in recent_events if e["status"] == "success")
            fail_count = sum(1 for e in recent_events if e["status"] != "success")
            total_count = len(recent_events)

            if total_count == 0:
                return anomalies

            success_rate = success_count / total_count

            # Check success rate anomaly
            anomaly = await self.detect_anomalies(
                node_name, "success_rate", success_rate, window_hours=168
            )

            if anomaly and anomaly["anomaly_type"] == "drop":
                # Low success rate is concerning
                anomalies.append(
                    {
                        "timestamp": datetime.datetime.now(datetime.timezone.utc),
                        "node_name": node_name,
                        "insight_type": "traffic_anomaly",
                        "severity": anomaly["severity"],
                        "title": f"Abnormal Success Rate: {success_rate * 100:.1f}%",
                        "description": f"Success rate has dropped significantly (Z-score: {anomaly['z_score']:.2f})",
                        "category": "performance",
                        "confidence": anomaly["confidence"],
                        "metadata": {
                            "success_rate": success_rate,
                            "z_score": anomaly["z_score"],
                            "baseline_mean": anomaly["baseline_mean"],
                        },
                    }
                )

            # Check for unusual error patterns
            if fail_count > 0:
                error_rate = fail_count / total_count

                if error_rate > 0.1:  # More than 10% errors
                    # Group errors by type
                    error_types = {}
                    for event in recent_events:
                        if event["status"] != "success" and event.get("error_reason"):
                            reason = event["error_reason"]
                            error_types[reason] = error_types.get(reason, 0) + 1

                    # Find dominant error
                    if error_types:
                        dominant_error = max(error_types.items(), key=lambda x: x[1])

                        anomalies.append(
                            {
                                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                                "node_name": node_name,
                                "insight_type": "error_pattern",
                                "severity": "warning",
                                "title": f"High Error Rate: {error_rate * 100:.1f}%",
                                "description": f"Unusual number of errors detected. Most common: {dominant_error[0]}",
                                "category": "errors",
                                "confidence": 0.8,
                                "metadata": {
                                    "error_rate": error_rate,
                                    "dominant_error": dominant_error[0],
                                    "error_count": dominant_error[1],
                                },
                            }
                        )

        except Exception:
            log.error(f"Failed to detect traffic anomalies for {node_name}:", exc_info=True)

        return anomalies

    async def detect_latency_anomalies(
        self, node_name: str, latency_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Detect anomalies in latency metrics.

        Args:
            node_name: Name of the node
            latency_data: Latency statistics

        Returns:
            List of detected anomalies
        """
        anomalies = []

        if not latency_data:
            return anomalies

        try:
            from .config import LATENCY_CRITICAL_MS, LATENCY_WARNING_MS

            # Check P99 latency
            p99 = latency_data.get("p99")
            if p99:
                if p99 >= LATENCY_CRITICAL_MS:
                    anomalies.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "latency_critical",
                            "severity": "critical",
                            "title": f"Critical Latency: P99={p99:.0f}ms",
                            "description": f"99th percentile latency is {p99:.0f}ms, exceeding critical threshold",
                            "category": "performance",
                            "confidence": 1.0,
                            "metadata": {"p99_ms": p99, "threshold_ms": LATENCY_CRITICAL_MS},
                        }
                    )
                elif p99 >= LATENCY_WARNING_MS:
                    anomalies.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "latency_warning",
                            "severity": "warning",
                            "title": f"High Latency: P99={p99:.0f}ms",
                            "description": f"99th percentile latency is {p99:.0f}ms, above warning threshold",
                            "category": "performance",
                            "confidence": 0.9,
                            "metadata": {"p99_ms": p99, "threshold_ms": LATENCY_WARNING_MS},
                        }
                    )

            # Check for latency spikes using Z-score
            p50 = latency_data.get("p50")
            if p50:
                anomaly = await self.detect_anomalies(
                    node_name, "latency_p50", p50, window_hours=168
                )

                if anomaly and anomaly["anomaly_type"] == "spike":
                    anomalies.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "latency_spike",
                            "severity": anomaly["severity"],
                            "title": "Latency Spike Detected",
                            "description": f"Median latency is unusually high: {p50:.0f}ms (Z-score: {anomaly['z_score']:.2f})",
                            "category": "performance",
                            "confidence": anomaly["confidence"],
                            "metadata": {
                                "p50_ms": p50,
                                "z_score": anomaly["z_score"],
                                "baseline_mean": anomaly["baseline_mean"],
                            },
                        }
                    )

        except Exception:
            log.error(f"Failed to detect latency anomalies for {node_name}:", exc_info=True)

        return anomalies

    async def detect_bandwidth_anomalies(
        self, node_name: str, bandwidth_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Detect anomalies in bandwidth usage.

        Args:
            node_name: Name of the node
            bandwidth_data: Bandwidth statistics

        Returns:
            List of detected anomalies
        """
        anomalies = []

        if not bandwidth_data:
            return anomalies

        try:
            egress_mbps = bandwidth_data.get("avg_egress_mbps", 0)
            ingress_mbps = bandwidth_data.get("avg_ingress_mbps", 0)

            # Check egress anomalies
            if egress_mbps > 0:
                anomaly = await self.detect_anomalies(
                    node_name, "egress_mbps", egress_mbps, window_hours=168
                )

                if anomaly:
                    if anomaly["anomaly_type"] == "spike":
                        anomalies.append(
                            {
                                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                                "node_name": node_name,
                                "insight_type": "bandwidth_spike",
                                "severity": "info",
                                "title": "Unusual Egress Activity",
                                "description": f"Egress bandwidth is unusually high: {egress_mbps:.2f} Mbps",
                                "category": "bandwidth",
                                "confidence": anomaly["confidence"],
                                "metadata": {
                                    "egress_mbps": egress_mbps,
                                    "z_score": anomaly["z_score"],
                                },
                            }
                        )
                    elif anomaly["anomaly_type"] == "drop":
                        anomalies.append(
                            {
                                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                                "node_name": node_name,
                                "insight_type": "bandwidth_drop",
                                "severity": "warning",
                                "title": "Low Egress Activity",
                                "description": f"Egress bandwidth is unusually low: {egress_mbps:.2f} Mbps",
                                "category": "bandwidth",
                                "confidence": anomaly["confidence"],
                                "metadata": {
                                    "egress_mbps": egress_mbps,
                                    "z_score": anomaly["z_score"],
                                },
                            }
                        )

            # Check ingress anomalies
            if ingress_mbps > 0:
                anomaly = await self.detect_anomalies(
                    node_name, "ingress_mbps", ingress_mbps, window_hours=168
                )

                if anomaly and anomaly["anomaly_type"] == "drop":
                    # Low ingress might indicate reduced uploads
                    anomalies.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "upload_activity_drop",
                            "severity": "info",
                            "title": "Reduced Upload Activity",
                            "description": f"Ingress bandwidth is unusually low: {ingress_mbps:.2f} Mbps",
                            "category": "bandwidth",
                            "confidence": anomaly["confidence"],
                            "metadata": {
                                "ingress_mbps": ingress_mbps,
                                "z_score": anomaly["z_score"],
                            },
                        }
                    )

        except Exception:
            log.error(f"Failed to detect bandwidth anomalies for {node_name}:", exc_info=True)

        return anomalies

    def get_recent_anomalies(
        self, node_name: Optional[str] = None, minutes: int = 60
    ) -> list[dict[str, Any]]:
        """
        Get recent anomalies from cache.

        Args:
            node_name: Optional node name filter
            minutes: Time window in minutes

        Returns:
            List of recent anomalies
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)

        filtered = [a for a in self.recent_anomalies if a["timestamp"] >= cutoff]

        if node_name:
            filtered = [a for a in filtered if a["node_name"] == node_name]

        return filtered
