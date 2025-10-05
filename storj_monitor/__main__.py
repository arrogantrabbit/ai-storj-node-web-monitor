import argparse
import logging
import os
import sys

# This boilerplate allows the script to be run directly (e.g., `uv run storj_monitor`)
# by adding the project root to the Python path. This ensures that the absolute
# imports below will always resolve, regardless of the execution method.
if __package__ is None or __package__ == '':
    # Get the directory of the current script (__main__.py)
    # e.g., /path/to/project/storj_monitor
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Get the parent directory (the project root)
    # e.g., /path/to/project
    project_root = os.path.dirname(script_dir)
    # Add the project root to the start of the Python path
    sys.path.insert(0, project_root)

# Now that the path is correctly set, we can use absolute imports.
from storj_monitor import server, database, log_processor, config

# --- Centralized Logging Configuration ---
log = logging.getLogger("StorjMonitor")


def parse_nodes(args: list[str]) -> dict[str, dict[str, any]]:
    """
    Parse node configuration from command-line arguments.
    
    Format: "NodeName:log_source[:api_endpoint]"
    
    Examples:
        NodeName:/path/to/log.log
        NodeName:/path/to/log.log:http://localhost:14002
        NodeName:192.168.1.100:9999
        NodeName:192.168.1.100:9999:http://192.168.1.100:14002
    """
    nodes = {}
    if not args:
        log.critical("No nodes specified. Use --node 'NodeName:/path/to/log[:api_endpoint]' argument.")
        sys.exit(1)

    for arg in args:
        parts = arg.split(':')
        if len(parts) < 2 or not parts[0]:
            log.critical(f"Invalid node format: '{arg}'. Expected 'NodeName:/path/to/log[:api_endpoint]'.")
            sys.exit(1)
        
        node_name = parts[0]
        api_endpoint = None
        
        # Check if log source starts with '/' or '.' (file path)
        if parts[1].startswith('/') or parts[1].startswith('.'):
            # File path
            log_source = parts[1]
            log_type = 'file'
            
            # Check for explicit API endpoint after file path
            if len(parts) >= 3:
                # "NodeName:/path/to/log:http://localhost:14002"
                api_endpoint = ':'.join(parts[2:])
                log.info(f"Configured node '{node_name}' with file source '{log_source}' and API endpoint '{api_endpoint}'.")
            else:
                # No explicit API, will attempt auto-discovery
                log.info(f"Configured node '{node_name}' with file source '{log_source}' (API endpoint will be auto-discovered).")
            
            # Check if file exists
            if os.path.exists(log_source):
                log.info(f"  -> Log file exists at '{log_source}'.")
            else:
                log.warning(f"  -> Log file does not currently exist at '{log_source}' (may be created later).")
            
            nodes[node_name] = {
                'type': 'file',
                'path': log_source,
                'api_endpoint': api_endpoint
            }
        else:
            # Network: "NodeName:host:port" or "NodeName:host:port:http://..."
            if len(parts) == 3:
                # "NodeName:host:port" - log forwarder only
                try:
                    host, port = parts[1], int(parts[2])
                    if 1 <= port <= 65535 and host:
                        log.info(f"Configured node '{node_name}' with network source '{host}:{port}' (no API).")
                        nodes[node_name] = {
                            'type': 'network',
                            'host': host,
                            'port': port,
                            'api_endpoint': None
                        }
                        continue
                except (ValueError, TypeError):
                    pass
            
            elif len(parts) >= 4:
                # "NodeName:host:port:http://host:api_port"
                try:
                    host, port_str = parts[1], parts[2]
                    port = int(port_str)
                    if 1 <= port <= 65535 and host:
                        api_endpoint = ':'.join(parts[3:])
                        log.info(f"Configured node '{node_name}' with network source '{host}:{port}' and API endpoint '{api_endpoint}'.")
                        nodes[node_name] = {
                            'type': 'network',
                            'host': host,
                            'port': port,
                            'api_endpoint': api_endpoint
                        }
                        continue
                except (ValueError, TypeError):
                    pass
            
            # If we got here, format is invalid
            log.critical(f"Invalid network node format: '{arg}'. Expected 'NodeName:host:port[:api_endpoint]'.")
            sys.exit(1)
    
    return nodes


