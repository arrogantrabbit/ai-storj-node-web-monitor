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

# --- Centralized Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger("StorjMonitor")

# --- Configuration ---
LOG_FILE_PATH = '/var/log/storagenode.log'
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
DATABASE_FILE = 'storj_stats.db'
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
PERFORMANCE_INTERVAL_SECONDS = 2
DB_WRITE_BATCH_INTERVAL_SECONDS = 10
DB_QUEUE_MAX_SIZE = 30000
EXPECTED_DB_COLUMNS = 12
HISTORICAL_HOURS_TO_SHOW = 6
MAX_GEOIP_CACHE_SIZE = 5000
# --- NEW: Added configuration for hourly aggregation ---
HOURLY_AGG_INTERVAL_MINUTES = 10

# --- In-Memory State ---
app_state = { 'websockets': set(), 'live_events': deque(), 'geoip_cache': {}, 'db_write_queue': asyncio.Queue(maxsize=DB_QUEUE_MAX_SIZE), 'last_perf_event_index': 0, 'recent_ingress_mbps': deque(maxlen=3), 'recent_egress_mbps': deque(maxlen=3) }

def init_db():
    conn = sqlite3.connect(DATABASE_FILE, timeout=10)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events';")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(events);")
        columns = cursor.fetchall()
        if len(columns) != EXPECTED_DB_COLUMNS:
            log.critical(f"!!! DATABASE SCHEMA MISMATCH !!! Expected {EXPECTED_DB_COLUMNS} columns, found {len(columns)}.")
            log.critical(f"Please stop the application, delete the file '{DATABASE_FILE}', and restart.")
            sys.exit(1)

    cursor.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, piece_id TEXT, satellite_id TEXT, remote_ip TEXT, country TEXT, latitude REAL, longitude REAL, error_reason TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS hourly_stats (hour_timestamp TEXT PRIMARY KEY, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')

    # --- Schema migration for hourly_stats table ---
    cursor.execute("PRAGMA table_info(hourly_stats);")
    columns = [col[1] for col in cursor.fetchall()]
    if 'total_download_size' not in columns:
        log.info("Upgrading 'hourly_stats' table. Adding 'total_download_size' column.")
        cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_download_size INTEGER DEFAULT 0;")
    if 'total_upload_size' not in columns:
        log.info("Upgrading 'hourly_stats' table. Adding 'total_upload_size' column.")
        cursor.execute("ALTER TABLE hourly_stats ADD COLUMN total_upload_size INTEGER DEFAULT 0;")

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
                log.info(f"Tailing log file with inode {current_inode}")
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        try:
                            if os.stat(log_path).st_ino != current_inode:
                                log.warning("Log rotation detected by blocking reader.")
                                break
                        except FileNotFoundError:
                            log.warning("Log file not found by blocking reader. Waiting...")
                            break
                        continue
                    yield line
        except FileNotFoundError:
            log.error(f"Log file not found at {log_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception:
            log.error(f"Critical error in blocking_log_reader:", exc_info=True)
            time.sleep(15)

async def robust_broadcast(websockets, payload):
    for ws in set(websockets):
        try:
            await ws.send_json(payload)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            log.error("An unexpected error occurred during websocket broadcast:", exc_info=True)

async def log_tailer_task(app):
    loop = asyncio.get_running_loop()
    geoip_reader = geoip2.database.Reader(GEOIP_DATABASE_PATH)
    geoip_cache = app_state['geoip_cache']
    log_generator = blocking_log_reader(LOG_FILE_PATH)
    log.info("Log tailer task started.")

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

                event = {"ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action, "status": status, "size": size, "piece_id": piece_id, "satellite_id": sat_id, "remote_ip": remote_ip, "location": location, "error_reason": error_reason}

                app_state['live_events'].append(event)
                broadcast_payload = {"type": "log_entry", "action": action, "status": status, "location": location, "error_reason": error_reason, "timestamp": timestamp_obj.isoformat()}
                await robust_broadcast(app_state['websockets'], broadcast_payload)

                if app_state['db_write_queue'].full():
                    log.warning(f"Database write queue is full. Pausing log tailing to allow DB to catch up.")
                await app_state['db_write_queue'].put(event)

            except (json.JSONDecodeError, AttributeError, KeyError, ValueError):
                continue
            except Exception:
                log.error(f"Unexpected error processing a log line:", exc_info=True)

        except Exception:
            log.error(f"Critical error in log_tailer_task main loop:", exc_info=True)
            await asyncio.sleep(5)

def blocking_db_batch_write(db_path, events):
    if not events: return
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.cursor()
        data_to_insert = [(e['timestamp'].isoformat(), e['action'], e['status'], e['size'], e['piece_id'], e['satellite_id'], e['remote_ip'], e['location']['country'], e['location']['lat'], e['location']['lon'], e['error_reason']) for e in events]
        cursor.executemany('INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', data_to_insert)
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
        log.info(f"[HEARTBEAT] Clients: {len(app_state['websockets'])}, Live Events: {len(app_state['live_events'])}, Perf Index: {app_state['last_perf_event_index']}, DB Queue: {app_state['db_write_queue'].qsize()}")

async def performance_calculator(app):
    log.info("Performance calculator task started.")
    while True:
        await asyncio.sleep(PERFORMANCE_INTERVAL_SECONDS)
        now = time.time()
        current_event_count = len(app_state['live_events'])
        start_index = app_state['last_perf_event_index']
        new_events_to_process = [app_state['live_events'][i] for i in range(start_index, current_event_count)]
        app_state['last_perf_event_index'] = current_event_count

        ingress_bytes, egress_bytes, ingress_pieces, egress_pieces = 0, 0, 0, 0
        concurrency = sum(1 for event in reversed(app_state['live_events']) if event['ts_unix'] > now - 1)

        for event in new_events_to_process:
            if 'GET' in event['action']: egress_bytes += event['size']; egress_pieces += 1
            else: ingress_bytes += event['size']; ingress_pieces += 1

        ingress_mbps = (ingress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)
        egress_mbps = (egress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6)
        app_state['recent_ingress_mbps'].append(ingress_mbps)
        app_state['recent_egress_mbps'].append(egress_mbps)

        avg_ingress_mbps = sum(app_state['recent_ingress_mbps']) / len(app_state['recent_ingress_mbps']) if app_state['recent_ingress_mbps'] else 0
        avg_egress_mbps = sum(app_state['recent_egress_mbps']) / len(app_state['recent_egress_mbps']) if app_state['recent_egress_mbps'] else 0

        payload = { "type": "performance_update", "timestamp": datetime.datetime.utcnow().isoformat(), "ingress_mbps": round(ingress_mbps, 2), "egress_mbps": round(egress_mbps, 2), "ingress_bytes": ingress_bytes, "egress_bytes": egress_bytes, "ingress_pieces": ingress_pieces, "egress_pieces": egress_pieces, "concurrency": concurrency, "avg_ingress_mbps": round(avg_ingress_mbps, 2), "avg_egress_mbps": round(avg_egress_mbps, 2) }

        await robust_broadcast(app_state['websockets'], payload)

async def prune_live_events_task(app):
    log.info("Event pruning task started.")
    while True:
        await asyncio.sleep(60)
        cutoff_unix = time.time() - (STATS_WINDOW_MINUTES * 60)
        events_to_prune_count = 0
        while app_state['live_events'] and app_state['live_events'][0]['ts_unix'] < cutoff_unix:
            app_state['live_events'].popleft()
            events_to_prune_count += 1
        if events_to_prune_count > 0:
            old_index = app_state['last_perf_event_index']
            app_state['last_perf_event_index'] = max(0, app_state['last_perf_event_index'] - events_to_prune_count)
            log.info(f"[PRUNER] Pruned {events_to_prune_count} events. Adjusted perf_index from {old_index} to {app_state['last_perf_event_index']}.")

# --- NEW: Restored blocking function for hourly aggregation ---
def blocking_hourly_aggregation():
    log.info("[AGGREGATOR] Running hourly aggregation.")
    now = datetime.datetime.now().astimezone(); hour_start = now.replace(minute=0, second=0, microsecond=0)
    hour_start_iso = hour_start.isoformat(); next_hour_start_iso = (hour_start + datetime.timedelta(hours=1)).isoformat()
    with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
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
            FROM events WHERE timestamp >= ? AND timestamp < ?
        """
        stats = conn.execute(query, (hour_start_iso, next_hour_start_iso)).fetchone()

        if stats and stats['dl_s'] is not None:
            conn.execute("""
                INSERT INTO hourly_stats (hour_timestamp, dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail, total_download_size, total_upload_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hour_timestamp) DO UPDATE SET
                    dl_success=excluded.dl_success, dl_fail=excluded.dl_fail,
                    ul_success=excluded.ul_success, ul_fail=excluded.ul_fail,
                    audit_success=excluded.audit_success, audit_fail=excluded.audit_fail,
                    total_download_size=excluded.total_download_size, total_upload_size=excluded.total_upload_size
            """, (hour_start_iso, stats['dl_s'], stats['dl_f'], stats['ul_s'], stats['ul_f'], stats['audit_s'], stats['audit_f'], stats['total_dl_size'], stats['total_ul_size']))
            conn.commit()
            log.info(f"[AGGREGATOR] Wrote hourly stats for {hour_start_iso}.")

# --- NEW: Restored async task for hourly aggregation ---
async def hourly_aggregator_task(app):
    log.info("Hourly aggregator task started.")
    while True:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(app['db_executor'], blocking_hourly_aggregation)
        except Exception:
            log.error("Error in hourly aggregator task:", exc_info=True)
        await asyncio.sleep(60 * HOURLY_AGG_INTERVAL_MINUTES)

def blocking_prepare_stats(live_events):
    events_copy = list(live_events)
    if not events_copy: return None
    first_event_iso, last_event_iso = events_copy[0]['timestamp'].isoformat(), events_copy[-1]['timestamp'].isoformat()

    dl_s, dl_f, ul_s, ul_f, a_s, a_f = 0, 0, 0, 0, 0, 0
    sats, dls, uls, cdl, cul, hp, errs = {}, Counter(), Counter(), Counter(), Counter(), Counter(), Counter()

    for e in events_copy:
        action, status, sat_id, size, country, piece_id, error_reason = e['action'], e['status'], e['satellite_id'], e['size'], e['location']['country'], e['piece_id'], e['error_reason']
        if sat_id not in sats: sats[sat_id] = {'uploads': 0, 'downloads': 0, 'audits': 0, 'ul_success': 0, 'dl_success': 0, 'audit_success': 0, 'total_upload_size': 0, 'total_download_size': 0}
        if action == 'GET_AUDIT':
            sats[sat_id]['audits'] += 1
            if status == 'success': a_s += 1; sats[sat_id]['audit_success'] += 1
            else: a_f += 1; errs[error_reason] += 1
        elif 'GET' in action:
            sats[sat_id]['downloads'] += 1; sats[sat_id]['total_download_size'] += size; hp[piece_id] += 1; dls[get_size_bucket(size)] += 1
            if country: cdl[country] += size
            if status == 'success': dl_s += 1; sats[sat_id]['dl_success'] += 1
            else: dl_f += 1; errs[error_reason] += 1
        elif action == 'PUT':
            sats[sat_id]['uploads'] += 1; sats[sat_id]['total_upload_size'] += size; uls[get_size_bucket(size)] += 1
            if country: cul[country] += size
            if status == 'success': ul_s += 1; sats[sat_id]['ul_success'] += 1
            else: ul_f += 1; errs[error_reason] += 1

    hist_stats = []
    with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        raw_hist_stats = conn.execute("SELECT * FROM hourly_stats ORDER BY hour_timestamp DESC LIMIT ?", (HISTORICAL_HOURS_TO_SHOW,)).fetchall()
        for row in raw_hist_stats:
            d_row = dict(row)
            d_row['dl_mbps'] = ((d_row.get('total_download_size', 0) or 0) * 8) / (3600 * 1000000)
            d_row['ul_mbps'] = ((d_row.get('total_upload_size', 0) or 0) * 8) / (3600 * 1000000)
            hist_stats.append(d_row)

    return { "type": "stats_update", "first_event_iso": first_event_iso, "last_event_iso": last_event_iso, "overall": {"dl_success": dl_s, "dl_fail": dl_f, "ul_success": ul_s, "ul_fail": ul_f, "audit_success": a_s, "audit_fail": a_f}, "satellites": sorted([{'satellite_id': k, **v} for k, v in sats.items()], key=lambda x: x['uploads'] + x['downloads'], reverse=True), "download_sizes": [{'bucket': k, 'count': v} for k, v in dls.most_common(10)], "upload_sizes": [{'bucket': k, 'count': v} for k, v in uls.most_common(10)], "historical_stats": hist_stats, "error_categories": [{'reason': k, 'count': v} for k,v in errs.most_common(5)], "top_pieces": [{'id': k, 'count': v} for k,v in hp.most_common(5)], "top_countries_dl": [{'country': k, 'size': v} for k,v in cdl.most_common(5) if k], "top_countries_ul": [{'country': k, 'size': v} for k,v in cul.most_common(5) if k] }

async def broadcast_full_stats(app, target_ws=None):
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(app['db_executor'], blocking_prepare_stats, app_state['live_events'])
    if payload:
        targets = [target_ws] if target_ws else app_state['websockets']
        await robust_broadcast(targets, payload)

async def periodic_stats_updater(app):
    log.info("Periodic stats updater task started.")
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        try:
            await broadcast_full_stats(app)
        except Exception:
            log.error("Error in periodic_stats_updater:", exc_info=True)

async def handle_index(request): return web.FileResponse('./index.html')
async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app_state['websockets'].add(ws)
    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")
    await broadcast_full_stats(request.app, ws)
    try:
        async for msg in ws: pass
    finally:
        app_state['websockets'].remove(ws)
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws

async def start_background_tasks(app):
    log.info("Starting background tasks...")
    app['db_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    app['log_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    # --- MODIFIED: Added hourly_aggregator_task to the startup list ---
    tasks_to_start = [log_tailer_task, prune_live_events_task, periodic_stats_updater, performance_calculator, debug_logger_task, database_writer_task, hourly_aggregator_task]
    for task_func in tasks_to_start:
        app[task_func.__name__] = asyncio.create_task(task_func(app))

async def cleanup_background_tasks(app):
    log.warning("Application cleanup started.")
    tasks = [t for t in app if t.endswith('_task')]
    for task_name in tasks: app[task_name].cancel()
    await asyncio.gather(*[app[t] for t in tasks], return_exceptions=True)
    log.info("Background tasks cancelled.")

    for executor_name in ['db_executor', 'log_executor']:
        if executor_name in app: app[executor_name].shutdown(wait=True)
    log.info("Executors shut down.")

if __name__ == "__main__":
    init_db()
    app = web.Application()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', websocket_handler)

    log.info(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)
