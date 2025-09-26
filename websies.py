--- START OF FILE websies.py ---

# /// script
# dependencies = [
#   "aiohttp",
#   "geoip2",
# ]
# requires-python = ">=3.11"
# ///

import asyncio
import json
import re
import datetime
import sqlite3
import aiohttp
from aiohttp import web
import geoip2.database
from collections import deque, Counter
import concurrent.futures
import os
import time
import traceback
import logging
import sys
import argparse
from typing import Deque, Dict, Any, List, Set, Optional


# --- Centralized Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger("StorjMonitor")

# --- Configuration ---
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
DATABASE_FILE = 'storj_stats.db'
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
PERFORMANCE_INTERVAL_SECONDS = 2
DB_WRITE_BATCH_INTERVAL_SECONDS = 10
DB_QUEUE_MAX_SIZE = 30000
EXPECTED_DB_COLUMNS = 13 # Increased for node_name
HISTORICAL_HOURS_TO_SHOW = 6
MAX_GEOIP_CACHE_SIZE = 5000
HOURLY_AGG_INTERVAL_MINUTES = 10
DB_EVENTS_RETENTION_DAYS = 2 # New: How many days of event data to keep
DB_PRUNE_INTERVAL_HOURS = 6  # New: How often to run the pruner

# Custom type for a node's state
NodeState = Dict[str, Any]

# --- In-Memory State ---
app_state: Dict[str, Any] = {
    'websockets': {},  # {ws: {"view": "Aggregate"}}
    'nodes': {},  # { "node_name": NodeState }
    'geoip_cache': {},
    'db_write_lock': asyncio.Lock(),  # Lock to serialize DB write operations
    'db_write_queue': asyncio.Queue(maxsize=DB_QUEUE_MAX_SIZE),
}

# --- New Helper Function for Parsing Size Strings ---
def parse_size_to_bytes(size_str: str) -> int:
    if not isinstance(size_str, str): return 0
    size_str = size_str.strip().upper()
    units = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
    try:
        # Split number from unit
        value_str = "".join(re.findall(r'[\d\.]', size_str))
        unit_str = "".join(re.findall(r'[A-Z]', size_str))
        if not unit_str.endswith("B"): unit_str += "B"
        if unit_str == "KB": unit_str = "KIB" # Handle common case

        value = float(value_str)
        unit_multiplier = next((v for k, v in units.items() if k.startswith(unit_str)), 1)
        return int(value * unit_multiplier)

    except (ValueError, IndexError, StopIteration):
        return 0

def parse_duration_str_to_seconds(duration_str: str) -> Optional[float]:
    """Parses a duration string like '198.8ms' or '4.5s' into seconds."""
    if not isinstance(duration_str, str):
        return None
    try:
        duration_str = duration_str.lower().strip()
        if 'ms' in duration_str:
            return float(duration_str.replace('ms', '')) / 1000.0
        if 's' in duration_str:
            return float(duration_str.replace('s', ''))
        # If no unit is found, we cannot reliably determine the value.
        return None
    except (ValueError, TypeError):
        return None


