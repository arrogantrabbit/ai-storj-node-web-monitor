import asyncio
import logging
import time

from .state import app_state
from .config import (
    DB_WRITE_BATCH_INTERVAL_SECONDS,
    STATS_WINDOW_MINUTES,
    HOURLY_AGG_INTERVAL_MINUTES,
    DB_PRUNE_INTERVAL_HOURS,
    STATS_INTERVAL_SECONDS,
    PERFORMANCE_INTERVAL_SECONDS,
    WEBSOCKET_BATCH_INTERVAL_MS,
    WEBSOCKET_BATCH_SIZE,
    DB_EVENTS_RETENTION_DAYS,
    DB_HASHSTORE_RETENTION_DAYS,
)
from .database import (
    blocking_db_batch_write,
    blocking_hourly_aggregation,
    blocking_db_prune,
    get_historical_stats,
)
from .websocket_utils import robust_broadcast

log = logging.getLogger("StorjMonitor.Tasks")


async def database_writer_task(app):
    log.info("Database writer task started.")
    from .config import DATABASE_FILE

    while True:
        await asyncio.sleep(DB_WRITE_BATCH_INTERVAL_SECONDS)
        events_to_write = []
        if app_state["db_write_queue"].empty():
            continue
        while not app_state["db_write_queue"].empty():
            try:
                events_to_write.append(app_state["db_write_queue"].get_nowait())
            except asyncio.QueueEmpty:
                break

        if events_to_write:
            log.info(
                f"[DB_WRITER] Preparing to write {len(events_to_write)} events. Queue size: {app_state['db_write_queue'].qsize()}"
            )
            loop = asyncio.get_running_loop()
            async with app_state["db_write_lock"]:
                try:
                    await loop.run_in_executor(
                        app["db_executor"], blocking_db_batch_write, DATABASE_FILE, events_to_write
                    )
                except Exception:
                    log.error("Error during blocking database write execution:", exc_info=True)


async def debug_logger_task(app):
    log.info("Debug heartbeat task started.")
    while True:
        await asyncio.sleep(30)
        total_live_events = sum(len(n["live_events"]) for n in app_state["nodes"].values())
        unprocessed_perf_events = sum(
            len(n["unprocessed_performance_events"]) for n in app_state["nodes"].values()
        )
        incremental_stats = len(app_state["incremental_stats"])
        log.info(
            f"[HEARTBEAT] Clients: {len(app_state['websockets'])}, Live Events: {total_live_events}, DB Queue: {app_state['db_write_queue'].qsize()}, Perf Queue: {unprocessed_perf_events}, Stats: {incremental_stats}"
        )
        for name, state in app_state["nodes"].items():
            log.info(
                f"  -> Node '{name}': {len(state['live_events'])} events, {len(state['unprocessed_performance_events'])} perf events"
            )


async def prune_live_events_task(app):
    log.info("Event pruning task started.")
    while True:
        await asyncio.sleep(60)
        cutoff_unix = time.time() - (STATS_WINDOW_MINUTES * 60)
        for node_name, node_state in app_state["nodes"].items():
            events_to_prune_count = 0
            while (
                node_state["live_events"] and node_state["live_events"][0]["ts_unix"] < cutoff_unix
            ):
                node_state["live_events"].popleft()
                events_to_prune_count += 1
            if events_to_prune_count > 0:
                log.info(f"[PRUNER] Node '{node_name}': Pruned {events_to_prune_count} events.")


async def hourly_aggregator_task(app):
    log.info("Hourly aggregator task started.")
    while True:
        await asyncio.sleep(60 * HOURLY_AGG_INTERVAL_MINUTES)
        try:
            loop = asyncio.get_running_loop()
            node_names = list(app["nodes"].keys())
            async with app_state["db_write_lock"]:
                await loop.run_in_executor(
                    app["db_executor"], blocking_hourly_aggregation, node_names
                )
        except Exception:
            log.error("Error in hourly aggregator task:", exc_info=True)


async def database_pruner_task(app):
    log.info("Database pruner task started.")
    from .config import DATABASE_FILE

    while True:
        try:
            loop = asyncio.get_running_loop()
            async with app_state["db_write_lock"]:
                await loop.run_in_executor(
                    app["db_executor"],
                    blocking_db_prune,
                    DATABASE_FILE,
                    DB_EVENTS_RETENTION_DAYS,
                    DB_HASHSTORE_RETENTION_DAYS,
                )
        except Exception:
            log.error("Error in database pruner task:", exc_info=True)
        await asyncio.sleep(3600 * DB_PRUNE_INTERVAL_HOURS)


