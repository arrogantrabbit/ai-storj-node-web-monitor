import asyncio
import datetime
import json
import logging
import os
import re
import threading
import time
from typing import Optional, Dict

import geoip2.database
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .state import app_state
from .websocket_utils import robust_broadcast
from .server import get_active_compactions_payload
from .database import blocking_write_hashstore_log

log = logging.getLogger("StorjMonitor.LogProcessor")


# --- New Helper Function for Parsing Size Strings ---
def parse_size_to_bytes(size_str: str) -> int:
    if not isinstance(size_str, str): return 0
    size_str = size_str.strip().upper()
    units = {"B": 1, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4}
    try:
        # Split number from unit
        value_str = "".join(re.findall(r'[\d\.]', size_str))
        unit_str = "".join(re.findall(r'[A-Z]', size_str))
        if not unit_str.endswith("B"): unit_str += "B"
        if unit_str == "KB": unit_str = "KIB"  # Handle common case

        value = float(value_str)
        unit_multiplier = next((v for k, v in units.items() if k.startswith(unit_str)), 1)
        return int(value * unit_multiplier)

    except (ValueError, IndexError, StopIteration):
        return 0


def parse_duration_str_to_seconds(duration_str: str) -> Optional[float]:
    """
    Parses a complex duration string like '1m37.535505102s' or '42.281s' into seconds.
    Handles hours (h), minutes (m), seconds (s), and milliseconds (ms).
    """
    if not isinstance(duration_str, str):
        return None

    duration_str = duration_str.strip()
    total_seconds = 0.0

    # This regex will find all number-unit pairs, like "1m", "37.5s", "500ms"
    # IMPORTANT: 'ms' must come before 'm' in alternation to match correctly
    pattern = re.compile(r'(\d+\.?\d*)\s*(ms|h|m|s)')
    matches = pattern.findall(duration_str)

    # If there are no unit matches, try to parse the whole string as a float (in seconds)
    if not matches:
        try:
            return float(duration_str)
        except ValueError:
            return None # Return None if it's not a simple float either

    try:
        for value_str, unit in matches:
            value = float(value_str)
            if unit == 'h':
                total_seconds += value * 3600
            elif unit == 'm':
                total_seconds += value * 60
            elif unit == 's':
                total_seconds += value
            elif unit == 'ms':
                total_seconds += value / 1000.0
        return total_seconds
    except (ValueError, TypeError):
        # This will catch errors during float conversion if the regex somehow fails
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


# --- New Centralized Parsing Logic ---
JSON_RE = re.compile(r'\{.*\}')


