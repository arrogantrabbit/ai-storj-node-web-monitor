import asyncio
import json
import logging
import os
import time
from typing import List, Any, Dict

import aiohttp
from aiohttp import web

from .state import app_state
from .tasks import start_background_tasks, cleanup_background_tasks
from . import database
from .config import SERVER_HOST, SERVER_PORT, PERFORMANCE_INTERVAL_SECONDS, DATABASE_FILE

log = logging.getLogger("StorjMonitor.Server")


# ===== Phase 9: Multi-Node Comparison Functions =====

def parse_time_range(time_range: str) -> int:
    """Parse time range string to hours."""
    if time_range == "24h":
        return 24
    elif time_range == "7d":
        return 24 * 7
    elif time_range == "30d":
        return 24 * 30
    else:
        return 24  # default


def calculate_percentile(values: List[float], percentile: int) -> float:
    """Calculate percentile from list of values using nearest-rank method."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    n = len(sorted_values)
    
    # Use nearest-rank method with rounding
    # Calculate position and round to nearest index
    position = (percentile / 100.0) * (n - 1)
    index = round(position)
    return float(sorted_values[index])


def calculate_success_rate(events: List[Dict]) -> float:
    """Calculate success rate for a list of events."""
    if not events:
        return 0.0
    successful = sum(1 for e in events if e.get("status") == "success")
    return (successful / len(events)) * 100


def calculate_earnings_per_tb(earnings_data: Dict) -> float:
    """Calculate earnings per TB stored."""
    total_earnings = earnings_data.get("total_earnings_net", 0)
    # Get used space in TB from storage data if available
    used_space_tb = earnings_data.get("used_space_tb", 0)
    
    if used_space_tb > 0:
        return total_earnings / used_space_tb
    return 0.0


def calculate_storage_efficiency(storage_data: Dict) -> float:
    """Calculate storage efficiency score (0-100)."""
    used_percent = storage_data.get("used_percent", 0)
    trash_percent = storage_data.get("trash_percent", 0)
    
    # Efficiency = used space - excessive trash
    # Penalize if trash > 10%
    efficiency = used_percent
    if trash_percent > 10:
        efficiency -= (trash_percent - 10)
    
    return max(0.0, min(100.0, efficiency))


def calculate_avg_score(reputation_data: List[Dict], score_field: str) -> float:
    """Calculate average score from reputation data."""
    if not reputation_data:
        return 0.0
    
    scores = []
    for rep in reputation_data:
        if score_field in rep and rep[score_field] is not None:
            scores.append(rep[score_field])
    
    if not scores:
        return 0.0
    
    return sum(scores) / len(scores)


async def gather_node_metrics(app, node_name: str, hours: int, comparison_type: str) -> Dict:
    """
    Gather all metrics for a single node.
    
    OPTIMIZED: Uses sampling and caching to prevent loading millions of events.
    """
    from .database import (
        blocking_get_events,
        blocking_get_latest_reputation,
        blocking_get_latest_storage_with_forecast,
        blocking_get_latest_earnings,
    )
    
    metrics = {}
    loop = asyncio.get_running_loop()
    
    try:
        if comparison_type in ["performance", "overall"]:
            # CRITICAL OPTIMIZATION: Limit events to prevent loading millions of rows
            # For comparison, we only need a statistically significant sample
            # 10,000 recent events provides excellent accuracy for metrics
            MAX_EVENTS_FOR_COMPARISON = 10000
            
            log.debug(f"[Comparison] Fetching up to {MAX_EVENTS_FOR_COMPARISON} events for {node_name}")
            
            events = await loop.run_in_executor(
                app["db_executor"],
                blocking_get_events,
                DATABASE_FILE,
                [node_name],
                hours,
                MAX_EVENTS_FOR_COMPARISON,  # CRITICAL: Add limit parameter
            )
            
            log.debug(f"[Comparison] Retrieved {len(events)} events for {node_name}")
            
            # Calculate success rates by action type
            downloads = [e for e in events if e.get("action") == "GET"]
            uploads = [e for e in events if e.get("action") == "PUT"]
            audits = [e for e in events if e.get("action") == "GET_AUDIT"]
            
            metrics["success_rate_download"] = calculate_success_rate(downloads)
            metrics["success_rate_upload"] = calculate_success_rate(uploads)
            metrics["success_rate_audit"] = calculate_success_rate(audits)
            
            # Calculate latency metrics
            durations = [e.get("duration_ms", 0) for e in events if e.get("duration_ms")]
            if durations:
                metrics["avg_latency_p50"] = calculate_percentile(durations, 50)
                metrics["avg_latency_p95"] = calculate_percentile(durations, 95)
                metrics["avg_latency_p99"] = calculate_percentile(durations, 99)
            else:
                metrics["avg_latency_p50"] = 0
                metrics["avg_latency_p95"] = 0
                metrics["avg_latency_p99"] = 0
            
            metrics["total_operations"] = len(events)
        
        if comparison_type in ["earnings", "overall"]:
            # Get earnings data - use financial tracker for current period
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            period = now.strftime("%Y-%m")
            
            log.info(f"[Comparison] Fetching earnings for {node_name}, period: {period}")
            
            # First try to get from financial tracker (handles current period calculation)
            tracker = app.get("financial_trackers", {}).get(node_name)
            if tracker:
                try:
                    # Calculate earnings on-demand using financial tracker
                    earnings_list = await tracker.calculate_monthly_earnings(
                        DATABASE_FILE, period, loop, app.get("db_executor")
                    )
                    
                    if earnings_list:
                        total_earnings = sum(e.get("total_earnings_net", 0) for e in earnings_list)
                        log.info(f"[Comparison] Total earnings for {node_name}: ${total_earnings:.2f}")
                        metrics["total_earnings"] = total_earnings
                        
                        # Get storage data for per-TB calculation
                        storage_list = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_latest_storage_with_forecast,
                            DATABASE_FILE,
                            [node_name],
                            7,
                        )
                        
                        if storage_list and storage_list[0].get("used_bytes"):
                            used_space_tb = storage_list[0]["used_bytes"] / (1024 ** 4)
                            if used_space_tb > 0:
                                metrics["earnings_per_tb"] = total_earnings / used_space_tb
                            else:
                                metrics["earnings_per_tb"] = 0
                        else:
                            metrics["earnings_per_tb"] = 0
                    else:
                        log.warning(f"[Comparison] No earnings calculated for {node_name}, period: {period}")
                        metrics["total_earnings"] = 0
                        metrics["earnings_per_tb"] = 0
                except Exception as e:
                    log.error(f"[Comparison] Failed to calculate earnings for {node_name}: {e}")
                    metrics["total_earnings"] = 0
                    metrics["earnings_per_tb"] = 0
            else:
                # Fallback to database query if tracker not available
                earnings_list = await loop.run_in_executor(
                    app["db_executor"],
                    blocking_get_latest_earnings,
                    DATABASE_FILE,
                    [node_name],
                    period,
                )
                
                if earnings_list:
                    total_earnings = sum(e.get("total_earnings_net", 0) for e in earnings_list)
                    log.info(f"[Comparison] Total earnings for {node_name} from DB: ${total_earnings:.2f}")
                    metrics["total_earnings"] = total_earnings
                    
                    # Get storage data for per-TB calculation
                    storage_list = await loop.run_in_executor(
                        app["db_executor"],
                        blocking_get_latest_storage_with_forecast,
                        DATABASE_FILE,
                        [node_name],
                        7,
                    )
                    
                    if storage_list and storage_list[0].get("used_bytes"):
                        used_space_tb = storage_list[0]["used_bytes"] / (1024 ** 4)
                        if used_space_tb > 0:
                            metrics["earnings_per_tb"] = total_earnings / used_space_tb
                        else:
                            metrics["earnings_per_tb"] = 0
                    else:
                        metrics["earnings_per_tb"] = 0
                else:
                    log.warning(f"[Comparison] No earnings found for {node_name}, tracker unavailable")
                    metrics["total_earnings"] = 0
                    metrics["earnings_per_tb"] = 0
        
        if comparison_type in ["efficiency", "overall"]:
            # Get storage efficiency
            storage_list = await loop.run_in_executor(
                app["db_executor"],
                blocking_get_latest_storage_with_forecast,
                DATABASE_FILE,
                [node_name],
                7,
            )
            
            if storage_list:
                storage = storage_list[0]
                metrics["storage_utilization"] = storage.get("used_percent", 0)
                metrics["storage_efficiency"] = calculate_storage_efficiency(storage)
            else:
                metrics["storage_utilization"] = 0
                metrics["storage_efficiency"] = 0
        
        # Get reputation scores from database (reputation tracker writes to DB)
        reputation_list = await loop.run_in_executor(
            app["db_executor"],
            blocking_get_latest_reputation,
            DATABASE_FILE,
            [node_name],
        )
        
        if reputation_list:
            audit_score = calculate_avg_score(reputation_list, "audit_score")
            online_score = calculate_avg_score(reputation_list, "online_score")
            log.info(f"[Comparison] Scores for {node_name}: audit={audit_score:.4f}, online={online_score:.4f}")
            metrics["avg_audit_score"] = audit_score
            metrics["avg_online_score"] = online_score
        else:
            # No reputation data in database - reputation tracking may not be enabled
            log.debug(f"[Comparison] No reputation history in database for {node_name} - reputation tracking may not be enabled")
            metrics["avg_audit_score"] = 0
            metrics["avg_online_score"] = 0
    
    except Exception as e:
        log.error(f"Error gathering metrics for node {node_name}: {e}", exc_info=True)
        # Return empty metrics on error
        metrics = {
            "success_rate_download": 0,
            "success_rate_upload": 0,
            "success_rate_audit": 0,
            "avg_latency_p50": 0,
            "avg_latency_p95": 0,
            "avg_latency_p99": 0,
            "total_operations": 0,
            "total_earnings": 0,
            "earnings_per_tb": 0,
            "storage_utilization": 0,
            "storage_efficiency": 0,
            "avg_audit_score": 0,
            "avg_online_score": 0,
        }
    
    return metrics


def calculate_rankings(nodes_data: List[Dict], comparison_type: str) -> Dict[str, List[str]]:
    """Calculate rankings for each metric (higher is better, except latency)."""
    rankings = {}
    
    # Get all metric keys
    if nodes_data:
        metric_keys = nodes_data[0]["metrics"].keys()
        
        for metric_key in metric_keys:
            # Extract values for this metric from all nodes
            metric_values = []
            for node in nodes_data:
                value = node["metrics"].get(metric_key, 0)
                metric_values.append((node["node_name"], value))
            
            # Sort by value (descending for most metrics, ascending for latency)
            if "latency" in metric_key:
                # Lower latency is better - filter out zeros first for proper ranking
                non_zero = [(name, val) for name, val in metric_values if val > 0]
                zero_vals = [(name, val) for name, val in metric_values if val == 0]
                non_zero.sort(key=lambda x: x[1])
                metric_values = non_zero + zero_vals
            else:
                # Higher is better
                metric_values.sort(key=lambda x: x[1], reverse=True)
            
            # Store ranking as list of node names
            rankings[metric_key] = [name for name, _ in metric_values]
    
    return rankings


async def calculate_comparison_metrics(
    app, node_names: List[str], comparison_type: str, time_range: str
) -> Dict:
    """
    Calculate normalized metrics for comparison.
    
    OPTIMIZED: Uses caching and concurrent execution for better performance.
    """
    # Parse time range
    hours = parse_time_range(time_range)
    
    # Check cache first
    cache_key = (tuple(sorted(node_names)), comparison_type, time_range)
    cache_ttl = 60  # Cache results for 1 minute
    
    if "comparison_cache" not in app:
        app["comparison_cache"] = {}
    
    if cache_key in app["comparison_cache"]:
        cached_data, cache_time = app["comparison_cache"][cache_key]
        if (time.time() - cache_time) < cache_ttl:
            log.info(f"[Comparison] Using cached results for {len(node_names)} nodes")
            return cached_data
    
    log.info(f"[Comparison] Computing metrics for {len(node_names)} nodes (type={comparison_type}, range={time_range})")
    
    # Gather data for each node concurrently for better performance
    tasks = [gather_node_metrics(app, node_name, hours, comparison_type) for node_name in node_names]
    metrics_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    nodes_data = []
    for node_name, result in zip(node_names, metrics_results):
        if isinstance(result, Exception):
            log.error(f"[Comparison] Error gathering metrics for {node_name}: {result}")
            # Provide empty metrics on error
            result = {
                "success_rate_download": 0,
                "success_rate_upload": 0,
                "success_rate_audit": 0,
                "avg_latency_p50": 0,
                "avg_latency_p95": 0,
                "avg_latency_p99": 0,
                "total_operations": 0,
                "total_earnings": 0,
                "earnings_per_tb": 0,
                "storage_utilization": 0,
                "storage_efficiency": 0,
                "avg_audit_score": 0,
                "avg_online_score": 0,
            }
        nodes_data.append({"node_name": node_name, "metrics": result})
    
    # Calculate rankings
    rankings = calculate_rankings(nodes_data, comparison_type)
    
    result = {"nodes": nodes_data, "rankings": rankings}
    
    # Cache the result
    app["comparison_cache"][cache_key] = (result, time.time())
    
    # Limit cache size to prevent memory bloat
    if len(app["comparison_cache"]) > 100:
        # Remove oldest entries
        oldest_keys = sorted(app["comparison_cache"].keys(), key=lambda k: app["comparison_cache"][k][1])[:50]
        for key in oldest_keys:
            del app["comparison_cache"][key]
    
    log.info(f"[Comparison] Computed and cached metrics for {len(node_names)} nodes")
    return result


async def handle_comparison_request(app, data: Dict) -> Dict:
    """
    Handle multi-node comparison data request.
    
    Request format:
    {
        "type": "get_comparison_data",
        "node_names": ["Node1", "Node2", "Node3"],
        "comparison_type": "performance",  # or "earnings", "efficiency", "overall"
        "time_range": "24h"  # or "7d", "30d"
    }
    """
    node_names = data.get("node_names", [])
    comparison_type = data.get("comparison_type", "overall")
    time_range = data.get("time_range", "24h")
    
    log.info(
        f"Comparison request: nodes={node_names}, type={comparison_type}, range={time_range}"
    )
    
    # Validate node names
    valid_nodes = set(app["nodes"].keys())
    valid_node_names = [n for n in node_names if n in valid_nodes]
    
    if not valid_node_names:
        return {
            "type": "comparison_data",
            "error": "No valid nodes specified",
            "nodes": [],
            "rankings": {},
        }
    
    # Calculate comparison metrics
    comparison_data = await calculate_comparison_metrics(
        app, valid_node_names, comparison_type, time_range
    )
    
    return {
        "type": "comparison_data",
        "comparison_type": comparison_type,
        "time_range": time_range,
        "nodes": comparison_data["nodes"],
        "rankings": comparison_data["rankings"],
    }

# Version string for cache busting (update when static files change)
STATIC_VERSION = str(int(time.time()))


async def safe_send_json(ws: web.WebSocketResponse, payload: dict) -> bool:
    """
    Safely send JSON data over WebSocket, handling connection errors gracefully.

    Returns:
        bool: True if sent successfully, False if connection was closed/closing
    """
    try:
        if ws.closed:
            return False
        await ws.send_json(payload)
        return True
    except (
        ConnectionResetError,
        aiohttp.client_exceptions.ClientConnectionResetError,
        RuntimeError,
        asyncio.CancelledError,
    ) as e:
        # Client disconnected or connection is closing - this is normal, don't log as error
        log.debug(f"Could not send message to client (connection closing): {type(e).__name__}")
        return False
    except Exception as e:
        # Unexpected error - log it
        log.warning(f"Unexpected error sending WebSocket message: {e}", exc_info=True)
        return False


def get_active_compactions_payload() -> Dict[str, Any]:
    """Gathers currently active compactions from all nodes and creates a payload."""
    active_list = []
    for node_name, node_state in app_state["nodes"].items():
        for key, start_time in node_state.get("active_compactions", {}).items():
            try:
                satellite, store = key.split(":", 1)
                active_list.append(
                    {
                        "node_name": node_name,
                        "satellite": satellite,
                        "store": store,
                        "start_iso": start_time.isoformat(),
                    }
                )
            except ValueError:
                log.warning(f"Malformed compaction key '{key}' for node '{node_name}'")

    return {"type": "active_compactions_update", "compactions": active_list}


async def send_initial_stats(app, ws, view: List[str]):
    """
    Sends stats to a client upon connection or view change.
    For the optimized version, we use the incremental stats if available.
    """
    from .state import IncrementalStats

    view_tuple = tuple(view)

    # Try to send from cache first
    if view_tuple in app_state["stats_cache"]:
        try:
            await safe_send_json(ws, app_state["stats_cache"][view_tuple])
            return
        except (ConnectionResetError, asyncio.CancelledError):
            return  # Client disconnected

    # If not in cache, compute it now for this one client
    log.info(f"Cache miss for view {view}. Computing stats on-demand.")

    # Create temporary incremental stats for this view
    stats = IncrementalStats()
    nodes_to_process = view if view != ["Aggregate"] else list(app_state["nodes"].keys())

    # Process all current events
    for node_name in nodes_to_process:
        if node_name in app_state["nodes"]:
            for event in list(app_state["nodes"][node_name]["live_events"]):
                stats.add_event(event, app_state["TOKEN_REGEX"])

    # Update live stats
    all_events = []
    for node_name in nodes_to_process:
        if node_name in app_state["nodes"]:
            all_events.extend(list(app_state["nodes"][node_name]["live_events"]))
    stats.update_live_stats(all_events)

    # Get historical stats
    historical_stats = database.get_historical_stats(view, app_state["nodes"])

    # Generate and send payload
    try:
        payload = stats.to_payload(historical_stats)
        app_state["stats_cache"][view_tuple] = payload
        await safe_send_json(ws, payload)
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # Client disconnected during computation
    except Exception:
        log.error(f"Error computing initial stats for view {view}:", exc_info=True)


@web.middleware
async def cache_control_middleware(request, handler):
    """Add cache control headers to static files"""
    response = await handler(request)

    # Add no-cache headers for static files to prevent caching issues during development
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


async def handle_index(request):
    """Serve index.html with version parameter for cache busting"""
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")

    # Read the file and inject version parameter
    with open(index_path, "r") as f:
        content = f.read()

    # Add version parameter to JS file imports
    content = content.replace("/static/js/app.js", f"/static/js/app.js?v={STATIC_VERSION}")

    response = web.Response(text=content, content_type="text/html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    app_state["websockets"][ws] = {"view": ["Aggregate"]}

    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")

    try:
        node_names = list(app["nodes"].keys())
        await safe_send_json(ws, {"type": "init", "nodes": node_names})
        await send_initial_stats(app, ws, ["Aggregate"])
        await safe_send_json(ws, get_active_compactions_payload())

        # Send initial earnings data from cache if available
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime("%Y-%m")
        cache_key = ("Aggregate", period)

        if cache_key in app_state.get("earnings_cache", {}):
            log.info(f"Sending cached earnings data for Aggregate view on connect")
            await safe_send_json(ws, app_state["earnings_cache"][cache_key])
        else:
            log.info(
                f"No cached earnings data available on connect, client will receive broadcast when ready"
            )
    except (ConnectionResetError, aiohttp.client_exceptions.ClientConnectionResetError):
        # Client disconnected during initial setup
        log.debug("Client disconnected during initial setup")
        if ws in app_state["websockets"]:
            del app_state["websockets"][ws]
        return ws

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")

                    if msg_type == "set_view":
                        new_view = data.get("view")
                        if isinstance(new_view, list) and new_view:
                            valid_nodes = set(app["nodes"].keys())
                            is_aggregate = new_view == ["Aggregate"]
                            are_nodes_valid = all(node in valid_nodes for node in new_view)

                            if is_aggregate or are_nodes_valid:
                                app_state["websockets"][ws]["view"] = new_view
                                log.info(f"Client switched view to: {new_view}")
                                await send_initial_stats(app, ws, new_view)

                                # Send cached earnings data for the new view if available
                                import datetime

                                now = datetime.datetime.now(datetime.timezone.utc)
                                period = now.strftime("%Y-%m")

                                # Determine cache key based on view
                                if is_aggregate:
                                    cache_key = ("Aggregate", period)
                                else:
                                    # For single or multiple specific nodes
                                    if len(new_view) == 1:
                                        cache_key = (new_view[0], period)
                                    else:
                                        cache_key = tuple(sorted(new_view)) + (period,)

                                if cache_key in app_state.get("earnings_cache", {}):
                                    log.info(
                                        f"Sending cached earnings data for view {new_view} on view switch"
                                    )
                                    await safe_send_json(ws, app_state["earnings_cache"][cache_key])

                    elif msg_type == "get_historical_performance":
                        view = data.get("view")  # This is now a list
                        points = data.get("points", 150)
                        interval = data.get("interval_sec", PERFORMANCE_INTERVAL_SECONDS)
                        loop = asyncio.get_running_loop()

                        events_to_process = []
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app_state["nodes"].keys())
                        )
                        for node_name in nodes_to_query:
                            if node_name in app_state["nodes"]:
                                events_to_process.extend(
                                    list(app_state["nodes"][node_name]["live_events"])
                                )

                        historical_data = await loop.run_in_executor(
                            app["db_executor"],
                            database.blocking_get_historical_performance,
                            events_to_process,
                            points,
                            interval,
                        )
                        payload = {
                            "type": "historical_performance_data",
                            "view": view,
                            "performance_data": historical_data,
                        }
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_aggregated_performance":
                        view = data.get("view")  # This is a list
                        time_window_hours = data.get("hours", 1)
                        loop = asyncio.get_running_loop()

                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        aggregated_data = await loop.run_in_executor(
                            app["db_executor"],
                            database.blocking_get_aggregated_performance,
                            nodes_to_query,
                            time_window_hours,
                        )

                        payload = {
                            "type": "aggregated_performance_data",
                            "view": view,
                            "performance_data": aggregated_data,
                        }
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_hashstore_stats":
                        filters = data.get("filters", {})
                        loop = asyncio.get_running_loop()
                        hashstore_data = await loop.run_in_executor(
                            app["db_executor"], database.blocking_get_hashstore_stats, filters
                        )
                        payload = {"type": "hashstore_stats_data", "data": hashstore_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_reputation_data":
                        # Phase 1.3: Get current reputation data
                        view = data.get("view", ["Aggregate"])
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE

                        reputation_data = await loop.run_in_executor(
                            app["db_executor"],
                            database.blocking_get_latest_reputation,
                            DATABASE_FILE,
                            nodes_to_query,
                        )
                        payload = {"type": "reputation_data", "data": reputation_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_latency_stats":
                        # Phase 2.1: Get latency statistics
                        view = data.get("view", ["Aggregate"])
                        hours = data.get("hours", 1)
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .performance_analyzer import blocking_get_latency_stats

                        latency_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_latency_stats,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours,
                        )
                        payload = {"type": "latency_stats", "data": latency_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_latency_histogram":
                        # Phase 2.1: Get latency histogram
                        view = data.get("view", ["Aggregate"])
                        hours = data.get("hours", 1)
                        bucket_size = data.get("bucket_size_ms", 100)
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .performance_analyzer import blocking_get_latency_histogram

                        histogram_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_latency_histogram,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours,
                            bucket_size,
                        )
                        payload = {"type": "latency_histogram", "data": histogram_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_storage_data":
                        # Phase 2.2: Get current storage data with configurable growth rate window
                        view = data.get("view", ["Aggregate"])
                        days = data.get("days", 7)  # Default to 7 days if not specified
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        log.info(
                            f"Storage data request received for view: {view}, nodes: {nodes_to_query}, days window: {days}"
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_latest_storage_with_forecast

                        storage_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_latest_storage_with_forecast,
                            DATABASE_FILE,
                            nodes_to_query,
                            days,
                        )

                        log.info(
                            f"Storage data query returned {len(storage_data) if storage_data else 0} result(s)"
                        )
                        if storage_data:
                            for item in storage_data:
                                log.info(
                                    f"  Sending to client: Node={item.get('node_name')}, Available={item.get('available_bytes', 0) / (1024**4):.2f} TB"
                                )

                        payload = {"type": "storage_data", "data": storage_data}
                        await safe_send_json(ws, payload)
                        log.info("Storage data payload sent to client")

                    elif msg_type == "get_storage_history":
                        # Phase 2.2: Get storage history
                        node_name = data.get("node_name")
                        days = data.get("days", 7)

                        if not node_name:
                            await safe_send_json(
                                ws, {"type": "error", "message": "node_name required"}
                            )
                            continue

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_storage_history

                        history_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_storage_history,
                            DATABASE_FILE,
                            node_name,
                            days,
                        )
                        payload = {"type": "storage_history", "data": history_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_active_alerts":
                        # Phase 4: Get active alerts
                        view = data.get("view", ["Aggregate"])
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        if "alert_manager" in app:
                            alerts = await app["alert_manager"].get_active_alerts(nodes_to_query)
                            payload = {"type": "active_alerts", "data": alerts}
                            await safe_send_json(ws, payload)
                        else:
                            await safe_send_json(
                                ws, {"type": "error", "message": "Alert manager not initialized"}
                            )

                    elif msg_type == "acknowledge_alert":
                        # Phase 4: Acknowledge an alert
                        alert_id = data.get("alert_id")

                        if not alert_id:
                            await safe_send_json(
                                ws, {"type": "error", "message": "alert_id required"}
                            )
                            continue

                        if "alert_manager" in app:
                            success = await app["alert_manager"].acknowledge_alert(alert_id)
                            payload = {
                                "type": "alert_acknowledge_result",
                                "success": success,
                                "alert_id": alert_id,
                            }
                            await safe_send_json(ws, payload)
                        else:
                            await safe_send_json(
                                ws, {"type": "error", "message": "Alert manager not initialized"}
                            )

                    elif msg_type == "get_insights":
                        # Phase 4: Get recent insights
                        view = data.get("view", ["Aggregate"])
                        hours = data.get("hours", 24)
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_insights

                        insights = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_insights,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours,
                        )
                        payload = {"type": "insights_data", "data": insights}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_alert_summary":
                        # Phase 4: Get alert summary
                        if "alert_manager" in app:
                            summary = app["alert_manager"].get_alert_summary()
                            payload = {"type": "alert_summary", "data": summary}
                            await safe_send_json(ws, payload)
                        else:
                            await safe_send_json(
                                ws,
                                {
                                    "type": "alert_summary",
                                    "data": {"critical": 0, "warning": 0, "info": 0, "total": 0},
                                },
                            )

                    elif msg_type == "get_earnings_data":
                        # Phase 5.3: Get earnings data for specified period
                        view = data.get("view", ["Aggregate"])
                        period_param = data.get("period", "current")
                        nodes_to_query = (
                            view if view != ["Aggregate"] else list(app["nodes"].keys())
                        )

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .financial_tracker import SATELLITE_NAMES
                        from .database import blocking_get_latest_earnings

                        # Calculate period based on parameter
                        import datetime

                        now = datetime.datetime.now(datetime.timezone.utc)

                        if period_param == "previous":
                            # Previous month
                            if now.month == 1:
                                period = f"{now.year - 1}-12"
                            else:
                                period = f"{now.year}-{str(now.month - 1).zfill(2)}"
                        elif period_param == "12months":
                            # Aggregate last 12 months of data
                            import datetime

                            now = datetime.datetime.now(datetime.timezone.utc)

                            # Get data for last 12 complete months
                            earnings_data = []
                            for months_ago in range(12):
                                month_date = now - datetime.timedelta(days=30 * months_ago)
                                month_period = month_date.strftime("%Y-%m")

                                month_data = await loop.run_in_executor(
                                    app["db_executor"],
                                    blocking_get_latest_earnings,
                                    DATABASE_FILE,
                                    nodes_to_query,
                                    month_period,
                                )
                                earnings_data.extend(month_data)

                            # Format aggregated data
                            formatted_data = []
                            # Group by node and satellite for 12-month totals
                            from collections import defaultdict

                            aggregated = defaultdict(
                                lambda: {
                                    "total_net": 0,
                                    "total_gross": 0,
                                    "held_amount": 0,
                                    "egress": 0,
                                    "storage": 0,
                                    "repair": 0,
                                    "audit": 0,
                                }
                            )

                            for estimate in earnings_data:
                                key = (estimate["node_name"], estimate["satellite"])
                                agg = aggregated[key]
                                agg["total_net"] += estimate.get("total_earnings_net", 0)
                                agg["total_gross"] += estimate.get("total_earnings_gross", 0)
                                agg["held_amount"] += estimate.get("held_amount", 0)
                                agg["egress"] += estimate.get("egress_earnings_net", 0)
                                agg["storage"] += estimate.get("storage_earnings_net", 0)
                                agg["repair"] += estimate.get("repair_earnings_net", 0)
                                agg["audit"] += estimate.get("audit_earnings_net", 0)

                            # Convert to response format
                            for (node_name, satellite), data in aggregated.items():
                                sat_name = SATELLITE_NAMES.get(satellite, satellite[:8])
                                formatted_data.append(
                                    {
                                        "node_name": node_name,
                                        "satellite": sat_name,
                                        "total_net": round(data["total_net"], 2),
                                        "total_gross": round(data["total_gross"], 2),
                                        "held_amount": round(data["held_amount"], 2),
                                        "breakdown": {
                                            "egress": round(data["egress"], 2),
                                            "storage": round(data["storage"], 2),
                                            "repair": round(data["repair"], 2),
                                            "audit": round(data["audit"], 2),
                                        },
                                        "forecast_month_end": None,  # No forecast for historical aggregate
                                        "confidence": None,
                                    }
                                )

                            payload = {"type": "earnings_data", "data": formatted_data}
                            await safe_send_json(ws, payload)
                            continue
                        else:
                            # 'current' or any other value defaults to current month
                            period = now.strftime("%Y-%m")

                        # Create cache key including period
                        # Normalize cache key: use 'Aggregate' for aggregate views
                        if view == ["Aggregate"]:
                            cache_key = ("Aggregate", period)
                        else:
                            cache_key = tuple(nodes_to_query) + (period,)

                        # Try to serve from cache first
                        if cache_key in app_state.get("earnings_cache", {}):
                            await safe_send_json(ws, app_state["earnings_cache"][cache_key])
                            continue

                        earnings_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_latest_earnings,
                            DATABASE_FILE,
                            nodes_to_query,
                            period,
                        )

                        # Format data with forecasts
                        formatted_data = []
                        for estimate in earnings_data:
                            # Calculate forecast if tracker available
                            tracker = app.get("financial_trackers", {}).get(estimate["node_name"])
                            forecast_info = None
                            if tracker:
                                try:
                                    forecast_info = await tracker.forecast_payout(
                                        DATABASE_FILE, period, loop, app.get("db_executor")
                                    )
                                except Exception as e:
                                    log.error(
                                        f"Failed to get forecast for {estimate['node_name']}: {e}"
                                    )

                            sat_name = SATELLITE_NAMES.get(
                                estimate["satellite"], estimate["satellite"][:8]
                            )

                            formatted_data.append(
                                {
                                    "node_name": estimate["node_name"],
                                    "satellite": sat_name,
                                    "total_net": round(estimate["total_earnings_net"], 2),
                                    "total_gross": round(estimate["total_earnings_gross"], 2),
                                    "held_amount": round(estimate["held_amount"], 2),
                                    "breakdown": {
                                        "egress": round(estimate["egress_earnings_net"], 2),
                                        "storage": round(estimate["storage_earnings_net"], 2),
                                        "repair": round(estimate["repair_earnings_net"], 2),
                                        "audit": round(estimate["audit_earnings_net"], 2),
                                    },
                                    "forecast_month_end": round(
                                        forecast_info["forecasted_payout"], 2
                                    )
                                    if forecast_info
                                    else None,
                                    "confidence": round(forecast_info["confidence"], 2)
                                    if forecast_info
                                    else None,
                                }
                            )

                        payload = {"type": "earnings_data", "data": formatted_data}

                        # Debug log to show what breakdown values are being sent
                        log.info(
                            f"Sending earnings for period {period} with {len(formatted_data)} satellites"
                        )
                        for item in formatted_data:
                            breakdown = item.get("breakdown", {})
                            total_breakdown = sum(breakdown.values())
                            log.debug(
                                f"[{item['node_name']}] {item['satellite']}: "
                                f"total_net=${item['total_net']:.2f}, "
                                f"breakdown sum=${total_breakdown:.2f}"
                            )

                        # Store in cache with period included in key
                        if "earnings_cache" not in app_state:
                            app_state["earnings_cache"] = {}
                        app_state["earnings_cache"][cache_key] = payload

                        await safe_send_json(ws, payload)

                    elif msg_type == "get_earnings_history":
                        # Phase 5.3: Get earnings history with LAZY IMPORT
                        node_name = data.get("node_name")
                        satellite = data.get("satellite")
                        days = data.get("days", 30)

                        log.info(
                            f"Earnings history requested: node={node_name}, satellite={satellite}, days={days}"
                        )

                        if not node_name:
                            await safe_send_json(
                                ws, {"type": "error", "message": "node_name required"}
                            )
                            continue

                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_earnings_estimates

                        # OPTIMIZATION: Trigger on-demand historical import if not done yet
                        tracker = app.get("financial_trackers", {}).get(node_name)
                        if tracker and not hasattr(tracker, '_historical_imported'):
                            log.info(f"[{node_name}] Lazy-loading historical earnings on first request...")
                            try:
                                await tracker.import_historical_payouts(
                                    DATABASE_FILE, loop, app.get("db_executor")
                                )
                                tracker._historical_imported = True
                                log.info(f"[{node_name}] Historical import complete")
                            except Exception as e:
                                log.warning(f"[{node_name}] Historical import failed: {e}")
                                tracker._historical_imported = True  # Mark as attempted

                        history_data = await loop.run_in_executor(
                            app["db_executor"],
                            blocking_get_earnings_estimates,
                            DATABASE_FILE,
                            [node_name],
                            satellite,
                            None,  # period
                            days,
                        )

                        log.info(
                            f"Earnings history query returned {len(history_data) if history_data else 0} records"
                        )
                        if history_data and len(history_data) > 0:
                            log.info(
                                f"Sample record: period={history_data[0].get('period')}, total_net={history_data[0].get('total_earnings_net')}"
                            )

                        payload = {"type": "earnings_history", "data": history_data}
                        await safe_send_json(ws, payload)

                    elif msg_type == "get_comparison_data":
                        # Phase 9: Multi-node comparison
                        response = await handle_comparison_request(app, data)
                        await safe_send_json(ws, response)

                except Exception:
                    log.error("Could not parse websocket message:", exc_info=True)

    finally:
        if ws in app_state["websockets"]:
            del app_state["websockets"][ws]
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws


def run_server(nodes_config: Dict[str, Any]):
    app = web.Application(middlewares=[cache_control_middleware])
    app["nodes"] = nodes_config

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", websocket_handler)

    static_path = os.path.join(os.path.dirname(__file__), "static")
    app.router.add_static("/static/", path=static_path, name="static")

    log.info(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    log.info(f"Static files version: {STATIC_VERSION}")
    log.info(f"Monitoring nodes: {list(app['nodes'].keys())}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)
