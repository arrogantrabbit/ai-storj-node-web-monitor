"""
Analytics Engine for Storj Node Monitor - Phase 4
Provides statistical analysis and pattern recognition capabilities.
"""

import asyncio
import datetime
import logging
import statistics
from typing import Any, Optional

from .config import MIN_STORAGE_DATA_POINTS_FOR_FORECAST

log = logging.getLogger("StorjMonitor.Analytics")


class AnalyticsEngine:
    """
    Core analytics engine for statistical analysis and pattern recognition.
    """

    def __init__(self, app):
        self.app = app
        self.baselines = {}  # Cache for baselines

    async def calculate_baseline(
        self, node_name: str, metric_name: str, values: list[float], window_hours: int = 168
    ) -> Optional[dict[str, float]]:
        """
        Calculate baseline statistics for a metric.

        Args:
            node_name: Name of the node
            metric_name: Name of the metric
            values: List of metric values
            window_hours: Time window for baseline (default: 7 days)

        Returns:
            Dict with mean, std_dev, min, max, count
        """
        if not values or len(values) < 2:
            return None

        try:
            stats = {
                "mean": statistics.mean(values),
                "std_dev": statistics.stdev(values) if len(values) > 1 else 0,
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }

            # Store baseline in database
            loop = asyncio.get_running_loop()
            from .config import DATABASE_FILE
            from .database import blocking_update_baseline

            await loop.run_in_executor(
                self.app["db_executor"],
                blocking_update_baseline,
                DATABASE_FILE,
                node_name,
                metric_name,
                window_hours,
                stats,
            )

            # Cache it
            cache_key = f"{node_name}:{metric_name}:{window_hours}"
            self.baselines[cache_key] = stats

            return stats

        except Exception:
            log.error(f"Failed to calculate baseline for {metric_name}:", exc_info=True)
            return None

    async def get_baseline(
        self, node_name: str, metric_name: str, window_hours: int = 168
    ) -> Optional[dict[str, float]]:
        """
        Get baseline statistics for a metric (from cache or database).
        """
        cache_key = f"{node_name}:{metric_name}:{window_hours}"

        # Check cache first
        if cache_key in self.baselines:
            return self.baselines[cache_key]

        # Load from database
        try:
            loop = asyncio.get_running_loop()
            from .config import DATABASE_FILE
            from .database import blocking_get_baseline

            baseline = await loop.run_in_executor(
                self.app["db_executor"],
                blocking_get_baseline,
                DATABASE_FILE,
                node_name,
                metric_name,
                window_hours,
            )

            if baseline:
                self.baselines[cache_key] = baseline

            return baseline

        except Exception:
            log.error(f"Failed to get baseline for {metric_name}:", exc_info=True)
            return None

    def calculate_z_score(self, value: float, baseline: dict[str, float]) -> Optional[float]:
        """
        Calculate Z-score for a value against a baseline.

        Args:
            value: Current value
            baseline: Baseline statistics (mean, std_dev)

        Returns:
            Z-score or None if std_dev is 0
        """
        if not baseline or baseline["std_dev"] == 0:
            return None

        z_score = (value - baseline["mean"]) / baseline["std_dev"]
        return z_score

    def detect_trend(self, values: list[float], threshold: float = 0.1) -> tuple[str, float]:
        """
        Detect trend in a series of values using linear regression.

        Args:
            values: List of values (ordered by time)
            threshold: Minimum slope to consider as trending

        Returns:
            Tuple of (trend_direction, slope)
            trend_direction: 'increasing', 'decreasing', or 'stable'
        """
        if not values or len(values) < 3:
            return ("stable", 0.0)

        try:
            # Simple linear regression
            n = len(values)
            x = list(range(n))

            x_mean = statistics.mean(x)
            y_mean = statistics.mean(values)

            numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
            denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

            if denominator == 0:
                return ("stable", 0.0)

            slope = numerator / denominator

            # Normalize slope relative to mean
            normalized_slope = slope / abs(y_mean) if y_mean != 0 else slope

            if abs(normalized_slope) < threshold:
                return ("stable", slope)
            elif normalized_slope > 0:
                return ("increasing", slope)
            else:
                return ("decreasing", slope)

        except Exception:
            log.error("Failed to detect trend:", exc_info=True)
            return ("stable", 0.0)

    def calculate_percentile(self, values: list[float], percentile: float) -> Optional[float]:
        """
        Calculate a percentile from a list of values.

        Args:
            values: List of values
            percentile: Percentile to calculate (0-100)

        Returns:
            Percentile value or None
        """
        if not values:
            return None

        try:
            sorted_values = sorted(values)
            index = (percentile / 100) * (len(sorted_values) - 1)

            if index.is_integer():
                return sorted_values[int(index)]
            else:
                lower = sorted_values[int(index)]
                upper = sorted_values[int(index) + 1]
                fraction = index - int(index)
                return lower + (upper - lower) * fraction

        except Exception:
            log.error(f"Failed to calculate percentile {percentile}:", exc_info=True)
            return None

    def calculate_rate_of_change(
        self, values: list[tuple[datetime.datetime, float]], window_hours: int = 24
    ) -> Optional[float]:
        """
        Calculate rate of change over time.

        Args:
            values: List of (timestamp, value) tuples
            window_hours: Time window to analyze

        Returns:
            Rate of change per hour, or None
        """
        if not values or len(values) < 2:
            return None

        try:
            # Filter out None values first
            valid_values = [(t, v) for t, v in values if v is not None]

            if not valid_values or len(valid_values) < 2:
                return None

            # Sort by timestamp
            sorted_values = sorted(valid_values, key=lambda x: x[0])

            # Filter to window
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                hours=window_hours
            )
            windowed = [(t, v) for t, v in sorted_values if t >= cutoff]

            if len(windowed) < 2:
                return None

            first_time, first_value = windowed[0]
            last_time, last_value = windowed[-1]

            time_diff_hours = (last_time - first_time).total_seconds() / 3600

            if time_diff_hours == 0:
                return None

            rate = (last_value - first_value) / time_diff_hours
            return rate

        except Exception:
            log.error("Failed to calculate rate of change:", exc_info=True)
            return None

    def forecast_linear(
        self, values: list[tuple[datetime.datetime, float]], forecast_hours: int = 24
    ) -> Optional[float]:
        """
        Simple linear forecast based on recent trend.

        Args:
            values: List of (timestamp, value) tuples
            forecast_hours: Hours into the future to forecast

        Returns:
            Forecasted value or None
        """
        rate = self.calculate_rate_of_change(values)

        if rate is None or not values:
            return None

        try:
            # Use most recent value as starting point
            latest_value = sorted(values, key=lambda x: x[0])[-1][1]
            forecasted = latest_value + (rate * forecast_hours)

            return forecasted

        except Exception:
            log.error("Failed to forecast:", exc_info=True)
            return None

    async def analyze_reputation_health(
        self, node_name: str, reputation_data: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Analyze reputation health and generate insights.

        Args:
            node_name: Name of the node
            reputation_data: Recent reputation data

        Returns:
            List of insights
        """
        insights = []

        if not reputation_data:
            return insights

        try:
            from .config import (
                AUDIT_SCORE_CRITICAL,
                AUDIT_SCORE_WARNING,
                ONLINE_SCORE_WARNING,
                SUSPENSION_SCORE_CRITICAL,
            )

            for sat_data in reputation_data:
                satellite = sat_data["satellite"]
                audit_score = sat_data.get("audit_score")
                suspension_score = sat_data.get("suspension_score")
                online_score = sat_data.get("online_score")

                # Check audit score
                if audit_score is not None:
                    if audit_score < AUDIT_SCORE_CRITICAL:
                        insights.append(
                            {
                                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                                "node_name": node_name,
                                "insight_type": "reputation_critical",
                                "severity": "critical",
                                "title": f"Critical Audit Score on {satellite}",
                                "description": f"Audit score is {audit_score:.2f}%, below critical threshold of {AUDIT_SCORE_CRITICAL}%",
                                "category": "reputation",
                                "confidence": 1.0,
                                "metadata": {"satellite": satellite, "score": audit_score},
                            }
                        )
                    elif audit_score < AUDIT_SCORE_WARNING:
                        insights.append(
                            {
                                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                                "node_name": node_name,
                                "insight_type": "reputation_warning",
                                "severity": "warning",
                                "title": f"Low Audit Score on {satellite}",
                                "description": f"Audit score is {audit_score:.2f}%, below warning threshold of {AUDIT_SCORE_WARNING}%",
                                "category": "reputation",
                                "confidence": 0.9,
                                "metadata": {"satellite": satellite, "score": audit_score},
                            }
                        )

                # Check suspension score
                if suspension_score is not None and suspension_score < SUSPENSION_SCORE_CRITICAL:
                    insights.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "suspension_risk",
                            "severity": "critical",
                            "title": f"Suspension Risk on {satellite}",
                            "description": f"Suspension score is {suspension_score:.2f}%, node may be suspended",
                            "category": "reputation",
                            "confidence": 1.0,
                            "metadata": {"satellite": satellite, "score": suspension_score},
                        }
                    )

                # Check online score
                if online_score is not None and online_score < ONLINE_SCORE_WARNING:
                    insights.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "uptime_warning",
                            "severity": "warning",
                            "title": f"Low Uptime Score on {satellite}",
                            "description": f"Online score is {online_score:.2f}%, indicating connectivity issues",
                            "category": "uptime",
                            "confidence": 0.8,
                            "metadata": {"satellite": satellite, "score": online_score},
                        }
                    )

        except Exception:
            log.error(f"Failed to analyze reputation health for {node_name}:", exc_info=True)

        return insights

    async def analyze_storage_health(
        self, node_name: str, storage_history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Analyze storage health and generate insights.

        Args:
            node_name: Name of the node
            storage_history: Recent storage snapshots

        Returns:
            List of insights
        """
        insights = []

        if not storage_history or len(storage_history) < 2:
            return insights

        try:
            from .config import (
                STORAGE_CRITICAL_PERCENT,
                STORAGE_FORECAST_CRITICAL_DAYS,
                STORAGE_FORECAST_WARNING_DAYS,
                STORAGE_WARNING_PERCENT,
            )

            latest = storage_history[-1]
            # Robustly compute used_percent (log-based snapshots may store None)
            used_percent = latest.get("used_percent")
            if used_percent is None:
                used_bytes = latest.get("used_bytes")
                total_bytes = latest.get("total_bytes") or latest.get("allocated_bytes")
                if isinstance(used_bytes, (int, float)) and isinstance(total_bytes, (int, float)) and total_bytes > 0:
                    used_percent = (used_bytes / total_bytes) * 100
                else:
                    used_percent = 0

            # Check current usage
            if used_percent >= STORAGE_CRITICAL_PERCENT:
                insights.append(
                    {
                        "timestamp": datetime.datetime.now(datetime.timezone.utc),
                        "node_name": node_name,
                        "insight_type": "storage_critical",
                        "severity": "critical",
                        "title": "Critical Storage Usage",
                        "description": f"Storage is {used_percent:.1f}% full, exceeding critical threshold",
                        "category": "storage",
                        "confidence": 1.0,
                        "metadata": {"used_percent": used_percent},
                    }
                )
            elif used_percent >= STORAGE_WARNING_PERCENT:
                insights.append(
                    {
                        "timestamp": datetime.datetime.now(datetime.timezone.utc),
                        "node_name": node_name,
                        "insight_type": "storage_warning",
                        "severity": "warning",
                        "title": "High Storage Usage",
                        "description": f"Storage is {used_percent:.1f}% full, approaching capacity",
                        "category": "storage",
                        "confidence": 0.9,
                        "metadata": {"used_percent": used_percent},
                    }
                )

            # Calculate growth rate and forecast
            values_with_time = [
                (datetime.datetime.fromisoformat(s["timestamp"]), s.get("used_bytes", 0))
                for s in storage_history
            ]

            growth_rate = None
            if len(values_with_time) >= MIN_STORAGE_DATA_POINTS_FOR_FORECAST:
                growth_rate = self.calculate_rate_of_change(values_with_time, window_hours=168)

            if growth_rate is not None and growth_rate > 0:
                available_bytes = latest.get("available_bytes", 0)
                hours_until_full = (
                    available_bytes / growth_rate if growth_rate > 0 else float("inf")
                )
                days_until_full = hours_until_full / 24

                if days_until_full < STORAGE_FORECAST_CRITICAL_DAYS:
                    insights.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "storage_forecast_critical",
                            "severity": "critical",
                            "title": "Storage Capacity Critical",
                            "description": f"Storage will be full in approximately {days_until_full:.1f} days at current growth rate",
                            "category": "storage",
                            "confidence": 0.7,
                            "metadata": {
                                "days_until_full": days_until_full,
                                "growth_rate_gb_per_day": growth_rate * 24 / (1024**3),
                            },
                        }
                    )
                elif days_until_full < STORAGE_FORECAST_WARNING_DAYS:
                    insights.append(
                        {
                            "timestamp": datetime.datetime.now(datetime.timezone.utc),
                            "node_name": node_name,
                            "insight_type": "storage_forecast_warning",
                            "severity": "warning",
                            "title": "Storage Capacity Warning",
                            "description": f"Storage will be full in approximately {days_until_full:.1f} days at current growth rate",
                            "category": "storage",
                            "confidence": 0.6,
                            "metadata": {
                                "days_until_full": days_until_full,
                                "growth_rate_gb_per_day": growth_rate * 24 / (1024**3),
                            },
                        }
                    )

        except Exception:
            log.error(f"Failed to analyze storage health for {node_name}:", exc_info=True)

        return insights
