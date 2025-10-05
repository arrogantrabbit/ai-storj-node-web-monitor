"""
Financial Tracker Module

This module tracks and calculates earnings estimates for Storj storage nodes,
including storage, egress, repair, and audit traffic. It uses both API data
and database-based calculations to provide accurate earnings forecasts.
"""

import asyncio
import logging
import datetime
from typing import Dict, Any, List, Optional, Tuple

from .config import (
    NODE_API_POLL_INTERVAL,
    PRICING_EGRESS_PER_TB,
    PRICING_STORAGE_PER_TB_MONTH,
    PRICING_REPAIR_PER_TB,
    PRICING_AUDIT_PER_TB,
    OPERATOR_SHARE_EGRESS,
    OPERATOR_SHARE_STORAGE,
    OPERATOR_SHARE_REPAIR,
    OPERATOR_SHARE_AUDIT,
    HELD_AMOUNT_MONTHS_1_TO_3,
    HELD_AMOUNT_MONTHS_4_TO_6,
    HELD_AMOUNT_MONTHS_7_TO_9,
    HELD_AMOUNT_MONTHS_10_TO_15,
    HELD_AMOUNT_MONTH_16_PLUS,
    SATELLITE_NAMES,
    ENABLE_FINANCIAL_TRACKING
)
from .database import (
    blocking_write_earnings_estimate,
    blocking_get_latest_earnings,
    DATABASE_FILE
)

log = logging.getLogger("StorjMonitor.FinancialTracker")


