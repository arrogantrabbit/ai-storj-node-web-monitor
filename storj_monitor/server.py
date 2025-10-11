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
    total_earnings = earnings_data.get("total_earnings_net") or 0
    # Get used space in TB from storage data if available
    used_space_tb = earnings_data.get("used_space_tb") or 0
    
    if isinstance(used_space_tb, (int, float)) and used_space_tb > 0 and isinstance(total_earnings, (int, float)):
        return total_earnings / used_space_tb
    return 0.0


def calculate_storage_efficiency(storage_data: Dict) -> float:
    """Calculate storage efficiency score (0-100)."""
    used_percent = storage_data.get("used_percent")
    trash_percent = storage_data.get("trash_percent")
    
    # Coerce None to 0 for safe comparison/arithmetic
    used_percent = 0 if used_percent is None else used_percent
    trash_percent = 0 if trash_percent is None else trash_percent
    
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
    import time
    start_time = time.time()
    log.info(f"[Comparison Debug] Starting metrics gathering for node {node_name}, type={comparison_type}")
    from .database import (
        blocking_get_events,
        blocking_get_event_counts,
        blocking_get_latest_reputation,
        blocking_get_latest_storage_with_forecast,
        blocking_get_latest_earnings,
    )
    
    metrics = {}
    counts = None
    loop = asyncio.get_running_loop()
    
    try:
        if comparison_type in ["performance", "overall", "earnings", "efficiency"]:
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
            
            # Calculate latency metrics from sampled events
            durations = [e.get("duration_ms", 0) for e in events if e.get("duration_ms")]
            if durations:
                metrics["avg_latency_p50"] = calculate_percentile(durations, 50)
                metrics["avg_latency_p95"] = calculate_percentile(durations, 95)
                metrics["avg_latency_p99"] = calculate_percentile(durations, 99)
            else:
                # No latency data available in the window -> display as N/A
                metrics["avg_latency_p50"] = None
                metrics["avg_latency_p95"] = None
                metrics["avg_latency_p99"] = None

            # Default total operations based on sampled events (will be overridden by aggregated counts if available)
            metrics["total_operations"] = len(events)

            # Use aggregated counts for accurate success rates and total operations over the full window
            try:
                counts_list = await loop.run_in_executor(
                    app["db_executor"],
                    blocking_get_event_counts,
                    DATABASE_FILE,
                    [node_name],
                    hours,
                )
                if counts_list:
                    c = counts_list[0]
                    dl_total = (c.get("dl_success") or 0) + (c.get("dl_fail") or 0)
                    ul_total = (c.get("ul_success") or 0) + (c.get("ul_fail") or 0)
                    audit_total = (c.get("audit_success") or 0) + (c.get("audit_fail") or 0)

                    metrics["success_rate_download"] = ((c.get("dl_success") or 0) / dl_total * 100.0) if dl_total > 0 else None
                    metrics["success_rate_upload"] = ((c.get("ul_success") or 0) / ul_total * 100.0) if ul_total > 0 else None
                    metrics["success_rate_audit"] = ((c.get("audit_success") or 0) / audit_total * 100.0) if audit_total > 0 else None

                    metrics["total_operations"] = int(c.get("total_ops") or 0)

                    # Save for later fallback usage (e.g., avg_audit_score if reputation data is missing)
                    counts = c
                else:
                    # Fallback to sample-based rates if aggregation returns nothing
                    downloads = [e for e in events if e.get("action") == "GET"]
                    uploads = [e for e in events if e.get("action") == "PUT"]
                    audits = [e for e in events if e.get("action") == "GET_AUDIT"]

                    metrics["success_rate_download"] = calculate_success_rate(downloads) if len(downloads) > 0 else None
                    metrics["success_rate_upload"] = calculate_success_rate(uploads) if len(uploads) > 0 else None
                    metrics["success_rate_audit"] = calculate_success_rate(audits) if len(audits) > 0 else None
            except Exception as agg_err:
                log.debug(f"[Comparison] Aggregated counts query failed for {node_name}: {agg_err}")
                # Fallback to sample-based calculation
                downloads = [e for e in events if e.get("action") == "GET"]
                uploads = [e for e in events if e.get("action") == "PUT"]
                audits = [e for e in events if e.get("action") == "GET_AUDIT"]

                metrics["success_rate_download"] = calculate_success_rate(downloads)
                metrics["success_rate_upload"] = calculate_success_rate(uploads)
                metrics["success_rate_audit"] = calculate_success_rate(audits)
        
        if comparison_type in ["earnings", "overall"]:
            # Get earnings data - PERFORMANCE FIX: Always use pre-computed earnings from database
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            period = now.strftime("%Y-%m")
            
            earnings_start_time = time.time()
            log.info(f"[Comparison] Fetching earnings for {node_name}, period: {period}")
            
            # CRITICAL OPTIMIZATION: Always use pre-computed earnings from database
            # instead of recalculating via financial tracker. This is the main bottleneck
            # when comparing multiple nodes.
            earnings_list = await loop.run_in_executor(
                app["db_executor"],
                blocking_get_latest_earnings,
                DATABASE_FILE,
                [node_name],
                period,
            )
            
            if earnings_list:
                total_earnings = sum(e.get("total_earnings_net", 0) for e in earnings_list)
                log.info(f"[Comparison] Total earnings for {node_name}: ${total_earnings:.2f}")
                metrics["total_earnings"] = total_earnings
                
                # This storage_list will be retrieved later in a single batch operation
                # to avoid redundant per-node database calls
                storage_list = None
                
                # Storage data is now handled at a higher level in calculate_comparison_metrics
                # The earnings_per_tb will be calculated there using the batch-loaded storage data
                metrics["earnings_per_tb"] = None  # Default to None; will be computed when storage data is injected
            else:
                log.warning(f"[Comparison] No earnings found for {node_name}, period: {period}")
                # Mark as not-yet-ready with None so UI shows N/A and cache logic can detect incompleteness
                metrics["total_earnings"] = None
                metrics["earnings_per_tb"] = None
            
            earnings_duration = time.time() - earnings_start_time
            log.info(f"[Comparison] Earnings lookup for {node_name} took {earnings_duration:.2f} seconds")
        
        if comparison_type in ["efficiency", "overall"]:
            # Storage data is now handled at a higher level in calculate_comparison_metrics
            # Default values set to None; they will be updated when storage data is injected
            metrics["storage_utilization"] = None
            metrics["storage_efficiency"] = None
        
        # Get reputation scores from database (reputation tracker writes to DB)
        reputation_start_time = time.time()
        log.info(f"[Comparison Debug] Starting reputation lookup for {node_name}")
        reputation_list = await loop.run_in_executor(
            app["db_executor"],
            blocking_get_latest_reputation,
            DATABASE_FILE,
            [node_name],
        )
        reputation_duration = time.time() - reputation_start_time
        log.info(f"[Comparison Debug] Reputation lookup for {node_name} took {reputation_duration:.2f} seconds")
        
        if reputation_list:
            audit_score = calculate_avg_score(reputation_list, "audit_score")
            online_score = calculate_avg_score(reputation_list, "online_score")
            log.info(f"[Comparison] Scores for {node_name}: audit={audit_score:.4f}, online={online_score:.4f}")
            metrics["avg_audit_score"] = audit_score
            metrics["avg_online_score"] = online_score
        else:
            # No reputation data in database - reputation tracking may not be enabled
            log.debug(f"[Comparison] No reputation history in database for {node_name} - reputation tracking may not be enabled")
            # Fallback: derive audit score from audit success rate if available
            if "success_rate_audit" in metrics and metrics["success_rate_audit"] is not None:
                metrics["avg_audit_score"] = metrics["success_rate_audit"]
            else:
                # As a last resort, set to None so UI displays N/A rather than 0
                metrics["avg_audit_score"] = None
            # Online score cannot be derived from logs; report as None for N/A in UI
            metrics["avg_online_score"] = None
    
    except Exception as e:
        log.error(f"Error gathering metrics for node {node_name}: {e}", exc_info=True)
        # Return N/A semantics on error so UI doesn't render misleading zeros
        metrics = {
            "success_rate_download": None,
            "success_rate_upload": None,
            "success_rate_audit": None,
            "avg_latency_p50": None,
            "avg_latency_p95": None,
            "avg_latency_p99": None,
            "total_operations": None,
            "total_earnings": None,
            "earnings_per_tb": None,
            "storage_utilization": None,
            "storage_efficiency": None,
            "avg_audit_score": None,
            "avg_online_score": None,
        }
    
    total_duration = time.time() - start_time
    log.info(f"[Comparison Debug] Total metrics gathering for {node_name} took {total_duration:.2f} seconds")
    return metrics