async def incremental_stats_updater_task(app):
    """
    Maintains incremental statistics for each active client view.
    Each node keeps independent stats, and 'Aggregate' views sum them up safely.
    """
    log.info("Incremental stats updater task started.")

    while True:
        await asyncio.sleep(STATS_INTERVAL_SECONDS)

        try:
            # Snapshot to prevent runtime dictionary changes
            websockets_snapshot = dict(app_state['websockets'])
            active_views = {
                tuple(state.get('view', ['Aggregate']))
                for state in websockets_snapshot.values()
            }

            # If no connected websockets, skip this iteration
            if not active_views:
                continue

            # --- STEP 1: Update per-node incremental stats ---
            for node_name, node_state in app_state['nodes'].items():
                all_events = list(node_state['live_events'])
                if not all_events:
                    continue

                # Create or get per-node IncrementalStats
                if node_name not in app_state['incremental_stats']:
                    from .state import IncrementalStats
                    app_state['incremental_stats'][node_name] = IncrementalStats()

                node_stats = app_state['incremental_stats'][node_name]
                last_index = node_stats.last_processed_indices.get(node_name, 0)

                if last_index < len(all_events):
                    new_events = all_events[last_index:]
                    for event in new_events:
                        node_stats.add_event(event, app_state['TOKEN_REGEX'])
                    node_stats.last_processed_indices[node_name] = len(all_events)

                # Update live stats (rolling 1-min window)
                node_stats.update_live_stats(all_events)
                node_state['has_new_events'] = False

            # --- STEP 2: Update and broadcast for each active view ---
            for view_tuple in active_views:
                view_list = list(view_tuple)

                # Determine which nodes this view includes
                if view_list == ['Aggregate']:
                    nodes_to_include = list(app_state['nodes'].keys())
                else:
                    nodes_to_include = [n for n in view_list if n in app_state['nodes']]

                # Merge stats from multiple nodes (aggregate view)
                from .state import IncrementalStats
                combined_stats = IncrementalStats()

                all_events_for_view = []
                for node_name in nodes_to_include:
                    if node_name in app_state['incremental_stats']:
                        node_stats = app_state['incremental_stats'][node_name]
                        all_events_for_view.extend(app_state['nodes'][node_name]['live_events'])
                        # Add up numeric counters and combine satellite/country data
                        combined_stats.dl_success += node_stats.dl_success
                        combined_stats.dl_fail += node_stats.dl_fail
                        combined_stats.ul_success += node_stats.ul_success
                        combined_stats.ul_fail += node_stats.ul_fail
                        combined_stats.audit_success += node_stats.audit_success
                        combined_stats.audit_fail += node_stats.audit_fail
                        combined_stats.total_dl_size += node_stats.total_dl_size
                        combined_stats.total_ul_size += node_stats.total_ul_size
                        combined_stats.live_dl_bytes += node_stats.live_dl_bytes
                        combined_stats.live_ul_bytes += node_stats.live_ul_bytes

                        # Merge dict-based and counter-based stats
                        for sat, sdata in node_stats.satellites.items():
                            if sat not in combined_stats.satellites:
                                combined_stats.satellites[sat] = sdata.copy()
                            else:
                                for k, v in sdata.items():
                                    combined_stats.satellites[sat][k] = (
                                        combined_stats.satellites[sat].get(k, 0) + v
                                    )

                        combined_stats.countries_dl.update(node_stats.countries_dl)
                        combined_stats.countries_ul.update(node_stats.countries_ul)
                        combined_stats.dls_success.update(node_stats.dls_success)
                        combined_stats.dls_failed.update(node_stats.dls_failed)
                        combined_stats.uls_success.update(node_stats.uls_success)
                        combined_stats.uls_failed.update(node_stats.uls_failed)

                # Update live stats for combined view
                combined_stats.update_live_stats(all_events_for_view)

                # Get historical data for this view
                historical_stats = get_historical_stats(view_list, app_state['nodes'])
                payload = combined_stats.to_payload(historical_stats)
                app_state['stats_cache'][view_tuple] = payload

                # Broadcast to clients subscribed to this view
                recipients = [
                    ws for ws, state in websockets_snapshot.items()
                    if tuple(state.get('view', ['Aggregate'])) == view_tuple
                ]
                for ws in recipients:
                    try:
                        await ws.send_json(payload)
                    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
                        pass

        except Exception:
            log.error("Error in incremental_stats_updater_task:", exc_info=True)