def init_db():
    log.info("Connecting to database and checking schema...")
    conn = sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0)
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

    cursor.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, piece_id TEXT, satellite_id TEXT, remote_ip TEXT, country TEXT, latitude REAL, longitude REAL, error_reason TEXT, node_name TEXT)')

    # --- Schema migration for hourly_stats table ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hourly_stats';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(hourly_stats);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'node_name' not in columns:
            log.info("Upgrading 'hourly_stats' table. Recreating with new composite primary key.")
            cursor.execute("PRAGMA table_info(hourly_stats);")
            old_columns = [col[1] for col in cursor.fetchall()]
            select_columns = ['hour_timestamp', "'default' as node_name", 'dl_success', 'dl_fail', 'ul_success', 'ul_fail', 'audit_success', 'audit_fail']
            if 'total_download_size' in old_columns: select_columns.append('total_download_size')
            else: select_columns.append('0 as total_download_size')
            if 'total_upload_size' in old_columns: select_columns.append('total_upload_size')
            else: select_columns.append('0 as total_upload_size')

            cursor.execute("ALTER TABLE hourly_stats RENAME TO hourly_stats_old;")
            cursor.execute('CREATE TABLE hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
            select_query = f"SELECT {', '.join(select_columns)} FROM hourly_stats_old"
            cursor.execute(f"INSERT INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size) {select_query}")
            cursor.execute("DROP TABLE hourly_stats_old;")
            log.info("'hourly_stats' table upgrade complete.")
        else:
            if 'total_download_size' not in columns: cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_download_size INTEGER DEFAULT 0;")
            if 'total_upload_size' not in columns: cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_upload_size INTEGER DEFAULT 0;")

    cursor.execute('CREATE TABLE IF NOT EXISTS hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_node_name ON events (node_name);')
    cursor.execute('CREATE TABLE IF NOT EXISTS app_persistent_state (key TEXT PRIMARY KEY, value TEXT)')

    # Add a composite index to optimize the hourly aggregation query
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_events_node_name_timestamp'")
    if not cursor.fetchone():
        log.info("Creating composite index for performance. This may take a very long time on large databases. Please wait...")
        cursor.execute('CREATE INDEX idx_events_node_name_timestamp ON events (node_name, timestamp);')
        log.info("Index creation complete.")

    conn.commit()
    conn.close()
    log.info("Database schema is valid and ready.")


def get_size_bucket(size_in_bytes):
    if size_in_bytes < 1024: return "< 1 KB"
    kb = size_in_bytes / 1024
    if kb < 4: return "1-4 KB"
    elif kb < 16: return "4-16 KB"
    elif kb < 64: return "16-64 KB"
    elif kb < 256: return "64-256 KB"
    elif kb < 1024: return "256 KB - 1 MB"
    else: return "> 1 MB"

def blocking_log_reader(log_path):
    while True:
        try:
            with open(log_path, 'r') as f:
                current_inode = os.fstat(f.fileno()).st_ino
                log.info(f"Tailing log file '{log_path}' with inode {current_inode}")
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        try:
                            if os.stat(log_path).st_ino != current_inode:
                                log.warning(f"Log rotation detected for '{log_path}'.")
                                break
                        except FileNotFoundError:
                            log.warning(f"Log file not found at '{log_path}'. Waiting...")
                            break
                        continue
                    yield line
        except FileNotFoundError:
            log.error(f"Log file not found at {log_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception:
            log.error(f"Critical error in blocking_log_reader for '{log_path}':", exc_info=True)
            time.sleep(15)

async def robust_broadcast(websockets_dict, payload):
    tasks = []
    for ws in set(websockets_dict.keys()):
        try:
            task = asyncio.create_task(ws.send_json(payload))
            tasks.append(task)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            log.error("An unexpected error occurred during websocket broadcast:", exc_info=True)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

def blocking_save_state(db_path: str, key: str, value: Any):
    """Saves a Python object as JSON into the state table."""
    try:
        json_value = json.dumps(value)
        with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO app_persistent_state (key, value) VALUES (?, ?)",
                (key, json_value)
            )
            conn.commit()
        log.info(f"Successfully persisted state for key '{key}'.")
    except Exception:
        log.error(f"Failed to persist state for key '{key}':", exc_info=True)


async def log_tailer_task(app, node_name: str, log_path: str):
    loop = asyncio.get_running_loop()
    geoip_reader = geoip2.database.Reader(GEOIP_DATABASE_PATH)
    geoip_cache = app_state['geoip_cache']
    log_generator = blocking_log_reader(log_path)
    log.info(f"Log tailer task started for node: {node_name}")
    node_state = app_state['nodes'][node_name]

    while True:
        try:
            line = await loop.run_in_executor(app['log_executor'], next, log_generator)
            try:
                # --- General Log Parsing ---
                log_level_part = "INFO" if "INFO" in line else "ERROR" if "ERROR" in line else None
                if not log_level_part: continue

                parts = line.split(log_level_part)
                timestamp_str = parts[0].strip()

                # --- DEFINITIVE TIMEZONE FIX ---
                # Assume the timestamp from the log is in the server's local timezone,
                # then correctly CONVERT it to UTC for storage and comparison.
                timestamp_obj = datetime.datetime.fromisoformat(timestamp_str).astimezone().astimezone(datetime.timezone.utc)
                # --- END DEFINITIVE TIMEZONE FIX ---

                json_match = re.search(r'\{.*\}', line)
                if not json_match: continue
                log_data = json.loads(json_match.group(0))

                # --- Hashstore Log Processing ---
                if "hashstore" in line:
                    hashstore_action = line.split(log_level_part)[1].split("hashstore")[1].strip().split('\t')[0]
                    satellite = log_data.get("satellite")
                    store = log_data.get("store")
                    if not all([hashstore_action, satellite, store]): continue

                    compaction_key = f"{satellite}:{store}"
                    if hashstore_action == "beginning compaction":
                        node_state['active_compactions'][compaction_key] = timestamp_obj
                    elif hashstore_action == "finished compaction":
                        start_time = node_state['active_compactions'].pop(compaction_key, None)
                        if start_time:
                            duration_seconds = (timestamp_obj - start_time).total_seconds()
                            if duration_seconds < 60:
                                duration_str = log_data.get("duration")
                                if duration_str:
                                    parsed_duration = parse_duration_str_to_seconds(duration_str)
                                    if parsed_duration is not None:
                                        duration_seconds = parsed_duration

                            stats = log_data.get("stats", {})
                            table_stats = stats.get("Table", {})
                            node_state['hashstore_stats'][compaction_key] = {
                                "satellite": satellite,
                                "store": store,
                                "last_run_iso": timestamp_obj.isoformat(),
                                "duration": round(duration_seconds, 2),
                                "data_reclaimed_bytes": parse_size_to_bytes(stats.get("DataReclaimed", "0 B")),
                                "data_rewritten_bytes": parse_size_to_bytes(stats.get("DataRewritten", "0 B")),
                                "table_load": table_stats.get("Load", 0) * 100,
                                "trash_percent": stats.get("TrashPercent", 0) * 100,
                            }
                            state_key = f"hashstore_stats_{node_name}"
                            stats_to_save = node_state['hashstore_stats'].copy()
                            asyncio.create_task(
                                loop.run_in_executor(
                                    app['db_executor'],
                                    blocking_save_state,
                                    DATABASE_FILE,
                                    state_key,
                                    stats_to_save
                                )
                            )
                    continue

                # --- Original Traffic Log Processing ---
                status, error_reason = "success", None
                if "download canceled" in line: status, error_reason = "canceled", log_data.get("reason", "context canceled")
                elif "failed" in line or "ERROR" in line: status, error_reason = "failed", log_data.get("error", "unknown error")

                action, size, piece_id, sat_id, remote_addr = log_data.get("Action"), log_data.get("Size"), log_data.get("Piece ID"), log_data.get("Satellite ID"), log_data.get("Remote Address")
                if not all([action, size, piece_id, sat_id, remote_addr]): continue

                remote_ip = remote_addr.split(':')[0]
                location = geoip_cache.get(remote_ip)
                if location is None:
                    try:
                        geo_response = geoip_reader.city(remote_ip)
                        location = {"lat": geo_response.location.latitude, "lon": geo_response.location.longitude, "country": geo_response.country.name}
                    except geoip2.errors.AddressNotFoundError: location = {"lat": None, "lon": None, "country": "Unknown"}
                    if len(geoip_cache) > MAX_GEOIP_CACHE_SIZE: geoip_cache.pop(next(iter(geoip_cache)))
                    geoip_cache[remote_ip] = location

                event = {"ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action, "status": status, "size": size, "piece_id": piece_id, "satellite_id": sat_id, "remote_ip": remote_ip, "location": location, "error_reason": error_reason, "node_name": node_name}
                node_state['live_events'].append(event)
                broadcast_payload = {"type": "log_entry", "action": action, "status": status, "size": size, "location": location, "error_reason": error_reason, "timestamp": timestamp_obj.isoformat(), "node_name": node_name}
                await robust_broadcast(app_state['websockets'], broadcast_payload)

                if app_state['db_write_queue'].full():
                    log.warning(f"Database write queue is full. Pausing log tailing to allow DB to catch up.")
                await app_state['db_write_queue'].put(event)

            except (json.JSONDecodeError, AttributeError, KeyError, ValueError):
                continue
            except Exception:
                log.error(f"Unexpected error processing a log line for {node_name}:", exc_info=True)

        except Exception:
            log.error(f"Critical error in log_tailer_task main loop for {node_name}:", exc_info=True)
            await asyncio.sleep(5)

def blocking_db_batch_write(db_path, events):
    if not events: return
    with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
        cursor = conn.cursor()
        data_to_insert = [(e['timestamp'].isoformat(), e['action'], e['status'], e['size'], e['piece_id'], e['satellite_id'], e['remote_ip'], e['location']['country'], e['location']['lat'], e['location']['lon'], e['error_reason'], e['node_name']) for e in events]
        cursor.executemany('INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', data_to_insert)
        conn.commit()
    log.info(f"Successfully wrote {len(events)} events to the database.")

async def database_writer_task(app):
    log.info("Database writer task started.")
    while True:
        await asyncio.sleep(DB_WRITE_BATCH_INTERVAL_SECONDS)
        events_to_write = []
        if app_state['db_write_queue'].empty(): continue
        while not app_state['db_write_queue'].empty():
            try: events_to_write.append(app_state['db_write_queue'].get_nowait())
            except asyncio.QueueEmpty: break

        if events_to_write:
            log.info(f"[DB_WRITER] Preparing to write {len(events_to_write)} events. Queue size: {app_state['db_write_queue'].qsize()}")
            loop = asyncio.get_running_loop()
            async with app_state['db_write_lock']:
                try:
                    await loop.run_in_executor(app['db_executor'], blocking_db_batch_write, DATABASE_FILE, events_to_write)
                except Exception:
                    log.error("Error during blocking database write execution:", exc_info=True)

async def debug_logger_task(app):
    log.info("Debug heartbeat task started.")
    while True:
        await asyncio.sleep(30)
        total_live_events = sum(len(n['live_events']) for n in app_state['nodes'].values())
        log.info(f"[HEARTBEAT] Clients: {len(app_state['websockets'])}, Live Events: {total_live_events}, DB Queue: {app_state['db_write_queue'].qsize()}")
        for name, state in app_state['nodes'].items():
            log.info(f"  -> Node '{name}': {len(state['live_events'])} events, Perf Index: {state['last_perf_event_index']}")


async def performance_calculator(app, node_name: str):
    log.info(f"Performance calculator task started for node: {node_name}")
    node_state = app_state['nodes'][node_name]

    while True:
        await asyncio.sleep(PERFORMANCE_INTERVAL_SECONDS)
        now = time.time()
        current_event_count = len(node_state['live_events'])
        start_index = node_state['last_perf_event_index']
        new_events_to_process = [node_state['live_events'][i] for i in range(start_index, current_event_count)]
        node_state['last_perf_event_index'] = current_event_count

        ingress_bytes, egress_bytes, ingress_pieces, egress_pieces = 0, 0, 0, 0

        concurrency = sum(1 for event in reversed(node_state['live_events']) if event['ts_unix'] > now - 1)

        for event in new_events_to_process:
            if 'GET' in event['action']: egress_bytes += event['size']; egress_pieces += 1
            else: ingress_bytes += event['size']; ingress_pieces += 1

        ingress_mbps = (ingress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)
        egress_mbps = (egress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)

        payload = { "type": "performance_update", "node_name": node_name, "timestamp": datetime.datetime.now(datetime.UTC).isoformat(), "ingress_mbps": round(ingress_mbps, 2), "egress_mbps": round(egress_mbps, 2), "ingress_bytes": ingress_bytes, "egress_bytes": egress_bytes, "ingress_pieces": ingress_pieces, "egress_pieces": egress_pieces, "concurrency": concurrency }

        await robust_broadcast(app_state['websockets'], payload)


async def prune_live_events_task(app):
    log.info("Event pruning task started.")
    while True:
        await asyncio.sleep(60)
        cutoff_unix = time.time() - (STATS_WINDOW_MINUTES * 60)
        for node_name, node_state in app_state['nodes'].items():
            events_to_prune_count = 0
            while node_state['live_events'] and node_state['live_events'][0]['ts_unix'] < cutoff_unix:
                node_state['live_events'].popleft()
                events_to_prune_count += 1
            if events_to_prune_count > 0:
                old_index = node_state['last_perf_event_index']
                node_state['last_perf_event_index'] = max(0, old_index - events_to_prune_count)
                log.info(f"[PRUNER] Node '{node_name}': Pruned {events_to_prune_count} events. Adjusted perf_index from {old_index} to {node_state['last_perf_event_index']}.")


def blocking_hourly_aggregation(node_names: List[str]):
    log.info("[AGGREGATOR] Running hourly aggregation.")
    now = datetime.datetime.now(datetime.timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_start_iso = hour_start.isoformat()
    next_hour_start_iso = (hour_start + datetime.timedelta(hours=1)).isoformat()

    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
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
                """, (hour_start_iso, node_name, stats['dl_s'], stats['dl_f'], stats['ul_s'], stats['ul_f'], stats['audit_s'], stats['audit_f'], stats['total_dl_size'], stats['total_ul_size']))
                conn.commit()
                log.info(f"[AGGREGATOR] Wrote hourly stats for node '{node_name}' at {hour_start_iso}.")

async def hourly_aggregator_task(app):
    log.info("Hourly aggregator task started.")
    while True:
        await asyncio.sleep(60 * HOURLY_AGG_INTERVAL_MINUTES)
        try:
            loop = asyncio.get_running_loop()
            node_names = list(app['nodes'].keys())
            async with app_state['db_write_lock']:
                await loop.run_in_executor(app['db_executor'], blocking_hourly_aggregation, node_names)
        except Exception:
            log.error("Error in hourly aggregator task:", exc_info=True)


def blocking_db_prune(db_path, retention_days):
    log.info(f"[DB_PRUNER] Starting database pruning task. Retaining last {retention_days} days of events.")
    cutoff_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=retention_days)
    cutoff_iso = cutoff_date.isoformat()

    with sqlite3.connect(db_path, timeout=30, detect_types=0) as conn:
        cursor = conn.cursor()

        log.info(f"Finding events older than {cutoff_iso} to delete...")
        cursor.execute("SELECT COUNT(*) FROM events WHERE timestamp < ?", (cutoff_iso,))
        count = cursor.fetchone()[0]

        if count > 0:
            log.warning(f"Deleting {count} old event(s) from the database. This might take a while...")
            cursor.execute("DELETE FROM events WHERE timestamp < ?", (cutoff_iso,))
            conn.commit()
            log.info(f"Successfully pruned {count} old event(s) from the database.")
        else:
            log.info("No old events found to prune.")

async def database_pruner_task(app):
    log.info("Database pruner task started.")
    while True:
        try:
            loop = asyncio.get_running_loop()
            async with app_state['db_write_lock']:
                await loop.run_in_executor(app['db_executor'], blocking_db_prune, DATABASE_FILE, DB_EVENTS_RETENTION_DAYS)
        except Exception:
            log.error("Error in database pruner task:", exc_info=True)
        await asyncio.sleep(3600 * DB_PRUNE_INTERVAL_HOURS)


def blocking_prepare_stats(view: str, all_nodes_state: Dict[str, NodeState]):
    events_copy = []
    hashstore_stats = []
    if view == 'Aggregate':
        for node_name, node_state in all_nodes_state.items():
            events_copy.extend(list(node_state['live_events']))
            for key, stats in node_state['hashstore_stats'].items():
                stat_copy = stats.copy()
                stat_copy['node_name'] = node_name
                hashstore_stats.append(stat_copy)
    elif view in all_nodes_state:
        events_copy = list(all_nodes_state[view]['live_events'])
        for key, stats in all_nodes_state[view]['hashstore_stats'].items():
             hashstore_stats.append(stats)


    if not events_copy:
        if hashstore_stats:
            return {
                "type": "stats_update",
                "hashstore_stats": sorted(hashstore_stats, key=lambda x: x['last_run_iso'], reverse=True),
                "first_event_iso": None, "last_event_iso": None, "overall": {}, "satellites": [], "transfer_sizes": [],
                "historical_stats": [], "error_categories": [], "top_pieces": [], "top_countries_dl": [], "top_countries_ul": []
            }
        return None

    first_event_iso, last_event_iso = min(e['timestamp'] for e in events_copy).isoformat(), max(e['timestamp'] for e in events_copy).isoformat()

    dl_s, dl_f, ul_s, ul_f, a_s, a_f = 0, 0, 0, 0, 0, 0
    total_dl_size, total_ul_size = 0, 0
    sats, cdl, cul, error_agg = {}, Counter(), Counter(), {}
    hp = {}

    dls_success, dls_failed = Counter(), Counter()
    uls_success, uls_failed = Counter(), Counter()

    TOKEN_REGEX = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b')

    def aggregate_error(reason):
        if not reason: return
        tokens = TOKEN_REGEX.findall(reason)
        template = TOKEN_REGEX.sub('#', reason)
        if template not in error_agg:
            placeholders = []
            for token in tokens:
                if '.' in token or ':' in token:
                    placeholders.append({'type': 'address', 'seen': {token}})
                else:
                    try:
                        num = int(token)
                        placeholders.append({'type': 'number', 'min': num, 'max': num})
                    except ValueError:
                        placeholders.append({'type': 'string', 'seen': {token}})
            error_agg[template] = {'count': 1, 'placeholders': placeholders}
        else:
            agg_item = error_agg[template]
            agg_item['count'] += 1
            if len(tokens) == len(agg_item['placeholders']):
                for i, token in enumerate(tokens):
                    ph = agg_item['placeholders'][i]
                    if ph['type'] == 'address':
                        ph['seen'].add(token)
                    elif ph['type'] == 'number':
                        try:
                            num = int(token)
                            ph['min'] = min(ph['min'], num)
                            ph['max'] = max(ph['max'], num)
                        except ValueError: pass

    for e in events_copy:
        action, status, sat_id, size, country, piece_id, error_reason = e['action'], e['status'], e['satellite_id'], e['size'], e['location']['country'], e['piece_id'], e['error_reason']
        if sat_id not in sats: sats[sat_id] = {'uploads': 0, 'downloads': 0, 'audits': 0, 'ul_success': 0, 'dl_success': 0, 'audit_success': 0, 'total_upload_size': 0, 'total_download_size': 0}
        if action == 'GET_AUDIT':
            sats[sat_id]['audits'] += 1
            if status == 'success': a_s += 1; sats[sat_id]['audit_success'] += 1
            else: a_f += 1; aggregate_error(error_reason)
        elif 'GET' in action:
            size_bucket = get_size_bucket(size)
            sats[sat_id]['downloads'] += 1
            if piece_id not in hp: hp[piece_id] = {'count': 0, 'size': 0}
            hp[piece_id]['count'] += 1
            hp[piece_id]['size'] += size
            if country: cdl[country] += size
            if status == 'success':
                dl_s += 1; sats[sat_id]['dl_success'] += 1; sats[sat_id]['total_download_size'] += size
                total_dl_size += size; dls_success[size_bucket] += 1
            else:
                dl_f += 1; aggregate_error(error_reason); dls_failed[size_bucket] += 1
        elif action == 'PUT':
            size_bucket = get_size_bucket(size)
            sats[sat_id]['uploads'] += 1
            if country: cul[country] += size
            if status == 'success':
                ul_s += 1; sats[sat_id]['ul_success'] += 1; sats[sat_id]['total_upload_size'] += size
                total_ul_size += size; uls_success[size_bucket] += 1
            else:
                ul_f += 1; aggregate_error(error_reason); uls_failed[size_bucket] += 1

    hist_stats = []
    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row
        if view == 'Aggregate':
             raw_hist_stats = conn.execute("""
                SELECT hour_timestamp,
                       SUM(dl_success) as dl_success, SUM(dl_fail) as dl_fail,
                       SUM(ul_success) as ul_success, SUM(ul_fail) as ul_fail,
                       SUM(audit_success) as audit_success, SUM(audit_fail) as audit_fail,
                       SUM(total_download_size) as total_download_size, SUM(total_upload_size) as total_upload_size
                FROM hourly_stats
                GROUP BY hour_timestamp
                ORDER BY hour_timestamp DESC LIMIT ?
             """, (HISTORICAL_HOURS_TO_SHOW,)).fetchall()
        else:
            raw_hist_stats = conn.execute("SELECT * FROM hourly_stats WHERE node_name = ? ORDER BY hour_timestamp DESC LIMIT ?", (view, HISTORICAL_HOURS_TO_SHOW,)).fetchall()

        for row in raw_hist_stats:
            d_row = dict(row)
            d_row['dl_mbps'] = ((d_row.get('total_download_size', 0) or 0) * 8) / (3600 * 1000000)
            d_row['ul_mbps'] = ((d_row.get('total_upload_size', 0) or 0) * 8) / (3600 * 1000000)
            hist_stats.append(d_row)

    one_min_ago = time.time() - 60
    live_dl_bytes = sum(e['size'] for e in events_copy if 'GET' in e['action'] and e['status'] == 'success' and e['ts_unix'] > one_min_ago)
    live_ul_bytes = sum(e['size'] for e in events_copy if 'PUT' in e['action'] and e['status'] == 'success' and e['ts_unix'] > one_min_ago)
    avg_egress_mbps = (live_dl_bytes * 8) / (60 * 1e6)
    avg_ingress_mbps = (live_ul_bytes * 8) / (60 * 1e6)

    all_buckets = ["< 1 KB", "1-4 KB", "4-16 KB", "16-64 KB", "64-256 KB", "256 KB - 1 MB", "> 1 MB"]
    transfer_sizes = [{'bucket': b, 'downloads_success': dls_success[b], 'downloads_failed': dls_failed[b], 'uploads_success': uls_success[b], 'uploads_failed': uls_failed[b]} for b in all_buckets]

    sorted_errors = sorted(error_agg.items(), key=lambda item: item[1]['count'], reverse=True)
    final_errors = []
    for template, data in sorted_errors[:10]:
        final_msg = template
        if 'placeholders' in data:
            for ph_data in data['placeholders']:
                if ph_data['type'] == 'number':
                    min_val, max_val = ph_data['min'], ph_data['max']
                    range_str = str(min_val) if min_val == max_val else f"({min_val}..{max_val})"
                    final_msg = final_msg.replace('#', range_str, 1)
                elif ph_data['type'] == 'address':
                    count = len(ph_data['seen'])
                    range_str = f"[{count} unique address{'es' if count > 1 else ''}]"
                    final_msg = final_msg.replace('#', range_str, 1)
        final_errors.append({'reason': final_msg, 'count': data['count']})

    return {
        "type": "stats_update",
        "first_event_iso": first_event_iso, "last_event_iso": last_event_iso,
        "overall": {
            "dl_success": dl_s, "dl_fail": dl_f, "ul_success": ul_s, "ul_fail": ul_f,
            "audit_success": a_s, "audit_fail": a_f, "avg_egress_mbps": avg_egress_mbps, "avg_ingress_mbps": avg_ingress_mbps
        },
        "satellites": sorted([{'satellite_id': k, **v} for k, v in sats.items()], key=lambda x: x['uploads'] + x['downloads'], reverse=True),
        "transfer_sizes": transfer_sizes,
        "historical_stats": hist_stats,
        "error_categories": final_errors,
        "top_pieces": [{'id': k, 'count': v['count'], 'size': v['size']} for k, v in sorted(hp.items(), key=lambda x: x[1]['count'], reverse=True)[:10]],
        "top_countries_dl": [{'country': k, 'size': v} for k,v in cdl.most_common(10) if k],
        "top_countries_ul": [{'country': k, 'size': v} for k,v in cul.most_common(10) if k],
        "hashstore_stats": sorted(hashstore_stats, key=lambda x: x['last_run_iso'], reverse=True),
    }

async def send_stats_for_view(app, ws, view):
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(app['db_executor'], blocking_prepare_stats, view, app_state['nodes'])
    if payload:
        try:
            await ws.send_json(payload)
        except (ConnectionResetError, asyncio.CancelledError):
            pass

async def periodic_stats_updater(app):
    log.info("Periodic stats updater task started.")
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        try:
            tasks = [send_stats_for_view(app, ws, ws_state['view']) for ws, ws_state in app_state['websockets'].items()]
            if tasks:
                await asyncio.gather(*tasks)
        except Exception:
            log.error("Error in periodic_stats_updater:", exc_info=True)


async def handle_index(request): return web.FileResponse('./index.html')

def blocking_get_historical_performance(node_name: str, points: int, interval_sec: int) -> List[Dict[str, Any]]:
    log.info(f"Fetching historical performance for node '{node_name}' ({points} points @ {interval_sec}s interval).")
    window_sec = points * interval_sec
    start_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=window_sec)
    start_time_iso = start_time.isoformat()

    results = []
    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row

        query = f"""
            SELECT
                (CAST(strftime('%s', timestamp) AS INTEGER) / ?) * ? as time_bucket_start,
                SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN size ELSE 0 END) as ingress_bytes,
                SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN size ELSE 0 END) as egress_bytes,
                SUM(CASE WHEN action LIKE '%PUT%' AND status = 'success' THEN 1 ELSE 0 END) as ingress_pieces,
                SUM(CASE WHEN action LIKE '%GET%' AND status = 'success' AND action != 'GET_AUDIT' THEN 1 ELSE 0 END) as egress_pieces
            FROM events
            WHERE timestamp >= ? AND node_name = ?
            GROUP BY time_bucket_start ORDER BY time_bucket_start ASC
        """
        params = [interval_sec, interval_sec, start_time_iso, node_name]

        cursor = conn.cursor()
        for row in cursor.execute(query, params).fetchall():
            row_dict = dict(row)
            ts_unix = row_dict['time_bucket_start']

            ingress_mbps = (row_dict.get('ingress_bytes', 0) * 8) / (interval_sec * 1e6)
            egress_mbps = (row_dict.get('egress_bytes', 0) * 8) / (interval_sec * 1e6)

            results.append({
                "timestamp": datetime.datetime.fromtimestamp(ts_unix, tz=datetime.UTC).isoformat(),
                "ingress_mbps": round(ingress_mbps, 2),
                "egress_mbps": round(egress_mbps, 2),
                "ingress_bytes": row_dict.get('ingress_bytes', 0),
                "egress_bytes": row_dict.get('egress_bytes', 0),
                "ingress_pieces": row_dict.get('ingress_pieces', 0),
                "egress_pieces": row_dict.get('egress_pieces', 0),
                "concurrency": 0
            })

    log.info(f"Returning {len(results)} historical performance data points for node '{node_name}'.")
    return results

async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    app_state['websockets'][ws] = {"view": "Aggregate"}

    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")

    node_names = ["Aggregate"] + list(app['nodes'].keys())
    await ws.send_json({"type": "init", "nodes": node_names})
    await send_stats_for_view(app, ws, "Aggregate")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

                    if msg_type == 'set_view':
                        new_view = data.get('view')
                        if new_view in node_names:
                            app_state['websockets'][ws]['view'] = new_view
                            log.info(f"Client switched view to: {new_view}")
                            await send_stats_for_view(app, ws, new_view)

                    elif msg_type == 'get_historical_performance':
                        view = data.get('view')
                        if view == 'Aggregate': continue # Should not be requested by new client

                        points = data.get('points', 150)
                        interval = data.get('interval_sec', PERFORMANCE_INTERVAL_SECONDS)

                        loop = asyncio.get_running_loop()
                        historical_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_historical_performance,
                            view, points, interval
                        )

                        payload = {
                            "type": "historical_performance_data",
                            "view": view,
                            "performance_data": historical_data
                        }
                        await ws.send_json(payload)

                except Exception:
                    log.error("Could not parse websocket message:", exc_info=True)

    finally:
        if ws in app_state['websockets']:
            del app_state['websockets'][ws]
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws


def load_initial_state_from_db(nodes_config: Dict[str, str]):
    """Connects to the DB to re-hydrate the in-memory state on startup."""
    log.info("Attempting to load initial state from database...")
    initial_state = {}
    cutoff_datetime = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=STATS_WINDOW_MINUTES)

    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for node_name in nodes_config.keys():
            node_state = {
                'live_events': deque(),
                'last_perf_event_index': 0,
                'active_compactions': {},
                'hashstore_stats': {}
            }
            log.info(f"Re-hydrating live events for node '{node_name}' since {cutoff_datetime.isoformat()}")
            cursor.execute(
                "SELECT * FROM events WHERE node_name = ? AND timestamp >= ? ORDER BY timestamp ASC",
                (node_name, cutoff_datetime.isoformat())
            )
            rehydrated_events = 0
            for row in cursor.fetchall():
                try:
                    timestamp_obj = datetime.datetime.fromisoformat(row['timestamp'])
                    event = {
                        "ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj,
                        "action": row['action'], "status": row['status'], "size": row['size'],
                        "piece_id": row['piece_id'], "satellite_id": row['satellite_id'],
                        "remote_ip": row['remote_ip'],
                        "location": {"lat": row['latitude'], "lon": row['longitude'], "country": row['country']},
                        "error_reason": row['error_reason'], "node_name": row['node_name']
                    }
                    node_state['live_events'].append(event)
                    rehydrated_events += 1
                except Exception:
                    log.error(f"Failed to process a database row for re-hydration.", exc_info=True)

            node_state['last_perf_event_index'] = rehydrated_events
            if rehydrated_events > 0:
                log.info(f"Successfully re-hydrated {rehydrated_events} live events for node '{node_name}'.")

            state_key = f"hashstore_stats_{node_name}"
            cursor.execute("SELECT value FROM app_persistent_state WHERE key = ?", (state_key,))
            result = cursor.fetchone()
            if result:
                try:
                    node_state['hashstore_stats'] = json.loads(result['value'])
                    log.info(f"Successfully loaded persisted hashstore stats for node '{node_name}'.")
                except json.JSONDecodeError:
                    log.error(f"Failed to parse persisted hashstore stats for node '{node_name}'.")

            initial_state[node_name] = node_state
    return initial_state

def blocking_backfill_hourly_stats(node_names: List[str]):
    log.info("[BACKFILL] Starting one-time backfill of hourly statistics.")
    with sqlite3.connect(DATABASE_FILE, timeout=30, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find the earliest and latest event timestamps
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events")
        result = cursor.fetchone()
        if not result or not result[0]:
            log.info("[BACKFILL] No events found in the database. Skipping backfill.")
            return

        start_ts_str, end_ts_str = result
        start_dt = datetime.datetime.fromisoformat(start_ts_str).replace(minute=0, second=0, microsecond=0)
        end_dt = datetime.datetime.fromisoformat(end_ts_str)

        log.info(f"[BACKFILL] Found events ranging from {start_dt.isoformat()} to {end_dt.isoformat()}.")

        current_hour_start = start_dt
        while current_hour_start <= end_dt:
            hour_start_iso = current_hour_start.isoformat()
            next_hour_start_iso = (current_hour_start + datetime.timedelta(hours=1)).isoformat()

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
                stats = cursor.execute(query, (node_name, hour_start_iso, next_hour_start_iso)).fetchone()

                if stats and stats['dl_s'] is not None:
                    cursor.execute("""
                        INSERT OR REPLACE INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (hour_start_iso, node_name, stats['dl_s'], stats['dl_f'], stats['ul_s'], stats['ul_f'], stats['audit_s'], stats['audit_f'], stats['total_dl_size'], stats['total_ul_size']))

            log.info(f"[BACKFILL] Processed and saved stats for hour starting {hour_start_iso}.")
            current_hour_start += datetime.timedelta(hours=1)

        conn.commit()
    log.info("[BACKFILL] Hourly statistics backfill complete.")