def parse_log_line(line: str, node_name: str, geoip_reader, geoip_cache: Dict) -> Optional[Dict]:
    """
    Parses a single log line and returns a structured dictionary, or None if the line is irrelevant.
    This function is now the single source of truth for parsing logic.
    """
    try:
        if 'piecestore' not in line and 'hashstore' not in line:
            return None

        log_level_part = "INFO" if "INFO" in line else "DEBUG" if "DEBUG" in line else "ERROR" if "ERROR" in line else None
        if not log_level_part: return None

        parts = line.split(log_level_part)
        timestamp_str = parts[0].strip()
        timestamp_obj = datetime.datetime.fromisoformat(timestamp_str).astimezone().astimezone(datetime.timezone.utc)

        json_match = JSON_RE.search(line)
        if not json_match: return None
        log_data = json.loads(json_match.group(0))

        # --- Hashstore Log Processing ---
        if "hashstore" in line:
            hashstore_action = line.split(log_level_part)[1].split("hashstore")[1].strip().split('\t')[0]
            satellite = log_data.get("satellite")
            store = log_data.get("store")
            if not all([hashstore_action, satellite, store]): return None

            compaction_key = f"{satellite}:{store}"
            if hashstore_action == "beginning compaction":
                return {"type": "hashstore_begin", "key": compaction_key, "timestamp": timestamp_obj}
            elif hashstore_action == "finished compaction":
                stats = log_data.get("stats", {})
                table_stats = stats.get("Table", {})
                duration_str = log_data.get("duration")
                duration_seconds = 0
                if duration_str:
                    parsed_duration = parse_duration_str_to_seconds(duration_str)
                    if parsed_duration is not None: duration_seconds = parsed_duration

                compaction_stats = {
                    "node_name": node_name, "satellite": satellite, "store": store,
                    "last_run_iso": timestamp_obj.isoformat(),
                    "duration": duration_seconds,
                    "data_reclaimed_bytes": parse_size_to_bytes(stats.get("DataReclaimed", "0 B")),
                    "data_rewritten_bytes": parse_size_to_bytes(stats.get("DataRewritten", "0 B")),
                    "table_load": (table_stats.get("Load") or 0) * 100,
                    "trash_percent": stats.get("TrashPercent", 0) * 100,
                }
                return {"type": "hashstore_end", "key": compaction_key, "timestamp": timestamp_obj,
                        "data": compaction_stats}
            return None

        # --- Check for operation start/completion messages (DEBUG level) ---
        if "download started" in line or "upload started" in line:
            # This is a start event - return it for tracking
            piece_id = log_data.get("Piece ID")
            sat_id = log_data.get("Satellite ID")
            action = log_data.get("Action")
            available_space = log_data.get("Available Space")
            
            if all([piece_id, sat_id, action]):
                result = {"type": "operation_start", "piece_id": piece_id,
                       "satellite_id": sat_id, "action": action, "timestamp": timestamp_obj}
                # Include available space if present for storage tracking
                if available_space:
                    result["available_space"] = available_space
                return result
            return None

        # --- Original Traffic Log Processing ---
        status, error_reason = "success", None
        if "download canceled" in line:
            status, error_reason = "canceled", log_data.get("reason", "context canceled")
        elif "failed" in line or "ERROR" in line:
            status, error_reason = "failed", log_data.get("error", "unknown error")

        action, size, piece_id, sat_id, remote_addr = log_data.get("Action"), log_data.get("Size"), log_data.get(
            "Piece ID"), log_data.get("Satellite ID"), log_data.get("Remote Address")

        if not all([action, piece_id, sat_id, remote_addr]) or size is None: return None

        remote_ip = remote_addr.split(':')[0]
        location = geoip_cache.get(remote_ip)
        if location is None:
            from .config import MAX_GEOIP_CACHE_SIZE
            try:
                geo_response = geoip_reader.city(remote_ip)
                location = {"lat": geo_response.location.latitude, "lon": geo_response.location.longitude,
                            "country": geo_response.country.name}
            except geoip2.errors.AddressNotFoundError:
                location = {"lat": None, "lon": None, "country": "Unknown"}
            if len(geoip_cache) > MAX_GEOIP_CACHE_SIZE: geoip_cache.pop(next(iter(geoip_cache)))
            geoip_cache[remote_ip] = location

        # Phase 2.1: Extract operation duration for latency analytics
        duration_ms = None
        duration_str = log_data.get("duration")
        if duration_str:
            duration_seconds = parse_duration_str_to_seconds(duration_str)
            if duration_seconds is not None:
                duration_ms = int(duration_seconds * 1000)  # Convert to milliseconds

        event = {
            "ts_unix": timestamp_obj.timestamp(), "timestamp": timestamp_obj, "action": action,
            "status": status, "size": size, "piece_id": piece_id, "satellite_id": sat_id,
            "remote_ip": remote_ip, "location": location, "error_reason": error_reason,
            "node_name": node_name, "category": categorize_action(action),
            "duration_ms": duration_ms  # Phase 2.1: Store operation duration
        }
        return {"type": "traffic_event", "data": event}

    except (json.JSONDecodeError, AttributeError, KeyError, ValueError):
        return None
    except Exception:
        # Avoid crashing the whole process for one bad line
        log.debug("Failed to parse log line", exc_info=True)
        return None