class FinancialTracker:
    """
    Tracks and calculates earnings for a Storj storage node.
    """
    
    def __init__(self, node_name: str, api_client=None):
        self.node_name = node_name
        self.api_client = api_client
        self.last_poll_time = None
        self.node_start_date = None  # Will be determined from API or storage history
    
    async def get_api_earnings(self) -> Optional[Dict[str, Any]]:
        """
        Fetch earnings estimates from the node API.
        
        Returns:
            Dict with earnings data per satellite, or None if API unavailable
        """
        if not self.api_client or not self.api_client.is_available:
            log.debug(f"[{self.node_name}] API client not available for earnings fetch")
            return None
        
        try:
            data = await self.api_client.get_estimated_payout()
            if data:
                log.info(f"[{self.node_name}] Successfully fetched earnings data from API")
                return data
            else:
                log.warning(f"[{self.node_name}] API returned no earnings data")
                return None
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to fetch earnings from API: {e}", exc_info=True)
            return None
    
    
    def calculate_held_percentage(self, node_age_months: int) -> float:
        """
        Calculate the held percentage based on node age.
        
        Args:
            node_age_months: Age of the node in months
        
        Returns:
            Held percentage as decimal (0.75 = 75%)
        """
        if node_age_months <= 3:
            return HELD_AMOUNT_MONTHS_1_TO_3
        elif node_age_months <= 6:
            return HELD_AMOUNT_MONTHS_4_TO_6
        elif node_age_months <= 9:
            return HELD_AMOUNT_MONTHS_7_TO_9
        elif node_age_months <= 15:
            return HELD_AMOUNT_MONTHS_10_TO_15
        else:
            return HELD_AMOUNT_MONTH_16_PLUS
    
    async def determine_node_age(self, db_path: str, executor=None) -> Optional[int]:
        """
        Determine node age in months from API or storage history.
        
        Args:
            db_path: Path to database
            executor: Thread pool executor for database operations (optional)
        
        Returns:
            Node age in months, or None if cannot be determined
        """
        # First try to get from API dashboard
        if self.api_client and self.api_client.is_available:
            try:
                dashboard = await self.api_client.get_dashboard()
                if dashboard and 'startedAt' in dashboard:
                    started_at = datetime.datetime.fromisoformat(
                        dashboard['startedAt'].replace('Z', '+00:00')
                    )
                    now = datetime.datetime.now(datetime.timezone.utc)
                    months = (now.year - started_at.year) * 12 + (now.month - started_at.month)
                    self.node_start_date = started_at
                    
                    # Calculate more accurate age including days
                    days_diff = (now - started_at).days
                    months = max(1, months)  # At least 1 month
                    
                    log.info(
                        f"[{self.node_name}] Node started: {started_at.date()} "
                        f"({days_diff} days ago = {months} months, held rate: "
                        f"{self.calculate_held_percentage(months)*100:.0f}%)"
                    )
                    return months
            except Exception as e:
                log.warning(f"[{self.node_name}] Could not get node age from API: {e}")
        
        # Fallback: Use earliest storage snapshot or event using executor
        loop = asyncio.get_running_loop()
        try:
            node_age = await loop.run_in_executor(
                executor,
                self._blocking_determine_node_age_from_db,
                db_path
            )
            return node_age
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to determine node age: {e}", exc_info=True)
        
        # Default to 16+ months (no held amount) if we can't determine
        log.warning(
            f"[{self.node_name}] Could not determine node age, defaulting to 16+ months"
        )
        return 16
    
    def _blocking_determine_node_age_from_db(self, db_path: str) -> int:
        """Blocking method to determine node age from database."""
        import sqlite3
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                cursor = conn.cursor()
                
                # Try storage snapshots first
                cursor.execute(
                    "SELECT MIN(timestamp) FROM storage_snapshots WHERE node_name = ?",
                    (self.node_name,)
                )
                result = cursor.fetchone()
                earliest_storage = result[0] if result and result[0] else None
                
                # Try events
                cursor.execute(
                    "SELECT MIN(timestamp) FROM events WHERE node_name = ?",
                    (self.node_name,)
                )
                result = cursor.fetchone()
                earliest_event = result[0] if result and result[0] else None
                
                # Use earliest timestamp
                earliest = None
                if earliest_storage and earliest_event:
                    earliest = min(earliest_storage, earliest_event)
                elif earliest_storage:
                    earliest = earliest_storage
                elif earliest_event:
                    earliest = earliest_event
                
                if earliest:
                    started_at = datetime.datetime.fromisoformat(earliest)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    months = (now.year - started_at.year) * 12 + (now.month - started_at.month)
                    self.node_start_date = started_at
                    log.info(
                        f"[{self.node_name}] Node age from database: {months} months "
                        f"(started {started_at.date()})"
                    )
                    return max(1, months)  # At least 1 month
                
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to get node age from DB: {e}")
        
        return 16  # Default to 16+ months
    
    async def calculate_monthly_earnings(
        self,
        db_path: str,
        period: Optional[str] = None,
        loop = None,
        executor = None
    ) -> List[Dict[str, Any]]:
        """
        Calculate current month earnings estimates for all satellites.
        
        Args:
            db_path: Path to database
            period: Optional period in YYYY-MM format (defaults to current month)
            loop: Event loop for executor (optional)
            executor: Thread pool executor for database operations (optional)
        
        Returns:
            List of earnings estimates per satellite
        """
        if period is None:
            now = datetime.datetime.now(datetime.timezone.utc)
            period = now.strftime('%Y-%m')
        
        if loop is None:
            loop = asyncio.get_running_loop()
        
        # Try to get from API first
        api_data = await self.get_api_earnings()
        
        # Determine node age for held amount calculation
        node_age_months = await self.determine_node_age(db_path, executor)
        held_percentage = self.calculate_held_percentage(node_age_months)
        
        estimates = []
        
        # Get satellites from database first (most reliable)
        satellites_to_process = await loop.run_in_executor(
            executor,
            self._get_satellites_from_db,
            db_path
        )
        
        # If no satellites in database (new node), try to get from API
        if not satellites_to_process and api_data:
            # Try to extract satellites from API data
            if 'currentMonthExpectations' in api_data:
                satellites_to_process = list(api_data.get('currentMonthExpectations', {}).keys())
                if satellites_to_process:
                    log.info(f"[{self.node_name}] Using {len(satellites_to_process)} satellites from API (no events in DB yet)")
        
        if not satellites_to_process:
            log.warning(f"[{self.node_name}] No satellites found in database or API for earnings calculation")
            return []
        
        for satellite in satellites_to_process:
            try:
                # Calculate traffic earnings using executor
                traffic_earnings = await loop.run_in_executor(
                    executor,
                    self._blocking_calculate_from_traffic,
                    db_path,
                    satellite,
                    period
                )
                
                # Calculate storage earnings using executor
                storage_result = await loop.run_in_executor(
                    executor,
                    self._blocking_calculate_storage_earnings,
                    db_path,
                    satellite,
                    period
                )
                storage_bytes_hour, storage_gross, storage_net = storage_result
                
                # Calculate totals
                total_gross = (
                    traffic_earnings['egress_earnings_gross'] +
                    traffic_earnings['repair_earnings_gross'] +
                    traffic_earnings['audit_earnings_gross'] +
                    storage_gross
                )
                
                total_net = (
                    traffic_earnings['egress_earnings_net'] +
                    traffic_earnings['repair_earnings_net'] +
                    traffic_earnings['audit_earnings_net'] +
                    storage_net
                )
                
                # Calculate held amount
                held_amount = total_gross * held_percentage
                
                estimate = {
                    'timestamp': datetime.datetime.now(datetime.timezone.utc),
                    'node_name': self.node_name,
                    'satellite': satellite,
                    'period': period,
                    'egress_bytes': traffic_earnings['egress_bytes'],
                    'egress_earnings_gross': traffic_earnings['egress_earnings_gross'],
                    'egress_earnings_net': traffic_earnings['egress_earnings_net'],
                    'storage_bytes_hour': storage_bytes_hour,
                    'storage_earnings_gross': storage_gross,
                    'storage_earnings_net': storage_net,
                    'repair_bytes': traffic_earnings['repair_bytes'],
                    'repair_earnings_gross': traffic_earnings['repair_earnings_gross'],
                    'repair_earnings_net': traffic_earnings['repair_earnings_net'],
                    'audit_bytes': traffic_earnings['audit_bytes'],
                    'audit_earnings_gross': traffic_earnings['audit_earnings_gross'],
                    'audit_earnings_net': traffic_earnings['audit_earnings_net'],
                    'total_earnings_gross': total_gross,
                    'total_earnings_net': total_net,
                    'held_amount': held_amount,
                    'node_age_months': node_age_months,
                    'held_percentage': held_percentage
                }
                
                estimates.append(estimate)
                
            except Exception as e:
                log.error(
                    f"[{self.node_name}] Failed to calculate earnings for satellite {satellite}: {e}",
                    exc_info=True
                )
        
        return estimates
    
    def _get_satellites_from_db(self, db_path: str) -> List[str]:
        """Blocking method to get satellites from database."""
        import sqlite3
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT DISTINCT satellite_id FROM events WHERE node_name = ?",
                    (self.node_name,)
                )
                satellites = [row[0] for row in cursor.fetchall()]
                if satellites:
                    log.debug(f"[{self.node_name}] Found {len(satellites)} satellites in database")
                return satellites
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to get satellites from DB: {e}")
            return []
    
    def _blocking_calculate_from_traffic(
        self,
        db_path: str,
        satellite: str,
        period: str
    ) -> Dict[str, Any]:
        """Blocking wrapper for calculate_from_traffic."""
        import sqlite3
        try:
            # Parse period
            year, month = map(int, period.split('-'))
            period_start = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
            if month == 12:
                period_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
            else:
                period_end = datetime.datetime(year, month + 1, 1, tzinfo=datetime.timezone.utc)
            
            period_start_iso = period_start.isoformat()
            period_end_iso = period_end.isoformat()
            
            with sqlite3.connect(db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Query traffic data for the period
                query = """
                    SELECT
                        SUM(CASE WHEN action LIKE '%GET%' AND action != 'GET_AUDIT' AND action != 'GET_REPAIR'
                                 AND status = 'success' THEN size ELSE 0 END) as egress_bytes,
                        SUM(CASE WHEN action = 'GET_REPAIR' AND status = 'success'
                                 THEN size ELSE 0 END) as repair_bytes,
                        SUM(CASE WHEN action = 'GET_AUDIT' AND status = 'success'
                                 THEN size ELSE 0 END) as audit_bytes
                    FROM events
                    WHERE node_name = ?
                        AND satellite_id = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                """
                
                result = cursor.execute(
                    query,
                    (self.node_name, satellite, period_start_iso, period_end_iso)
                ).fetchone()
                
                egress_bytes = result['egress_bytes'] or 0
                repair_bytes = result['repair_bytes'] or 0
                audit_bytes = result['audit_bytes'] or 0
                
                # Calculate earnings
                egress_tb = egress_bytes / (1024 ** 4)
                repair_tb = repair_bytes / (1024 ** 4)
                audit_tb = audit_bytes / (1024 ** 4)
                
                egress_gross = egress_tb * PRICING_EGRESS_PER_TB
                egress_net = egress_gross * OPERATOR_SHARE_EGRESS
                
                repair_gross = repair_tb * PRICING_REPAIR_PER_TB
                repair_net = repair_gross * OPERATOR_SHARE_REPAIR
                
                audit_gross = audit_tb * PRICING_AUDIT_PER_TB
                audit_net = audit_gross * OPERATOR_SHARE_AUDIT
                
                return {
                    'egress_bytes': egress_bytes,
                    'egress_earnings_gross': egress_gross,
                    'egress_earnings_net': egress_net,
                    'repair_bytes': repair_bytes,
                    'repair_earnings_gross': repair_gross,
                    'repair_earnings_net': repair_net,
                    'audit_bytes': audit_bytes,
                    'audit_earnings_gross': audit_gross,
                    'audit_earnings_net': audit_net
                }
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to calculate traffic earnings: {e}")
            return {
                'egress_bytes': 0, 'egress_earnings_gross': 0, 'egress_earnings_net': 0,
                'repair_bytes': 0, 'repair_earnings_gross': 0, 'repair_earnings_net': 0,
                'audit_bytes': 0, 'audit_earnings_gross': 0, 'audit_earnings_net': 0
            }
    
    def _blocking_calculate_storage_earnings(
        self,
        db_path: str,
        satellite: str,
        period: str
    ) -> Tuple[int, float, float]:
        """
        Calculate storage earnings for a specific satellite by proportionally
        allocating total storage earnings based on the satellite's share of traffic.
        
        Note: Storage snapshots are per-node, not per-satellite. We allocate
        storage earnings proportionally based on each satellite's traffic share.
        """
        import sqlite3
        import calendar
        try:
            # Parse period
            year, month = map(int, period.split('-'))
            period_start = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
            days_in_month = calendar.monthrange(year, month)[1]
            if month == 12:
                period_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
            else:
                period_end = datetime.datetime(year, month + 1, 1, tzinfo=datetime.timezone.utc)
            
            period_start_iso = period_start.isoformat()
            period_end_iso = period_end.isoformat()
            
            with sqlite3.connect(db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # First, get total storage earnings for the node
                query_snapshots = """
                    SELECT timestamp, used_bytes
                    FROM storage_snapshots
                    WHERE node_name = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                        AND used_bytes IS NOT NULL
                    ORDER BY timestamp ASC
                """
                
                snapshots = cursor.execute(
                    query_snapshots,
                    (self.node_name, period_start_iso, period_end_iso)
                ).fetchall()
                
                if not snapshots:
                    return (0, 0.0, 0.0)
                
                # Calculate total byte-hours for the node
                total_byte_hours = 0
                for i in range(len(snapshots) - 1):
                    t1 = datetime.datetime.fromisoformat(snapshots[i]['timestamp'])
                    t2 = datetime.datetime.fromisoformat(snapshots[i + 1]['timestamp'])
                    hours_diff = (t2 - t1).total_seconds() / 3600
                    avg_bytes = (snapshots[i]['used_bytes'] + snapshots[i + 1]['used_bytes']) / 2
                    total_byte_hours += avg_bytes * hours_diff
                
                if snapshots:
                    last_snapshot = snapshots[-1]
                    last_time = datetime.datetime.fromisoformat(last_snapshot['timestamp'])
                    if last_time < period_end:
                        hours_remaining = (period_end - last_time).total_seconds() / 3600
                        total_byte_hours += last_snapshot['used_bytes'] * hours_remaining
                
                # Calculate total storage earnings
                gb_hours = total_byte_hours / (1024 ** 3)
                hours_in_month = days_in_month * 24
                tb_months = gb_hours / (1024 * hours_in_month)
                total_storage_gross = tb_months * PRICING_STORAGE_PER_TB_MONTH
                total_storage_net = total_storage_gross * OPERATOR_SHARE_STORAGE
                
                # Get this satellite's traffic share to proportionally allocate storage
                query_satellite_traffic = """
                    SELECT SUM(size) as satellite_bytes
                    FROM events
                    WHERE node_name = ?
                        AND satellite_id = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                        AND status = 'success'
                """
                
                satellite_result = cursor.execute(
                    query_satellite_traffic,
                    (self.node_name, satellite, period_start_iso, period_end_iso)
                ).fetchone()
                
                satellite_bytes = satellite_result['satellite_bytes'] or 0
                
                # Get total traffic for all satellites
                query_total_traffic = """
                    SELECT SUM(size) as total_bytes
                    FROM events
                    WHERE node_name = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                        AND status = 'success'
                """
                
                total_result = cursor.execute(
                    query_total_traffic,
                    (self.node_name, period_start_iso, period_end_iso)
                ).fetchone()
                
                total_bytes = total_result['total_bytes'] or 0
                
                # Calculate proportional allocation
                if total_bytes > 0:
                    proportion = satellite_bytes / total_bytes
                    allocated_gross = total_storage_gross * proportion
                    allocated_net = total_storage_net * proportion
                    allocated_byte_hours = int(total_byte_hours * proportion)
                    
                    log.debug(
                        f"[{self.node_name}] Storage allocation for {SATELLITE_NAMES.get(satellite, satellite[:8])}: "
                        f"{proportion:.1%} ({satellite_bytes / (1024**4):.2f} TB / {total_bytes / (1024**4):.2f} TB) = "
                        f"${allocated_net:.4f}"
                    )
                else:
                    # No traffic data - split evenly across satellites found
                    # This is a fallback for very new nodes
                    query_satellite_count = """
                        SELECT COUNT(DISTINCT satellite_id) as sat_count
                        FROM events
                        WHERE node_name = ?
                            AND timestamp >= ?
                            AND timestamp < ?
                    """
                    count_result = cursor.execute(
                        query_satellite_count,
                        (self.node_name, period_start_iso, period_end_iso)
                    ).fetchone()
                    
                    sat_count = count_result['sat_count'] or 1
                    proportion = 1.0 / max(sat_count, 1)
                    allocated_gross = total_storage_gross * proportion
                    allocated_net = total_storage_net * proportion
                    allocated_byte_hours = int(total_byte_hours * proportion)
                    
                    log.debug(
                        f"[{self.node_name}] Storage allocation for {SATELLITE_NAMES.get(satellite, satellite[:8])}: "
                        f"equal split ({proportion:.1%}) = ${allocated_net:.4f}"
                    )
                
                return (allocated_byte_hours, allocated_gross, allocated_net)
                
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to calculate storage earnings: {e}")
            return (0, 0.0, 0.0)
    
    async def forecast_payout(
        self,
        db_path: str,
        period: Optional[str] = None,
        loop = None,
        executor = None
    ) -> Dict[str, Any]:
        """
        Forecast month-end payout with confidence score.
        
        Args:
            db_path: Path to database
            period: Optional period in YYYY-MM format (defaults to current month)
            loop: Event loop for executor (optional)
            executor: Thread pool executor for database operations (optional)
        
        Returns:
            Dict with forecast data including confidence score
        """
        if period is None:
            now = datetime.datetime.now(datetime.timezone.utc)
            period = now.strftime('%Y-%m')
        
        # Get current month estimates
        estimates = await self.calculate_monthly_earnings(db_path, period, loop, executor)
        
        if not estimates:
            return {
                'period': period,
                'forecasted_payout': 0.0,
                'confidence': 0.0,
                'reason': 'No data available'
            }
        
        # Calculate total across all satellites
        total_forecast = sum(est['total_earnings_net'] for est in estimates)
        total_held = sum(est['held_amount'] for est in estimates)
        
        # Calculate confidence score based on data availability and time progress
        year, month = map(int, period.split('-'))
        now = datetime.datetime.now(datetime.timezone.utc)
        
        if year == now.year and month == now.month:
            # Current month - calculate progress
            import calendar
            days_in_month = calendar.monthrange(year, month)[1]
            days_elapsed = now.day
            progress = days_elapsed / days_in_month
            
            # Confidence increases with progress through the month
            # 50% at start, 100% at end
            time_confidence = 0.5 + (progress * 0.5)
        else:
            # Past or future month
            time_confidence = 1.0 if year < now.year or (year == now.year and month < now.month) else 0.3
        
        # Data confidence based on having storage data
        has_storage_data = any(est['storage_bytes_hour'] > 0 for est in estimates)
        data_confidence = 1.0 if has_storage_data else 0.7
        
        # Overall confidence
        confidence = time_confidence * data_confidence
        
        log.info(
            f"[{self.node_name}] Payout forecast for {period}: "
            f"${total_forecast:.4f} net (${total_held:.4f} held), "
            f"confidence={confidence:.1%}"
        )
        
        return {
            'period': period,
            'forecasted_payout': total_forecast,
            'forecasted_payout_before_held': total_forecast + total_held,
            'held_amount': total_held,
            'confidence': confidence,
            'time_confidence': time_confidence,
            'data_confidence': data_confidence,
            'satellites': len(estimates)
        }
    
    async def import_historical_payouts(self, db_path: str, loop, executor=None):
        """
        Import historical payout data from API to populate earnings history.
        
        This allows the graph to show historical data immediately without waiting months.
        Only imports data once - won't duplicate if database already has historical data.
        
        Args:
            db_path: Path to database
            loop: Event loop for executor
            executor: Thread pool executor for database operations
        """
        if not self.api_client or not self.api_client.is_available:
            log.debug(f"[{self.node_name}] API not available for historical import")
            return
        
        try:
            # Get all payout history from API
            payout_history = await self.api_client.get_payout_history()
            
            if not payout_history:
                log.info(f"[{self.node_name}] No historical payout data available from API")
                return
            
            # Check if this is a dict with period keys or some other structure
            log.info(f"[{self.node_name}] Received payout history data: {type(payout_history)}")
            
            # The API might return data in various formats:
            # Option 1: {'2021-10': {...}, '2021-11': {...}, ...}
            # Option 2: {'currentMonthPayouts': {...}, 'previousMonthsPayouts': [...]}
            # We'll handle both
            
            imported_count = 0
            
            # If it's a dict with period-like keys (YYYY-MM format)
            if isinstance(payout_history, dict):
                for period_key, period_data in payout_history.items():
                    # Check if key looks like a period (YYYY-MM)
                    if len(period_key) == 7 and period_key[4] == '-':
                        # This looks like a period key
                        # period_data should contain per-satellite payout info
                        log.info(f"[{self.node_name}] Processing historical period: {period_key}")
                        
                        # Convert API payout data to earnings estimate format
                        # Note: Historical payouts are actual paid amounts, not estimates
                        # We store them as estimates for consistency but mark them as historical
                        
                        if isinstance(period_data, dict):
                            for satellite_id, satellite_data in period_data.items():
                                if not isinstance(satellite_data, dict):
                                    continue
                                
                                # Extract payout information
                                # API format might vary, adapt as needed
                                payout_amount = satellite_data.get('paid', satellite_data.get('totalAmount', 0))
                                held = satellite_data.get('held', 0)
                                
                                if payout_amount > 0 or held > 0:
                                    estimate = {
                                        'timestamp': datetime.datetime.now(datetime.timezone.utc),
                                        'node_name': self.node_name,
                                        'satellite': satellite_id,
                                        'period': period_key,
                                        'egress_bytes': 0,  # Historical data doesn't have breakdown
                                        'egress_earnings_gross': 0,
                                        'egress_earnings_net': 0,
                                        'storage_bytes_hour': 0,
                                        'storage_earnings_gross': 0,
                                        'storage_earnings_net': 0,
                                        'repair_bytes': 0,
                                        'repair_earnings_gross': 0,
                                        'repair_earnings_net': 0,
                                        'audit_bytes': 0,
                                        'audit_earnings_gross': 0,
                                        'audit_earnings_net': 0,
                                        'total_earnings_gross': payout_amount + held,
                                        'total_earnings_net': payout_amount,
                                        'held_amount': held,
                                        'node_age_months': 0,  # Unknown for historical
                                        'held_percentage': held / (payout_amount + held) if (payout_amount + held) > 0 else 0
                                    }
                                    
                                    # Write to database
                                    success = await loop.run_in_executor(
                                        executor,
                                        blocking_write_earnings_estimate,
                                        db_path,
                                        estimate
                                    )
                                    
                                    if success:
                                        imported_count += 1
            
            if imported_count > 0:
                log.info(f"[{self.node_name}] Successfully imported {imported_count} historical payout records")
            else:
                log.info(f"[{self.node_name}] No historical payouts to import (or data format not recognized)")
                
        except Exception as e:
            log.error(f"[{self.node_name}] Error importing historical payouts: {e}", exc_info=True)
    
    async def track_earnings(self, db_path: str, loop, executor=None):
        """
        Main tracking function - calculates and stores earnings estimates.
        
        Args:
            db_path: Path to database
            loop: Event loop for executor
            executor: Thread pool executor for database operations
        """
        try:
            # Calculate current month earnings
            now = datetime.datetime.now(datetime.timezone.utc)
            period = now.strftime('%Y-%m')
            
            estimates = await self.calculate_monthly_earnings(db_path, period, loop, executor)
            
            if estimates:
                # Write estimates to database
                for estimate in estimates:
                    success = await loop.run_in_executor(
                        executor,
                        blocking_write_earnings_estimate,
                        db_path,
                        estimate
                    )
                    if success:
                        sat_name = SATELLITE_NAMES.get(estimate['satellite'], estimate['satellite'][:8])
                        log.info(
                            f"[{self.node_name}] Wrote earnings estimate for {sat_name}: "
                            f"${estimate['total_earnings_net']:.4f} net "
                            f"(${estimate['held_amount']:.4f} held)"
                        )
                
                self.last_poll_time = now
            else:
                log.warning(f"[{self.node_name}] No earnings estimates calculated - possibly no events in database yet")
                
        except Exception as e:
            log.error(f"[{self.node_name}] Error tracking earnings: {e}", exc_info=True)


async def financial_polling_task(app: Dict[str, Any]):
    """
    Background task that polls earnings data for all nodes and broadcasts updates.
    
    Args:
        app: Application context
    """
    if not ENABLE_FINANCIAL_TRACKING:
        log.info("Financial tracking is disabled in configuration")
        return
    
    log.info("Financial tracking polling task started")
    
    # Initialize financial trackers for each node
    if 'financial_trackers' not in app:
        app['financial_trackers'] = {}
    
    for node_name in app['nodes'].keys():
        api_client = app.get('api_clients', {}).get(node_name)
        app['financial_trackers'][node_name] = FinancialTracker(node_name, api_client)
        log.info(f"[{node_name}] Financial tracker initialized")
    
    # Import historical payouts on first run (one-time operation)
    log.info("Importing historical payout data from node APIs...")
    for node_name, tracker in app['financial_trackers'].items():
        try:
            await tracker.import_historical_payouts(
                DATABASE_FILE,
                asyncio.get_running_loop(),
                app.get('db_executor')
            )
        except Exception as e:
            log.error(f"[{node_name}] Failed to import historical payouts: {e}")
    
    # Initial poll
    await asyncio.sleep(10)  # Wait for other systems to initialize
    
    loop = asyncio.get_running_loop()
    
    while True:
        try:
            for node_name, tracker in app['financial_trackers'].items():
                try:
                    await tracker.track_earnings(DATABASE_FILE, loop, app.get('db_executor'))
                except Exception as e:
                    log.error(
                        f"[{node_name}] Failed to track earnings: {e}",
                        exc_info=True
                    )
            
            # Broadcast earnings update to all connected clients
            await broadcast_earnings_update(app, loop)
            
            # Wait for next poll interval
            await asyncio.sleep(NODE_API_POLL_INTERVAL)
            
        except asyncio.CancelledError:
            log.info("Financial polling task cancelled")
            break
        except Exception as e:
            log.error(f"Error in financial polling task: {e}", exc_info=True)
            await asyncio.sleep(60)  # Wait before retry on error


async def broadcast_earnings_update(app: Dict[str, Any], loop=None):
    """
    Broadcast earnings updates to all connected WebSocket clients.
    
    Args:
        app: Application context
        loop: Event loop for executor (optional)
    """
    try:
        from .state import app_state
        from .websocket_utils import robust_broadcast
        
        if loop is None:
            loop = asyncio.get_running_loop()
        
        # Get current period
        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime('%Y-%m')
        
        # Get latest earnings for all nodes
        node_names = list(app['nodes'].keys())
        
        earnings_data = await loop.run_in_executor(
            app.get('db_executor'),
            blocking_get_latest_earnings,
            DATABASE_FILE,
            node_names,
            period
        )
        
        if not earnings_data:
            return
        
        # Format data for WebSocket transmission
        # Group by node for forecasting (calculate forecast once per node, not per satellite)
        formatted_data = []
        node_forecasts = {}
        
        for estimate in earnings_data:
            node_name = estimate['node_name']
            
            # Calculate forecast once per node (not per satellite to reduce load)
            if node_name not in node_forecasts:
                tracker = app['financial_trackers'].get(node_name)
                if tracker:
                    try:
                        node_forecasts[node_name] = await tracker.forecast_payout(
                            DATABASE_FILE, period, loop, app.get('db_executor')
                        )
                    except Exception as e:
                        log.error(f"Failed to get forecast for {node_name}: {e}")
                        node_forecasts[node_name] = None
                else:
                    node_forecasts[node_name] = None
            
            forecast_info = node_forecasts[node_name]
            sat_name = SATELLITE_NAMES.get(estimate['satellite'], estimate['satellite'][:8])
            
            formatted_data.append({
                'node_name': estimate['node_name'],
                'satellite': sat_name,
                'total_net': round(estimate['total_earnings_net'], 2),
                'total_gross': round(estimate['total_earnings_gross'], 2),
                'held_amount': round(estimate['held_amount'], 2),
                'breakdown': {
                    'egress': round(estimate['egress_earnings_net'], 2),
                    'storage': round(estimate['storage_earnings_net'], 2),
                    'repair': round(estimate['repair_earnings_net'], 2),
                    'audit': round(estimate['audit_earnings_net'], 2)
                },
                'forecast_month_end': round(forecast_info['forecasted_payout'], 2) if forecast_info else None,
                'confidence': round(forecast_info['confidence'], 2) if forecast_info else None
            })
        
        # Broadcast to all clients
        payload = {
            'type': 'earnings_data',
            'data': formatted_data
        }
        
        await robust_broadcast(app_state['websockets'], payload)
        log.debug(f"Broadcast earnings update with {len(formatted_data)} estimates")
        
    except Exception as e:
        log.error(f"Failed to broadcast earnings update: {e}", exc_info=True)