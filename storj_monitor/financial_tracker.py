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
        
        Prioritizes historical earnings data over API startedAt to handle
        cases where API reports incorrect start dates.
        
        Args:
            db_path: Path to database
            executor: Thread pool executor for database operations (optional)
        
        Returns:
            Node age in months, or None if cannot be determined
        """
        # First check if we have historical earnings data (most reliable for age)
        loop = asyncio.get_running_loop()
        try:
            earliest_earning_date = await loop.run_in_executor(
                executor,
                self._blocking_get_earliest_earning_date,
                db_path
            )
            if earliest_earning_date:
                started_at = datetime.datetime.fromisoformat(earliest_earning_date)
                now = datetime.datetime.now(datetime.timezone.utc)
                months = (now.year - started_at.year) * 12 + (now.month - started_at.month)
                days_diff = (now - started_at).days
                months = max(1, months)
                self.node_start_date = started_at
                
                log.info(
                    f"[{self.node_name}] Node age from earnings history: "
                    f"{started_at.date()} ({days_diff} days ago = {months} months, "
                    f"held rate: {self.calculate_held_percentage(months)*100:.0f}%)"
                )
                return months
        except Exception as e:
            log.debug(f"[{self.node_name}] No earnings history for age determination: {e}")
        
        # Fallback to API dashboard
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
                    
                    days_diff = (now - started_at).days
                    months = max(1, months)
                    
                    log.info(
                        f"[{self.node_name}] Node age from API: {started_at.date()} "
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
    
    def _blocking_get_earliest_earning_date(self, db_path: str) -> Optional[str]:
        """Get the earliest period from earnings_estimates table."""
        import sqlite3
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT MIN(period) FROM earnings_estimates WHERE node_name = ?",
                    (self.node_name,)
                )
                result = cursor.fetchone()
                if result and result[0]:
                    # period is YYYY-MM, convert to first day of that month
                    period = result[0]
                    return f"{period}-01T00:00:00+00:00"
        except Exception as e:
            log.debug(f"[{self.node_name}] Error getting earliest earning date: {e}")
        return None
    
    def _blocking_determine_node_age_from_db(self, db_path: str) -> int:
        """Blocking method to determine node age from database events/storage."""
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
        
        # Determine node age for held amount calculation
        node_age_months = await self.determine_node_age(db_path, executor)
        held_percentage = self.calculate_held_percentage(node_age_months)
        
        # For current month: use API data directly if available (more accurate than our calculations)
        now = datetime.datetime.now(datetime.timezone.utc)
        current_period = now.strftime('%Y-%m')
        
        if period == current_period:
            api_data = await self.get_api_earnings()
            if api_data and 'currentMonth' in api_data:
                # Use API's accurate current month data
                cm = api_data['currentMonth']
                payout_cents = cm.get('payout', 0)
                held_cents = cm.get('held', 0)
                
                # Convert from cents to dollars
                total_net = payout_cents / 100.0
                held_amount = held_cents / 100.0
                total_gross = total_net + held_amount
                
                log.info(
                    f"[{self.node_name}] Using API data for {period}: "
                    f"${total_net:.2f} net, ${held_amount:.2f} held"
                )
                
                # Delete any old per-satellite entries for current month to avoid double-counting
                await loop.run_in_executor(
                    executor,
                    self._blocking_delete_current_month_estimates,
                    db_path,
                    period
                )
                
                # Calculate breakdown from database to show proportional distribution
                # even though API only gives us totals
                breakdown_from_db = await loop.run_in_executor(
                    executor,
                    self._blocking_calculate_breakdown_from_all_satellites,
                    db_path,
                    period
                )
                
                # Scale the breakdown proportionally to match API total
                db_total = sum([
                    breakdown_from_db['egress_net'],
                    breakdown_from_db['storage_net'],
                    breakdown_from_db['repair_net'],
                    breakdown_from_db['audit_net']
                ])
                
                if db_total > 0:
                    # Scale to match API total
                    scale_factor = total_net / db_total
                    egress_net = breakdown_from_db['egress_net'] * scale_factor
                    storage_net = breakdown_from_db['storage_net'] * scale_factor
                    repair_net = breakdown_from_db['repair_net'] * scale_factor
                    audit_net = breakdown_from_db['audit_net'] * scale_factor
                    
                    log.info(
                        f"[{self.node_name}] Calculated breakdown from DB (scaled to API total): "
                        f"egress=${egress_net:.2f}, storage=${storage_net:.2f}, "
                        f"repair=${repair_net:.2f}, audit=${audit_net:.2f}"
                    )
                else:
                    # No DB data yet, set all to 0
                    egress_net = storage_net = repair_net = audit_net = 0
                    log.warning(
                        f"[{self.node_name}] No DB events yet for breakdown calculation"
                    )
                
                # Create a single aggregate estimate with calculated breakdown
                estimates = [{
                    'timestamp': datetime.datetime.now(datetime.timezone.utc),
                    'node_name': self.node_name,
                    'satellite': 'aggregate',  # API gives totals, not per-satellite
                    'period': period,
                    'egress_bytes': breakdown_from_db.get('egress_bytes', 0),
                    'egress_earnings_gross': egress_net / OPERATOR_SHARE_EGRESS if OPERATOR_SHARE_EGRESS > 0 else 0,
                    'egress_earnings_net': egress_net,
                    'storage_bytes_hour': breakdown_from_db.get('storage_bytes_hour', 0),
                    'storage_earnings_gross': storage_net / OPERATOR_SHARE_STORAGE if OPERATOR_SHARE_STORAGE > 0 else 0,
                    'storage_earnings_net': storage_net,
                    'repair_bytes': breakdown_from_db.get('repair_bytes', 0),
                    'repair_earnings_gross': repair_net / OPERATOR_SHARE_REPAIR if OPERATOR_SHARE_REPAIR > 0 else 0,
                    'repair_earnings_net': repair_net,
                    'audit_bytes': breakdown_from_db.get('audit_bytes', 0),
                    'audit_earnings_gross': audit_net / OPERATOR_SHARE_AUDIT if OPERATOR_SHARE_AUDIT > 0 else 0,
                    'audit_earnings_net': audit_net,
                    'total_earnings_gross': total_gross,
                    'total_earnings_net': total_net,
                    'held_amount': held_amount,
                    'node_age_months': node_age_months,
                    'held_percentage': held_percentage
                }]
                
                return estimates
        
        # For past months or if API unavailable: calculate from database
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
    
    def _blocking_calculate_breakdown_from_all_satellites(
        self,
        db_path: str,
        period: str
    ) -> Dict[str, Any]:
        """
        Calculate earnings breakdown from database events across all satellites.
        Returns totals that can be scaled to match API data.
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
                
                # Calculate traffic breakdown
                traffic_query = """
                    SELECT
                        SUM(CASE WHEN action LIKE '%GET%' AND action != 'GET_AUDIT' AND action != 'GET_REPAIR'
                                 AND status = 'success' THEN size ELSE 0 END) as egress_bytes,
                        SUM(CASE WHEN action = 'GET_REPAIR' AND status = 'success'
                                 THEN size ELSE 0 END) as repair_bytes,
                        SUM(CASE WHEN action = 'GET_AUDIT' AND status = 'success'
                                 THEN size ELSE 0 END) as audit_bytes
                    FROM events
                    WHERE node_name = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                """
                
                traffic_result = cursor.execute(
                    traffic_query,
                    (self.node_name, period_start_iso, period_end_iso)
                ).fetchone()
                
                egress_bytes = traffic_result['egress_bytes'] or 0
                repair_bytes = traffic_result['repair_bytes'] or 0
                audit_bytes = traffic_result['audit_bytes'] or 0
                
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
                
                # Calculate storage earnings
                storage_query = """
                    SELECT timestamp, used_bytes
                    FROM storage_snapshots
                    WHERE node_name = ?
                        AND timestamp >= ?
                        AND timestamp < ?
                        AND used_bytes IS NOT NULL
                    ORDER BY timestamp ASC
                """
                
                snapshots = cursor.execute(
                    storage_query,
                    (self.node_name, period_start_iso, period_end_iso)
                ).fetchall()
                
                storage_bytes_hour = 0
                if snapshots:
                    # Calculate byte-hours
                    for i in range(len(snapshots) - 1):
                        t1 = datetime.datetime.fromisoformat(snapshots[i]['timestamp'])
                        t2 = datetime.datetime.fromisoformat(snapshots[i + 1]['timestamp'])
                        hours_diff = (t2 - t1).total_seconds() / 3600
                        avg_bytes = (snapshots[i]['used_bytes'] + snapshots[i + 1]['used_bytes']) / 2
                        storage_bytes_hour += avg_bytes * hours_diff
                    
                    # Project to now (not month end, for current earnings)
                    last_snapshot = snapshots[-1]
                    last_time = datetime.datetime.fromisoformat(last_snapshot['timestamp'])
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if last_time < now and now <= period_end:
                        hours_since_last = (now - last_time).total_seconds() / 3600
                        storage_bytes_hour += last_snapshot['used_bytes'] * hours_since_last
                
                gb_hours = storage_bytes_hour / (1024 ** 3)
                hours_in_month = days_in_month * 24
                tb_months = gb_hours / (1024 * hours_in_month)
                storage_gross = tb_months * PRICING_STORAGE_PER_TB_MONTH
                storage_net = storage_gross * OPERATOR_SHARE_STORAGE
                
                return {
                    'egress_bytes': egress_bytes,
                    'egress_net': egress_net,
                    'repair_bytes': repair_bytes,
                    'repair_net': repair_net,
                    'audit_bytes': audit_bytes,
                    'audit_net': audit_net,
                    'storage_bytes_hour': int(storage_bytes_hour),
                    'storage_net': storage_net
                }
                
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to calculate breakdown from DB: {e}")
            return {
                'egress_bytes': 0, 'egress_net': 0,
                'repair_bytes': 0, 'repair_net': 0,
                'audit_bytes': 0, 'audit_net': 0,
                'storage_bytes_hour': 0, 'storage_net': 0
            }
    
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
                
                # Project from last snapshot to NOW (not to end of period)
                # This gives us current accumulated earnings, not month-end projection
                if snapshots:
                    last_snapshot = snapshots[-1]
                    last_time = datetime.datetime.fromisoformat(last_snapshot['timestamp'])
                    now = datetime.datetime.now(datetime.timezone.utc)
                    # Only project to now if we're still in the current period
                    if last_time < now and now <= period_end:
                        hours_since_last = (now - last_time).total_seconds() / 3600
                        total_byte_hours += last_snapshot['used_bytes'] * hours_since_last
                
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

    def _blocking_delete_current_month_estimates(self, db_path: str, period: str):
        """Deletes all earnings estimates for the current month to prevent duplicates."""
        import sqlite3
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM earnings_estimates WHERE node_name = ? AND period = ?",
                    (self.node_name, period)
                )
                log.info(f"[{self.node_name}] Deleted old estimates for {period} to replace with API data")
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to delete current month estimates: {e}")
    
    async def forecast_payout(
        self,
        db_path: str,
        period: Optional[str] = None,
        loop = None,
        executor = None
    ) -> Dict[str, Any]:
        """
        Forecast month-end payout with confidence score.
        
        Extrapolates current month earnings to project end-of-month payout based on
        progress through the month. For past months, returns actual earnings.
        
        Args:
            db_path: Path to database
            period: Optional period in YYYY-MM format (defaults to current month)
            loop: Event loop for executor (optional)
            executor: Thread pool executor for database operations (optional)
        
        Returns:
            Dict with forecast data including confidence score and extrapolation details
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
        
        # Calculate total across all satellites (current accumulated earnings)
        total_current = sum(est['total_earnings_net'] for est in estimates)
        total_held = sum(est['held_amount'] for est in estimates)
        
        # Calculate confidence score and extrapolation based on time progress
        year, month = map(int, period.split('-'))
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Default: no extrapolation (for past/future months)
        total_forecast = total_current
        extrapolation_factor = 1.0
        
        if year == now.year and month == now.month:
            # Current month - calculate progress and extrapolate
            import calendar
            days_in_month = calendar.monthrange(year, month)[1]
            days_elapsed = now.day
            progress = days_elapsed / days_in_month
            
            # Extrapolate to end of month if we have meaningful progress
            if progress > 0.05:  # At least 5% through month (1.5 days)
                extrapolation_factor = 1.0 / progress
                total_forecast = total_current * extrapolation_factor
                
                log.info(
                    f"[{self.node_name}] Extrapolating forecast: ${total_current:.2f} "
                    f"(day {days_elapsed}/{days_in_month}) × {extrapolation_factor:.2f} = "
                    f"${total_forecast:.2f}"
                )
            
            # Confidence increases with progress through the month
            # 50% at start, 100% at end
            time_confidence = 0.5 + (progress * 0.5)
        else:
            # Past month: high confidence, no extrapolation needed
            # Future month: low confidence
            time_confidence = 1.0 if year < now.year or (year == now.year and month < now.month) else 0.3
        
        # Data confidence based on having storage data
        has_storage_data = any(est['storage_bytes_hour'] > 0 for est in estimates)
        data_confidence = 1.0 if has_storage_data else 0.7
        
        # Overall confidence
        confidence = time_confidence * data_confidence
        
        log.info(
            f"[{self.node_name}] Payout forecast for {period}: "
            f"${total_forecast:.4f} net (${total_held * extrapolation_factor:.4f} held), "
            f"confidence={confidence:.1%}"
        )
        
        return {
            'period': period,
            'forecasted_payout': total_forecast,
            'forecasted_payout_before_held': total_forecast + (total_held * extrapolation_factor),
            'held_amount': total_held * extrapolation_factor,
            'current_earnings': total_current,  # Actual accumulated so far
            'extrapolation_factor': extrapolation_factor,
            'confidence': confidence,
            'time_confidence': time_confidence,
            'data_confidence': data_confidence,
            'satellites': len(estimates)
        }
    
    async def import_historical_payouts(self, db_path: str, loop, executor=None):
        """
        Import historical payout data from API to populate earnings history.
        
        Iterates through periods from 2022-01 to current month, querying paystubs
        for each period and storing them as earnings estimates.
        
        Args:
            db_path: Path to database
            loop: Event loop for executor
            executor: Thread pool executor for database operations
        """
        if not self.api_client or not self.api_client.is_available:
            log.debug(f"[{self.node_name}] API not available for historical import")
            return
        
        try:
            imported_count = 0
            now = datetime.datetime.now(datetime.timezone.utc)
            current_year = now.year
            current_month = now.month
            
            # Start from 2022-01 (reasonable start for most nodes)
            # Could be optimized to use node start date, but this is simpler
            start_year = 2022
            start_month = 1
            
            year = start_year
            month = start_month
            
            log.info(f"[{self.node_name}] Importing historical payouts from {year}-{month:02d} to {current_year}-{current_month:02d}")
            
            while year < current_year or (year == current_year and month <= current_month):
                period = f"{year}-{month:02d}"
                
                try:
                    # Query paystubs for this period
                    # API endpoint: /api/heldamount/paystubs/{period}/{period}
                    # Returns: list of paystubs, one per satellite, or None if no data
                    paystubs = await self.api_client.get_payout_paystubs(period)
                    
                    if paystubs and isinstance(paystubs, list) and len(paystubs) > 0:
                        # Process each satellite's paystub
                        for stub in paystubs:
                            if not isinstance(stub, dict):
                                continue
                            
                            # Note: API returns 'satelliteId' (camelCase), not 'satelliteID'
                            satellite_id = stub.get('satelliteId')
                            paid_microdollars = stub.get('paid', 0)
                            held_microdollars = stub.get('held', 0)
                            
                            if not satellite_id:
                                continue
                            
                            # Convert from micro-dollars to dollars
                            # Example: paid: 59246 = $0.059246
                            paid_dollars = paid_microdollars / 1_000_000
                            held_dollars = held_microdollars / 1_000_000
                            
                            if paid_dollars > 0 or held_dollars > 0:
                                estimate = {
                                    'timestamp': datetime.datetime.now(datetime.timezone.utc),
                                    'node_name': self.node_name,
                                    'satellite': satellite_id,
                                    'period': period,
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
                                    'total_earnings_gross': paid_dollars + held_dollars,
                                    'total_earnings_net': paid_dollars,
                                    'held_amount': held_dollars,
                                    'node_age_months': 0,  # Unknown for historical
                                    'held_percentage': held_dollars / (paid_dollars + held_dollars) if (paid_dollars + held_dollars) > 0 else 0
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
                        
                        if len(paystubs) > 0:
                            log.debug(f"[{self.node_name}] Imported {len(paystubs)} paystubs for {period}")
                    
                except Exception as e:
                    # Log but continue with next period
                    log.debug(f"[{self.node_name}] Could not fetch paystubs for {period}: {e}")
                
                # Move to next month
                month += 1
                if month > 12:
                    month = 1
                    year += 1
            
            if imported_count > 0:
                log.info(f"[{self.node_name}] Successfully imported {imported_count} historical payout records")
            else:
                log.info(f"[{self.node_name}] No historical payouts found (node may be new)")
                
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
                        # Don't truncate special 'aggregate' satellite name
                        if estimate['satellite'] == 'aggregate':
                            sat_name = 'Aggregate'
                        else:
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
    
    # Attempt to import historical payouts (will gracefully skip if endpoint unavailable)
    log.info("Attempting to import historical payout data from node APIs...")
    for node_name, tracker in app['financial_trackers'].items():
        try:
            await tracker.import_historical_payouts(
                DATABASE_FILE,
                asyncio.get_running_loop(),
                app.get('db_executor')
            )
        except Exception as e:
            log.debug(f"[{node_name}] Historical import skipped: {e}")

    # Initial poll
    loop = asyncio.get_running_loop()
    await broadcast_earnings_update(app, loop)
    last_historical_import_day = datetime.datetime.now(datetime.timezone.utc).day
    
    while True:
        try:
            # Check if we should run daily historical import (catches new paystubs like Sept reported in Oct)
            now = datetime.datetime.now(datetime.timezone.utc)
            if now.day != last_historical_import_day:
                log.info("Running daily historical paystub check...")
                for node_name, tracker in app['financial_trackers'].items():
                    try:
                        await tracker.import_historical_payouts(
                            DATABASE_FILE,
                            loop,
                            app.get('db_executor')
                        )
                    except Exception as e:
                        log.debug(f"[{node_name}] Historical import check skipped: {e}")
                last_historical_import_day = now.day
            
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
            # Don't truncate special 'aggregate' satellite name
            if estimate['satellite'] == 'aggregate':
                sat_name = 'Aggregate'
            else:
                sat_name = SATELLITE_NAMES.get(estimate['satellite'], estimate['satellite'][:8])
            
            breakdown_data = {
                'egress': round(estimate['egress_earnings_net'], 2),
                'storage': round(estimate['storage_earnings_net'], 2),
                'repair': round(estimate['repair_earnings_net'], 2),
                'audit': round(estimate['audit_earnings_net'], 2)
            }
            
            formatted_item = {
                'node_name': estimate['node_name'],
                'satellite': sat_name,
                'total_net': round(estimate['total_earnings_net'], 2),
                'total_gross': round(estimate['total_earnings_gross'], 2),
                'held_amount': round(estimate['held_amount'], 2),
                'breakdown': breakdown_data,
                'forecast_month_end': round(forecast_info['forecasted_payout'], 2) if forecast_info else None,
                'confidence': round(forecast_info['confidence'], 2) if forecast_info else None
            }
            
            # Debug log breakdown values
            breakdown_sum = sum(breakdown_data.values())
            if breakdown_sum == 0 and formatted_item['total_net'] > 0:
                log.warning(
                    f"[{estimate['node_name']}] Breakdown is zero but total_net is ${formatted_item['total_net']:.2f} "
                    f"for satellite {sat_name}"
                )
            
            formatted_data.append(formatted_item)
        
        # Broadcast to all clients
        payload = {
            'type': 'earnings_data',
            'data': formatted_data
        }
        
        # Store in cache before broadcasting (include period in cache key)
        if 'earnings_cache' not in app_state:
            app_state['earnings_cache'] = {}
        
        # Cache per-node and aggregate views with period
        nodes_in_payload = {item['node_name'] for item in formatted_data}
        for node_name in nodes_in_payload:
            node_payload = {
                'type': 'earnings_data',
                'data': [item for item in formatted_data if item['node_name'] == node_name]
            }
            app_state['earnings_cache'][(node_name, period)] = node_payload
            
        app_state['earnings_cache'][('Aggregate', period)] = payload

        await robust_broadcast(app_state['websockets'], payload)
        log.debug(f"Broadcast earnings update with {len(formatted_data)} estimates")
        
    except Exception as e:
        log.error(f"Failed to broadcast earnings update: {e}", exc_info=True)