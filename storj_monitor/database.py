import sqlite3
import logging
import datetime
from typing import List, Dict, Any, Optional
import json
from .config import (DATABASE_FILE, HISTORICAL_HOURS_TO_SHOW, DB_CONNECTION_TIMEOUT,
                     DB_MAX_RETRIES, DB_RETRY_BASE_DELAY, DB_RETRY_MAX_DELAY)
from .db_utils import retry_on_db_lock, get_optimized_connection

log = logging.getLogger("StorjMonitor.Database")


def init_db():
    log.info("Connecting to database and checking schema...")
    conn = get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT)
    cursor = conn.cursor()

    # Enable Write-Ahead Logging (WAL) mode for better concurrency. This is a persistent setting.
    cursor.execute('PRAGMA journal_mode=WAL;')
    cursor.execute('PRAGMA journal_mode;')
    mode = cursor.fetchone()
    if mode and mode[0].lower() == 'wal':
        log.info("Database journal mode is set to WAL.")
    else:
        log.warning(f"Failed to set database journal mode to WAL. Current mode: {mode[0] if mode else 'unknown'}")

    log.info("Performing one-time database schema validation and upgrades. This may take a long time on large databases...")

    # --- Hashstore Compaction History Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hashstore_compaction_history (
            node_name TEXT NOT NULL,
            satellite TEXT NOT NULL,
            store TEXT NOT NULL,
            last_run_iso TEXT NOT NULL,
            duration REAL,
            data_reclaimed_bytes INTEGER,
            data_rewritten_bytes INTEGER,
            table_load REAL,
            trash_percent REAL,
            PRIMARY KEY (node_name, satellite, store, last_run_iso)
        )
    ''')

    # --- Schema migration for events table ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(events);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'node_name' not in columns:
            log.info("Upgrading 'events' table: Adding 'node_name' column. Please wait...")
            cursor.execute("ALTER TABLE events ADD COLUMN node_name TEXT;")
            cursor.execute("UPDATE events SET node_name = 'default' WHERE node_name IS NULL;")
            log.info("'events' table upgrade complete.")
        
        # Phase 2.1: Add duration_ms column for latency analytics
        if 'duration_ms' not in columns:
            log.info("Upgrading 'events' table: Adding 'duration_ms' column for latency tracking...")
            cursor.execute("ALTER TABLE events ADD COLUMN duration_ms INTEGER;")
            log.info("'duration_ms' column added successfully.")

    cursor.execute(
        'CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, piece_id TEXT, satellite_id TEXT, remote_ip TEXT, country TEXT, latitude REAL, longitude REAL, error_reason TEXT, node_name TEXT, duration_ms INTEGER)')

    # --- Schema migration for hourly_stats table ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hourly_stats';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(hourly_stats);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'node_name' not in columns:
            log.info("Upgrading 'hourly_stats' table. Recreating with new composite primary key.")
            cursor.execute("PRAGMA table_info(hourly_stats);")
            old_columns = [col[1] for col in cursor.fetchall()]
            select_columns = ['hour_timestamp', "'default' as node_name", 'dl_success', 'dl_fail', 'ul_success',
                              'ul_fail', 'audit_success', 'audit_fail']
            if 'total_download_size' in old_columns:
                select_columns.append('total_download_size')
            else:
                select_columns.append('0 as total_download_size')
            if 'total_upload_size' in old_columns:
                select_columns.append('total_upload_size')
            else:
                select_columns.append('0 as total_upload_size')

            cursor.execute("ALTER TABLE hourly_stats RENAME TO hourly_stats_old;")
            cursor.execute(
                'CREATE TABLE hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
            select_query = f"SELECT {', '.join(select_columns)} FROM hourly_stats_old"
            cursor.execute(
                f"INSERT INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size) {select_query}")
            cursor.execute("DROP TABLE hourly_stats_old;")
            log.info("'hourly_stats' table upgrade complete.")
        else:
            if 'total_download_size' not in columns: cursor.execute(
                "ALTER TABLE hourly_stats ADD COLUMN total_download_size INTEGER DEFAULT 0;")
            if 'total_upload_size' not in columns: cursor.execute(
                "ALTER TABLE hourly_stats ADD COLUMN total_upload_size INTEGER DEFAULT 0;")

    cursor.execute(
        'CREATE TABLE IF NOT EXISTS hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_node_name ON events (node_name);')
    cursor.execute('CREATE TABLE IF NOT EXISTS app_persistent_state (key TEXT PRIMARY KEY, value TEXT)')

    # Add a composite index to optimize the hourly aggregation query
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_events_node_name_timestamp'")
    if not cursor.fetchone():
        log.info(
            "Creating composite index for performance. This may take a very long time on large databases. Please wait...")
        cursor.execute('CREATE INDEX idx_events_node_name_timestamp ON events (node_name, timestamp);')
        log.info("Index creation complete.")

    # --- Reputation History Table (Phase 1.3) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reputation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            satellite TEXT NOT NULL,
            audit_score REAL,
            suspension_score REAL,
            online_score REAL,
            audit_success_count INTEGER,
            audit_total_count INTEGER,
            is_disqualified INTEGER DEFAULT 0,
            is_suspended INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_reputation_node_time ON reputation_history (node_name, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_reputation_satellite ON reputation_history (satellite);')
    
    # --- Storage Snapshots Table (Phase 2.2) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS storage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            total_bytes INTEGER,
            used_bytes INTEGER,
            available_bytes INTEGER,
            trash_bytes INTEGER,
            used_percent REAL,
            trash_percent REAL,
            available_percent REAL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_storage_node_time ON storage_snapshots (node_name, timestamp);')
    
    # --- Alerts Table (Phase 4) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0,
            acknowledged_at DATETIME,
            resolved INTEGER DEFAULT 0,
            resolved_at DATETIME,
            metadata TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_node_time ON alerts (node_name, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts (acknowledged, resolved, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts (severity, timestamp);')
    
    # --- Insights Table (Phase 4) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            insight_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT,
            confidence REAL,
            acknowledged INTEGER DEFAULT 0,
            metadata TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_insights_node_time ON insights (node_name, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_insights_type ON insights (insight_type, timestamp);')
    
    # --- Analytics Baselines Table (Phase 4) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics_baselines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_name TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            window_hours INTEGER NOT NULL,
            mean_value REAL,
            std_dev REAL,
            min_value REAL,
            max_value REAL,
            sample_count INTEGER,
            last_updated DATETIME NOT NULL,
            UNIQUE(node_name, metric_name, window_hours)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_baselines_node_metric ON analytics_baselines (node_name, metric_name);')
    
    # --- Earnings Estimates Table (Phase 5) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS earnings_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            satellite TEXT NOT NULL,
            period TEXT NOT NULL,
            egress_bytes INTEGER,
            egress_earnings_gross REAL,
            egress_earnings_net REAL,
            storage_bytes_hour INTEGER,
            storage_earnings_gross REAL,
            storage_earnings_net REAL,
            repair_bytes INTEGER,
            repair_earnings_gross REAL,
            repair_earnings_net REAL,
            audit_bytes INTEGER,
            audit_earnings_gross REAL,
            audit_earnings_net REAL,
            total_earnings_gross REAL,
            total_earnings_net REAL,
            held_amount REAL,
            node_age_months INTEGER,
            held_percentage REAL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_earnings_node_time ON earnings_estimates (node_name, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_earnings_satellite ON earnings_estimates (satellite);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_earnings_period ON earnings_estimates (period, timestamp);')
    
    # --- Payout History Table (Phase 5) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payout_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            node_name TEXT NOT NULL,
            satellite TEXT NOT NULL,
            period TEXT NOT NULL,
            actual_payout REAL NOT NULL,
            estimated_payout REAL,
            variance REAL,
            variance_percent REAL,
            payout_address TEXT,
            transaction_hash TEXT,
            notes TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_payout_node_time ON payout_history (node_name, timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_payout_satellite ON payout_history (satellite);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_payout_period ON payout_history (period);')
    
    conn.commit()
    conn.close()
    log.info("Database schema is valid and ready.")


@retry_on_db_lock(max_attempts=DB_MAX_RETRIES, base_delay=DB_RETRY_BASE_DELAY, max_delay=DB_RETRY_MAX_DELAY)
def blocking_write_hashstore_log(db_path: str, stats_dict: dict) -> bool:
    """Writes a single hashstore compaction event to the database. Returns True on success."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO hashstore_compaction_history
                (node_name, satellite, store, last_run_iso, duration, data_reclaimed_bytes, data_rewritten_bytes, table_load, trash_percent)
                VALUES (:node_name, :satellite, :store, :last_run_iso, :duration, :data_reclaimed_bytes, :data_rewritten_bytes, :table_load, :trash_percent)
            ''', stats_dict)
            conn.commit()
        log.info(f"Successfully wrote hashstore log for {stats_dict['node_name']}:{stats_dict['satellite']}:{stats_dict['store']}.")
        return True
    except Exception:
        log.error("Failed to write hashstore log to DB:", exc_info=True)
        return False


