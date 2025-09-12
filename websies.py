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

# --- Configuration ---
LOG_FILE_PATH = '/var/log/storagenode.log'
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
DATABASE_FILE = 'storj_stats.db'
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
PERFORMANCE_INTERVAL_SECONDS = 2
HOURLY_AGG_INTERVAL_MINUTES = 10
HISTORICAL_HOURS_TO_SHOW = 6
DB_WRITE_BATCH_INTERVAL_SECONDS = 10
DB_CLEANUP_INTERVAL_HOURS = 1
DB_MAX_DATA_AGE_HOURS = 24
MAX_GEOIP_CACHE_SIZE = 5000

# --- In-Memory State ---
app_state = {
    'websockets': set(),
    'live_events': deque(),
    'geoip_cache': {},
    'db_write_queue': asyncio.Queue(maxsize=20000),
    'last_perf_event_index': 0
}

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, piece_id TEXT, satellite_id TEXT, remote_ip TEXT, country TEXT, latitude REAL, longitude REAL, error_reason TEXT)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')
    cursor.execute('CREATE TABLE IF NOT EXISTS hourly_stats (hour_timestamp TEXT PRIMARY KEY, dl_success INTEGER DEFAULT 0, dl_fail INTEGER DEFAULT 0, ul_success INTEGER DEFAULT 0, ul_fail INTEGER DEFAULT 0, audit_success INTEGER DEFAULT 0, audit_fail INTEGER DEFAULT 0)')
    conn.commit()
    conn.close()

# --- Helper function ---
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
                print(f"Tailing log file with inode {current_inode}")
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        try:
                            if os.stat(log_path).st_ino != current_inode:
                                print("Log rotation detected by blocking reader.")
                                break
                        except FileNotFoundError:
                            print("Log file not found by blocking reader. Waiting...")
                            break
                        continue
                    yield line
        except FileNotFoundError:
            print(f"Log file not found at {log_path}. Retrying in 5 seconds...")
            time.sleep(5)
        except Exception:
            print(f"Error in blocking_log_reader:")
            traceback.print_exc()
            time.sleep(15)

async def log_tailer_task(app):
    loop = asyncio.get_running_loop()
    geoip_reader = geoip2.database.Reader(GEOIP_DATABASE_PATH)
    geoip_cache = app_state['geoip_cache']
    log_generator = blocking_log_reader(LOG_FILE_PATH)
    
    while True:
        try:
            line = await loop.run_in_executor(app['log_executor'], next, log_generator)
            
            try:
                timestamp_str = line.split("INFO")[0].strip() if "INFO" in line else line.split("ERROR")[0].strip()
                timestamp_obj = datetime.datetime.fromisoformat(timestamp_str); json_match = re.search(r'\{.*\}', line)
                if not json_match: continue
                log_data = json.loads(json_match.group(0)); status, error_reason = "success", None
                if "download canceled" in line: status, error_reason = "canceled", log_data.get("reason", "context canceled")
                elif "failed" in line or "ERROR" in line: status, error_reason = "failed", log_data.get("error", "unknown error")
                action, size, piece_id, sat_id, remote_addr = log_data.get("Action"), log_data.get("Size"), log_data.get("Piece ID"), log_data.get("Satellite ID"), log_data.get("Remote Address")
                if not all([action, size, piece_id, sat_id, remote_addr]): continue
                remote_ip = remote_addr.split(':')[0]; location = geoip_cache.get(remote_ip)
                if location is None:
                    try:
                        geo_response = geoip_reader.city(remote_ip)
                        location = {"lat": geo_response.location.latitude, "lon": geo_response.location.longitude, "country": geo_response.country.name}
                    except geoip2.errors.AddressNotFoundError: location = {"lat": None, "lon": None, "country": "Unknown"}
                    if len(geoip_cache) > MAX_GEOIP_CACHE_SIZE: geoip_cache.pop(next(iter(geoip_cache)))
                    geoip_cache[remote_ip] = location
                event = {"ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action, "status": status, "size": size, "piece_id": piece_id,
                         "satellite_id": sat_id, "remote_ip": remote_ip, "location": location, "error_reason": error_reason}
                app_state['live_events'].append(event)
                await app_state['db_write_queue'].put(event)
                broadcast_payload = {"type": "log_entry", "action": action, "status": status, "location": location,
                                     "error_reason": error_reason, "timestamp": timestamp_obj.isoformat()}
                
                for ws in set(app_state['websockets']):
                    try:
                        await ws.send_json(broadcast_payload)
                    except Exception:
                        pass
            
            except (json.JSONDecodeError, AttributeError, KeyError, ValueError) as e:
                print(f"Skipping malformed log line. Error: {e}, Line: {line.strip()}")
                continue
            except Exception:
                print(f"An unexpected error occurred processing a log line.")
                traceback.print_exc()
        except Exception:
            print(f"A critical error occurred in the main loop of log_tailer_task.")
            traceback.print_exc()
            await asyncio.sleep(5)

