
# /// script
# dependencies = [
#   "aiohttp",
#   "geoip2",
#   "watchdog",
# ]
# requires-python = ">=3.9"
# ///

import asyncio
import json
import re
import datetime
import sqlite3
import aiohttp
from aiohttp import web
import geoip2.database
from collections import deque, Counter, defaultdict
import concurrent.futures
import os
import time
import traceback
import logging
import sys
import argparse
from typing import Deque, Dict, Any, List, Set, Optional
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import heapq
from dataclasses import dataclass, field


# --- Centralized Logging Configuration ---
# Set up later in main after parsing args
log = logging.getLogger("StorjMonitor")

# --- Configuration ---
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
DATABASE_FILE = 'storj_stats.db'
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
PERFORMANCE_INTERVAL_SECONDS = 2
WEBSOCKET_BATCH_INTERVAL_MS = 25   # Batch websocket events every 25ms (very frequent, small batches)
WEBSOCKET_BATCH_SIZE = 10  # Maximum events per batch (small batches for continuous flow)
DB_WRITE_BATCH_INTERVAL_SECONDS = 10
DB_QUEUE_MAX_SIZE = 30000
EXPECTED_DB_COLUMNS = 13 # Increased for node_name
HISTORICAL_HOURS_TO_SHOW = 6
MAX_GEOIP_CACHE_SIZE = 5000
HOURLY_AGG_INTERVAL_MINUTES = 10
DB_EVENTS_RETENTION_DAYS = 2 # New: How many days of event data to keep
DB_PRUNE_INTERVAL_HOURS = 6  # New: How often to run the pruner

# --- Global Constants ---
SATELLITE_NAMES = { '121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1', '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1', '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1', '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake' }