async def start_background_tasks(app):
    log.info("Starting background tasks...")
    app['db_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    app['log_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=len(app['nodes']) + 1)
    app['tasks'] = []

    loop = asyncio.get_running_loop()

    # Run the one-time backfill before starting other tasks
    node_names = list(app['nodes'].keys())
    await loop.run_in_executor(app['db_executor'], blocking_backfill_hourly_stats, node_names)

    initial_node_states = await loop.run_in_executor(
        app['db_executor'], load_initial_state_from_db, app['nodes']
    )
    app_state['nodes'] = initial_node_states
    log.info("Initial state has been populated from the database.")

    for node_name, log_path in app['nodes'].items():
        if node_name not in app_state['nodes']:
             app_state['nodes'][node_name] = {
                'live_events': deque(), 'last_perf_event_index': 0,
                'active_compactions': {}, 'hashstore_stats': {}
            }
        app['tasks'].append(asyncio.create_task(log_tailer_task(app, node_name, log_path)))
        app['tasks'].append(asyncio.create_task(performance_calculator(app, node_name)))

    app['tasks'].extend([
        asyncio.create_task(prune_live_events_task(app)),
        asyncio.create_task(periodic_stats_updater(app)),
        asyncio.create_task(debug_logger_task(app)),
        asyncio.create_task(database_writer_task(app)),
        asyncio.create_task(hourly_aggregator_task(app)),
        asyncio.create_task(database_pruner_task(app))
    ])