def ingest_log_file(node_name: str, log_path: str):
    """Reads a log file from start to finish, parsing and inserting all relevant events into the database."""
    log.info(f"Starting ingestion for node '{node_name}' from log file '{log_path}'.")

    if not os.path.exists(log_path):
        log.critical(f"Log file not found: {log_path}")
        return

    try:
        import geoip2.database
        geoip_reader = geoip2.database.Reader(config.GEOIP_DATABASE_PATH)
    except Exception as e:
        log.critical(f"Could not load GeoIP database: {e}. Ingestion cannot proceed.")
        return

    geoip_cache = {}
    events_to_write = []
    hashstore_records_to_write = []
    storage_snapshots_to_write = []
    active_compactions = {}
    
    # Storage tracking during ingestion (based on log timestamps, not arrival time)
    last_storage_sample_timestamp = None
    last_available_space = None
    STORAGE_SAMPLE_INTERVAL_SECONDS = 300  # 5 minutes

    traffic_event_count = 0
    hashstore_event_count = 0
    storage_sample_count = 0
    line_count = 0

    with open(log_path, 'r', errors='ignore') as f:
        for line in f:
            line_count += 1
            if line_count % 100000 == 0:
                log.info(f"Processed {line_count} lines...")

            parsed = log_processor.parse_log_line(line, node_name, geoip_reader, geoip_cache)
            if not parsed:
                continue

            if parsed['type'] == 'traffic_event':
                events_to_write.append(parsed['data'])
            elif parsed['type'] == 'operation_start':
                # Extract storage data from operation_start events during ingestion
                available_space = parsed.get('available_space')
                if available_space:
                    current_timestamp = parsed['timestamp']
                    
                    # Sample based on log timestamp (not arrival time like live mode)
                    should_sample = False
                    if last_storage_sample_timestamp is None:
                        # First sample
                        should_sample = True
                    else:
                        time_since_last_sample = (current_timestamp - last_storage_sample_timestamp).total_seconds()
                        if time_since_last_sample >= STORAGE_SAMPLE_INTERVAL_SECONDS:
                            # Check if space changed significantly (>1GB)
                            if last_available_space is None or abs(available_space - last_available_space) > 1024**3:
                                should_sample = True
                    
                    if should_sample:
                        last_storage_sample_timestamp = current_timestamp
                        last_available_space = available_space
                        
                        # Create storage snapshot
                        snapshot = {
                            'timestamp': current_timestamp,
                            'node_name': node_name,
                            'available_bytes': available_space,
                            'total_bytes': None,
                            'used_bytes': None,
                            'trash_bytes': None,
                            'used_percent': None,
                            'trash_percent': None,
                            'available_percent': None,
                            'source': 'logs'
                        }
                        storage_snapshots_to_write.append(snapshot)
                        storage_sample_count += 1
                        
                        # Debug logging every 10 samples
                        if storage_sample_count % 10 == 1:
                            log.info(f"Storage sample #{storage_sample_count}: {available_space / (1024**4):.2f} TB at {current_timestamp}")
            elif parsed['type'] == 'hashstore_begin':
                active_compactions[parsed['key']] = parsed['timestamp']
            elif parsed['type'] == 'hashstore_end':
                start_time = active_compactions.pop(parsed['key'], None)
                record = parsed['data']
                if start_time and record['duration'] == 0:
                    record['duration'] = round((parsed['timestamp'] - start_time).total_seconds(), 2)
                hashstore_records_to_write.append(record)

            if len(events_to_write) >= 50000:
                log.info(f"Writing a batch of {len(events_to_write)} traffic events to the database...")
                database.blocking_db_batch_write(config.DATABASE_FILE, events_to_write)
                traffic_event_count += len(events_to_write)
                events_to_write.clear()

    if events_to_write:
        log.info(f"Writing the final batch of {len(events_to_write)} traffic events...")
        database.blocking_db_batch_write(config.DATABASE_FILE, events_to_write)
        traffic_event_count += len(events_to_write)

    if hashstore_records_to_write:
        log.info(f"Writing {len(hashstore_records_to_write)} hashstore records...")
        database.blocking_batch_write_hashstore_ingest(config.DATABASE_FILE, hashstore_records_to_write)
        hashstore_event_count = len(hashstore_records_to_write)
    
    if storage_snapshots_to_write:
        log.info(f"Writing {len(storage_snapshots_to_write)} storage snapshots from log data...")
        # Show first and last sample for verification
        if storage_snapshots_to_write:
            first = storage_snapshots_to_write[0]
            last = storage_snapshots_to_write[-1]
            log.info(f"  First sample: {first['available_bytes'] / (1024**4):.2f} TB at {first['timestamp']}")
            log.info(f"  Last sample: {last['available_bytes'] / (1024**4):.2f} TB at {last['timestamp']}")
        
        for snapshot in storage_snapshots_to_write:
            success = database.blocking_write_storage_snapshot(config.DATABASE_FILE, snapshot)
            if not success:
                log.error(f"Failed to write storage snapshot at {snapshot['timestamp']}")
        log.info(f"Storage snapshots written successfully.")
    else:
        log.warning("No storage snapshots were collected during ingestion. This might mean:")
        log.warning("  1. The log file doesn't contain DEBUG-level entries with 'Available Space'")
        log.warning("  2. The log format has changed")
        log.warning("  Make sure your log contains lines like: 'DEBUG piecestore upload started ... \"Available Space\": 14540395224064'")

    log.info(f"Ingestion complete. Total lines processed: {line_count}. "
             f"Traffic events ingested: {traffic_event_count}. "
             f"Hashstore records ingested: {hashstore_event_count}. "
             f"Storage samples ingested: {storage_sample_count}.")
    geoip_reader.close()