# Incremental Stats Accumulator
@dataclass
class IncrementalStats:
    """Maintains running statistics that can be updated incrementally."""
    # Overall counters
    dl_success: int = 0
    dl_fail: int = 0
    ul_success: int = 0
    ul_fail: int = 0
    audit_success: int = 0
    audit_fail: int = 0
    total_dl_size: int = 0
    total_ul_size: int = 0
    
    # Live stats (last minute)
    live_dl_bytes: int = 0
    live_ul_bytes: int = 0
    
    # Satellite stats
    satellites: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Country stats
    countries_dl: Counter = field(default_factory=Counter)
    countries_ul: Counter = field(default_factory=Counter)
    
    # Transfer size buckets
    dls_success: Counter = field(default_factory=Counter)
    dls_failed: Counter = field(default_factory=Counter)
    uls_success: Counter = field(default_factory=Counter)
    uls_failed: Counter = field(default_factory=Counter)
    
    # Error aggregation
    error_agg: Dict[str, Dict] = field(default_factory=dict)
    error_templates_cache: Dict[str, tuple] = field(default_factory=dict)
    
    # Hot pieces tracking
    hot_pieces: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Time tracking
    first_event_ts: Optional[datetime.datetime] = None
    last_event_ts: Optional[datetime.datetime] = None
    
    # Last processed event index for incremental updates
    last_processed_index: int = 0
    
    def get_or_create_satellite(self, sat_id: str) -> Dict[str, int]:
        """Get or create satellite stats."""
        if sat_id not in self.satellites:
            self.satellites[sat_id] = {
                'uploads': 0, 'downloads': 0, 'audits': 0,
                'ul_success': 0, 'dl_success': 0, 'audit_success': 0,
                'total_upload_size': 0, 'total_download_size': 0
            }
        return self.satellites[sat_id]
    
    def add_event(self, event: Dict[str, Any], TOKEN_REGEX: re.Pattern):
        """Add a single event to the running statistics."""
        timestamp = event['timestamp']
        
        # Update time tracking
        if self.first_event_ts is None or timestamp < self.first_event_ts:
            self.first_event_ts = timestamp
        if self.last_event_ts is None or timestamp > self.last_event_ts:
            self.last_event_ts = timestamp
        
        # Extract event data
        category = event['category']
        status = event['status']
        sat_id = event['satellite_id']
        size = event['size']
        ts_unix = event['ts_unix']
        
        sat_stats = self.get_or_create_satellite(sat_id)
        is_success = status == 'success'
        
        # Update stats based on category
        if category == 'audit':
            sat_stats['audits'] += 1
            if is_success:
                self.audit_success += 1
                sat_stats['audit_success'] += 1
            else:
                self.audit_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)
                
        elif category == 'get':
            sat_stats['downloads'] += 1
            
            # Update hot pieces
            piece_id = event['piece_id']
            if piece_id not in self.hot_pieces:
                self.hot_pieces[piece_id] = {'count': 0, 'size': 0}
            hot_piece = self.hot_pieces[piece_id]
            hot_piece['count'] += 1
            hot_piece['size'] += size
            
            # Update country stats
            country = event['location']['country']
            if country:
                self.countries_dl[country] += size
                
            if is_success:
                self.dl_success += 1
                sat_stats['dl_success'] += 1
                sat_stats['total_download_size'] += size
                self.total_dl_size += size
                # Defer size bucket calculation
                size_bucket = get_size_bucket(size)
                self.dls_success[size_bucket] += 1
            else:
                self.dl_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)
                # Defer size bucket calculation
                size_bucket = get_size_bucket(size)
                self.dls_failed[size_bucket] += 1
                
        elif category == 'put':
            sat_stats['uploads'] += 1
            
            # Update country stats
            country = event['location']['country']
            if country:
                self.countries_ul[country] += size
                
            if is_success:
                self.ul_success += 1
                sat_stats['ul_success'] += 1
                sat_stats['total_upload_size'] += size
                self.total_ul_size += size
                # Defer size bucket calculation
                size_bucket = get_size_bucket(size)
                self.uls_success[size_bucket] += 1
            else:
                self.ul_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)
                # Defer size bucket calculation
                size_bucket = get_size_bucket(size)
                self.uls_failed[size_bucket] += 1
    
    def _aggregate_error(self, reason: str, TOKEN_REGEX: re.Pattern):
        """Aggregate error reasons efficiently with optimized template building."""
        if not reason:
            return
        
        # Check cache first
        if reason in self.error_templates_cache:
            template, tokens = self.error_templates_cache[reason]
        else:
            # Build template - optimized version
            matches = TOKEN_REGEX.finditer(reason)
            first_match = None
            tokens = []
            
            # Peek at first match to decide strategy
            try:
                first_match = next(matches)
                tokens.append(first_match.group(0))
            except StopIteration:
                # No matches - use reason as template
                if len(self.error_templates_cache) < 1000:
                    self.error_templates_cache[reason] = (reason, [])
                template = reason
                tokens = []
            
            if first_match is not None:
                # Build template efficiently
                template_parts = [reason[:first_match.start()], '#']
                last_end = first_match.end()
                
                for match in matches:
                    start = match.start()
                    if start > last_end:
                        template_parts.append(reason[last_end:start])
                    template_parts.append('#')
                    tokens.append(match.group(0))
                    last_end = match.end()
                
                if last_end < len(reason):
                    template_parts.append(reason[last_end:])
                
                template = "".join(template_parts)
                
                # Cache it
                if len(self.error_templates_cache) < 1000:
                    self.error_templates_cache[reason] = (template, tokens)
        
        # Update error aggregation
        if template not in self.error_agg:
            # Build placeholders only once
            placeholders = []
            for token in tokens:
                if '.' in token or ':' in token:
                    placeholders.append({'type': 'address', 'seen': {token}})
                else:
                    # Try to parse as number
                    try:
                        num = int(token)
                        placeholders.append({'type': 'number', 'min': num, 'max': num})
                    except ValueError:
                        placeholders.append({'type': 'string', 'seen': {token}})
            self.error_agg[template] = {'count': 1, 'placeholders': placeholders}
        else:
            agg_item = self.error_agg[template]
            agg_item['count'] += 1
            # Only update placeholders if counts match
            if len(tokens) == len(agg_item['placeholders']):
                for i, token in enumerate(tokens):
                    ph = agg_item['placeholders'][i]
                    if ph['type'] == 'address':
                        if len(ph['seen']) < 100:  # Limit stored addresses
                            ph['seen'].add(token)
                    elif ph['type'] == 'number':
                        try:
                            num = int(token)
                            if num < ph['min']:
                                ph['min'] = num
                            elif num > ph['max']:
                                ph['max'] = num
                        except ValueError:
                            pass
    
    def update_live_stats(self, events: List[Dict[str, Any]]):
        """Update live stats for the last minute."""
        one_min_ago = time.time() - 60
        self.live_dl_bytes = 0
        self.live_ul_bytes = 0
        
        for event in events:
            if event['ts_unix'] > one_min_ago and event['status'] == 'success':
                if event['category'] == 'get':
                    self.live_dl_bytes += event['size']
                elif event['category'] == 'put':
                    self.live_ul_bytes += event['size']
    
    def to_payload(self, historical_stats: List[Dict] = None) -> Dict[str, Any]:
        """Convert stats to a JSON payload."""
        # Calculate speeds
        avg_egress_mbps = (self.live_dl_bytes * 8) / (60 * 1e6)
        avg_ingress_mbps = (self.live_ul_bytes * 8) / (60 * 1e6)
        
        # Format satellites
        satellites = sorted([
            {'satellite_id': k, **v} 
            for k, v in self.satellites.items()
        ], key=lambda x: x['uploads'] + x['downloads'], reverse=True)
        
        # Format transfer sizes
        all_buckets = ["< 1 KB", "1-4 KB", "4-16 KB", "16-64 KB", "64-256 KB", "256 KB - 1 MB", "> 1 MB"]
        transfer_sizes = [
            {
                'bucket': b,
                'downloads_success': self.dls_success[b],
                'downloads_failed': self.dls_failed[b],
                'uploads_success': self.uls_success[b],
                'uploads_failed': self.uls_failed[b]
            }
            for b in all_buckets
        ]
        
        # Format errors
        sorted_errors = sorted(self.error_agg.items(), key=lambda item: item[1]['count'], reverse=True)
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
        
        # Top pieces
        top_pieces = [
            {'id': k, 'count': v['count'], 'size': v['size']}
            for k, v in heapq.nlargest(10, self.hot_pieces.items(), key=lambda item: item[1]['count'])
        ]
        
        return {
            "type": "stats_update",
            "first_event_iso": self.first_event_ts.isoformat() if self.first_event_ts else None,
            "last_event_iso": self.last_event_ts.isoformat() if self.last_event_ts else None,
            "overall": {
                "dl_success": self.dl_success,
                "dl_fail": self.dl_fail,
                "ul_success": self.ul_success,
                "ul_fail": self.ul_fail,
                "audit_success": self.audit_success,
                "audit_fail": self.audit_fail,
                "avg_egress_mbps": avg_egress_mbps,
                "avg_ingress_mbps": avg_ingress_mbps
            },
            "satellites": satellites,
            "transfer_sizes": transfer_sizes,
            "historical_stats": historical_stats or [],
            "error_categories": final_errors,
            "top_pieces": top_pieces,
            "top_countries_dl": [{'country': k, 'size': v} for k, v in self.countries_dl.most_common(10) if k],
            "top_countries_ul": [{'country': k, 'size': v} for k, v in self.countries_ul.most_common(10) if k],
        }


# Custom type for a node's state
NodeState = Dict[str, Any]

