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
            pass  # Doesn't look like a network address.

        # Fallback: Treat as a non-existent file path. This is valid for log files that will be created.
        log.warning(f"Configured node '{node_name}' with file source '{source}' (path does not currently exist).")
        nodes[node_name] = {'type': 'file', 'path': source}
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
    active_compactions = {}

    traffic_event_count = 0
    hashstore_event_count = 0
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

    log.info(f"Ingestion complete. Total lines processed: {line_count}. Traffic events ingested: {traffic_event_count}. Hashstore records ingested: {hashstore_event_count}.")
    geoip_reader.close()


def main():
    parser = argparse.ArgumentParser(description="Storagenode Pro Monitor")

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument('--node', action='append',
                            help="SERVER MODE: Specify a node in 'NodeName:/path/to/log.log' or 'NodeName:host:port' format. Can be used multiple times.")
    mode_group.add_argument('--ingest-log',
                            help="INGEST MODE: Ingest a log file for a specific node and exit. Format: 'NodeName:/path/to/log.log'")

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