def main():
    parser = argparse.ArgumentParser(
        description="Storagenode Pro Monitor - Real-time monitoring dashboard for Storj storage nodes",
        epilog="""
Examples:
  # Single local node with auto-discovered API
  %(prog)s --node "My-Node:/var/log/storagenode.log"
  
  # Local node with explicit API endpoint
  %(prog)s --node "My-Node:/var/log/storagenode.log:http://localhost:14002"
  
  # Multiple local nodes with different API ports
  %(prog)s --node "Node1:/var/log/node1.log:http://localhost:14002" \\
           --node "Node2:/var/log/node2.log:http://localhost:15002"
  
  # Remote log forwarder without API
  %(prog)s --node "Remote-Node:192.168.1.100:9999"
  
  # Remote log forwarder with API
  %(prog)s --node "Remote-Node:192.168.1.100:9999:http://192.168.1.100:14002"
  
  # One-time historical log ingestion
  %(prog)s --ingest-log "My-Node:/var/log/old-logs.log"

Enhanced Features (with API):
  When API endpoint is provided or auto-discovered, additional features are enabled:
  - Node reputation monitoring (audit, suspension, online scores)
  - Storage capacity tracking and forecasting
  - Performance latency analytics
  - Proactive alerting
  
  If API endpoint is not specified for local nodes, the system attempts
  auto-discovery at http://localhost:14002 (default Storj node API port).
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        '--node',
        action='append',
        metavar='NODE_CONFIG',
        help="SERVER MODE: Specify a node configuration. Format: 'NodeName:log_source[:api_endpoint]' "
             "where log_source is either a file path (/path/to/log.log) or network address (host:port). "
             "API endpoint is optional for enhanced monitoring features. Can be specified multiple times for multi-node monitoring."
    )
    mode_group.add_argument(
        '--ingest-log',
        metavar='NODE:PATH',
        help="INGEST MODE: One-time ingestion of a log file into the database and exit. Format: 'NodeName:/path/to/log.log'"
    )

    parser.add_argument('--debug', action='store_true', help="Enable debug logging.")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

    database.init_db()

    if args.ingest_log:
        try:
            node_name, log_path = args.ingest_log.split(':', 1)
            if not node_name or not log_path:
                raise ValueError("Invalid format")
        except ValueError:
            log.critical(f"Invalid format for --ingest-log: '{args.ingest_log}'. Expected 'NodeName:/path/to/log.log'.")
            sys.exit(1)

        ingest_log_file(node_name, log_path)
        log.info("Ingestion finished. Now backfilling hourly statistics. This may take a while...")
        database.blocking_backfill_hourly_stats([node_name])
        log.info("Hourly statistics backfilled. Process complete.")
        sys.exit(0)

    else:  # Run in server mode
        nodes_config = parse_nodes(args.node)
        server.run_server(nodes_config)


if __name__ == "__main__":
    main()
