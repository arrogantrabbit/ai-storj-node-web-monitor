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
from typing import Deque, Dict, Any, List, Set


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

# Custom type for a node's state
NodeState = Dict[str, Any]

# --- In-Memory State ---
app_state: Dict[str, Any] = {
    'websockets': {},  # {ws: {"view": "Aggregate"}}
    'nodes': {},  # { "node_name": NodeState }
    'geoip_cache': {},
    'db_write_queue': asyncio.Queue(maxsize=DB_QUEUE_MAX_SIZE),
}


def init_db():
    conn = sqlite3.connect(DATABASE_FILE, timeout=10)
    cursor = conn.cursor()

    # --- Schema migration for events table ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(events);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'node_name' not in columns:
            log.info("Upgrading 'events' table. Adding 'node_name' column.")
            cursor.execute("ALTER TABLE events ADD COLUMN node_name TEXT;")
            # Backfill with a default value if needed, though for new data it's fine
            cursor.execute("UPDATE events SET node_name = 'default' WHERE node_name IS NULL;")

    cursor.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, piece_id TEXT, satellite_id TEXT, remote_ip TEXT, country TEXT, latitude REAL, longitude REAL, error_reason TEXT, node_name TEXT)')

    # --- Schema migration for hourly_stats table ---
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hourly_stats';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(hourly_stats);")
        columns = [col[1] for col in cursor.fetchall()]
        if 'node_name' not in columns:
            log.info("Upgrading 'hourly_stats' table. Recreating with new composite primary key.")
            # The easiest way to change a primary key is to rename, create, and copy data
            cursor.execute("ALTER TABLE hourly_stats RENAME TO hourly_stats_old;")
            cursor.execute('CREATE TABLE hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
            cursor.execute("INSERT INTO hourly_stats (hour_timestamp, node_name, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size) SELECT hour_timestamp, 'default', dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size FROM hourly_stats_old;")
            cursor.execute("DROP TABLE hourly_stats_old;")
        if 'total_download_size' not in columns:
             cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_download_size INTEGER DEFAULT 0;")
        if 'total_upload_size' not in columns:
             cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_upload_size INTEGER DEFAULT 0;")

    cursor.execute('CREATE TABLE IF NOT EXISTS hourly_stats (hour_timestamp TEXT, node_name TEXT, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0, total_download_size INTEGER DEFAULT 0, total_upload_size INTEGER DEFAULT 0, PRIMARY KEY (hour_timestamp, node_name))')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_node_name ON events (node_name);')
    conn.commit()
    conn.close()
    log.info("Database schema is valid.")


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
                timestamp_str = line.split("INFO")[0].strip() if "INFO" in line else line.split("ERROR")[0].strip()
                timestamp_obj = datetime.datetime.fromisoformat(timestamp_str)
                json_match = re.search(r'\{.*\}', line)
                if not json_match: continue
                log_data = json.loads(json_match.group(0))

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
    with sqlite3.connect(db_path, timeout=10) as conn:
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
            log.info(f"[DB_WRITER] Writing {len(events_to_write)} events. Queue size: {app_state['db_write_queue'].qsize()}")
            loop = asyncio.get_running_loop()
            try: await loop.run_in_executor(app['db_executor'], blocking_db_batch_write, DATABASE_FILE, events_to_write)
            except Exception: log.error("Error during blocking database write execution:", exc_info=True)

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

        # Concurrency is calculated from events in the last second for this specific node
        concurrency = sum(1 for event in reversed(node_state['live_events']) if event['ts_unix'] > now - 1)

        for event in new_events_to_process:
            if 'GET' in event['action']: egress_bytes += event['size']; egress_pieces += 1
            else: ingress_bytes += event['size']; ingress_pieces += 1

        ingress_mbps = (ingress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)
        egress_mbps = (egress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)

        payload = { "type": "performance_update", "node_name": node_name, "timestamp": datetime.datetime.utcnow().isoformat(), "ingress_mbps": round(ingress_mbps, 2), "egress_mbps": round(egress_mbps, 2), "ingress_bytes": ingress_bytes, "egress_bytes": egress_bytes, "ingress_pieces": ingress_pieces, "egress_pieces": egress_pieces, "concurrency": concurrency }

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
    now = datetime.datetime.now().astimezone(); hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_start_iso = hour_start.isoformat(); next_hour_start_iso = (hour_start + datetime.timedelta(hours=1)).isoformat()

    with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
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
                FROM events WHERE timestamp >= ? AND timestamp < ? AND node_name = ?
            """
            stats = conn.execute(query, (hour_start_iso, next_hour_start_iso, node_name)).fetchone()

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
        try:
            loop = asyncio.get_running_loop()
            node_names = list(app['nodes'].keys())
            await loop.run_in_executor(app['db_executor'], blocking_hourly_aggregation, node_names)
        except Exception:
            log.error("Error in hourly aggregator task:", exc_info=True)
        await asyncio.sleep(60 * HOURLY_AGG_INTERVAL_MINUTES)

def blocking_prepare_stats(view: str, all_nodes_state: Dict[str, NodeState]):
    events_copy = []
    if view == 'Aggregate':
        for node_state in all_nodes_state.values():
            events_copy.extend(list(node_state['live_events']))
    elif view in all_nodes_state:
        events_copy = list(all_nodes_state[view]['live_events'])

    if not events_copy: return None
    first_event_iso, last_event_iso = min(e['timestamp'] for e in events_copy).isoformat(), max(e['timestamp'] for e in events_copy).isoformat()

    dl_s, dl_f, ul_s, ul_f, a_s, a_f = 0, 0, 0, 0, 0, 0
    total_dl_size, total_ul_size = 0, 0
    sats, dls, uls, cdl, cul, hp, errs = {}, Counter(), Counter(), Counter(), Counter(), Counter(), Counter()

    for e in events_copy:
        action, status, sat_id, size, country, piece_id, error_reason = e['action'], e['status'], e['satellite_id'], e['size'], e['location']['country'], e['piece_id'], e['error_reason']
        if sat_id not in sats: sats[sat_id] = {'uploads': 0, 'downloads': 0, 'audits': 0, 'ul_success': 0, 'dl_success': 0, 'audit_success': 0, 'total_upload_size': 0, 'total_download_size': 0}
        if action == 'GET_AUDIT':
            sats[sat_id]['audits'] += 1
            if status == 'success': a_s += 1; sats[sat_id]['audit_success'] += 1
            else: a_f += 1; errs[error_reason] += 1
        elif 'GET' in action:
            sats[sat_id]['downloads'] += 1; hp[piece_id] += 1; dls[get_size_bucket(size)] += 1
            if country: cdl[country] += size
            if status == 'success': dl_s += 1; sats[sat_id]['dl_success'] += 1; sats[sat_id]['total_download_size'] += size; total_dl_size += size
            else: dl_f += 1; errs[error_reason] += 1
        elif action == 'PUT':
            sats[sat_id]['uploads'] += 1; uls[get_size_bucket(size)] += 1
            if country: cul[country] += size
            if status == 'success': ul_s += 1; sats[sat_id]['ul_success'] += 1; sats[sat_id]['total_upload_size'] += size; total_ul_size += size
            else: ul_f += 1; errs[error_reason] += 1

    hist_stats = []
    with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        if view == 'Aggregate':
             # For aggregate view, we need to aggregate the historical data too.
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

    # Calculate average speed over the last minute from live events
    one_min_ago = time.time() - 60
    live_dl_bytes = sum(e['size'] for e in events_copy if 'GET' in e['action'] and e['status'] == 'success' and e['ts_unix'] > one_min_ago)
    live_ul_bytes = sum(e['size'] for e in events_copy if 'PUT' in e['action'] and e['status'] == 'success' and e['ts_unix'] > one_min_ago)
    avg_egress_mbps = (live_dl_bytes * 8) / (60 * 1e6)
    avg_ingress_mbps = (live_ul_bytes * 8) / (60 * 1e6)


    return { "type": "stats_update", "first_event_iso": first_event_iso, "last_event_iso": last_event_iso, "overall": {"dl_success": dl_s, "dl_fail": dl_f, "ul_success": ul_s, "ul_fail": ul_f, "audit_success": a_s, "audit_fail": a_f, "avg_egress_mbps": avg_egress_mbps, "avg_ingress_mbps": avg_ingress_mbps}, "satellites": sorted([{'satellite_id': k, **v} for k, v in sats.items()], key=lambda x: x['uploads'] + x['downloads'], reverse=True), "download_sizes": [{'bucket': k, 'count': v} for k, v in dls.most_common(10)], "upload_sizes": [{'bucket': k, 'count': v} for k, v in uls.most_common(10)], "historical_stats": hist_stats, "error_categories": [{'reason': k, 'count': v} for k,v in errs.most_common(5)], "top_pieces": [{'id': k, 'count': v} for k,v in hp.most_common(5)], "top_countries_dl": [{'country': k, 'size': v} for k,v in cdl.most_common(5) if k], "top_countries_ul": [{'country': k, 'size': v} for k,v in cul.most_common(5) if k] }

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
            # Create tasks for each websocket to send its specific view data
            tasks = [send_stats_for_view(app, ws, ws_state['view']) for ws, ws_state in app_state['websockets'].items()]
            if tasks:
                await asyncio.gather(*tasks)
        except Exception:
            log.error("Error in periodic_stats_updater:", exc_info=True)


async def handle_index(request): return web.FileResponse('./index.html')

async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    app_state['websockets'][ws] = {"view": "Aggregate"}

    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")

    # Send init message with available nodes
    node_names = ["Aggregate"] + list(app['nodes'].keys())
    await ws.send_json({"type": "init", "nodes": node_names})

    # Send initial stats for the default view
    await send_stats_for_view(app, ws, "Aggregate")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get('type') == 'set_view':
                        new_view = data.get('view')
                        if new_view in node_names:
                            app_state['websockets'][ws]['view'] = new_view
                            log.info(f"Client switched view to: {new_view}")
                            await send_stats_for_view(app, ws, new_view)
                except Exception:
                    log.error("Could not parse websocket message:", exc_info=True)

    finally:
        if ws in app_state['websockets']:
            del app_state['websockets'][ws]
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws


async def start_background_tasks(app):
    log.info("Starting background tasks...")
    app['db_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    app['log_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=len(app['nodes']) + 1)

    app['tasks'] = []

    # Create tasks for each node
    for node_name, log_path in app['nodes'].items():
        app_state['nodes'][node_name] = {
            'live_events': deque(),
            'last_perf_event_index': 0,
        }
        app['tasks'].append(asyncio.create_task(log_tailer_task(app, node_name, log_path)))
        app['tasks'].append(asyncio.create_task(performance_calculator(app, node_name)))

    # Generic tasks
    app['tasks'].extend([
        asyncio.create_task(prune_live_events_task(app)),
        asyncio.create_task(periodic_stats_updater(app)),
        asyncio.create_task(debug_logger_task(app)),
        asyncio.create_task(database_writer_task(app)),
        asyncio.create_task(hourly_aggregator_task(app))
    ])

async def cleanup_background_tasks(app):
    log.warning("Application cleanup started.")
    for task in app.get('tasks', []):
        task.cancel()
    if 'tasks' in app:
        await asyncio.gather(*app['tasks'], return_exceptions=True)
    log.info("Background tasks cancelled.")

    for executor_name in ['db_executor', 'log_executor']:
        if executor_name in app and app[executor_name]:
            app[executor_name].shutdown(wait=True)
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