def calculate_rankings(nodes_data: List[Dict], comparison_type: str) -> Dict[str, List[str]]:
    """Calculate rankings for each metric (higher is better, except latency).

    Robust against None values by placing them at the end of rankings.
    """
    rankings: Dict[str, List[str]] = {}

    if not nodes_data:
        return rankings

    # Use union of keys across nodes to avoid missing metrics
    metric_keys = set()
    for node in nodes_data:
        metric_keys.update(node.get("metrics", {}).keys())

    for metric_key in metric_keys:
        is_latency = "latency" in metric_key

        # Build sortable tuples (node_name, sort_key) while handling None gracefully
        sortable = []
        for node in nodes_data:
            value = node.get("metrics", {}).get(metric_key)

            if is_latency:
                # Lower is better; None, 0, or negative treated as worst
                if isinstance(value, (int, float)) and value is not None and value > 0:
                    sort_key = float(value)
                else:
                    sort_key = float("inf")
            else:
                # Higher is better; None treated as very low
                if isinstance(value, (int, float)) and value is not None:
                    sort_key = float(value)
                else:
                    sort_key = float("-inf")

            sortable.append((node["node_name"], sort_key))

        # Sort accordingly
        if is_latency:
            sortable.sort(key=lambda t: t[1])  # ascending (low is good)
        else:
            sortable.sort(key=lambda t: t[1], reverse=True)  # descending (high is good)

        rankings[metric_key] = [name for name, _ in sortable]

    return rankings