# --- NEW: Blocking function to prepare stats payload in a thread ---
def blocking_prepare_stats(live_events):
    dl_success, dl_fail, ul_success, ul_fail, audit_success, audit_fail = 0, 0, 0, 0, 0, 0
    satellites = {}
    dl_sizes, ul_sizes = Counter(), Counter()
    countries_dl, countries_ul = Counter(), Counter()
    hot_pieces = Counter()
    error_reasons = Counter()
    first_event_ts, last_event_ts = None, None

    # This is a safe copy for thread-safety
    events_copy = list(live_events)
    if not events_copy:
        return None # Return None if there are no events to process

    first_event_ts, last_event_ts = events_copy[0]['timestamp'].isoformat(), events_copy[-1]['timestamp'].isoformat()
    
    for event in events_copy:
        action, status, sat_id, size, country = event['action'], event['status'], event['satellite_id'], event['size'], event['location']['country']
        
        if sat_id not in satellites:
            satellites[sat_id] = {'uploads': 0, 'downloads': 0, 'audits': 0, 'ul_success': 0, 'dl_success': 0, 'audit_success': 0, 'total_upload_size': 0, 'total_download_size': 0}

        if action == 'GET_AUDIT':
            satellites[sat_id]['audits'] += 1
            if status == 'success': audit_success += 1; satellites[sat_id]['audit_success'] += 1
            else: audit_fail += 1; error_reasons[event['error_reason']] += 1
        elif 'GET' in action:
            satellites[sat_id]['downloads'] += 1
            satellites[sat_id]['total_download_size'] += size
            if country: countries_dl[country] += size
            dl_sizes[get_size_bucket(size)] += 1
            hot_pieces[event['piece_id']] += 1
            if status == 'success': dl_success += 1; satellites[sat_id]['dl_success'] += 1
            else: dl_fail += 1; error_reasons[event['error_reason']] += 1
        else: # PUT
            satellites[sat_id]['uploads'] += 1
            satellites[sat_id]['total_upload_size'] += size
            if country: countries_ul[country] += size
            ul_sizes[get_size_bucket(size)] += 1
            if status == 'success': ul_success += 1; satellites[sat_id]['ul_success'] += 1
            else: ul_fail += 1; error_reasons[event['error_reason']] += 1
    
    historical_stats = []
    try:
        with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            current_hour_start_iso = datetime.datetime.now().astimezone().replace(minute=0, second=0, microsecond=0).isoformat()
            historical_stats = [dict(row) for row in conn.execute("SELECT * FROM hourly_stats WHERE hour_timestamp < ? ORDER BY hour_timestamp DESC LIMIT ?", (current_hour_start_iso, HISTORICAL_HOURS_TO_SHOW)).fetchall()]
    except sqlite3.Error as e:
        print(f"Database error in blocking_prepare_stats: {e}")

    sat_list = [{'satellite_id': k, **v} for k, v in satellites.items()]
    payload = { 
        "type": "stats_update", "first_event_iso": first_event_ts, "last_event_iso": last_event_ts, 
        "overall": {"dl_success": dl_success, "dl_fail": dl_fail, "ul_success": ul_success, "ul_fail": ul_fail, "audit_success": audit_success, "audit_fail": audit_fail},
        "satellites": sorted(sat_list, key=lambda x: x['uploads'] + x['downloads'], reverse=True), 
        "download_sizes": [{'bucket': k, 'count': v} for k, v in dl_sizes.most_common(10)],
        "upload_sizes": [{'bucket': k, 'count': v} for k, v in ul_sizes.most_common(10)],
        "historical_stats": historical_stats,
        "error_categories": [{'reason': k, 'count': v} for k,v in error_reasons.most_common(5)],
        "top_pieces": [{'id': k, 'count': v} for k,v in hot_pieces.most_common(5)],
        "top_countries_dl": [{'country': k, 'size': v} for k,v in countries_dl.most_common(5) if k],
        "top_countries_ul": [{'country': k, 'size': v} for k,v in countries_ul.most_common(5) if k],
    }
    return payload