async def log_processor_task(app, node_name: str, line_queue: asyncio.Queue):
    """
    Consumes log lines from a queue, parses them, and updates application state.
    This task is agnostic to the source of the log lines (file or network).
    """
    loop = asyncio.get_running_loop()
    geoip_reader = app['geoip_reader']
    geoip_cache = app_state['geoip_cache']
    node_state = app_state['nodes'][node_name]
    log.info(f"Log processor task started for node: {node_name}")
    
    # Track operation start times for duration calculation
    # Key: (piece_id, satellite_id, action) -> (arrival_time, timestamp_obj)
    operation_start_times = {}
    MAX_TRACKED_OPERATIONS = 10000  # Prevent memory growth
    
    # Track storage samples to avoid excessive writes
    # Sample every 5 minutes to avoid database spam
    last_storage_sample_time = 0
    STORAGE_SAMPLE_INTERVAL = 300  # 5 minutes in seconds
    last_available_space = None

    try:
        while True:
            line, arrival_time = await line_queue.get()
            parsed = parse_log_line(line, node_name, geoip_reader, geoip_cache)
            if not parsed:
                continue

            if parsed['type'] == 'operation_start':
                # Store both arrival_time and timestamp for hybrid duration calculation
                key = (parsed['piece_id'], parsed['satellite_id'], parsed['action'])
                operation_start_times[key] = (arrival_time, parsed['timestamp'])
                log.debug(f"[{node_name}] Stored operation_start: action={parsed['action']}, piece={parsed['piece_id'][:16]}..., sat={parsed['satellite_id'][:12]}...")
                
                # Extract available space for storage tracking (from DEBUG logs)
                available_space = parsed.get('available_space')
                if available_space:
                    current_time = arrival_time
                    # Only sample storage every STORAGE_SAMPLE_INTERVAL seconds
                    if current_time - last_storage_sample_time >= STORAGE_SAMPLE_INTERVAL:
                        # Check if space has changed significantly (>1GB or first sample)
                        if last_available_space is None or abs(available_space - last_available_space) > 1024**3:
                            last_storage_sample_time = current_time
                            last_available_space = available_space
                            
                            # Create storage snapshot from log data
                            # Note: We only know available space, not used space from logs
                            snapshot = {
                                'timestamp': parsed['timestamp'],
                                'node_name': node_name,
                                'available_bytes': available_space,
                                'total_bytes': None,  # Unknown from logs
                                'used_bytes': None,  # Unknown from logs
                                'trash_bytes': None,  # Unknown from logs
                                'used_percent': None,  # Cannot calculate without total
                                'trash_percent': None,  # Unknown from logs
                                'available_percent': None,  # Cannot calculate without total
                                'source': 'logs'  # Mark as coming from logs vs API
                            }
                            
                            # Write to database
                            from .config import DATABASE_FILE
                            try:
                                from .database import blocking_write_storage_snapshot
                                await loop.run_in_executor(
                                    app['db_executor'],
                                    blocking_write_storage_snapshot,
                                    DATABASE_FILE,
                                    snapshot
                                )
                                log.info(f"[{node_name}] Sampled storage from logs: {available_space / (1024**4):.2f} TB available")
                            except Exception as e:
                                log.error(f"[{node_name}] Failed to write storage snapshot from logs: {e}")
                
                # Prevent unbounded growth
                if len(operation_start_times) > MAX_TRACKED_OPERATIONS:
                    # Remove oldest 20% of entries
                    to_remove = len(operation_start_times) // 5
                    for _ in range(to_remove):
                        operation_start_times.pop(next(iter(operation_start_times)))
                    log.warning(f"[{node_name}] operation_start_times exceeded {MAX_TRACKED_OPERATIONS}, removed {to_remove} oldest entries")
                continue

            if parsed['type'] == 'traffic_event':
                event = parsed['data']
                
                # Hybrid duration calculation: prefer arrival_time for sub-second precision,
                # but fall back to log timestamps when buffering artifacts detected (>4s)
                if event['duration_ms'] is None:
                    key = (event['piece_id'], event['satellite_id'], event['action'])
                    start_data = operation_start_times.pop(key, None)
                    if start_data is not None:
                        start_arrival_time, start_timestamp = start_data
                        
                        # Calculate duration using arrival times (sub-second precision)
                        arrival_duration_seconds = arrival_time - start_arrival_time
                        arrival_duration_ms = int(arrival_duration_seconds * 1000)
                        
                        # If arrival_time shows suspiciously high duration (>4s),
                        # it's likely a buffering artifact - use log timestamps instead
                        if arrival_duration_ms > 4000:
                            # Fallback to log timestamp calculation
                            timestamp_duration_seconds = (event['timestamp'] - start_timestamp).total_seconds()
                            timestamp_duration_ms = int(timestamp_duration_seconds * 1000)
                            event['duration_ms'] = timestamp_duration_ms
                            log.debug(f"[{node_name}] Used timestamp fallback: {timestamp_duration_ms}ms (arrival suggested {arrival_duration_ms}ms) for {event['action']}")
                        else:
                            # Normal case: use arrival_time for best precision
                            event['duration_ms'] = arrival_duration_ms
                            log.debug(f"[{node_name}] Calculated duration: {arrival_duration_ms}ms for {event['action']}")
                    else:
                        log.debug(f"[{node_name}] No duration available for {event['action']}")
                
                if event['category'] != 'other':
                    node_state['unprocessed_performance_events'].append({
                        'ts_unix': event['ts_unix'], 'category': event['category'],
                        'status': event['status'], 'size': event['size']
                    })

                event['arrival_time'] = arrival_time
                node_state['live_events'].append(event)
                node_state['has_new_events'] = True

                websocket_event = {
                    "type": "log_entry", "action": event['action'], "size": event['size'], "location": event['location'],
                    "timestamp": event['timestamp'].isoformat(), "node_name": node_name, "arrival_time": arrival_time
                }
                async with app_state['websocket_queue_lock']:
                    app_state['websocket_event_queue'].append(websocket_event)

                if app_state['db_write_queue'].full():
                    log.warning("Database write queue is full. Pausing log processing to allow DB to catch up.")
                await app_state['db_write_queue'].put(event)

            elif parsed['type'] == 'hashstore_begin':
                node_state['active_compactions'][parsed['key']] = parsed['timestamp']
                await robust_broadcast(app_state['websockets'], get_active_compactions_payload())

            elif parsed['type'] == 'hashstore_end':
                node_state['active_compactions'].pop(parsed['key'], None)
                await robust_broadcast(app_state['websockets'], get_active_compactions_payload())

                # Write historical record to DB
                from .config import DATABASE_FILE
                was_written = await loop.run_in_executor(
                    app['db_executor'], blocking_write_hashstore_log, DATABASE_FILE, parsed['data']
                )
                if was_written:
                    await robust_broadcast(app_state['websockets'], {"type": "hashstore_updated"})

    except asyncio.CancelledError:
        log.warning(f"Log processor task for '{node_name}' is cancelled.")
    except Exception:
        log.error(f"Critical error in log_processor_task main loop for {node_name}:", exc_info=True)


def blocking_log_reader(log_path: str, loop: asyncio.AbstractEventLoop, aio_queue: asyncio.Queue,
                        shutdown_event: threading.Event):
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
        log.error(
            f"Cannot watch log file: directory '{directory}' does not exist. Reader thread for this log will exit.")
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
                    f.close()
                    f = None
                    continue
                if f.tell() > st.st_size:
                    log.warning(f"Log truncation detected for '{log_path}'. Seeking to start.")
                    f.seek(0)
            except FileNotFoundError:
                log.warning(f"Log file '{log_path}' disappeared. Will attempt to re-open.")
                f.close()
                f = None
            except Exception as e:
                log.error(f"Error checking log status for '{log_path}': {e}. Re-opening.", exc_info=True)
                f.close()
                f = None
                shutdown_event.wait(5)
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
            backoff = 2  # Reset backoff on successful connection
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
            log.error(f"[{node_name}] Unexpected error in network log reader for {host}:{port}. Retrying in {backoff}s.",
                      exc_info=True)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)  # Exponential backoff up to 1 minute
