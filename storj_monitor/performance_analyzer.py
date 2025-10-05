"""
Performance Analyzer

Analyzes operation latency data to identify performance bottlenecks
and slow operations.
"""

import logging
from typing import List, Dict, Any
import statistics


log = logging.getLogger("StorjMonitor.PerformanceAnalyzer")


def calculate_percentiles(values: List[float], percentiles: List[int] = [50, 95, 99]) -> Dict[str, float]:
    """
    Calculate percentiles from a list of values.
    
    Args:
        values: List of numeric values
        percentiles: List of percentile values to calculate (e.g., [50, 95, 99])
    
    Returns:
        Dict mapping percentile names (e.g., 'p50') to values
    """
    if not values:
        return {f'p{p}': 0.0 for p in percentiles}
    
    sorted_values = sorted(values)
    result = {}
    
    for p in percentiles:
        # Use the nearest rank method
        rank = (p / 100.0) * len(sorted_values)
        if rank < 1:
            result[f'p{p}'] = sorted_values[0]
        elif rank >= len(sorted_values):
            result[f'p{p}'] = sorted_values[-1]
        else:
            # Linear interpolation between two nearest values
            lower_idx = int(rank) - 1
            upper_idx = min(lower_idx + 1, len(sorted_values) - 1)
            fraction = rank - int(rank)
            result[f'p{p}'] = sorted_values[lower_idx] + fraction * (sorted_values[upper_idx] - sorted_values[lower_idx])
    
    return result


def analyze_latency_data(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze latency data from events and return statistics.
    
    Args:
        events: List of event dicts with 'duration_ms', 'category', 'status' fields
    
    Returns:
        Dict with latency statistics by category
    """
    # Group by category
    latencies_by_category = {
        'get': [],
        'put': [],
        'audit': [],
        'all': []
    }
    
    for event in events:
        duration = event.get('duration_ms')
        if duration is None or duration <= 0:
            continue
        
        category = event.get('category', 'other')
        status = event.get('status')
        
        # Only include successful operations for latency metrics
        if status == 'success':
            if category in latencies_by_category:
                latencies_by_category[category].append(duration)
            latencies_by_category['all'].append(duration)
    
    # Calculate percentiles for each category
    results = {}
    for category, durations in latencies_by_category.items():
        if durations:
            percentiles = calculate_percentiles(durations, [50, 95, 99])
            results[category] = {
                'count': len(durations),
                'mean': round(statistics.mean(durations), 2),
                'median': round(statistics.median(durations), 2),
                **{k: round(v, 2) for k, v in percentiles.items()},
                'min': min(durations),
                'max': max(durations)
            }
        else:
            results[category] = {
                'count': 0,
                'mean': 0,
                'median': 0,
                'p50': 0,
                'p95': 0,
                'p99': 0,
                'min': 0,
                'max': 0
            }
    
    return results


def detect_slow_operations(
    events: List[Dict[str, Any]],
    threshold_ms: int = 5000,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Detect slow operations that exceed a threshold.
    
    Args:
        events: List of event dicts
        threshold_ms: Threshold in milliseconds for slow operations
        limit: Maximum number of slow operations to return
    
    Returns:
        List of slow operations sorted by duration (slowest first)
    """
    slow_ops = []
    
    for event in events:
        duration = event.get('duration_ms')
        if duration and duration >= threshold_ms:
            slow_ops.append({
                'timestamp': event.get('timestamp'),
                'action': event.get('action'),
                'duration_ms': duration,
                'piece_id': event.get('piece_id', ''),
                'satellite_id': event.get('satellite_id', ''),
                'status': event.get('status'),
                'size': event.get('size'),
                'node_name': event.get('node_name')
            })
    
    # Sort by duration (slowest first) and limit
    slow_ops.sort(key=lambda x: x['duration_ms'], reverse=True)
    return slow_ops[:limit]


def blocking_get_latency_stats(
    db_path: str,
    node_names: List[str],
    hours: int = 1
) -> Dict[str, Any]:
    """
    Get latency statistics from database for specified nodes and time window.
    
    Args:
        db_path: Path to database
        node_names: List of node names to query
        hours: Number of hours of history
    
    Returns:
        Dict with latency statistics and slow operations
    """
    import sqlite3
    import datetime
    
    if not node_names:
        return {'statistics': {}, 'slow_operations': []}
    
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        
        with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
            conn.row_factory = sqlite3.Row
            
            # Query events with duration data
            placeholders = ','.join('?' for _ in node_names)
            query = f"""
                SELECT timestamp, action, status, size, piece_id, satellite_id,
                       duration_ms, node_name
                FROM events
                WHERE node_name IN ({placeholders})
                  AND timestamp >= ?
                  AND duration_ms IS NOT NULL
                  AND duration_ms > 0
                ORDER BY timestamp DESC
                LIMIT 10000
            """
            
            rows = conn.execute(query, (*node_names, cutoff_iso)).fetchall()
            events = [dict(row) for row in rows]
            
            # Categorize events
            for event in events:
                action = event['action']
                if action == 'GET_AUDIT':
                    event['category'] = 'audit'
                elif 'GET' in action:
                    event['category'] = 'get'
                elif 'PUT' in action:
                    event['category'] = 'put'
                else:
                    event['category'] = 'other'
        
        # Analyze latency
        statistics_data = analyze_latency_data(events)
        
        # Detect slow operations
        slow_operations = detect_slow_operations(events, threshold_ms=5000, limit=10)
        
        return {
            'statistics': statistics_data,
            'slow_operations': slow_operations,
            'total_operations': len(events),
            'operations_with_latency': len([e for e in events if e.get('duration_ms')])
        }
        
    except Exception as e:
        log.error(f"Error getting latency stats: {e}", exc_info=True)
        return {'statistics': {}, 'slow_operations': [], 'error': str(e)}


def blocking_get_latency_histogram(
    db_path: str,
    node_names: List[str],
    hours: int = 1,
    bucket_size_ms: int = 100
) -> List[Dict[str, Any]]:
    """
    Get latency distribution histogram data.
    
    Args:
        db_path: Path to database
        node_names: List of node names
        hours: Hours of history
        bucket_size_ms: Size of histogram buckets in milliseconds
    
    Returns:
        List of histogram buckets with counts
    """
    import sqlite3
    import datetime
    
    if not node_names:
        return []
    
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_iso = cutoff.isoformat()
        
        with sqlite3.connect(db_path, timeout=10, detect_types=0) as conn:
            placeholders = ','.join('?' for _ in node_names)
            
            # Query to create histogram buckets
            query = f"""
                SELECT 
                    (duration_ms / ?) * ? as bucket_start,
                    COUNT(*) as count
                FROM events
                WHERE node_name IN ({placeholders})
                  AND timestamp >= ?
                  AND duration_ms IS NOT NULL
                  AND duration_ms > 0
                  AND status = 'success'
                GROUP BY bucket_start
                ORDER BY bucket_start
            """
            
            cursor = conn.execute(query, (bucket_size_ms, bucket_size_ms, *node_names, cutoff_iso))
            
            histogram = []
            for row in cursor:
                bucket_start = row[0]
                count = row[1]
                histogram.append({
                    'bucket_start_ms': bucket_start,
                    'bucket_end_ms': bucket_start + bucket_size_ms,
                    'count': count,
                    'label': f'{bucket_start}-{bucket_start + bucket_size_ms}ms'
                })
            
            return histogram
            
    except Exception as e:
        log.error(f"Error getting latency histogram: {e}", exc_info=True)
        return []