async def calculate_comparison_metrics(
    app, node_names: List[str], comparison_type: str, time_range: str
) -> Dict:
    """
    Calculate normalized metrics for comparison.
    
    OPTIMIZED: Uses caching, concurrent execution, and pre-computed data
    from database for better performance.
    """
    # Import at the top of function to avoid scope issues
    import asyncio
    import time as time_module  # Use a different name to avoid variable shadowing
    from .database import (
        blocking_get_latest_storage_with_forecast,
        blocking_get_latest_reputation,
        blocking_get_latest_earnings,
    )
    
    # Parse time range
    hours = parse_time_range(time_range)
    
    # PERFORMANCE OPTIMIZATION: Enhanced caching for comparison data
    # Scale cache TTL based on number of nodes being compared and comparison type
    cache_key = (tuple(sorted(node_names)), comparison_type, time_range)
    
    # Longer TTL for more intensive comparison types and larger node sets
    base_ttl = 60  # Base TTL of 1 minute
    node_factor = len(node_names)  # Scale with number of nodes
    
    # Different types have different computation costs
    type_factor = {
        "performance": 1,
        "efficiency": 1.5,
        "earnings": 2,  # Most expensive, cache longer
        "overall": 2
    }.get(comparison_type, 1)
    
    # Calculate final TTL with an upper bound of 20 minutes
    cache_ttl = min(base_ttl * node_factor * type_factor, 1200)
    
    if "comparison_cache" not in app:
        app["comparison_cache"] = {}
    
    if cache_key in app["comparison_cache"]:
        cached_data, cache_time = app["comparison_cache"][cache_key]
        cache_age_ok = (time_module.time() - cache_time) < cache_ttl

        # Warm-up guard: during first few minutes after server start, avoid using
        # cached earnings/overall results if they contain missing or zero earnings.
        warmup_guard = False
        if comparison_type in ["earnings", "overall"]:
            app_start = app.get("start_time", 0)
            warmup_window = 300  # 5 minutes
            if app_start and (time_module.time() - app_start) < warmup_window:
                try:
                    nodes = cached_data.get("nodes", [])
                    warmup_guard = any(
                        (n.get("metrics", {}).get("total_earnings") is None) or
                        (n.get("metrics", {}).get("total_earnings") == 0)
                        for n in nodes
                    )
                except Exception:
                    warmup_guard = False

        if cache_age_ok and not warmup_guard:
            log.info(f"[Comparison] Using cached results for {len(node_names)} nodes (type={comparison_type})")
            return cached_data
        else:
            if warmup_guard:
                log.info("[Comparison] Ignoring cached comparison during warm-up due to incomplete earnings")
    
    start_time = time_module.time()
    loop = asyncio.get_running_loop()  # Get the current event loop
    
    log.info(f"[Comparison Debug] Computing metrics for {len(node_names)} nodes (type={comparison_type}, range={time_range})")
    
    # PERFORMANCE OPTIMIZATION: Get storage data in a single batch operation for all nodes
    # This prevents redundant database calls when each node requests storage data
    storage_data_start = time_module.time()
    storage_data = {}
    if comparison_type in ["earnings", "overall", "efficiency"]:
        storage_list = await loop.run_in_executor(
            app["db_executor"],
            blocking_get_latest_storage_with_forecast,
            DATABASE_FILE,
            node_names,  # Pass all nodes at once instead of individual queries
            7,
        )
        
        # Create a lookup map for fast access
        for item in storage_list:
            if "node_name" in item:
                storage_data[item["node_name"]] = item
        
        log.info(f"[Comparison Debug] Batch storage data retrieval took {time_module.time() - storage_data_start:.2f} seconds")

    # Batch-load earnings for the current month to avoid per-node gaps and ensure consistency
    earnings_lookup = {}
    if comparison_type in ["earnings", "overall"]:
        import datetime
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        current_period = now_dt.strftime("%Y-%m")
        earnings_list = await loop.run_in_executor(
            app["db_executor"],
            blocking_get_latest_earnings,
            DATABASE_FILE,
            node_names,
            current_period,
        )
        from collections import defaultdict
        agg = defaultdict(float)
        for row in (earnings_list or []):
            try:
                agg[row.get("node_name")] += float(row.get("total_earnings_net") or 0.0)
            except Exception:
                pass
        earnings_lookup = dict(agg)
        # Fallback: if DB returns no rows for current month (e.g., race with writer),
        # pull totals from in-memory earnings cache used by the Financial card.
        try:
            cache = app_state.get("earnings_cache", {})
            for n in node_names:
                v = earnings_lookup.get(n)
                if not isinstance(v, (int, float)) or v <= 0:
                    payload = cache.get((n, current_period))
                    if payload and isinstance(payload.get("data"), list) and payload["data"]:
                        total_net = sum(float(item.get("total_net") or 0.0) for item in payload["data"])
                        if total_net > 0:
                            earnings_lookup[n] = total_net
        except Exception:
            # Non-fatal; just means fallback cache wasn't available
            pass
        log.info(f"[Comparison Debug] Batch earnings aggregation prepared for {len(earnings_lookup)} node(s)")

    # Batch-load latest reputation for all nodes; use as fallback if per-node call returns empty
    avg_audit_by_node = {}
    avg_online_by_node = {}
    try:
        rep_list_all = await loop.run_in_executor(
            app["db_executor"],
            blocking_get_latest_reputation,
            DATABASE_FILE,
            node_names,
        )
        from collections import defaultdict
        rep_by_node = defaultdict(list)
        for r in (rep_list_all or []):
            key = r.get("node_name")
            if key:
                rep_by_node[key].append(r)
        for n, rows in rep_by_node.items():
            try:
                avg_audit_by_node[n] = calculate_avg_score(rows, "audit_score")
                avg_online_by_node[n] = calculate_avg_score(rows, "online_score")
            except Exception:
                pass
        log.info(f"[Comparison Debug] Batch reputation prepared for {len(rep_by_node)} node(s)")
    except Exception:
        log.debug("[Comparison Debug] Batch reputation retrieval failed (non-fatal)", exc_info=True)

    # Gather metrics for each node concurrently - but now with pre-loaded storage data
    async def gather_with_storage(node_name):
        metrics = await gather_node_metrics(app, node_name, hours, comparison_type)
        
        # Inject storage data if we have it
        if node_name in storage_data:
            storage = storage_data[node_name]
            
            # Update earnings per TB calculation if we have earnings data
            te = metrics.get("total_earnings")
            if comparison_type in ["earnings", "overall"] and isinstance(te, (int, float)) and te > 0:
                if storage.get("used_bytes"):
                    used_space_tb = storage["used_bytes"] / (1024 ** 4)
                    if used_space_tb and used_space_tb > 0:
                        metrics["earnings_per_tb"] = te / used_space_tb
            
            # Update storage efficiency metrics
            if comparison_type in ["efficiency", "overall"]:
                up = storage.get("used_percent")
                metrics["storage_utilization"] = up if (up is not None) else None
                metrics["storage_efficiency"] = calculate_storage_efficiency(storage) if (up is not None) else None
        
        return metrics
    
    tasks = [gather_with_storage(node_name) for node_name in node_names]
    metrics_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    gather_duration = time_module.time() - start_time
    log.info(f"[Comparison Debug] Gathering metrics for {len(node_names)} nodes took {gather_duration:.2f} seconds")
    
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

    # Overlay any missing metrics using batch lookups to avoid N/A when data exists
    for node in nodes_data:
        name = node["node_name"]
        metrics = node["metrics"]

        # Earnings overlay
        if comparison_type in ["earnings", "overall"]:
            # If per-node call didn't find earnings, overlay from batch DB/cache results
            if (metrics.get("total_earnings") is None) or (metrics.get("total_earnings") == 0):
                te = earnings_lookup.get(name)
                if isinstance(te, (int, float)) and te > 0:
                    metrics["total_earnings"] = te

            # Compute earnings_per_tb if not set and we have storage + earnings
            te_val = metrics.get("total_earnings")
            if (
                (metrics.get("earnings_per_tb") is None or not isinstance(metrics.get("earnings_per_tb"), (int, float)))
                and isinstance(te_val, (int, float))
                and te_val > 0
            ):
                s = storage_data.get(name)
                used_bytes = s.get("used_bytes") if s else None
                if isinstance(used_bytes, (int, float)) and used_bytes > 0:
                    used_tb = used_bytes / (1024 ** 4)
                    if used_tb > 0:
                        metrics["earnings_per_tb"] = te_val / used_tb

        # Reputation overlay (ensure online/audit scores present for all comparison types)
        if metrics.get("avg_online_score") is None and name in avg_online_by_node:
            v = avg_online_by_node.get(name)
            if isinstance(v, (int, float)):
                metrics["avg_online_score"] = v
        if metrics.get("avg_audit_score") is None and name in avg_audit_by_node:
            v = avg_audit_by_node.get(name)
            if isinstance(v, (int, float)):
                metrics["avg_audit_score"] = v
    
    # PERFORMANCE OPTIMIZATION: Skip ranking calculation for large node sets if data is limited
    if len(node_names) >= 5 and comparison_type == "performance":
        # For large node sets with performance comparison,
        # only calculate rankings for the most important metrics
        important_metrics = ["success_rate_download", "avg_latency_p50", "total_operations", "avg_online_score"]
        nodes_data_subset = []
        for node in nodes_data:
            node_subset = {"node_name": node["node_name"], "metrics": {}}
            for key in important_metrics:
                if key in node["metrics"]:
                    node_subset["metrics"][key] = node["metrics"][key]
            nodes_data_subset.append(node_subset)
        rankings = calculate_rankings(nodes_data_subset, comparison_type)
    else:
        # For smaller node sets or non-performance comparisons, calculate all rankings
        rankings = calculate_rankings(nodes_data, comparison_type)
    
    # Add ranking calculation timing
    # OPTIMIZATION: Skip redundant ranking calculation
    # Calculate rankings based on either subset or full data as decided earlier
    ranking_start_time = time_module.time()
    # No need to recalculate rankings here, already done in the optimization branch above
    ranking_duration = time_module.time() - ranking_start_time
    
    log.info(f"[Comparison Debug] Ranking calculation took {ranking_duration:.2f} seconds")
    
    result = {"nodes": nodes_data, "rankings": rankings}
    
    total_duration = time_module.time() - start_time
    log.info(f"[Comparison Debug] Total comparison calculation for {len(node_names)} nodes took {total_duration:.2f} seconds")
    
    # Determine if results are complete enough to cache.
    # If earnings are missing (None) for any node in an earnings-related comparison,
    # skip caching to avoid pinning zeros/unknown values after startup.
    earnings_incomplete = False
    if comparison_type in ["earnings", "overall"]:
        try:
            earnings_incomplete = any(n.get("metrics", {}).get("total_earnings") is None for n in nodes_data)
        except Exception:
            earnings_incomplete = False
    
    if earnings_incomplete:
        log.info("[Comparison] Skipping cache for this result because earnings are not ready for all nodes")
    else:
        # Cache the result
        app["comparison_cache"][cache_key] = (result, time_module.time())
        
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

    # Add version parameter to JS file imports (cache-busting)
    content = content.replace("/static/js/app.js", f"/static/js/app.js?v={STATIC_VERSION}")
    content = content.replace("/static/js/charts.js", f"/static/js/charts.js?v={STATIC_VERSION}")
    content = content.replace("/static/js/comparison.js", f"/static/js/comparison.js?v={STATIC_VERSION}")
    content = content.replace("/static/js/AlertsPanel.js", f"/static/js/AlertsPanel.js?v={STATIC_VERSION}")

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
                        # DEBUG: trace inbound request
                        try:
                            log.info(f"[EARNINGS][REQ] period_param={period_param} view={view} nodes_to_query={nodes_to_query}")
                        except Exception:
                            pass

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

                            payload = {
                                "type": "earnings_data",
                                "period_name": "12months",
                                "data": formatted_data,
                                "view": view  # include view so clients can ignore mismatched payloads
                            }
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
                            # CRITICAL: Sort node list to ensure consistent cache keys across requests
                            cache_key = tuple(sorted(nodes_to_query)) + (period,)

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

                        # SAFETY FALLBACK:
                        # In rare cases an IN (...) query can yield no rows due to planner quirks or parameterization,
                        # while per-node queries do return results. If we see zero rows for a multi-node selection,
                        # query each node individually and merge the results.
                        if (not earnings_data) and len(nodes_to_query) > 1:
                            merged = []
                            for node in nodes_to_query:
                                try:
                                    part = await loop.run_in_executor(
                                        app["db_executor"],
                                        blocking_get_latest_earnings,
                                        DATABASE_FILE,
                                        [node],
                                        period,
                                    )
                                    if part:
                                        merged.extend(part)
                                except Exception as e:
                                    log.debug(f"[EARNINGS][FALLBACK] per-node query failed for {node}: {e}")
                            if merged:
                                earnings_data = merged
                                log.info(f"[EARNINGS][FALLBACK] Combined per-node queries returned {len(merged)} records")

                        # DEBUG: trace DB result set
                        try:
                            total_records = len(earnings_data) if earnings_data else 0
                            per_node_counts = {}
                            for n in nodes_to_query:
                                per_node_counts[n] = sum(1 for r in (earnings_data or []) if r.get("node_name") == n)
                            log.info(f"[EARNINGS][DB] period={period} records={total_records} per_node={per_node_counts}")
                            if total_records == 0:
                                log.warning(f"[EARNINGS][DB] No earnings returned for nodes={nodes_to_query} period={period}")
                        except Exception:
                            pass

                        # Format data with forecasts
                        # If DB returned nothing for a multi-node request, synthesize from cached per-node payloads
                        use_cached_merge = False
                        formatted_data = []

                        if (not earnings_data) and len(nodes_to_query) > 1:
                            cache = app_state.get("earnings_cache", {})
                            cached_merge = []
                            for n in nodes_to_query:
                                ck = (n, period)
                                cp = cache.get(ck)
                                if cp and isinstance(cp.get("data"), list) and cp["data"]:
                                    cached_merge.extend(cp["data"])
                            if cached_merge:
                                formatted_data = cached_merge
                                use_cached_merge = True
                                log.info(f"[EARNINGS][CACHE] Merged cached per-node data for view {view}: {len(formatted_data)} items")

                        if not use_cached_merge:
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

                        # Include pending flag so frontend can show a loading state during startup/warm-up
                        payload = {
                            "type": "earnings_data",
                            "period": period,
                            "period_name": period_param,
                            "data": formatted_data,
                            "view": view,  # include view so frontend can filter out stale updates
                            "pending": True if (not formatted_data and period_param == "current") else False
                        }

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

                        # DEBUG: trace payload summary
                        try:
                            log.info(f"[EARNINGS][SEND] view={view} period_name={period_param} items={len(formatted_data)} cache_key={cache_key}")
                        except Exception:
                            pass

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
    # Record server start time for cache warm-up logic
    app["start_time"] = time.time()

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
