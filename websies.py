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
from collections import deque
import concurrent.futures
import os
import time

# --- Configuration ---
LOG_FILE_PATH = '/var/log/storj.log'
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
DATABASE_FILE = 'storj_stats.db'
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
BANDWIDTH_INTERVAL_SECONDS = 2
DB_CLEANUP_INTERVAL_HOURS = 1
DB_MAX_DATA_AGE_HOURS = 24
MAX_GEOIP_CACHE_SIZE = 5000

# --- In-Memory State ---
app_state = {
    'websockets': set(),
    'live_events': deque(),
    'geoip_cache': {}
}

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp DATETIME, action TEXT, status TEXT, size INTEGER, satellite_id TEXT, remote_ip TEXT, latitude REAL, longitude REAL, error_reason TEXT)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);')
    conn.commit()
    conn.close()

# --- Helper function for better piece size bucketing ---
def get_size_bucket(size_in_bytes):
    if size_in_bytes < 1024: return "< 1 KB"
    kb = size_in_bytes / 1024
    if kb < 4: return "1-4 KB"
    if kb < 16: return "4-16 KB"
    if kb < 64: return "16-64 KB"
    if kb < 256: return "64-256 KB"
    if kb < 1024: return "256 KB - 1 MB"
    return "> 1 MB"

# --- THE NEW, LOG-ROTATION-AWARE Tailing Task ---
async def log_tailer_task(app):
    print(f"Starting log monitoring for: {LOG_FILE_PATH}")
    geoip_reader = geoip2.database.Reader(GEOIP_DATABASE_PATH)
    geoip_cache, db_executor, loop = app_state['geoip_cache'], app['db_executor'], asyncio.get_running_loop()
    
    # --- THIS IS THE FIX: An outer loop to handle re-opening the file ---
    while True:
        try:
            with open(LOG_FILE_PATH, 'r') as f:
                current_inode = os.fstat(f.fileno()).st_ino
                print(f"Tailing log file with inode {current_inode}")
                f.seek(0, os.SEEK_END)

                # Inner loop for reading lines from the current file
                while True:
                    line = f.readline()
                    if not line:
                        await asyncio.sleep(0.2)
                        try:
                            # Check for rotation by comparing inodes
                            if os.stat(LOG_FILE_PATH).st_ino != current_inode:
                                print(f"Log rotation detected. Re-opening file...")
                                break # Exit inner loop to trigger re-open
                        except FileNotFoundError:
                            print(f"Log file not found. Waiting for it to reappear...")
                            break # Exit inner loop
                        continue
                    
                    # --- Same processing logic as before ---
                    try:
                        timestamp_str = line.split("INFO")[0].strip() if "INFO" in line else line.split("ERROR")[0].strip()
                        timestamp_obj = datetime.datetime.fromisoformat(timestamp_str)
                        json_match = re.search(r'\{.*\}', line)
                        if not json_match: continue
                        log_data = json.loads(json_match.group(0))
                        status, error_reason = "success", None
                        if "download canceled" in line: status, error_reason = "canceled", log_data.get("reason")
                        elif "failed" in line or "ERROR" in line: status, error_reason = "failed", log_data.get("error")
                        action, size, sat_id, remote_addr = log_data.get("Action"), log_data.get("Size"), log_data.get("Satellite ID"), log_data.get("Remote Address")
                        if not all([action, size, sat_id, remote_addr]): continue
                        remote_ip = remote_addr.split(':')[0]
                        location = geoip_cache.get(remote_ip)
                        if location is None:
                            try:
                                geo_response = geoip_reader.city(remote_ip)
                                location = {"lat": geo_response.location.latitude, "lon": geo_response.location.longitude}
                            except geoip2.errors.AddressNotFoundError: location = {"lat": None, "lon": None}
                            if len(geoip_cache) > MAX_GEOIP_CACHE_SIZE: geoip_cache.pop(next(iter(geoip_cache)))
                            geoip_cache[remote_ip] = location
                        event = {"ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action, "status": status, "size": size,
                                 "satellite_id": sat_id, "remote_ip": remote_ip, "location": location, "error_reason": error_reason}
                        app_state['live_events'].append(event)
                        broadcast_payload = {"type": "log_entry", "action": action, "status": status, "location": location,
                                             "error_reason": error_reason, "timestamp": timestamp_obj.isoformat()}
                        for ws in set(app_state['websockets']): await ws.send_json(broadcast_payload)
                        await loop.run_in_executor(db_executor, blocking_db_write, DATABASE_FILE, event)
                    except Exception: continue
        except FileNotFoundError:
            print(f"Log file not found. Retrying in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"An unexpected error occurred in the log tailer: {e}. Retrying in 15 seconds.")
            await asyncio.sleep(15)

# --- All other functions are unchanged ---
async def handle_index(request): return web.FileResponse('./index.html')
async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app_state['websockets'].add(ws)
    try:
        await broadcast_full_stats(request.app, ws)
        async for msg in ws: pass
    finally: app_state['websockets'].remove(ws)
    return ws
async def bandwidth_calculator(app):
    while True:
        await asyncio.sleep(BANDWIDTH_INTERVAL_SECONDS)
        if not app_state['live_events']: continue
        ingress_bytes, egress_bytes = 0, 0
        last_event_time = app_state['live_events'][-1]['ts_unix']
        cutoff_unix = last_event_time - BANDWIDTH_INTERVAL_SECONDS
        for event in reversed(app_state['live_events']):
            if event['ts_unix'] < cutoff_unix: break
            if 'GET' in event['action']: egress_bytes += event['size']
            else: ingress_bytes += event['size']
        payload = { "type": "bandwidth", "timestamp": datetime.datetime.utcnow().isoformat(),
                    "ingress_mbps": round((ingress_bytes * 8) / (BANDWIDTH_INTERVAL_SECONDS * 1e6), 2),
                    "egress_mbps": round((egress_bytes * 8) / (BANDWIDTH_INTERVAL_SECONDS * 1e6), 2) }
        for ws in set(app_state['websockets']): await ws.send_json(payload)
async def prune_live_events_task(app):
    while True:
        await asyncio.sleep(60)
        cutoff_unix = time.time() - (STATS_WINDOW_MINUTES * 60)
        while app_state['live_events'] and app_state['live_events'][0]['ts_unix'] < cutoff_unix:
            app_state['live_events'].popleft()
async def periodic_stats_updater(app):
    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)
        await broadcast_full_stats(app)