async def cleanup_background_tasks(app):
    log.warning("Application cleanup started.")
    for task in app.get('tasks', []): task.cancel()
    if 'tasks' in app: await asyncio.gather(*app['tasks'], return_exceptions=True)
    log.info("Background tasks cancelled.")
    for executor_name in ['db_executor', 'log_executor']:
        if executor_name in app and app[executor_name]: app[executor_name].shutdown(wait=True)
    log.info("Executors shut down.")


def parse_nodes(args: List[str]) -> Dict[str, str]:
    nodes = {}
    if not args:
        log.critical("No nodes specified. Use --node 'NodeName:/path/to/log' argument.")
        sys.exit(1)
    for arg in args:
        parts = arg.split(':', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            log.critical(f"Invalid node format: '{arg}'. Expected 'NodeName:/path/to/log'.")
            sys.exit(1)
        node_name, log_path = parts
        if not os.path.exists(log_path):
            log.warning(f"Log path for node '{node_name}' does not exist: {log_path}")
        nodes[node_name] = log_path
    return nodes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Storagenode Pro Monitor")
    parser.add_argument('--node', action='append', help="Specify a node in 'NodeName:/path/to/log.log' format. Can be used multiple times.", required=True)
    args = parser.parse_args()

    init_db()
    app = web.Application()
    app['nodes'] = parse_nodes(args.node)

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', websocket_handler)

    log.info(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    log.info(f"Monitoring nodes: {list(app['nodes'].keys())}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)