# --- In-Memory State ---
app_state: Dict[str, Any] = {
    'websockets': {},  # {ws: {"view": ["Aggregate"]}}
    'nodes': {},  # { "node_name": NodeState }
    'geoip_cache': {},
    'db_write_lock': asyncio.Lock(),  # Lock to serialize DB write operations
    'db_write_queue': asyncio.Queue(maxsize=DB_QUEUE_MAX_SIZE),
    'stats_cache': {}, # Cache for pre-computed stats payloads
    'incremental_stats': {},  # New: { view_tuple: IncrementalStats }
    'websocket_event_queue': [],  # Queue for batching websocket events
    'websocket_queue_lock': asyncio.Lock(),  # Lock for websocket queue operations
    'TOKEN_REGEX': re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b'),  # Pre-compiled regex
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


# Pre-computed size bucket thresholds and labels for faster lookup
SIZE_BUCKET_THRESHOLDS = [
    (1024, "< 1 KB"),
    (4096, "1-4 KB"),
    (16384, "4-16 KB"),
    (65536, "16-64 KB"),
    (262144, "64-256 KB"),
    (1048576, "256 KB - 1 MB")
]

# Cache for size bucket calculations (LRU with max size)
_size_bucket_cache = {}
_CACHE_MAX_SIZE = 10000

def get_size_bucket(size_in_bytes):
    """Get size bucket with caching for frequently seen sizes."""
    # Check cache first
    if size_in_bytes in _size_bucket_cache:
        return _size_bucket_cache[size_in_bytes]
    
    # Calculate bucket
    for threshold, label in SIZE_BUCKET_THRESHOLDS:
        if size_in_bytes < threshold:
            bucket = label
            break
    else:
        bucket = "> 1 MB"
    
    # Cache result (with simple size limit)
    if len(_size_bucket_cache) < _CACHE_MAX_SIZE:
        _size_bucket_cache[size_in_bytes] = bucket
    
    return bucket

def categorize_action(action: str) -> str:
    """Efficiently categorizes a log action string."""
    if action == 'GET_AUDIT':
        return 'audit'
    if 'GET' in action:
        return 'get'
    if 'PUT' in action:
        return 'put'
    return 'other'


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

    # --- One-time migration of old hashstore stats ---
    cursor.execute("SELECT key, value FROM app_persistent_state WHERE key LIKE 'hashstore_stats_%'")
    old_stats_entries = cursor.fetchall()
    if old_stats_entries:
        log.info(f"Found {len(old_stats_entries)} old hashstore stat entries. Migrating to new table...")
        migrated_count = 0
        for key, value in old_stats_entries:
            try:
                node_name = key.replace('hashstore_stats_', '')
                stats_dict = json.loads(value)
                for compaction_key, stats in stats_dict.items():
                    cursor.execute('''
                        INSERT OR IGNORE INTO hashstore_compaction_history
                        (node_name, satellite, store, last_run_iso, duration, data_reclaimed_bytes, data_rewritten_bytes, table_load, trash_percent)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        node_name, stats.get('satellite'), stats.get('store'),
                        stats.get('last_run_iso'), stats.get('duration'), stats.get('data_reclaimed_bytes'),
                        stats.get('data_rewritten_bytes'), stats.get('table_load'), stats.get('trash_percent')
                    ))
                    migrated_count += 1
                # Delete old key after successful migration of its contents
                cursor.execute("DELETE FROM app_persistent_state WHERE key = ?", (key,))
                log.info(f"Successfully migrated and deleted old state for key '{key}'.")
            except Exception:
                log.error(f"Failed to migrate hashstore stats for key {key}:", exc_info=True)

        if migrated_count > 0:
            log.info(f"Migration complete. {migrated_count} compaction records moved to the new historical table.")


    conn.commit()
    conn.close()
    log.info("Database schema is valid and ready.")


def blocking_log_reader(log_path: str, loop: asyncio.AbstractEventLoop, aio_queue: asyncio.Queue, shutdown_event: threading.Event):
    """
    An efficient, event-driven log reader that runs in a separate thread.
    Uses watchdog for file system notifications and falls back to polling.
    Handles log rotation and truncation gracefully.
    Puts (line, arrival_time) tuples onto the queue.
    """
    log.info(f"Starting event-driven log reader for {log_path}")
    file_changed_event = threading.Event()
    directory = os.path.dirname(log_path)

    class ChangeHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            file_changed_event.set()

    observer = Observer()
    handler = ChangeHandler()

    if not os.path.isdir(directory):
        log.error(f"Cannot watch log file: directory '{directory}' does not exist. Reader thread for this log will exit.")
        return

    observer.schedule(handler, directory, recursive=False)
    observer.start()

    f = None
    current_inode = None
    try:
        while not shutdown_event.is_set():
            if f is None:
                try:
                    f = open(log_path, 'r')
                    current_inode = os.fstat(f.fileno()).st_ino
                    log.info(f"Tailing log file '{log_path}' with inode {current_inode}")
                    f.seek(0, os.SEEK_END)
                except FileNotFoundError:
                    shutdown_event.wait(5.0)
                    continue
                except Exception as e:
                    log.error(f"Error opening log file '{log_path}': {e}. Retrying in 5s.")
                    shutdown_event.wait(5.0)
                    continue

            line = f.readline()
            if line:
                # Add arrival timestamp here, in the reader thread, for maximum accuracy
                loop.call_soon_threadsafe(aio_queue.put_nowait, (line, time.time()))
                continue

            file_changed_event.clear()
            file_changed_event.wait(timeout=5.0)
            if shutdown_event.is_set(): break

            try:
                st = os.stat(log_path)
                if st.st_ino != current_inode:
                    log.warning(f"Log rotation by inode change detected for '{log_path}'. Re-opening.")
                    f.close(); f = None; continue
                if f.tell() > st.st_size:
                    log.warning(f"Log truncation detected for '{log_path}'. Seeking to start.")
                    f.seek(0)
            except FileNotFoundError:
                log.warning(f"Log file '{log_path}' disappeared. Will attempt to re-open.")
                f.close(); f = None
            except Exception as e:
                log.error(f"Error checking log status for '{log_path}': {e}. Re-opening.", exc_info=True)
                f.close(); f = None; shutdown_event.wait(5)
    finally:
        observer.stop()
        observer.join()
        if f: f.close()
        log.info(f"Log reader for {log_path} has stopped.")


async def network_log_reader_task(node_name: str, host: str, port: int, queue: asyncio.Queue):
    """Connects to a remote log forwarder and reads timestamped log lines."""
    log.info(f"[{node_name}] Starting network log reader for {host}:{port}")
    backoff = 2
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            log.info(f"[{node_name}] Connected to remote log source at {host}:{port}")
            backoff = 2 # Reset backoff on successful connection
            while True:
                line_bytes = await reader.readline()
                if not line_bytes:
                    log.warning(f"[{node_name}] Connection to {host}:{port} closed by remote end.")
                    break

                line = line_bytes.decode('utf-8').strip()
                parts = line.split(' ', 1)
                if len(parts) == 2:
                    try:
                        ts = float(parts[0])
                        log_line = parts[1]
                        await queue.put((log_line, ts))
                    except (ValueError, TypeError):
                        log.warning(f"[{node_name}] Received malformed line from {host}:{port}, ignoring: {line[:100]}")
                else:
                     log.warning(f"[{node_name}] Received malformed line from {host}:{port}, ignoring: {line[:100]}")

        except asyncio.CancelledError:
            log.info(f"[{node_name}] Network log reader for {host}:{port} cancelled.")
            break
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
            log.error(f"[{node_name}] Cannot connect to {host}:{port}: {e}. Retrying in {backoff}s.")
        except Exception:
            log.error(f"[{node_name}] Unexpected error in network log reader for {host}:{port}. Retrying in {backoff}s.", exc_info=True)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60) # Exponential backoff up to 1 minute

async def robust_broadcast(websockets_dict, payload, node_name: Optional[str] = None):
    tasks = []
    # If this is a node-specific message, filter the recipients
    if node_name:
        recipients = { ws for ws, state in websockets_dict.items()
            if state.get("view") and (state.get("view") == ["Aggregate"] or node_name in state.get("view"))
        }
    else: # Broadcast to all
        recipients = set(websockets_dict.keys())

    for ws in recipients:
        try:
            task = asyncio.create_task(ws.send_json(payload))
            tasks.append(task)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            log.error("An unexpected error occurred during websocket broadcast:", exc_info=True)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def blocking_write_hashstore_log(db_path, stats_dict) -> bool:
    """Writes a single hashstore compaction event to the database. Returns True on success."""
    try:
        with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
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


async def log_processor_task(app, node_name: str, line_queue: asyncio.Queue):
    """
    Consumes log lines from a queue, parses them, and updates application state.
    This task is agnostic to the source of the log lines (file or network).
    """
    loop = asyncio.get_running_loop()
    geoip_reader = app['geoip_reader']
    geoip_cache = app_state['geoip_cache']

    log.info(f"Log processor task started for node: {node_name}")
    node_state = app_state['nodes'][node_name]
    JSON_RE = re.compile(r'\{.*\}')

    try:
        while True:
            line, arrival_time = await line_queue.get()
            try:
                # --- PERFORMANCE: Quick filter for relevant log components ---
                if 'piecestore' not in line and 'hashstore' not in line:
                    continue

                # --- General Log Parsing ---
                log_level_part = "INFO" if "INFO" in line else "ERROR" if "ERROR" in line else None
                if not log_level_part: continue

                parts = line.split(log_level_part)
                timestamp_str = parts[0].strip()

                # --- DEFINITIVE TIMEZONE FIX ---
                timestamp_obj = datetime.datetime.fromisoformat(timestamp_str).astimezone().astimezone(datetime.timezone.utc)

                json_match = JSON_RE.search(line)
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

                            compaction_stats = {
                                "node_name": node_name, "satellite": satellite, "store": store,
                                "last_run_iso": timestamp_obj.isoformat(),
                                "duration": round(duration_seconds, 2),
                                "data_reclaimed_bytes": parse_size_to_bytes(stats.get("DataReclaimed", "0 B")),
                                "data_rewritten_bytes": parse_size_to_bytes(stats.get("DataRewritten", "0 B")),
                                "table_load": (table_stats.get("Load") or 0) * 100,
                                "trash_percent": stats.get("TrashPercent", 0) * 100,
                            }

                            was_written = await loop.run_in_executor(
                                app['db_executor'], blocking_write_hashstore_log, DATABASE_FILE, compaction_stats
                            )
                            if was_written:
                                await robust_broadcast(app_state['websockets'], {"type": "hashstore_updated"})
                    continue

                # --- Original Traffic Log Processing ---
                status, error_reason = "success", None
                if "download canceled" in line: status, error_reason = "canceled", log_data.get("reason", "context canceled")
                elif "failed" in line or "ERROR" in line: status, error_reason = "failed", log_data.get("error", "unknown error")

                action, size, piece_id, sat_id, remote_addr = log_data.get("Action"), log_data.get("Size"), log_data.get("Piece ID"), log_data.get("Satellite ID"), log_data.get("Remote Address")

                if not all([action, piece_id, sat_id, remote_addr]) or size is None: continue

                remote_ip = remote_addr.split(':')[0]
                location = geoip_cache.get(remote_ip)
                if location is None:
                    try:
                        geo_response = geoip_reader.city(remote_ip)
                        location = {"lat": geo_response.location.latitude, "lon": geo_response.location.longitude, "country": geo_response.country.name}
                    except geoip2.errors.AddressNotFoundError: location = {"lat": None, "lon": None, "country": "Unknown"}
                    if len(geoip_cache) > MAX_GEOIP_CACHE_SIZE: geoip_cache.pop(next(iter(geoip_cache)))
                    geoip_cache[remote_ip] = location

                category = categorize_action(action)
                if category != 'other':
                    node_state['unprocessed_performance_events'].append({
                        'ts_unix': timestamp_obj.timestamp(), 'category': category,
                        'status': status, 'size': size
                    })

                event = {
                    "ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action,
                    "status": status, "size": size, "piece_id": piece_id, "satellite_id": sat_id,
                    "remote_ip": remote_ip, "location": location, "error_reason": error_reason,
                    "node_name": node_name, "category": category, "arrival_time": arrival_time
                }
                node_state['live_events'].append(event)
                
                # NEW: Mark that we have new events that need processing
                node_state['has_new_events'] = True

                websocket_event = {
                    "type": "log_entry", "action": action, "size": size, "location": location,
                    "timestamp": timestamp_obj.isoformat(), "node_name": node_name, "arrival_time": arrival_time
                }
                async with app_state['websocket_queue_lock']:
                    app_state['websocket_event_queue'].append(websocket_event)

                if app_state['db_write_queue'].full():
                    log.warning(f"Database write queue is full. Pausing log processing to allow DB to catch up.")
                await app_state['db_write_queue'].put(event)

            except (json.JSONDecodeError, AttributeError, KeyError, ValueError):
                continue
            except Exception:
                log.error(f"Unexpected error processing a log line for {node_name}:", exc_info=True)
    except asyncio.CancelledError:
        log.warning(f"Log processor task for '{node_name}' is cancelled.")
    except Exception:
        log.error(f"Critical error in log_processor_task main loop for {node_name}:", exc_info=True)

def blocking_db_batch_write(db_path, events):
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
            e['error_reason'], e['node_name']
        ))
    
    with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
        cursor = conn.cursor()
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
        unprocessed_perf_events = sum(len(n['unprocessed_performance_events']) for n in app_state['nodes'].values())
        incremental_stats = len(app_state['incremental_stats'])
        log.info(f"[HEARTBEAT] Clients: {len(app_state['websockets'])}, Live Events: {total_live_events}, DB Queue: {app_state['db_write_queue'].qsize()}, Perf Queue: {unprocessed_perf_events}, Stats: {incremental_stats}")
        for name, state in app_state['nodes'].items():
            log.info(f"  -> Node '{name}': {len(state['live_events'])} events, {len(state['unprocessed_performance_events'])} perf events")


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
                log.info(f"[PRUNER] Node '{node_name}': Pruned {events_to_prune_count} events.")


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
    cutoff_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=retention_days)
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


def get_historical_stats(view: List[str], all_nodes_state: Dict[str, NodeState]) -> List[Dict]:
    """Fetch historical stats from the database."""
    hist_stats = []
    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
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


async def incremental_stats_updater_task(app):
    """
    New task that maintains incremental statistics for each view.
    This replaces the old stats_baker_and_broadcaster_task.
    """
    log.info("Incremental stats updater task started.")
    
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        
        try:
            # Collect all unique views from websockets
            views_to_update = set()
            for ws, state in app_state['websockets'].items():
                view_tuple = tuple(state.get('view', ['Aggregate']))
                views_to_update.add(view_tuple)
            
            if not views_to_update:
                continue
            
            # Update stats for each view
            for view_tuple in views_to_update:
                view_list = list(view_tuple)
                
                # Get or create incremental stats for this view
                if view_tuple not in app_state['incremental_stats']:
                    app_state['incremental_stats'][view_tuple] = IncrementalStats()
                
                stats = app_state['incremental_stats'][view_tuple]
                
                # Determine which nodes to process
                nodes_to_process = view_list if view_list != ['Aggregate'] else list(app_state['nodes'].keys())
                
                # Process only NEW events since last update
                new_events_processed = False
                for node_name in nodes_to_process:
                    if node_name in app_state['nodes']:
                        node_state = app_state['nodes'][node_name]
                        
                        # Check if this node has new events
                        if node_state.get('has_new_events', False):
                            # Get events to process
                            all_events = list(node_state['live_events'])
                            
                            # Process only new events since last update
                            if stats.last_processed_index < len(all_events):
                                new_events = all_events[stats.last_processed_index:]
                                for event in new_events:
                                    stats.add_event(event, app_state['TOKEN_REGEX'])
                                stats.last_processed_index = len(all_events)
                                new_events_processed = True
                
                # Update live stats (last minute)
                if new_events_processed:
                    all_events_for_view = []
                    for node_name in nodes_to_process:
                        if node_name in app_state['nodes']:
                            all_events_for_view.extend(list(app_state['nodes'][node_name]['live_events']))
                    stats.update_live_stats(all_events_for_view)
                    
                    # Clear the new events flag for processed nodes
                    for node_name in nodes_to_process:
                        if node_name in app_state['nodes']:
                            app_state['nodes'][node_name]['has_new_events'] = False
                
                    # Get historical stats
                    historical_stats = get_historical_stats(view_list, app_state['nodes'])
                    
                    # Generate payload
                    payload = stats.to_payload(historical_stats)
                    
                    # Cache and broadcast
                    app_state['stats_cache'][view_tuple] = payload
                    
                    # Broadcast to all websockets subscribed to this view
                    for ws, state in app_state['websockets'].items():
                        if tuple(state.get('view', ['Aggregate'])) == view_tuple:
                            try:
                                await ws.send_json(payload)
                            except (ConnectionResetError, asyncio.CancelledError):
                                pass  # Client disconnected
        
        except Exception:
            log.error("Error in incremental_stats_updater_task:", exc_info=True)


async def performance_aggregator_task(app):
    log.info("Live performance aggregator task started.")
    while True:
        await asyncio.sleep(PERFORMANCE_INTERVAL_SECONDS)
        try:
            for node_name, node_state in app_state['nodes'].items():
                # Atomically get and clear the list of unprocessed events
                events_to_process = node_state['unprocessed_performance_events']
                node_state['unprocessed_performance_events'] = []

                if not events_to_process:
                    continue

                bins = {}
                bin_size_sec = PERFORMANCE_INTERVAL_SECONDS

                for event in events_to_process:
                    ts_unix = event['ts_unix']
                    # Bin events into discrete time buckets
                    binned_timestamp_ms = int(ts_unix / bin_size_sec) * bin_size_sec * 1000
                    ts_key = str(binned_timestamp_ms)

                    if ts_key not in bins:
                        bins[ts_key] = { 'ingress_bytes': 0, 'egress_bytes': 0, 'ingress_pieces': 0, 'egress_pieces': 0, 'total_ops': 0 }

                    bin_data = bins[ts_key]
                    bin_data['total_ops'] += 1

                    if event['status'] == 'success':
                        category = event['category']
                        size = event['size']
                        if category == 'get':
                            bin_data['egress_bytes'] += size
                            bin_data['egress_pieces'] += 1
                        elif category == 'put':
                            bin_data['ingress_bytes'] += size
                            bin_data['ingress_pieces'] += 1

                if bins:
                    payload = { "type": "performance_batch_update", "node_name": node_name, "bins": bins }
                    # This broadcast is smart enough to send to 'Aggregate' viewers as well
                    await robust_broadcast(app_state['websockets'], payload, node_name=node_name)

        except Exception:
            log.error("Error in performance_aggregator_task:", exc_info=True)


async def websocket_batch_broadcaster_task(app):
    """
    Batches websocket events and sends them at regular intervals to reduce traffic.
    Also staggers events within the same second using arrival time for progressive display.
    """
    log.info("Websocket batch broadcaster task started.")

    while True:
        await asyncio.sleep(WEBSOCKET_BATCH_INTERVAL_MS / 1000.0)  # Convert ms to seconds

        try:
            # Atomically get and clear the event queue
            async with app_state['websocket_queue_lock']:
                events_to_send = app_state['websocket_event_queue'][:WEBSOCKET_BATCH_SIZE]
                app_state['websocket_event_queue'] = app_state['websocket_event_queue'][WEBSOCKET_BATCH_SIZE:]

            if not events_to_send:
                continue

            # Group events by the same log timestamp to enable progressive display
            events_by_timestamp = {}
            for event in events_to_send:
                timestamp_key = event['timestamp']  # Original log timestamp (to the second)
                if timestamp_key not in events_by_timestamp:
                    events_by_timestamp[timestamp_key] = []
                events_by_timestamp[timestamp_key].append(event)

            # Sort events within each timestamp group by arrival time and add display delays
            for timestamp_key, timestamp_events in events_by_timestamp.items():
                # Sort by arrival time
                timestamp_events.sort(key=lambda e: e['arrival_time'])

                # Add progressive display delays based on arrival order within the same second
                base_arrival_time = timestamp_events[0]['arrival_time']
                for i, event in enumerate(timestamp_events):
                    # Add small progressive delays (10ms increments) for events in the same second
                    event['display_delay_ms'] = i * 10
                    # Calculate time since first event in this timestamp group
                    event['arrival_offset_ms'] = int((event['arrival_time'] - base_arrival_time) * 1000)

            # Create batched payload
            if len(events_to_send) == 1:
                # Send single event as before for backward compatibility
                payload = events_to_send[0]
            else:
                # Send as batch
                payload = {
                    "type": "log_entry_batch",
                    "events": events_to_send,
                    "count": len(events_to_send)
                }

            # Broadcast to all relevant websockets
            await robust_broadcast(app_state['websockets'], payload)

        except Exception:
            log.error("Error in websocket_batch_broadcaster_task:", exc_info=True)


async def handle_index(request): return web.FileResponse('./index.html')

def _zero_fill_performance_data(sparse_data: List[Dict], start_unix: float, end_unix: float, interval_sec: int) -> List[Dict]:
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


def blocking_get_historical_performance(events: List[Dict[str, Any]], points: int, interval_sec: int) -> List[Dict[str, Any]]:
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
    recent_events = [e for e in events if e.get('ts_unix', 0) >= cutoff_unix and e.get('ts_unix', 0) < last_full_bin_unix + interval_sec]
    if not recent_events:
        return _zero_fill_performance_data([], cutoff_unix, last_full_bin_unix, interval_sec)

    buckets: Dict[int, Dict[str, int]] = {}
    for event in recent_events:
        ts_unix = event['ts_unix']
        bucket_start_unix = int(ts_unix / interval_sec) * interval_sec
        if bucket_start_unix not in buckets:
            buckets[bucket_start_unix] = {'ingress_bytes': 0, 'egress_bytes': 0, 'ingress_pieces': 0, 'egress_pieces': 0, 'total_ops': 0}

        bucket = buckets[bucket_start_unix]
        bucket['total_ops'] += 1
        if event.get('status') == 'success':
            category, size = event.get('category'), event.get('size', 0)
            if category == 'get':
                bucket['egress_bytes'] += size; bucket['egress_pieces'] += 1
            elif category == 'put':
                bucket['ingress_bytes'] += size; bucket['ingress_pieces'] += 1

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

def blocking_get_aggregated_performance(node_names: List[str], time_window_hours: int) -> List[Dict[str, Any]]:
    if not node_names: return []
    log.info(f"Fetching AGGREGATED performance for nodes {node_names} (last {time_window_hours} hours).")
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    start_time = now_dt - datetime.timedelta(hours=time_window_hours)
    start_time_iso = start_time.isoformat()
    placeholders = ','.join('?' * len(node_names))

    if time_window_hours <= 1: bin_size_min = 2
    elif time_window_hours <= 6: bin_size_min = 10
    else: bin_size_min = 30
    bin_sec = bin_size_min * 60

    sparse_results = []
    with sqlite3.connect(DATABASE_FILE, timeout=20, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row; cursor = conn.cursor()
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
            iso_ts = row_dict.get('time_bucket_start_iso') or datetime.datetime.fromtimestamp(ts_unix, tz=datetime.timezone.utc).isoformat()
            sparse_results.append({
                "timestamp": iso_ts,
                "ingress_mbps": round((row_dict.get('ingress_bytes', 0) * 8) / (actual_bin_sec * 1e6), 2), "egress_mbps": round((row_dict.get('egress_bytes', 0) * 8) / (actual_bin_sec * 1e6), 2),
                "ingress_bytes": row_dict.get('ingress_bytes', 0), "egress_bytes": row_dict.get('egress_bytes', 0),
                "ingress_pieces": row_dict.get('ingress_pieces', 0), "egress_pieces": row_dict.get('egress_pieces', 0),
                "concurrency": round(row_dict.get('total_ops', 0) / actual_bin_sec, 2) if actual_bin_sec > 0 else 0,
                "total_ops": row_dict.get('total_ops', 0), "bin_duration_seconds": actual_bin_sec
            })

    filled_results = _zero_fill_performance_data(sparse_results, start_time.timestamp(), now_dt.timestamp(), actual_bin_sec)
    log.info(f"Returning {len(filled_results)} aggregated performance data points for nodes {node_names}.")
    return filled_results

def blocking_get_hashstore_stats(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
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
            else: # handle empty list case to return no results
                 where_clauses.append("1 = 0")
        else: # It's a single string
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

    with sqlite3.connect(DATABASE_FILE, timeout=10, detect_types=0) as conn:
        conn.row_factory = sqlite3.Row
        results = [dict(row) for row in conn.execute(query, params).fetchall()]

    log.info(f"Found {len(results)} hashstore events matching filters.")
    return results

async def send_initial_stats(app, ws, view: List[str]):
    """
    Sends stats to a client upon connection or view change.
    For the optimized version, we use the incremental stats if available.
    """
    view_tuple = tuple(view)

    # Try to send from cache first
    if view_tuple in app_state['stats_cache']:
        try:
            await ws.send_json(app_state['stats_cache'][view_tuple])
            return
        except (ConnectionResetError, asyncio.CancelledError):
            return # Client disconnected

    # If not in cache, compute it now for this one client
    log.info(f"Cache miss for view {view}. Computing stats on-demand.")
    
    # Create temporary incremental stats for this view
    stats = IncrementalStats()
    nodes_to_process = view if view != ['Aggregate'] else list(app_state['nodes'].keys())
    
    # Process all current events
    for node_name in nodes_to_process:
        if node_name in app_state['nodes']:
            for event in list(app_state['nodes'][node_name]['live_events']):
                stats.add_event(event, app_state['TOKEN_REGEX'])
    
    # Update live stats
    all_events = []
    for node_name in nodes_to_process:
        if node_name in app_state['nodes']:
            all_events.extend(list(app_state['nodes'][node_name]['live_events']))
    stats.update_live_stats(all_events)
    
    # Get historical stats
    historical_stats = get_historical_stats(view, app_state['nodes'])
    
    # Generate and send payload
    try:
        payload = stats.to_payload(historical_stats)
        app_state['stats_cache'][view_tuple] = payload
        await ws.send_json(payload)
    except (ConnectionResetError, asyncio.CancelledError):
        pass # Client disconnected during computation
    except Exception:
        log.error(f"Error computing initial stats for view {view}:", exc_info=True)

async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    app_state['websockets'][ws] = {"view": ["Aggregate"]}

    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")

    node_names = list(app['nodes'].keys())
    await ws.send_json({"type": "init", "nodes": node_names})
    await send_initial_stats(app, ws, ["Aggregate"])

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

                    if msg_type == 'set_view':
                        new_view = data.get('view')
                        if isinstance(new_view, list) and new_view:
                            valid_nodes = set(app['nodes'].keys())
                            is_aggregate = new_view == ['Aggregate']
                            are_nodes_valid = all(node in valid_nodes for node in new_view)

                            if is_aggregate or are_nodes_valid:
                                app_state['websockets'][ws]['view'] = new_view
                                log.info(f"Client switched view to: {new_view}")
                                await send_initial_stats(app, ws, new_view)

                    elif msg_type == 'get_historical_performance':
                        view = data.get('view') # This is now a list
                        points = data.get('points', 150)
                        interval = data.get('interval_sec', PERFORMANCE_INTERVAL_SECONDS)
                        loop = asyncio.get_running_loop()

                        events_to_process = []
                        nodes_to_query = view if view != ['Aggregate'] else list(app_state['nodes'].keys())
                        for node_name in nodes_to_query:
                            if node_name in app_state['nodes']:
                                events_to_process.extend(list(app_state['nodes'][node_name]['live_events']))

                        historical_data = await loop.run_in_executor(
                            app['db_executor'], blocking_get_historical_performance,
                            events_to_process, points, interval
                        )
                        payload = {"type": "historical_performance_data", "view": view, "performance_data": historical_data}
                        await ws.send_json(payload)

                    elif msg_type == 'get_aggregated_performance':
                        view = data.get('view') # This is a list
                        time_window_hours = data.get('hours', 1)
                        loop = asyncio.get_running_loop()

                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())

                        aggregated_data = await loop.run_in_executor(
                            app['db_executor'], blocking_get_aggregated_performance,
                            nodes_to_query, time_window_hours
                        )

                        payload = {"type": "aggregated_performance_data", "view": view, "performance_data": aggregated_data}
                        await ws.send_json(payload)

                    elif msg_type == 'get_hashstore_stats':
                        filters = data.get('filters', {})
                        loop = asyncio.get_running_loop()
                        hashstore_data = await loop.run_in_executor(
                            app['db_executor'], blocking_get_hashstore_stats, filters
                        )
                        payload = {"type": "hashstore_stats_data", "data": hashstore_data}
                        await ws.send_json(payload)


                except Exception:
                    log.error("Could not parse websocket message:", exc_info=True)

    finally:
        if ws in app_state['websockets']:
            del app_state['websockets'][ws]
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws


def load_initial_state_from_db(nodes_config: Dict[str, Dict[str, Any]]):
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
                    log.error(f"Failed to process a database row for re-hydration.", exc_info=True)


            initial_state[node_name] = node_state
    return initial_state

def blocking_backfill_hourly_stats(node_names: List[str]):
    log.info("[BACKFILL] Starting smart backfill of hourly statistics.")
    with sqlite3.connect(DATABASE_FILE, timeout=30, detect_types=0) as conn:
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

async def start_background_tasks(app):
    log.info("Starting background tasks...")

    log.info("Loading GeoIP database into memory...")
    try:
        app['geoip_reader'] = geoip2.database.Reader(GEOIP_DATABASE_PATH)
        log.info("GeoIP database loaded successfully.")
    except FileNotFoundError:
        log.critical(f"GeoIP database not found at '{GEOIP_DATABASE_PATH}'. Please download it. Exiting.")
        sys.exit(1)
    except Exception as e:
        log.critical(f"Failed to load GeoIP database: {e}", exc_info=True)
        sys.exit(1)

    app['db_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    app['log_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=len(app['nodes']) + 1)
    app['tasks'] = []
    app['log_reader_shutdown_events'] = {}

    loop = asyncio.get_running_loop()

    # Run the one-time backfill before starting other tasks
    node_names = list(app['nodes'].keys())
    await loop.run_in_executor(app['db_executor'], blocking_backfill_hourly_stats, node_names)

    initial_node_states = await loop.run_in_executor(
        app['db_executor'], load_initial_state_from_db, app['nodes']
    )
    app_state['nodes'] = initial_node_states
    log.info("Initial state has been populated from the database.")

    for node_name, node_config in app['nodes'].items():
        if node_name not in app_state['nodes']:
             app_state['nodes'][node_name] = {
                'live_events': deque(),
                'active_compactions': {},
                'unprocessed_performance_events': [],
                'has_new_events': False
            }

        line_queue = asyncio.Queue(maxsize=5000)

        if node_config['type'] == 'file':
            shutdown_event = threading.Event()
            app['log_reader_shutdown_events'][node_name] = shutdown_event
            loop.run_in_executor(
                app['log_executor'],
                blocking_log_reader,
                node_config['path'], loop, line_queue, shutdown_event
            )
        elif node_config['type'] == 'network':
            app['tasks'].append(asyncio.create_task(
                network_log_reader_task(
                    node_name, node_config['host'], node_config['port'], line_queue
                )
            ))

        app['tasks'].append(asyncio.create_task(log_processor_task(app, node_name, line_queue)))

    app['tasks'].extend([
        asyncio.create_task(prune_live_events_task(app)),
        asyncio.create_task(incremental_stats_updater_task(app)),  # NEW: replaces stats_baker_and_broadcaster_task
        asyncio.create_task(performance_aggregator_task(app)),
        asyncio.create_task(websocket_batch_broadcaster_task(app)),
        asyncio.create_task(debug_logger_task(app)),
        asyncio.create_task(database_writer_task(app)),
        asyncio.create_task(hourly_aggregator_task(app)),
        asyncio.create_task(database_pruner_task(app))
    ])

async def cleanup_background_tasks(app):
    log.warning("Application cleanup started.")
    for task in app.get('tasks', []):
        task.cancel()
    if 'tasks' in app:
        await asyncio.gather(*app['tasks'], return_exceptions=True)
    log.info("Asyncio background tasks cancelled.")

    # Explicitly signal file reader threads to shut down
    for node_name, event in app.get('log_reader_shutdown_events', {}).items():
        log.info(f"Signaling file log reader for '{node_name}' to shut down.")
        event.set()

    if 'geoip_reader' in app and hasattr(app['geoip_reader'], 'close'):
        app['geoip_reader'].close()
        log.info("GeoIP database reader closed.")

    for executor_name in ['db_executor', 'log_executor']:
        if executor_name in app and app[executor_name]:
            app[executor_name].shutdown(wait=True)
    log.info("Executors shut down.")


def parse_nodes(args: List[str]) -> Dict[str, Dict[str, Any]]:
    nodes = {}
    if not args:
        log.critical("No nodes specified. Use --node 'NodeName:/path/to/log' or 'NodeName:host:port' argument.")
        sys.exit(1)

    for arg in args:
        parts = arg.split(':', 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            log.critical(f"Invalid node format: '{arg}'. Expected 'NodeName:/path/to/log' or 'NodeName:host:port'.")
            sys.exit(1)
        node_name, source = parts

        # Heuristic 1: If the source path exists on disk, it's a file.
        if os.path.exists(source):
            log.info(f"Configured node '{node_name}' with file source '{source}' (path exists).")
            nodes[node_name] = {'type': 'file', 'path': source}
            continue

        # Heuristic 2: If it doesn't exist, check if it looks like host:port.
        try:
            host, port_str = source.rsplit(':', 1)
            port = int(port_str)
            if 1 <= port <= 65535 and host:
                log.info(f"Configured node '{node_name}' with network source '{source}'.")
                nodes[node_name] = {'type': 'network', 'host': host, 'port': port}
                continue
        except (ValueError, TypeError):
            pass # Doesn't look like a network address.

        # Fallback: Treat as a non-existent file path. This is valid for log files that will be created.
        log.warning(f"Configured node '{node_name}' with file source '{source}' (path does not currently exist).")
        nodes[node_name] = {'type': 'file', 'path': source}
    return nodes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Storagenode Pro Monitor - Optimized Version")
    parser.add_argument('--node', action='append', help="Specify a node in 'NodeName:/path/to/log.log' or 'NodeName:host:port' format. Can be used multiple times.", required=True)
    parser.add_argument('--debug', action='store_true', help="Enable debug logging.")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

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