def blocking_db_batch_write(db_path, events):
    # This function is already blocking and runs in an executor, so it's correct.
    if not events: return
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.cursor()
        data_to_insert = [(e['timestamp'].isoformat(), e['action'], e['status'], e['size'], e['piece_id'], e['satellite_id'], e['remote_ip'], e['location']['country'], e['location']['lat'], e['location']['lon'], e['error_reason']) for e in events]
        cursor.executemany('INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', data_to_insert)
        conn.commit()

async def database_writer_task(app):
    while True:
        await asyncio.sleep(DB_WRITE_BATCH_INTERVAL_SECONDS)
        events_to_write = []
        while not app_state['db_write_queue'].empty():
            try:
                events_to_write.append(app_state['db_write_queue'].get_nowait())
            except asyncio.QueueEmpty: break
        if events_to_write:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(app['db_executor'], blocking_db_batch_write, DATABASE_FILE, events_to_write)

async def handle_index(request): return web.FileResponse('./index.html')
async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10); await ws.prepare(request); app_state['websockets'].add(ws)
    print(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")
    try:
        await broadcast_full_stats(request.app, ws)
        async for msg in ws: pass
    finally:
        app_state['websockets'].remove(ws)
        print(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws

async def performance_calculator(app):
    # This task is lightweight and can remain as is.
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
        payload = { "type": "performance_update", "timestamp": datetime.datetime.utcnow().isoformat(), "ingress_mbps": round((ingress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6), 2), "egress_mbps": round((egress_bytes * 8) / (PERFORMANCE_INTERVAL_SECONDS * 1e6), 2), "ingress_bytes": ingress_bytes, "egress_bytes": egress_bytes, "ingress_pieces": ingress_pieces, "egress_pieces": egress_pieces, "concurrency": concurrency }
        for ws in set(app_state['websockets']): 
            try: await ws.send_json(payload)
            except Exception: pass

async def prune_live_events_task(app):
    while True:
        await asyncio.sleep(60)
        cutoff_unix = time.time() - (STATS_WINDOW_MINUTES * 60)
        events_to_prune_count = 0
        while app_state['live_events'] and app_state['live_events'][0]['ts_unix'] < cutoff_unix:
            app_state['live_events'].popleft()
            events_to_prune_count += 1
        if events_to_prune_count > 0:
            app_state['last_perf_event_index'] = max(0, app_state['last_perf_event_index'] - events_to_prune_count)

# --- MODIFIED: broadcast_full_stats is now a non-blocking async wrapper ---
async def broadcast_full_stats(app, target_ws=None):
    loop = asyncio.get_running_loop()
    
    # Run the heavy computation in a thread pool
    payload = await loop.run_in_executor(app['db_executor'], blocking_prepare_stats, app_state['live_events'])
    
    if payload is None: # Happens if there are no events
        return

    target_ws_list = [target_ws] if target_ws else set(app_state['websockets'])
    for ws in target_ws_list: 
        try:
            await ws.send_json(payload)
        except Exception:
            pass # Ignore broken websockets, they will be cleaned up

async def periodic_stats_updater(app):
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        try:
            await broadcast_full_stats(app)
        except Exception:
            print("Error occurred during periodic_stats_updater.")
            traceback.print_exc()

def blocking_db_cleanup():
    # This function is unchanged
    pass

async def cleanup_db_task(app):
    # This function is unchanged
    pass
    
async def start_background_tasks(app):
    # Create separate executors for DB/CPU-bound tasks and Log I/O
    app['db_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    app['log_executor'] = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    
    app['log_tailer_task'] = asyncio.create_task(log_tailer_task(app))
    app['prune_task'] = asyncio.create_task(prune_live_events_task(app))
    app['stats_task'] = asyncio.create_task(periodic_stats_updater(app))
    app['db_cleanup_task'] = asyncio.create_task(cleanup_db_task(app))
    app['performance_task'] = asyncio.create_task(performance_calculator(app))
    app['db_writer_task'] = asyncio.create_task(database_writer_task(app))

async def cleanup_background_tasks(app):
    task_names = ['log_tailer_task', 'stats_task', 'db_cleanup_task', 'prune_task', 'performance_task', 'db_writer_task']
    for task_name in task_names:
        if task_name in app:
            app[task_name].cancel()
    await asyncio.gather(*[app[task_name] for task_name in task_names if task_name in app], return_exceptions=True)
    
    if 'db_executor' in app: app['db_executor'].shutdown(wait=True)
    if 'log_executor' in app: app['log_executor'].shutdown(wait=True)

if __name__ == "__main__":
    init_db()
    app = web.Application()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', websocket_handler)
    print(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)