async def performance_aggregator_task(app):
    log.info("Live performance aggregator task started.")
    while True:
        await asyncio.sleep(PERFORMANCE_INTERVAL_SECONDS)
        try:
            for node_name, node_state in app_state["nodes"].items():
                events_to_process = node_state["unprocessed_performance_events"]
                node_state["unprocessed_performance_events"] = []
                if not events_to_process:
                    continue

                bins = {}
                bin_size_sec = PERFORMANCE_INTERVAL_SECONDS
                for event in events_to_process:
                    ts_unix = event["ts_unix"]
                    binned_timestamp_ms = int(ts_unix / bin_size_sec) * bin_size_sec * 1000
                    ts_key = str(binned_timestamp_ms)
                    if ts_key not in bins:
                        bins[ts_key] = {
                            "ingress_bytes": 0,
                            "egress_bytes": 0,
                            "ingress_pieces": 0,
                            "egress_pieces": 0,
                            "total_ops": 0,
                        }
                    bin_data = bins[ts_key]
                    bin_data["total_ops"] += 1
                    if event["status"] == "success":
                        category, size = event["category"], event["size"]
                        if category == "get":
                            bin_data["egress_bytes"] += size
                            bin_data["egress_pieces"] += 1
                        elif category == "put":
                            bin_data["ingress_bytes"] += size
                            bin_data["ingress_pieces"] += 1

                if bins:
                    payload = {
                        "type": "performance_batch_update",
                        "node_name": node_name,
                        "bins": bins,
                    }
                    await robust_broadcast(app_state["websockets"], payload, node_name=node_name)
        except Exception:
            log.error("Error in performance_aggregator_task:", exc_info=True)


async def websocket_batch_broadcaster_task(app):
    """
    Batches websocket log entries and sends them at regular intervals.
    """
    log.info("Websocket batch broadcaster task started.")
    while True:
        await asyncio.sleep(WEBSOCKET_BATCH_INTERVAL_MS / 1000.0)
        try:
            async with app_state["websocket_queue_lock"]:
                if not app_state["websocket_event_queue"]:
                    continue
                events_to_send = app_state["websocket_event_queue"][:WEBSOCKET_BATCH_SIZE]
                app_state["websocket_event_queue"] = app_state["websocket_event_queue"][
                    WEBSOCKET_BATCH_SIZE:
                ]

            base_arrival_time = events_to_send[0]["arrival_time"]
            for event in events_to_send:
                event["arrival_offset_ms"] = int((event["arrival_time"] - base_arrival_time) * 1000)

            payload = {"type": "log_entry_batch", "events": events_to_send}
            await robust_broadcast(app_state["websockets"], payload)
        except Exception:
            log.error("Error in websocket_batch_broadcaster_task:", exc_info=True)


async def connection_status_broadcaster_task(app):
    """
    Periodically broadcasts connection status to all WebSocket clients.
    """
    log.info("Connection status broadcaster task started.")
    broadcast_interval = 10  # seconds

    while True:
        try:
            await asyncio.sleep(broadcast_interval)

            # Gather connection states
            connection_states = {}

            # Get API client states
            for node_name, client in app.get("api_clients", {}).items():
                connection_states[node_name] = {"api_client": client.get_connection_state()}

            # Get log reader states from app_state
            for node_name, states in app_state.get("connection_states", {}).items():
                if node_name not in connection_states:
                    connection_states[node_name] = {}
                connection_states[node_name].update(states)

            # Broadcast to all connected clients
            if connection_states:
                payload = {"type": "connection_status", "states": connection_states}
                await robust_broadcast(app_state["websockets"], payload)

        except asyncio.CancelledError:
            log.info("Connection status broadcaster task cancelled")
            break
        except Exception:
            log.error("Error in connection_status_broadcaster_task:", exc_info=True)