@retry_on_db_lock(max_attempts=DB_MAX_RETRIES, base_delay=DB_RETRY_BASE_DELAY, max_delay=DB_RETRY_MAX_DELAY)
def blocking_db_batch_write(db_path: str, events: list):
    """Optimized batch write with pre-allocated tuple creation."""
    if not events: return

    # Pre-allocate list for better performance
    data_to_insert = []
    data_to_insert_extend = data_to_insert.append  # Cache method reference

    # Build tuples more efficiently
    for e in events:
        loc = e['location']
        data_to_insert_extend((
            e['timestamp'].isoformat(), e['action'], e['status'], e['size'],
            e['piece_id'], e['satellite_id'], e['remote_ip'],
            loc['country'], loc['lat'], loc['lon'],
            e['error_reason'], e['node_name'], e.get('duration_ms')  # Phase 2.1: Include duration
        ))

    with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
        cursor = conn.cursor()
        cursor.executemany('INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', data_to_insert)
        conn.commit()
    log.info(f"Successfully wrote {len(events)} events to the database.")


def blocking_hourly_aggregation(node_names: List[str]):
    log.info("[AGGREGATOR] Running hourly aggregation.")
    now = datetime.datetime.now(datetime.timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_start_iso = hour_start.isoformat()
    next_hour_start_iso = (hour_start + datetime.timedelta(hours=1)).isoformat()

    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row
        for node_name in node_names:
            query = """
                SELECT
                    SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as dl_s,
                    SUM(CASE WHEN action LIKE '%GET%' AND status != 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as dl_f,
                    SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN 1 ELSE 0 END) as ul_s,
                    SUM(CASE WHEN action LIKE '%PUT%' AND status != 'success' THEN 1 ELSE 0 END) as ul_f,
                    SUM(CASE WHEN action = 'GET_AUDIT' AND status = 'success' THEN 1 ELSE 0 END) as audit_s,
                    SUM(CASE WHEN action = 'GET_AUDIT' AND status != 'success' THEN 1 ELSE 0 END) as audit_f,
                    SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN size ELSE 0 END) as total_dl_size,
                    SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN size ELSE 0 END) as total_ul_size
                FROM events WHERE node_name = ? AND timestamp >= ? AND timestamp < ?
            """
            stats = conn.execute(query, (node_name, hour_start_iso, next_hour_start_iso)).fetchone()

            if stats and stats['dl_s'] is not None:
                conn.execute("""
                    INSERT INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hour_timestamp, node_name) DO UPDATE SET
                        dl_success=excluded.dl_success, dl_fail=excluded.dl_fail,
                        ul_success=excluded.ul_success, ul_fail=excluded.ul_fail,
                        audit_success=excluded.audit_success, audit_fail=excluded.audit_fail,
                        total_download_size=excluded.total_download_size, total_upload_size=excluded.total_upload_size
                """, (
                hour_start_iso, node_name, stats['dl_s'], stats['dl_f'], stats['ul_s'], stats['ul_f'], stats['audit_s'],
                stats['audit_f'], stats['total_dl_size'], stats['total_ul_size']))
                conn.commit()
                log.info(f"[AGGREGATOR] Wrote hourly stats for node '{node_name}' at {hour_start_iso}.")


def blocking_db_prune(db_path, events_retention_days, hashstore_retention_days,
                     earnings_retention_days=None, alerts_retention_days=None,
                     insights_retention_days=None, analytics_retention_days=None):
    """
    Prune old data from the database based on retention policies.
    
    Args:
        db_path: Path to database file
        events_retention_days: Days to retain event data
        hashstore_retention_days: Days to retain hashstore compaction history
        earnings_retention_days: Days to retain earnings estimates (defaults to config)
        alerts_retention_days: Days to retain alerts (defaults to config)
        insights_retention_days: Days to retain insights (defaults to config)
        analytics_retention_days: Days to retain analytics baselines (defaults to config)
    """
    # Import retention settings from config
    from .config import (DB_EARNINGS_RETENTION_DAYS, DB_ALERTS_RETENTION_DAYS,
                        DB_INSIGHTS_RETENTION_DAYS, DB_ANALYTICS_RETENTION_DAYS)
    
    # Use config defaults if not specified
    earnings_retention_days = earnings_retention_days or DB_EARNINGS_RETENTION_DAYS
    alerts_retention_days = alerts_retention_days or DB_ALERTS_RETENTION_DAYS
    insights_retention_days = insights_retention_days or DB_INSIGHTS_RETENTION_DAYS
    analytics_retention_days = analytics_retention_days or DB_ANALYTICS_RETENTION_DAYS
    
    log.info(
        f"[DB_PRUNER] Starting database pruning task. Retention: events={events_retention_days}d, "
        f"hashstore={hashstore_retention_days}d, earnings={earnings_retention_days}d, "
        f"alerts={alerts_retention_days}d, insights={insights_retention_days}d, analytics={analytics_retention_days}d")

    with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
        cursor = conn.cursor()

        # Prune events table
        events_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=events_retention_days)
        events_cutoff_iso = events_cutoff_date.isoformat()
        log.info(f"Finding events older than {events_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM events WHERE timestamp < ?", (events_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old event(s) from the database. This might take a while...")
            cursor.execute("DELETE FROM events WHERE timestamp < ?", (events_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old event(s) from the database.")
        else:
            log.info("No old events found to prune.")

        # Prune hashstore_compaction_history table
        hashstore_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=hashstore_retention_days)
        hashstore_cutoff_iso = hashstore_cutoff_date.isoformat()
        log.info(f"Finding hashstore history older than {hashstore_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM hashstore_compaction_history WHERE last_run_iso < ?",
                       (hashstore_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old hashstore compaction record(s) from the database...")
            cursor.execute("DELETE FROM hashstore_compaction_history WHERE last_run_iso < ?", (hashstore_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old hashstore compaction record(s).")
        else:
            log.info("No old hashstore compaction records found to prune.")
        
        # Prune earnings_estimates table (Phase 5)
        earnings_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=earnings_retention_days)
        earnings_cutoff_iso = earnings_cutoff_date.isoformat()
        log.info(f"Finding earnings estimates older than {earnings_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM earnings_estimates WHERE timestamp < ?", (earnings_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old earnings estimate(s) from the database...")
            cursor.execute("DELETE FROM earnings_estimates WHERE timestamp < ?", (earnings_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old earnings estimate(s).")
        else:
            log.info("No old earnings estimates found to prune.")
        
        # Prune alerts table (Phase 4)
        alerts_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=alerts_retention_days)
        alerts_cutoff_iso = alerts_cutoff_date.isoformat()
        log.info(f"Finding alerts older than {alerts_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE timestamp < ?", (alerts_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old alert(s) from the database...")
            cursor.execute("DELETE FROM alerts WHERE timestamp < ?", (alerts_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old alert(s).")
        else:
            log.info("No old alerts found to prune.")
        
        # Prune insights table (Phase 4)
        insights_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=insights_retention_days)
        insights_cutoff_iso = insights_cutoff_date.isoformat()
        log.info(f"Finding insights older than {insights_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM insights WHERE timestamp < ?", (insights_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old insight(s) from the database...")
            cursor.execute("DELETE FROM insights WHERE timestamp < ?", (insights_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old insight(s).")
        else:
            log.info("No old insights found to prune.")
        
        # Prune analytics_baselines table (Phase 4) - based on last_updated
        analytics_cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=analytics_retention_days)
        analytics_cutoff_iso = analytics_cutoff_date.isoformat()
        log.info(f"Finding analytics baselines older than {analytics_cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM analytics_baselines WHERE last_updated < ?", (analytics_cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old analytics baseline(s) from the database...")
            cursor.execute("DELETE FROM analytics_baselines WHERE last_updated < ?", (analytics_cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old analytics baseline(s).")
        else:
            log.info("No old analytics baselines found to prune.")


@retry_on_db_lock(max_attempts=DB_MAX_RETRIES, base_delay=DB_RETRY_BASE_DELAY, max_delay=DB_RETRY_MAX_DELAY)
def get_historical_stats(view: List[str], all_nodes_state: Dict[str, Any]) -> List[Dict]:
    """Fetch historical stats from the database."""
    hist_stats = []
    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row

        nodes_for_hist = view
        if view == ['Aggregate']:
            nodes_for_hist = list(all_nodes_state.keys())

        if nodes_for_hist:
            placeholders = ','.join('?' for _ in nodes_for_hist)
            raw_hist_stats = conn.execute(f"""
                SELECT hour_timestamp,
                       SUM(dl_success) as dl_success, SUM(dl_fail) as dl_fail,
                       SUM(ul_success) as ul_success, SUM(ul_fail) as ul_fail,
                       SUM(audit_success) as audit_success, SUM(audit_fail) as audit_fail,
                       SUM(total_download_size) as total_download_size, SUM(total_upload_size) as total_upload_size
                FROM hourly_stats WHERE node_name IN ({placeholders})
                GROUP BY hour_timestamp ORDER BY hour_timestamp DESC LIMIT ?
            """, (*nodes_for_hist, HISTORICAL_HOURS_TO_SHOW)).fetchall()
        else:
            raw_hist_stats = []

        for row in raw_hist_stats:
            d_row = dict(row)
            d_row['dl_mbps'] = ((d_row.get('total_download_size', 0) or 0) * 8) / (3600 * 1000000)
            d_row['ul_mbps'] = ((d_row.get('total_upload_size', 0) or 0) * 8) / (3600 * 1000000)
            hist_stats.append(d_row)

    return hist_stats


def blocking_get_historical_performance(events: List[Dict[str, Any]], points: int, interval_sec: int) -> List[
    Dict[str, Any]]:
    import time
    log.info(f"Processing {len(events)} in-memory events for historical performance graph generation.")

    now_unix = time.time()
    time_window_seconds = points * interval_sec
    cutoff_unix = now_unix - time_window_seconds

    # To prevent a race condition where the historical data includes a partial, final bin,
    # we will only process data up to the last *full* bin. The live data stream will provide
    # the subsequent bin, ensuring a clean handoff.
    last_full_bin_unix = (int(now_unix / interval_sec) - 1) * interval_sec

    if not events:
        return _zero_fill_performance_data([], cutoff_unix, last_full_bin_unix, interval_sec)

    # Filter events to only include those that fall within the full bins we are considering.
    recent_events = [e for e in events if
                     e.get('ts_unix', 0) >= cutoff_unix and e.get('ts_unix', 0) < last_full_bin_unix + interval_sec]
    if not recent_events:
        return _zero_fill_performance_data([], cutoff_unix, last_full_bin_unix, interval_sec)

    buckets: Dict[int, Dict[str, int]] = {}
    for event in recent_events:
        ts_unix = event['ts_unix']
        bucket_start_unix = int(ts_unix / interval_sec) * interval_sec
        if bucket_start_unix not in buckets:
            buckets[bucket_start_unix] = {'ingress_bytes': 0, 'egress_bytes': 0, 'ingress_pieces': 0,
                                          'egress_pieces': 0, 'total_ops': 0}

        bucket = buckets[bucket_start_unix]
        bucket['total_ops'] += 1
        if event.get('status') == 'success':
            category, size = event.get('category'), event.get('size', 0)
            if category == 'get':
                bucket['egress_bytes'] += size
                bucket['egress_pieces'] += 1
            elif category == 'put':
                bucket['ingress_bytes'] += size
                bucket['ingress_pieces'] += 1

    sparse_results = []
    for ts_unix, data in buckets.items():
        sparse_results.append({
            "timestamp": datetime.datetime.fromtimestamp(ts_unix, tz=datetime.timezone.utc).isoformat(),
            "ingress_mbps": round((data['ingress_bytes'] * 8) / (interval_sec * 1e6), 2),
            "egress_mbps": round((data['egress_bytes'] * 8) / (interval_sec * 1e6), 2),
            "ingress_bytes": data['ingress_bytes'], "egress_bytes": data['egress_bytes'],
            "ingress_pieces": data['ingress_pieces'], "egress_pieces": data['egress_pieces'],
            "concurrency": round(data['total_ops'] / interval_sec, 2) if interval_sec > 0 else 0,
            "total_ops": data['total_ops'], "bin_duration_seconds": interval_sec
        })

    filled_results = _zero_fill_performance_data(sparse_results, cutoff_unix, last_full_bin_unix, interval_sec)
    log.info(f"Returning {len(filled_results)} historical performance data points from in-memory events.")
    return filled_results


def _zero_fill_performance_data(sparse_data: List[Dict], start_unix: float, end_unix: float, interval_sec: int) -> List[
    Dict]:
    """Fills in missing time buckets with zero values."""
    start_bucket_unix = int(start_unix / interval_sec) * interval_sec
    end_bucket_unix = int(end_unix / interval_sec) * interval_sec

    results_map = {
        int(datetime.datetime.fromisoformat(r['timestamp']).timestamp() / interval_sec) * interval_sec: r
        for r in sparse_data
    }

    filled_results = []
    current_bucket_unix = start_bucket_unix
    while current_bucket_unix <= end_bucket_unix:
        if current_bucket_unix in results_map:
            filled_results.append(results_map[current_bucket_unix])
        else:
            filled_results.append({
                "timestamp": datetime.datetime.fromtimestamp(current_bucket_unix, tz=datetime.timezone.utc).isoformat(),
                "ingress_mbps": 0, "egress_mbps": 0,
                "ingress_bytes": 0, "egress_bytes": 0,
                "ingress_pieces": 0, "egress_pieces": 0,
                "concurrency": 0, "total_ops": 0,
                "bin_duration_seconds": interval_sec
            })
        current_bucket_unix += interval_sec
    return filled_results


def blocking_get_aggregated_performance(node_names: List[str], time_window_hours: int) -> List[Dict[str, Any]]:
    if not node_names: return []
    log.info(f"Fetching AGGREGATED performance for nodes {node_names} (last {time_window_hours} hours).")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    start_time = now_dt - datetime.timedelta(hours=time_window_hours)
    start_time_iso = start_time.isoformat()
    placeholders = ','.join('?' * len(node_names))

    if time_window_hours <= 1:
        bin_size_min = 2
    elif time_window_hours <= 6:
        bin_size_min = 10
    else:
        bin_size_min = 30
    bin_sec = bin_size_min * 60

    sparse_results = []
    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if time_window_hours > 6:
            query = f"""
                SELECT hour_timestamp as time_bucket_start_iso,
                       SUM(total_upload_size) as ingress_bytes,
                       SUM(total_download_size) as egress_bytes,
                       SUM(ul_success) as ingress_pieces, SUM(dl_success) as egress_pieces,
                       SUM(dl_success + dl_fail + ul_success + ul_fail + audit_success + audit_fail) as total_ops
                FROM hourly_stats WHERE hour_timestamp >= ? AND node_name IN ({placeholders})
                GROUP BY hour_timestamp ORDER BY hour_timestamp ASC
            """
            params = [start_time_iso, *node_names]
            actual_bin_sec = 3600
        else:
            query = f"""
                SELECT (CAST(strftime('%s', timestamp) AS INTEGER) / ?) * ? as time_bucket_start,
                       SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN size ELSE 0 END) as ingress_bytes,
                       SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN size ELSE 0 END) as egress_bytes,
                       SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN 1 ELSE 0 END) as ingress_pieces,
                       SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as egress_pieces,
                       COUNT(*) as total_ops FROM events WHERE timestamp >= ? AND node_name IN ({placeholders})
                GROUP BY time_bucket_start ORDER BY time_bucket_start ASC
            """
            params = [bin_sec, bin_sec, start_time_iso, *node_names]
            actual_bin_sec = bin_sec

        for row in cursor.execute(query, params).fetchall():
            row_dict = dict(row)
            ts_unix = row_dict.get('time_bucket_start')
            iso_ts = row_dict.get('time_bucket_start_iso') or datetime.datetime.fromtimestamp(ts_unix,
                                                                                              tz=datetime.timezone.utc).isoformat()
            sparse_results.append({
                "timestamp": iso_ts,
                "ingress_mbps": round((row_dict.get('ingress_bytes', 0) * 8) / (actual_bin_sec * 1e6), 2),
                "egress_mbps": round((row_dict.get('egress_bytes', 0) * 8) / (actual_bin_sec * 1e6), 2),
                "ingress_bytes": row_dict.get('ingress_bytes', 0), "egress_bytes": row_dict.get('egress_bytes', 0),
                "ingress_pieces": row_dict.get('ingress_pieces', 0),
                "egress_pieces": row_dict.get('egress_pieces', 0),
                "concurrency": round(row_dict.get('total_ops', 0) / actual_bin_sec, 2) if actual_bin_sec > 0 else 0,
                "total_ops": row_dict.get('total_ops', 0), "bin_duration_seconds": actual_bin_sec
            })

    filled_results = _zero_fill_performance_data(sparse_results, start_time.timestamp(), now_dt.timestamp(),
                                                 actual_bin_sec)
    log.info(f"Returning {len(filled_results)} aggregated performance data points for nodes {node_names}.")
    return filled_results


def blocking_get_hashstore_stats(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .config import SATELLITE_NAMES
    log.info(f"Fetching hashstore stats with filters: {filters}")

    where_clauses = []
    params = []

    node_filter = filters.get('node_name')
    if node_filter and node_filter != 'all':
        if isinstance(node_filter, list):
            if node_filter:
                placeholders = ','.join('?' for _ in node_filter)
                where_clauses.append(f"node_name IN ({placeholders})")
                params.extend(node_filter)
            else:  # handle empty list case to return no results
                where_clauses.append("1 = 0")
        else:  # It's a single string
            where_clauses.append("node_name = ?")
            params.append(node_filter)

    if filters.get('satellite') and filters['satellite'] != 'all':
        sat_ids = [k for k, v in SATELLITE_NAMES.items() if v == filters['satellite']]
        if sat_ids:
            where_clauses.append("satellite = ?")
            params.append(sat_ids[0])

    if filters.get('store') and filters['store'] != 'all':
        where_clauses.append("store = ?")
        params.append(filters['store'])

    query = "SELECT * FROM hashstore_compaction_history"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY last_run_iso DESC"

    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row
        results = [dict(row) for row in conn.execute(query, params).fetchall()]

    log.info(f"Found {len(results)} hashstore events matching filters.")
    return results


def blocking_backfill_hourly_stats(node_names: List[str]):
    log.info("[BACKFILL] Starting smart backfill of hourly statistics.")
    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find the last hour we have stats for.
        cursor.execute("SELECT MAX(hour_timestamp) FROM hourly_stats")
        result = cursor.fetchone()
        last_aggregated_hour = result[0] if result and result[0] else None

        start_from_iso = None

        if last_aggregated_hour:
            # Re-aggregate the last saved hour to catch any events that arrived after the last run.
            start_from_iso = last_aggregated_hour
            log.info(f"[BACKFILL] Last aggregated hour found: {start_from_iso}. Backfilling from this point.")
        else:
            # No stats yet, find the earliest event to start from.
            cursor.execute("SELECT MIN(timestamp) FROM events")
            result = cursor.fetchone()
            if not result or not result[0]:
                log.info("[BACKFILL] No events found in the database. Skipping backfill.")
                return

            # Truncate to the beginning of the hour for the very first run.
            first_event_dt = datetime.datetime.fromisoformat(result[0])
            start_from_dt = first_event_dt.replace(minute=0, second=0, microsecond=0)
            start_from_iso = start_from_dt.isoformat()
            log.info(f"[BACKFILL] No existing hourly stats. Backfilling from earliest event hour: {start_from_iso}")

        # This single aggregation query is much more efficient than iterating in Python.
        aggregation_query = """
            SELECT
                strftime('%Y-%m-%dT%H:00:00.000Z', timestamp) as hour_timestamp,
                node_name,
                SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as dl_s,
                SUM(CASE WHEN action LIKE '%GET%' AND status != 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as dl_f,
                SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN 1 ELSE 0 END) as ul_s,
                SUM(CASE WHEN action LIKE '%PUT%' AND status != 'success' THEN 1 ELSE 0 END) as ul_f,
                SUM(CASE WHEN action = 'GET_AUDIT' AND status = 'success' THEN 1 ELSE 0 END) as audit_s,
                SUM(CASE WHEN action = 'GET_AUDIT' AND status != 'success' THEN 1 ELSE 0 END) as audit_f,
                SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN size ELSE 0 END) as total_dl_size,
                SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN size ELSE 0 END) as total_ul_size
            FROM events
            WHERE timestamp >= ?
            GROUP BY hour_timestamp, node_name
        """
        log.info(f"[BACKFILL] Running aggregation query for events since {start_from_iso}...")
        cursor.execute(aggregation_query, (start_from_iso,))

        rows = cursor.fetchall()
        log.info(f"[BACKFILL] Aggregation query produced {len(rows)} hourly records to process.")

        if not rows:
            log.info("[BACKFILL] No new events to aggregate. Backfill complete.")
            return

        stats_to_insert = [
            (
                stats['hour_timestamp'], stats['node_name'],
                stats['dl_s'], stats['dl_f'], stats['ul_s'], stats['ul_f'],
                stats['audit_s'], stats['audit_f'],
                stats['total_dl_size'], stats['total_ul_size']
            )
            for stats in rows if stats['dl_s'] is not None
        ]

        if stats_to_insert:
            cursor.executemany("""
                INSERT OR REPLACE INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, stats_to_insert)
            log.info(f"[BACKFILL] Wrote/updated {cursor.rowcount} hourly stat records in the database.")

    log.info("[BACKFILL] Hourly statistics backfill complete.")


def load_initial_state_from_db(nodes_config: Dict[str, Dict[str, Any]]):
    from collections import deque
    from .config import STATS_WINDOW_MINUTES
    from .log_processor import categorize_action

    """Connects to the DB to re-hydrate the in-memory state on startup."""
    log.info("Attempting to load initial state from database...")
    initial_state = {}
    cutoff_datetime = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=STATS_WINDOW_MINUTES)

    with get_optimized_connection(DATABASE_FILE, timeout=DB_CONNECTION_TIMEOUT) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for node_name in nodes_config.keys():
            node_state = {
                'live_events': deque(),
                'active_compactions': {},
                'unprocessed_performance_events': [],
                'has_new_events': False
            }
            log.info(f"Re-hydrating live events for node '{node_name}' since {cutoff_datetime.isoformat()}")
            cursor.execute(
                "SELECT * FROM events WHERE node_name = ? AND timestamp >= ? ORDER BY timestamp ASC",
                (node_name, cutoff_datetime.isoformat())
            )
            rehydrated_events = 0
            for row in cursor.fetchall():
                try:
                    action = row['action']
                    timestamp_obj = datetime.datetime.fromisoformat(row['timestamp'])
                    event = {
                        "ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj,
                        "action": action, "status": row['status'], "size": row['size'],
                        "piece_id": row['piece_id'], "satellite_id": row['satellite_id'],
                        "remote_ip": row['remote_ip'],
                        "location": {"lat": row['latitude'], "lon": row['longitude'], "country": row['country']},
                        "error_reason": row['error_reason'], "node_name": row['node_name'],
                        "category": categorize_action(action)
                    }
                    node_state['live_events'].append(event)
                    rehydrated_events += 1
                except Exception:
                    log.error("Failed to process a database row for re-hydration.", exc_info=True)

            initial_state[node_name] = node_state
    return initial_state


def blocking_batch_write_hashstore_ingest(db_path: str, records: List[Dict]):
    if not records: return
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT OR IGNORE INTO hashstore_compaction_history
                (node_name, satellite, store, last_run_iso, duration, data_reclaimed_bytes, data_rewritten_bytes, table_load, trash_percent)
                VALUES (:node_name, :satellite, :store, :last_run_iso, :duration, :data_reclaimed_bytes, :data_rewritten_bytes, :table_load, :trash_percent)
            ''', records)
            conn.commit()
        log.info(f"Successfully ingested {len(records)} hashstore compaction records into the database.")
    except Exception:
        log.error("Failed to write ingested hashstore logs to DB:", exc_info=True)


# --- Reputation Tracking Functions (Phase 1.3) ---

def blocking_write_reputation_history(db_path: str, records: List[Dict]) -> bool:
    """
    Write reputation history records to database.
    
    Args:
        db_path: Path to database file
        records: List of reputation records
    
    Returns:
        True if successful
    """
    if not records:
        return False
    
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT INTO reputation_history
                (timestamp, node_name, satellite, audit_score, suspension_score, online_score,
                 audit_success_count, audit_total_count, is_disqualified, is_suspended)
                VALUES (:timestamp, :node_name, :satellite, :audit_score, :suspension_score, :online_score,
                        :audit_success_count, :audit_total_count, :is_disqualified, :is_suspended)
            ''', [
                {
                    'timestamp': r['timestamp'].isoformat(),
                    'node_name': r['node_name'],
                    'satellite': r['satellite'],
                    'audit_score': r['audit_score'],
                    'suspension_score': r['suspension_score'],
                    'online_score': r['online_score'],
                    'audit_success_count': r['audit_success_count'],
                    'audit_total_count': r['audit_total_count'],
                    'is_disqualified': 1 if r.get('is_disqualified') else 0,
                    'is_suspended': 1 if r.get('is_suspended') else 0
                }
                for r in records
            ])
            conn.commit()
        log.info(f"Successfully wrote {len(records)} reputation history records")
        return True
    except Exception:
        log.error("Failed to write reputation history to DB:", exc_info=True)
        return False


def blocking_get_latest_reputation(db_path: str, node_names: List[str]) -> List[Dict[str, Any]]:
    """
    Get the most recent reputation data for specified nodes.
    
    Args:
        db_path: Path to database file
        node_names: List of node names
    
    Returns:
        List of reputation summaries with latest data per node per satellite
    """
    if not node_names:
        return []
    
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get latest reputation for each node-satellite combination
            placeholders = ','.join('?' for _ in node_names)
            query = f"""
                SELECT r1.*
                FROM reputation_history r1
                INNER JOIN (
                    SELECT node_name, satellite, MAX(timestamp) as max_timestamp
                    FROM reputation_history
                    WHERE node_name IN ({placeholders})
                    GROUP BY node_name, satellite
                ) r2 ON r1.node_name = r2.node_name
                    AND r1.satellite = r2.satellite
                    AND r1.timestamp = r2.max_timestamp
                ORDER BY r1.node_name, r1.satellite
            """
            
            results = [dict(row) for row in conn.execute(query, node_names).fetchall()]
            return results
    except Exception:
        log.error("Failed to get latest reputation:", exc_info=True)
        return []


def blocking_get_reputation_history(
    db_path: str,
    node_name: str,
    satellite: Optional[str] = None,
    hours: int = 24
) -> List[Dict[str, Any]]:
    """
    Get reputation history for a node.
    
    Args:
        db_path: Path to database file
        node_name: Name of the node
        satellite: Optional satellite ID to filter by
        hours: Number of hours of history to retrieve
    
    Returns:
        List of reputation records ordered by timestamp
    """
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            if satellite:
                query = """
                    SELECT * FROM reputation_history
                    WHERE node_name = ? AND satellite = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                """
                results = conn.execute(query, (node_name, satellite, cutoff_iso)).fetchall()
            else:
                query = """
                    SELECT * FROM reputation_history
                    WHERE node_name = ? AND timestamp >= ?
                    ORDER BY timestamp ASC, satellite
                """
                results = conn.execute(query, (node_name, cutoff_iso)).fetchall()
            
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get reputation history:", exc_info=True)
        return []


# --- Storage Tracking Functions (Phase 2.2) ---

def blocking_write_storage_snapshot(db_path: str, snapshot: Dict[str, Any]) -> bool:
    """
    Write storage snapshot to database.
    
    Supports both complete snapshots (from API) and partial snapshots (from logs).
    Log-based snapshots only have available_bytes, other fields may be None.
    
    Args:
        db_path: Path to database file
        snapshot: Storage snapshot data
    
    Returns:
        True if successful
    """
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO storage_snapshots
                (timestamp, node_name, total_bytes, used_bytes, available_bytes, trash_bytes,
                 used_percent, trash_percent, available_percent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                snapshot['timestamp'].isoformat(),
                snapshot['node_name'],
                snapshot.get('total_bytes'),  # May be None for log-based snapshots
                snapshot.get('used_bytes'),   # May be None for log-based snapshots
                snapshot.get('available_bytes'),
                snapshot.get('trash_bytes'),  # May be None for log-based snapshots
                snapshot.get('used_percent'), # May be None for log-based snapshots
                snapshot.get('trash_percent'),# May be None for log-based snapshots
                snapshot.get('available_percent') # May be None for log-based snapshots
            ))
            conn.commit()
        source = snapshot.get('source', 'API')
        log.info(f"Successfully wrote storage snapshot for {snapshot['node_name']} (source: {source})")
        return True
    except Exception:
        log.error("Failed to write storage snapshot to DB:", exc_info=True)
        return False


@retry_on_db_lock(max_attempts=DB_MAX_RETRIES, base_delay=DB_RETRY_BASE_DELAY, max_delay=DB_RETRY_MAX_DELAY)
def blocking_get_storage_history(
    db_path: str,
    node_name: str,
    days: int = 7
) -> List[Dict[str, Any]]:
    """
    Get storage history for a node.
    
    Args:
        db_path: Path to database file
        node_name: Name of the node
        days: Number of days of history to retrieve
    
    Returns:
        List of storage snapshots ordered by timestamp
    """
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            query = """
                SELECT * FROM storage_snapshots
                WHERE node_name = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """
            results = conn.execute(query, (node_name, cutoff_iso)).fetchall()
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get storage history:", exc_info=True)
        return []


# --- Alert Management Functions (Phase 4) ---

def blocking_write_alert(db_path: str, alert: Dict[str, Any]) -> bool:
    """
    Write an alert to the database.
    
    Args:
        db_path: Path to database file
        alert: Alert data dict
    
    Returns:
        True if successful
    """
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO alerts
                (timestamp, node_name, alert_type, severity, title, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                alert['timestamp'].isoformat(),
                alert['node_name'],
                alert['alert_type'],
                alert['severity'],
                alert['title'],
                alert['message'],
                json.dumps(alert.get('metadata', {}))
            ))
            conn.commit()
        log.info(f"Successfully wrote alert for {alert['node_name']}: {alert['title']}")
        return True
    except Exception:
        log.error("Failed to write alert to DB:", exc_info=True)
        return False


def blocking_get_active_alerts(db_path: str, node_names: List[str] = None) -> List[Dict[str, Any]]:
    """
    Get active (unacknowledged and unresolved) alerts.
    
    Args:
        db_path: Path to database file
        node_names: Optional list of node names to filter
    
    Returns:
        List of active alerts
    """
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            if node_names:
                placeholders = ','.join('?' for _ in node_names)
                query = f"""
                    SELECT * FROM alerts
                    WHERE node_name IN ({placeholders})
                      AND acknowledged = 0
                      AND resolved = 0
                    ORDER BY timestamp DESC
                """
                results = conn.execute(query, node_names).fetchall()
            else:
                query = """
                    SELECT * FROM alerts
                    WHERE acknowledged = 0 AND resolved = 0
                    ORDER BY timestamp DESC
                """
                results = conn.execute(query).fetchall()
            
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get active alerts:", exc_info=True)
        return []


def blocking_acknowledge_alert(db_path: str, alert_id: int) -> bool:
    """Acknowledge an alert."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE alerts
                SET acknowledged = 1, acknowledged_at = ?
                WHERE id = ?
            ''', (datetime.datetime.now(datetime.timezone.utc).isoformat(), alert_id))
            conn.commit()
        return True
    except Exception:
        log.error(f"Failed to acknowledge alert {alert_id}:", exc_info=True)
        return False


def blocking_resolve_alert(db_path: str, alert_id: int) -> bool:
    """Resolve an alert."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE alerts
                SET resolved = 1, resolved_at = ?
                WHERE id = ?
            ''', (datetime.datetime.now(datetime.timezone.utc).isoformat(), alert_id))
            conn.commit()
        return True
    except Exception:
        log.error(f"Failed to resolve alert {alert_id}:", exc_info=True)
        return False


def blocking_get_alert_history(
    db_path: str,
    node_name: str,
    hours: int = 24,
    include_resolved: bool = True
) -> List[Dict[str, Any]]:
    """Get alert history for a node."""
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            if include_resolved:
                query = """
                    SELECT * FROM alerts
                    WHERE node_name = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                """
            else:
                query = """
                    SELECT * FROM alerts
                    WHERE node_name = ? AND timestamp >= ? AND resolved = 0
                    ORDER BY timestamp DESC
                """
            
            results = conn.execute(query, (node_name, cutoff_iso)).fetchall()
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get alert history:", exc_info=True)
        return []


# --- Insights Functions (Phase 4) ---

def blocking_write_insight(db_path: str, insight: Dict[str, Any]) -> bool:
    """Write an insight to the database."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO insights
                (timestamp, node_name, insight_type, severity, title, description,
                 category, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                insight['timestamp'].isoformat(),
                insight['node_name'],
                insight['insight_type'],
                insight['severity'],
                insight['title'],
                insight['description'],
                insight.get('category'),
                insight.get('confidence'),
                json.dumps(insight.get('metadata', {}))
            ))
            conn.commit()
        return True
    except Exception:
        log.error("Failed to write insight to DB:", exc_info=True)
        return False


def blocking_get_insights(
    db_path: str,
    node_names: List[str] = None,
    hours: int = 24
) -> List[Dict[str, Any]]:
    """Get recent insights."""
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            if node_names:
                placeholders = ','.join('?' for _ in node_names)
                query = f"""
                    SELECT * FROM insights
                    WHERE node_name IN ({placeholders}) AND timestamp >= ?
                    ORDER BY timestamp DESC
                """
                results = conn.execute(query, (*node_names, cutoff_iso)).fetchall()
            else:
                query = """
                    SELECT * FROM insights
                    WHERE timestamp >= ?
                    ORDER BY timestamp DESC
                """
                results = conn.execute(query, (cutoff_iso,)).fetchall()
            
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get insights:", exc_info=True)
        return []


# --- Analytics Baseline Functions (Phase 4) ---

def blocking_update_baseline(
    db_path: str,
    node_name: str,
    metric_name: str,
    window_hours: int,
    stats: Dict[str, float]
) -> bool:
    """Update or create a baseline for a metric."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO analytics_baselines
                (node_name, metric_name, window_hours, mean_value, std_dev,
                 min_value, max_value, sample_count, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                node_name,
                metric_name,
                window_hours,
                stats['mean'],
                stats['std_dev'],
                stats['min'],
                stats['max'],
                stats['count'],
                datetime.datetime.now(datetime.timezone.utc).isoformat()
            ))
            conn.commit()
        return True
    except Exception:
        log.error(f"Failed to update baseline for {metric_name}:", exc_info=True)
        return False


def blocking_get_baseline(
    db_path: str,
    node_name: str,
    metric_name: str,
    window_hours: int = 168  # 7 days default
) -> Optional[Dict[str, Any]]:
    """Get baseline statistics for a metric."""
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            query = """
                SELECT * FROM analytics_baselines
                WHERE node_name = ? AND metric_name = ? AND window_hours = ?
            """
            result = conn.execute(query, (node_name, metric_name, window_hours)).fetchone()
            
            if result:
                return dict(result)
            return None
    except Exception:
        log.error(f"Failed to get baseline for {metric_name}:", exc_info=True)
        return None


@retry_on_db_lock(max_attempts=DB_MAX_RETRIES, base_delay=DB_RETRY_BASE_DELAY, max_delay=DB_RETRY_MAX_DELAY)
def blocking_get_latest_storage(
    db_path: str,
    node_names: List[str]
) -> List[Dict[str, Any]]:
    """
    Get the most recent storage data for specified nodes.
    
    Args:
        db_path: Path to database file
        node_names: List of node names
    
    Returns:
        List of latest storage snapshots
    """
    if not node_names:
        log.debug("blocking_get_latest_storage: No node names provided")
        return []
    
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            # First check if we have any storage data at all
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM storage_snapshots WHERE node_name IN ({})".format(','.join('?' * len(node_names))), node_names)
            count = cursor.fetchone()[0]
            log.info(f"blocking_get_latest_storage: Found {count} storage snapshot(s) for nodes {node_names}")
            
            if count == 0:
                return []
            
            placeholders = ','.join('?' for _ in node_names)
            query = f"""
                SELECT s1.*
                FROM storage_snapshots s1
                INNER JOIN (
                    SELECT node_name, MAX(timestamp) as max_timestamp
                    FROM storage_snapshots
                    WHERE node_name IN ({placeholders})
                    GROUP BY node_name
                ) s2 ON s1.node_name = s2.node_name
                    AND s1.timestamp = s2.max_timestamp
                ORDER BY s1.node_name
            """
            
            results = [dict(row) for row in conn.execute(query, node_names).fetchall()]
            log.info(f"blocking_get_latest_storage: Returning {len(results)} result(s)")
            if results:
                for r in results:
                    log.info(f"  Node: {r['node_name']}, Available: {r.get('available_bytes', 0) / (1024**4):.2f} TB, Timestamp: {r['timestamp']}")
            return results
    except Exception:
        log.error("Failed to get latest storage:", exc_info=True)
        return []


def blocking_get_latest_storage_with_forecast(
    db_path: str,
    node_names: List[str],
    days_window: int = 7
) -> List[Dict[str, Any]]:
    """
    Get the most recent storage data for specified nodes with growth rate forecasts
    for multiple time windows (1 day, 7 days, 30 days).
    
    Args:
        db_path: Path to database file
        node_names: List of node names
        days_window: Legacy parameter, kept for compatibility but now calculates all windows
    
    Returns:
        List of latest storage snapshots with multi-window forecast data
    """
    if not node_names:
        log.debug("blocking_get_latest_storage_with_forecast: No node names provided")
        return []
    
    try:
        # Get latest snapshots
        latest_storage = blocking_get_latest_storage(db_path, node_names)
        
        if not latest_storage:
            return []
        
        # Calculate growth rate forecasts for each node using multiple time windows
        for storage in latest_storage:
            node_name = storage['node_name']
            available_bytes = storage.get('available_bytes', 0)
            
            # Get 30 days of history (maximum window we need)
            history_30d = blocking_get_storage_history(db_path, node_name, 30)
            
            # Filter to only API-based snapshots with used_bytes
            valid_history_30d = [h for h in history_30d if h.get('used_bytes') is not None]
            
            if len(valid_history_30d) < 2:
                # Not enough data for forecast
                storage['growth_rate_bytes_per_day'] = None
                storage['growth_rate_gb_per_day'] = None
                storage['days_until_full'] = None
                storage['forecast_data_points'] = len(valid_history_30d)
                storage['growth_rates'] = {}
                continue
            
            # Calculate growth rates for multiple time windows
            import datetime
            time_windows = [1, 7, 30]  # days
            growth_rates = {}
            
            for days in time_windows:
                # Filter history to the specified window
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
                valid_history = [h for h in valid_history_30d
                               if datetime.datetime.fromisoformat(h['timestamp']) >= cutoff]
                
                if len(valid_history) < 2:
                    # Not enough data for this window
                    growth_rates[f'{days}d'] = {
                        'growth_rate_bytes_per_day': None,
                        'growth_rate_gb_per_day': None,
                        'days_until_full': None,
                        'data_points': len(valid_history)
                    }
                    continue
                
                # Calculate linear regression for this window
                timestamps = [(datetime.datetime.fromisoformat(h['timestamp']).timestamp() / 86400)
                             for h in valid_history]  # Convert to days
                used_bytes = [h['used_bytes'] for h in valid_history]
                
                # Validate time span: require at least 1 hour of data for meaningful regression
                time_span_days = max(timestamps) - min(timestamps)
                if time_span_days < (1.0 / 24.0):  # Less than 1 hour
                    growth_rates[f'{days}d'] = {
                        'growth_rate_bytes_per_day': None,
                        'growth_rate_gb_per_day': None,
                        'days_until_full': None,
                        'data_points': len(valid_history),
                        'reason': 'insufficient_timespan'
                    }
                    continue
                
                # Validate data variance: check if data is essentially flat
                mean_used = sum(used_bytes) / len(used_bytes)
                if mean_used > 0:
                    variance = sum((x - mean_used) ** 2 for x in used_bytes) / len(used_bytes)
                    std_dev = variance ** 0.5
                    # If standard deviation is < 0.1% of mean, data is too flat for meaningful slope
                    if std_dev < (mean_used * 0.001):
                        growth_rates[f'{days}d'] = {
                            'growth_rate_bytes_per_day': 0,
                            'growth_rate_gb_per_day': 0,
                            'days_until_full': None,
                            'data_points': len(valid_history),
                            'reason': 'data_too_flat'
                        }
                        continue
                
                n = len(timestamps)
                sum_x = sum(timestamps)
                sum_y = sum(used_bytes)
                sum_xy = sum(x * y for x, y in zip(timestamps, used_bytes))
                sum_x2 = sum(x * x for x in timestamps)
                
                # Calculate slope (bytes per day)
                denominator = (n * sum_x2) - (sum_x * sum_x)
                if denominator == 0:
                    growth_rates[f'{days}d'] = {
                        'growth_rate_bytes_per_day': None,
                        'growth_rate_gb_per_day': None,
                        'days_until_full': None,
                        'data_points': n
                    }
                    continue
                
                slope = ((n * sum_xy) - (sum_x * sum_y)) / denominator
                
                # Calculate days until full for this growth rate
                if slope <= 0:
                    days_until_full = None
                elif available_bytes > 0:
                    days_until_full = available_bytes / slope
                else:
                    days_until_full = None
                
                growth_rates[f'{days}d'] = {
                    'growth_rate_bytes_per_day': round(slope) if slope > 0 else 0,
                    'growth_rate_gb_per_day': round(slope / (1024**3), 2) if slope > 0 else 0,
                    'days_until_full': round(days_until_full, 1) if days_until_full else None,
                    'data_points': n
                }
                
                log.debug(f"[{node_name}] {days}d window: {growth_rates[f'{days}d']['growth_rate_gb_per_day']} GB/day "
                         f"using {n} points")
            
            # Use 7-day window as the primary forecast for backward compatibility
            primary_rate = growth_rates.get('7d', {})
            storage['growth_rate_bytes_per_day'] = primary_rate.get('growth_rate_bytes_per_day')
            storage['growth_rate_gb_per_day'] = primary_rate.get('growth_rate_gb_per_day')
            storage['days_until_full'] = primary_rate.get('days_until_full')
            storage['forecast_data_points'] = primary_rate.get('data_points', 0)
            
            # Add all time windows
            storage['growth_rates'] = growth_rates
        
        return latest_storage
        
    except Exception:
        log.error("Failed to get latest storage with forecast:", exc_info=True)
        return []


# --- Financial Tracking Functions (Phase 5) ---

def blocking_write_earnings_estimate(db_path: str, estimate: Dict[str, Any]) -> bool:
    """
    Write an earnings estimate to the database.
    
    Args:
        db_path: Path to database file
        estimate: Earnings estimate data dict
    
    Returns:
        True if successful
    """
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO earnings_estimates
                (timestamp, node_name, satellite, period, egress_bytes, egress_earnings_gross,
                 egress_earnings_net, storage_bytes_hour, storage_earnings_gross, storage_earnings_net,
                 repair_bytes, repair_earnings_gross, repair_earnings_net, audit_bytes,
                 audit_earnings_gross, audit_earnings_net, total_earnings_gross, total_earnings_net,
                 held_amount, node_age_months, held_percentage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                estimate['timestamp'].isoformat(),
                estimate['node_name'],
                estimate['satellite'],
                estimate['period'],
                estimate.get('egress_bytes'),
                estimate.get('egress_earnings_gross'),
                estimate.get('egress_earnings_net'),
                estimate.get('storage_bytes_hour'),
                estimate.get('storage_earnings_gross'),
                estimate.get('storage_earnings_net'),
                estimate.get('repair_bytes'),
                estimate.get('repair_earnings_gross'),
                estimate.get('repair_earnings_net'),
                estimate.get('audit_bytes'),
                estimate.get('audit_earnings_gross'),
                estimate.get('audit_earnings_net'),
                estimate.get('total_earnings_gross'),
                estimate.get('total_earnings_net'),
                estimate.get('held_amount'),
                estimate.get('node_age_months'),
                estimate.get('held_percentage')
            ))
            conn.commit()
        log.info(f"Successfully wrote earnings estimate for {estimate['node_name']}/{estimate['satellite']}")
        return True
    except Exception:
        log.error("Failed to write earnings estimate to DB:", exc_info=True)
        return False


def blocking_get_earnings_estimates(
    db_path: str,
    node_names: List[str] = None,
    satellite: str = None,
    period: str = None,
    days: int = 30
) -> List[Dict[str, Any]]:
    """
    Get earnings estimates with optional filters.
    
    Args:
        db_path: Path to database file
        node_names: Optional list of node names to filter
        satellite: Optional satellite to filter
        period: Optional period to filter (e.g., '2025-01')
        days: Number of days of history to retrieve
    
    Returns:
        List of earnings estimates
    """
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            where_clauses = ["timestamp >= ?"]
            params = [cutoff_iso]
            
            if node_names:
                placeholders = ','.join('?' for _ in node_names)
                where_clauses.append(f"node_name IN ({placeholders})")
                params.extend(node_names)
            
            if satellite:
                where_clauses.append("satellite = ?")
                params.append(satellite)
            
            if period:
                where_clauses.append("period = ?")
                params.append(period)
            
            query = f"""
                SELECT * FROM earnings_estimates
                WHERE {' AND '.join(where_clauses)}
                ORDER BY timestamp DESC
            """
            
            results = conn.execute(query, params).fetchall()
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get earnings estimates:", exc_info=True)
        return []


def blocking_get_latest_earnings(
    db_path: str,
    node_names: List[str],
    period: str = None
) -> List[Dict[str, Any]]:
    """
    Get the most recent earnings estimates for specified nodes.
    
    Args:
        db_path: Path to database file
        node_names: List of node names
        period: Optional period filter (e.g., '2025-01' for current month)
    
    Returns:
        List of latest earnings estimates per node per satellite
    """
    if not node_names:
        return []
    
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            placeholders = ','.join('?' for _ in node_names)
            
            if period:
                query = f"""
                    SELECT e1.*
                    FROM earnings_estimates e1
                    INNER JOIN (
                        SELECT node_name, satellite, MAX(timestamp) as max_timestamp
                        FROM earnings_estimates
                        WHERE node_name IN ({placeholders}) AND period = ?
                        GROUP BY node_name, satellite
                    ) e2 ON e1.node_name = e2.node_name
                        AND e1.satellite = e2.satellite
                        AND e1.timestamp = e2.max_timestamp
                    ORDER BY e1.node_name, e1.satellite
                """
                params = [*node_names, period]
            else:
                query = f"""
                    SELECT e1.*
                    FROM earnings_estimates e1
                    INNER JOIN (
                        SELECT node_name, satellite, MAX(timestamp) as max_timestamp
                        FROM earnings_estimates
                        WHERE node_name IN ({placeholders})
                        GROUP BY node_name, satellite
                    ) e2 ON e1.node_name = e2.node_name
                        AND e1.satellite = e2.satellite
                        AND e1.timestamp = e2.max_timestamp
                    ORDER BY e1.node_name, e1.satellite
                """
                params = node_names
            
            results = [dict(row) for row in conn.execute(query, params).fetchall()]
            return results
    except Exception:
        log.error("Failed to get latest earnings:", exc_info=True)
        return []


def blocking_write_payout_history(db_path: str, payout: Dict[str, Any]) -> bool:
    """
    Write a payout record to the database.
    
    Args:
        db_path: Path to database file
        payout: Payout data dict
    
    Returns:
        True if successful
    """
    try:
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO payout_history
                (timestamp, node_name, satellite, period, actual_payout, estimated_payout,
                 variance, variance_percent, payout_address, transaction_hash, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                payout['timestamp'].isoformat(),
                payout['node_name'],
                payout['satellite'],
                payout['period'],
                payout['actual_payout'],
                payout.get('estimated_payout'),
                payout.get('variance'),
                payout.get('variance_percent'),
                payout.get('payout_address'),
                payout.get('transaction_hash'),
                payout.get('notes')
            ))
            conn.commit()
        log.info(f"Successfully wrote payout history for {payout['node_name']}/{payout['satellite']}")
        return True
    except Exception:
        log.error("Failed to write payout history to DB:", exc_info=True)
        return False


def blocking_get_payout_history(
    db_path: str,
    node_names: List[str] = None,
    satellite: str = None,
    months: int = 12
) -> List[Dict[str, Any]]:
    """
    Get payout history with optional filters.
    
    Args:
        db_path: Path to database file
        node_names: Optional list of node names to filter
        satellite: Optional satellite to filter
        months: Number of months of history to retrieve
    
    Returns:
        List of payout records
    """
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=months * 30)
        cutoff_iso = cutoff.isoformat()
        
        with get_optimized_connection(db_path, timeout=DB_CONNECTION_TIMEOUT) as conn:
            conn.row_factory = sqlite3.Row
            
            where_clauses = ["timestamp >= ?"]
            params = [cutoff_iso]
            
            if node_names:
                placeholders = ','.join('?' for _ in node_names)
                where_clauses.append(f"node_name IN ({placeholders})")
                params.extend(node_names)
            
            if satellite:
                where_clauses.append("satellite = ?")
                params.append(satellite)
            
            query = f"""
                SELECT * FROM payout_history
                WHERE {' AND '.join(where_clauses)}
                ORDER BY timestamp DESC
            """
            
            results = conn.execute(query, params).fetchall()
            return [dict(row) for row in results]
    except Exception:
        log.error("Failed to get payout history:", exc_info=True)
        return []