def blocking_db_write(db_path, event):
    with sqlite3.connect(db_path, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (event['timestamp'].isoformat(), event['action'], event['status'], event['size'],
                        event['satellite_id'], event['remote_ip'], event['location']['lat'],
                        event['location']['lon'], event['error_reason']))
        conn.commit()
async def broadcast_full_stats(app, target_ws=None):
    dl_success, dl_fail, ul_success, ul_fail = 0, 0, 0, 0; satellites, dl_sizes, ul_sizes = {}, {}, {}
    first_event_ts, last_event_ts = None, None
    if app_state['live_events']:
        first_event_ts = app_state['live_events'][0]['timestamp'].isoformat()
        last_event_ts = app_state['live_events'][-1]['timestamp'].isoformat()
    for event in app_state['live_events']:
        is_dl = 'GET' in event['action']
        if event['status'] == 'success':
            if is_dl: dl_success += 1
            else: ul_success += 1
        else:
            if is_dl: dl_fail += 1
            else: ul_fail += 1
        sat_id = event['satellite_id']
        if sat_id not in satellites: satellites[sat_id] = {'uploads': 0, 'downloads': 0}
        if is_dl: satellites[sat_id]['downloads'] += 1
        else: satellites[sat_id]['uploads'] += 1
        size_bucket = get_size_bucket(event['size'])
        if is_dl: dl_sizes[size_bucket] = dl_sizes.get(size_bucket, 0) + 1
        else: ul_sizes[size_bucket] = ul_sizes.get(size_bucket, 0) + 1
    sat_list = [{'satellite_id': k, **v} for k, v in satellites.items()]
    dl_sizes_list = [{'bucket': k, 'count': v} for k, v in dl_sizes.items()]
    ul_sizes_list = [{'bucket': k, 'count': v} for k, v in ul_sizes.items()]
    payload = { "type": "stats_update", "first_event_iso": first_event_ts, "last_event_iso": last_event_ts,
                "overall": {"dl_success": dl_success, "dl_fail": dl_fail, "ul_success": ul_success, "ul_fail": ul_fail},
                "satellites": sorted(sat_list, key=lambda x: x['uploads'] + x['downloads'], reverse=True),
                "download_sizes": sorted(dl_sizes_list, key=lambda x: x['count'], reverse=True)[:10],
                "upload_sizes": sorted(ul_sizes_list, key=lambda x: x['count'], reverse=True)[:10] }
    if target_ws: await target_ws.send_json(payload)
    else:
        for ws in set(app_state['websockets']): await ws.send_json(payload)
async def cleanup_db_task(app):
    while True:
        await asyncio.sleep(3600 * DB_CLEANUP_INTERVAL_HOURS)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(app['db_executor'], blocking_db_cleanup)
def blocking_db_cleanup():
    with sqlite3.connect(DATABASE_FILE, timeout=10) as conn:
        cutoff_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=DB_MAX_DATA_AGE_HOURS)).isoformat()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM events WHERE timestamp < ?", (cutoff_time,))
        conn.commit()
        print(f"[{datetime.datetime.now()}] Database cleanup: Removed {cursor.rowcount} old records.")
async def start_background_tasks(app):
    app['db_executor'] = concurrent.futures.ThreadPoolExecutor()
    app['log_tailer_task'] = asyncio.create_task(log_tailer_task(app))
    app['prune_task'] = asyncio.create_task(prune_live_events_task(app))
    app['stats_task'] = asyncio.create_task(periodic_stats_updater(app))
    app['db_cleanup_task'] = asyncio.create_task(cleanup_db_task(app))
    app['bandwidth_task'] = asyncio.create_task(bandwidth_calculator(app))
async def cleanup_background_tasks(app):
    app['log_tailer_task'].cancel(); app['stats_task'].cancel(); app['db_cleanup_task'].cancel(); app['prune_task'].cancel(); app['bandwidth_task'].cancel()
    app['db_executor'].shutdown()

if __name__ == "__main__":
    init_db()
    app = web.Application()
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', websocket_handler)
    print(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)