async def start_background_tasks(app):
    import concurrent.futures
    import sys
    import geoip2.database
    from collections import deque
    from .log_processor import blocking_log_reader, network_log_reader_task, log_processor_task
    from .database import blocking_backfill_hourly_stats, load_initial_state_from_db
    from .config import (
        GEOIP_DATABASE_PATH,
        DATABASE_FILE,
        DB_THREAD_POOL_SIZE,
        DB_CONNECTION_POOL_SIZE,
        DB_CONNECTION_TIMEOUT,
    )
    from .storj_api_client import auto_discover_api_endpoint, setup_api_client
    from .db_utils import init_connection_pool

    log.info("Starting background tasks...")
    try:
        app["geoip_reader"] = geoip2.database.Reader(GEOIP_DATABASE_PATH)
        log.info("GeoIP database loaded successfully.")
    except FileNotFoundError:
        log.critical(
            f"GeoIP database not found at '{GEOIP_DATABASE_PATH}'. Please download it. Exiting."
        )
        sys.exit(1)

    # Initialize database connection pool for better concurrency
    init_connection_pool(DATABASE_FILE, DB_CONNECTION_POOL_SIZE, DB_CONNECTION_TIMEOUT)
    log.info(f"Database connection pool initialized with {DB_CONNECTION_POOL_SIZE} connections")

    app["db_executor"] = concurrent.futures.ThreadPoolExecutor(max_workers=DB_THREAD_POOL_SIZE)
    log.info(f"Database thread pool initialized with {DB_THREAD_POOL_SIZE} workers")
    app["log_executor"] = concurrent.futures.ThreadPoolExecutor(max_workers=len(app["nodes"]) + 1)
    app["tasks"] = []
    app["log_reader_shutdown_events"] = {}
    app["api_clients"] = {}  # Initialize API clients dict

    loop = asyncio.get_running_loop()

    node_names = list(app["nodes"].keys())
    await loop.run_in_executor(app["db_executor"], blocking_backfill_hourly_stats, node_names)

    initial_node_states = await loop.run_in_executor(
        app["db_executor"], load_initial_state_from_db, app["nodes"]
    )
    app_state["nodes"] = initial_node_states
    log.info("Initial state has been populated from the database.")

    # Initialize nodes with log readers and API clients
    for node_name, node_config in app["nodes"].items():
        if node_name not in app_state["nodes"]:
            app_state["nodes"][node_name] = {
                "live_events": deque(),
                "active_compactions": {},
                "unprocessed_performance_events": [],
                "has_new_events": False,
            }

        # Setup log reader
        line_queue = asyncio.Queue(maxsize=5000)
        if node_config["type"] == "file":
            import threading

            shutdown_event = threading.Event()
            app["log_reader_shutdown_events"][node_name] = shutdown_event
            loop.run_in_executor(
                app["log_executor"],
                blocking_log_reader,
                node_config["path"],
                loop,
                line_queue,
                shutdown_event,
            )
        elif node_config["type"] == "network":
            app["tasks"].append(
                asyncio.create_task(
                    network_log_reader_task(
                        node_name, node_config["host"], node_config["port"], line_queue
                    )
                )
            )
        app["tasks"].append(asyncio.create_task(log_processor_task(app, node_name, line_queue)))

        # Setup API client (Phase 1.2)
        node_config_with_name = {**node_config, "name": node_name}
        api_endpoint = await auto_discover_api_endpoint(node_config_with_name)
        if api_endpoint:
            await setup_api_client(app, node_name, api_endpoint)

    app["tasks"].extend(
        [
            asyncio.create_task(prune_live_events_task(app)),
            asyncio.create_task(incremental_stats_updater_task(app)),
            asyncio.create_task(performance_aggregator_task(app)),
            asyncio.create_task(websocket_batch_broadcaster_task(app)),
            asyncio.create_task(connection_status_broadcaster_task(app)),
            asyncio.create_task(debug_logger_task(app)),
            asyncio.create_task(database_writer_task(app)),
            asyncio.create_task(hourly_aggregator_task(app)),
            asyncio.create_task(database_pruner_task(app)),
        ]
    )
    log.info("Connection status broadcaster task initialized")

    # Add reputation and storage polling tasks if we have any API clients
    if app["api_clients"]:
        from .reputation_tracker import reputation_polling_task
        from .storage_tracker import storage_polling_task

        log.info(f"Enhanced monitoring enabled for {len(app['api_clients'])} node(s)")
        app["tasks"].append(asyncio.create_task(reputation_polling_task(app)))  # Phase 1.3
        app["tasks"].append(asyncio.create_task(storage_polling_task(app)))  # Phase 2.2

    # Add alert evaluation task (Phase 4)
    from .alert_manager import alert_evaluation_task

    app["tasks"].append(asyncio.create_task(alert_evaluation_task(app)))
    log.info("Alert evaluation task initialized")

    # Add financial tracking task (Phase 5.2)
    from .financial_tracker import financial_polling_task

    app["tasks"].append(asyncio.create_task(financial_polling_task(app)))
    log.info("Financial tracking task initialized")


async def cleanup_background_tasks(app):
    log.warning("Application cleanup started.")

    # Cancel all background tasks
    for task in app.get("tasks", []):
        task.cancel()
    if "tasks" in app:
        await asyncio.gather(*app["tasks"], return_exceptions=True)
    log.info("Asyncio background tasks cancelled.")

    # Shutdown log reader threads
    for event in app.get("log_reader_shutdown_events", {}).values():
        event.set()

    # Close API clients (Phase 1.2)
    if "api_clients" in app:
        from .storj_api_client import cleanup_api_clients

        await cleanup_api_clients(app)

    # Close GeoIP reader
    if "geoip_reader" in app and hasattr(app["geoip_reader"], "close"):
        app["geoip_reader"].close()
        log.info("GeoIP database reader closed.")

    # Cleanup database connection pool
    from .db_utils import cleanup_connection_pool

    cleanup_connection_pool()
    log.info("Database connection pool cleaned up.")

    # Shutdown executors
    for executor_name in ["db_executor", "log_executor"]:
        if executor_name in app and app[executor_name]:
            app[executor_name].shutdown(wait=True)
            log.info(f"{executor_name} shut down.")
