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
from .config import SERVER_HOST, SERVER_PORT, PERFORMANCE_INTERVAL_SECONDS

log = logging.getLogger("StorjMonitor.Server")

# Version string for cache busting (update when static files change)
STATIC_VERSION = str(int(time.time()))


def get_active_compactions_payload() -> Dict[str, Any]:
    """Gathers currently active compactions from all nodes and creates a payload."""
    active_list = []
    for node_name, node_state in app_state['nodes'].items():
        for key, start_time in node_state.get('active_compactions', {}).items():
            try:
                satellite, store = key.split(':', 1)
                active_list.append({
                    "node_name": node_name,
                    "satellite": satellite,
                    "store": store,
                    "start_iso": start_time.isoformat()
                })
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
    if view_tuple in app_state['stats_cache']:
        try:
            await ws.send_json(app_state['stats_cache'][view_tuple])
            return
        except (ConnectionResetError, asyncio.CancelledError):
            return  # Client disconnected

    # If not in cache, compute it now for this one client
    log.info(f"Cache miss for view {view}. Computing stats on-demand.")

    # Create temporary incremental stats for this view
    stats = IncrementalStats()
    nodes_to_process = view if view != ['Aggregate'] else list(app_state['nodes'].keys())

    # Process all current events
    for node_name in nodes_to_process:
        if node_name in app_state['nodes']:
            for event in list(app_state['nodes'][node_name]['live_events']):
                stats.add_event(event, app_state['TOKEN_REGEX'])

    # Update live stats
    all_events = []
    for node_name in nodes_to_process:
        if node_name in app_state['nodes']:
            all_events.extend(list(app_state['nodes'][node_name]['live_events']))
    stats.update_live_stats(all_events)

    # Get historical stats
    historical_stats = database.get_historical_stats(view, app_state['nodes'])

    # Generate and send payload
    try:
        payload = stats.to_payload(historical_stats)
        app_state['stats_cache'][view_tuple] = payload
        await ws.send_json(payload)
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # Client disconnected during computation
    except Exception:
        log.error(f"Error computing initial stats for view {view}:", exc_info=True)


@web.middleware
async def cache_control_middleware(request, handler):
    """Add cache control headers to static files"""
    response = await handler(request)
    
    # Add no-cache headers for static files to prevent caching issues during development
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
    return response


async def handle_index(request):
    """Serve index.html with version parameter for cache busting"""
    index_path = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
    
    # Read the file and inject version parameter
    with open(index_path, 'r') as f:
        content = f.read()
    
    # Add version parameter to JS file imports
    content = content.replace('/static/js/app.js', f'/static/js/app.js?v={STATIC_VERSION}')
    
    response = web.Response(text=content, content_type='text/html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=10)
    await ws.prepare(request)
    app = request.app
    app_state['websockets'][ws] = {"view": ["Aggregate"]}

    log.info(f"WebSocket client connected. Total clients: {len(app_state['websockets'])}")

    try:
        node_names = list(app['nodes'].keys())
        await ws.send_json({"type": "init", "nodes": node_names})
        await send_initial_stats(app, ws, ["Aggregate"])
        await ws.send_json(get_active_compactions_payload())
        
        # Send initial earnings data from cache if available
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        period = now.strftime('%Y-%m')
        cache_key = ('Aggregate', period)
        
        if cache_key in app_state.get('earnings_cache', {}):
            log.info(f"Sending cached earnings data for Aggregate view on connect")
            await ws.send_json(app_state['earnings_cache'][cache_key])
        else:
            log.info(f"No cached earnings data available on connect, client will receive broadcast when ready")
    except (ConnectionResetError, aiohttp.client_exceptions.ClientConnectionResetError):
        # Client disconnected during initial setup
        log.debug("Client disconnected during initial setup")
        if ws in app_state['websockets']:
            del app_state['websockets'][ws]
        return ws

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

                    if msg_type == 'set_view':
                        new_view = data.get('view')
                        if isinstance(new_view, list) and new_view:
                            valid_nodes = set(app['nodes'].keys())
                            is_aggregate = new_view == ['Aggregate']
                            are_nodes_valid = all(node in valid_nodes for node in new_view)

                            if is_aggregate or are_nodes_valid:
                                app_state['websockets'][ws]['view'] = new_view
                                log.info(f"Client switched view to: {new_view}")
                                await send_initial_stats(app, ws, new_view)
                                
                                # Send cached earnings data for the new view if available
                                import datetime
                                now = datetime.datetime.now(datetime.timezone.utc)
                                period = now.strftime('%Y-%m')
                                
                                # Determine cache key based on view
                                if is_aggregate:
                                    cache_key = ('Aggregate', period)
                                else:
                                    # For single or multiple specific nodes
                                    if len(new_view) == 1:
                                        cache_key = (new_view[0], period)
                                    else:
                                        cache_key = tuple(sorted(new_view)) + (period,)
                                
                                if cache_key in app_state.get('earnings_cache', {}):
                                    log.info(f"Sending cached earnings data for view {new_view} on view switch")
                                    await ws.send_json(app_state['earnings_cache'][cache_key])

                    elif msg_type == 'get_historical_performance':
                        view = data.get('view')  # This is now a list
                        points = data.get('points', 150)
                        interval = data.get('interval_sec', PERFORMANCE_INTERVAL_SECONDS)
                        loop = asyncio.get_running_loop()

                        events_to_process = []
                        nodes_to_query = view if view != ['Aggregate'] else list(app_state['nodes'].keys())
                        for node_name in nodes_to_query:
                            if node_name in app_state['nodes']:
                                events_to_process.extend(list(app_state['nodes'][node_name]['live_events']))

                        historical_data = await loop.run_in_executor(
                            app['db_executor'], database.blocking_get_historical_performance,
                            events_to_process, points, interval
                        )
                        payload = {"type": "historical_performance_data", "view": view,
                                   "performance_data": historical_data}
                        await ws.send_json(payload)

                    elif msg_type == 'get_aggregated_performance':
                        view = data.get('view')  # This is a list
                        time_window_hours = data.get('hours', 1)
                        loop = asyncio.get_running_loop()

                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())

                        aggregated_data = await loop.run_in_executor(
                            app['db_executor'], database.blocking_get_aggregated_performance,
                            nodes_to_query, time_window_hours
                        )

                        payload = {"type": "aggregated_performance_data", "view": view,
                                   "performance_data": aggregated_data}
                        await ws.send_json(payload)

                    elif msg_type == 'get_hashstore_stats':
                        filters = data.get('filters', {})
                        loop = asyncio.get_running_loop()
                        hashstore_data = await loop.run_in_executor(
                            app['db_executor'], database.blocking_get_hashstore_stats, filters
                        )
                        payload = {"type": "hashstore_stats_data", "data": hashstore_data}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_reputation_data':
                        # Phase 1.3: Get current reputation data
                        view = data.get('view', ['Aggregate'])
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        reputation_data = await loop.run_in_executor(
                            app['db_executor'],
                            database.blocking_get_latest_reputation,
                            DATABASE_FILE,
                            nodes_to_query
                        )
                        payload = {"type": "reputation_data", "data": reputation_data}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_latency_stats':
                        # Phase 2.1: Get latency statistics
                        view = data.get('view', ['Aggregate'])
                        hours = data.get('hours', 1)
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .performance_analyzer import blocking_get_latency_stats
                        
                        latency_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_latency_stats,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours
                        )
                        payload = {"type": "latency_stats", "data": latency_data}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_latency_histogram':
                        # Phase 2.1: Get latency histogram
                        view = data.get('view', ['Aggregate'])
                        hours = data.get('hours', 1)
                        bucket_size = data.get('bucket_size_ms', 100)
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .performance_analyzer import blocking_get_latency_histogram
                        
                        histogram_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_latency_histogram,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours,
                            bucket_size
                        )
                        payload = {"type": "latency_histogram", "data": histogram_data}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_storage_data':
                        # Phase 2.2: Get current storage data with configurable growth rate window
                        view = data.get('view', ['Aggregate'])
                        days = data.get('days', 7)  # Default to 7 days if not specified
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        log.info(f"Storage data request received for view: {view}, nodes: {nodes_to_query}, days window: {days}")
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_latest_storage_with_forecast
                        
                        storage_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_latest_storage_with_forecast,
                            DATABASE_FILE,
                            nodes_to_query,
                            days
                        )
                        
                        log.info(f"Storage data query returned {len(storage_data) if storage_data else 0} result(s)")
                        if storage_data:
                            for item in storage_data:
                                log.info(f"  Sending to client: Node={item.get('node_name')}, Available={item.get('available_bytes', 0) / (1024**4):.2f} TB")
                        
                        payload = {"type": "storage_data", "data": storage_data}
                        await ws.send_json(payload)
                        log.info("Storage data payload sent to client")
                    
                    elif msg_type == 'get_storage_history':
                        # Phase 2.2: Get storage history
                        node_name = data.get('node_name')
                        days = data.get('days', 7)
                        
                        if not node_name:
                            await ws.send_json({"type": "error", "message": "node_name required"})
                            continue
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_storage_history
                        
                        history_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_storage_history,
                            DATABASE_FILE,
                            node_name,
                            days
                        )
                        payload = {"type": "storage_history", "data": history_data}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_active_alerts':
                        # Phase 4: Get active alerts
                        view = data.get('view', ['Aggregate'])
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        if 'alert_manager' in app:
                            alerts = await app['alert_manager'].get_active_alerts(nodes_to_query)
                            payload = {"type": "active_alerts", "data": alerts}
                            await ws.send_json(payload)
                        else:
                            await ws.send_json({"type": "error", "message": "Alert manager not initialized"})
                    
                    elif msg_type == 'acknowledge_alert':
                        # Phase 4: Acknowledge an alert
                        alert_id = data.get('alert_id')
                        
                        if not alert_id:
                            await ws.send_json({"type": "error", "message": "alert_id required"})
                            continue
                        
                        if 'alert_manager' in app:
                            success = await app['alert_manager'].acknowledge_alert(alert_id)
                            payload = {"type": "alert_acknowledge_result", "success": success, "alert_id": alert_id}
                            await ws.send_json(payload)
                        else:
                            await ws.send_json({"type": "error", "message": "Alert manager not initialized"})
                    
                    elif msg_type == 'get_insights':
                        # Phase 4: Get recent insights
                        view = data.get('view', ['Aggregate'])
                        hours = data.get('hours', 24)
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_insights
                        
                        insights = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_insights,
                            DATABASE_FILE,
                            nodes_to_query,
                            hours
                        )
                        payload = {"type": "insights_data", "data": insights}
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_alert_summary':
                        # Phase 4: Get alert summary
                        if 'alert_manager' in app:
                            summary = app['alert_manager'].get_alert_summary()
                            payload = {"type": "alert_summary", "data": summary}
                            await ws.send_json(payload)
                        else:
                            await ws.send_json({"type": "alert_summary", "data": {"critical": 0, "warning": 0, "info": 0, "total": 0}})
                    
                    elif msg_type == 'get_earnings_data':
                        # Phase 5.3: Get earnings data for specified period
                        view = data.get('view', ['Aggregate'])
                        period_param = data.get('period', 'current')
                        nodes_to_query = view if view != ['Aggregate'] else list(app['nodes'].keys())
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .financial_tracker import SATELLITE_NAMES
                        from .database import blocking_get_latest_earnings
                        
                        # Calculate period based on parameter
                        import datetime
                        now = datetime.datetime.now(datetime.timezone.utc)
                        
                        if period_param == 'previous':
                            # Previous month
                            if now.month == 1:
                                period = f"{now.year - 1}-12"
                            else:
                                period = f"{now.year}-{str(now.month - 1).zfill(2)}"
                        elif period_param == '12months':
                            # Not implemented yet - would need aggregation across 12 months
                            # For now, return empty
                            await ws.send_json({"type": "earnings_data", "data": []})
                            continue
                        else:
                            # 'current' or any other value defaults to current month
                            period = now.strftime('%Y-%m')
                        
                        # Create cache key including period
                        # Normalize cache key: use 'Aggregate' for aggregate views
                        if view == ['Aggregate']:
                            cache_key = ('Aggregate', period)
                        else:
                            cache_key = tuple(nodes_to_query) + (period,)

                        # Try to serve from cache first
                        if cache_key in app_state.get('earnings_cache', {}):
                            await ws.send_json(app_state['earnings_cache'][cache_key])
                            continue
                        
                        earnings_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_latest_earnings,
                            DATABASE_FILE,
                            nodes_to_query,
                            period
                        )
                        
                        # Format data with forecasts
                        formatted_data = []
                        for estimate in earnings_data:
                            # Calculate forecast if tracker available
                            tracker = app.get('financial_trackers', {}).get(estimate['node_name'])
                            forecast_info = None
                            if tracker:
                                try:
                                    forecast_info = await tracker.forecast_payout(DATABASE_FILE, period, loop, app.get('db_executor'))
                                except Exception as e:
                                    log.error(f"Failed to get forecast for {estimate['node_name']}: {e}")
                            
                            sat_name = SATELLITE_NAMES.get(estimate['satellite'], estimate['satellite'][:8])
                            
                            formatted_data.append({
                                'node_name': estimate['node_name'],
                                'satellite': sat_name,
                                'total_net': round(estimate['total_earnings_net'], 2),
                                'total_gross': round(estimate['total_earnings_gross'], 2),
                                'held_amount': round(estimate['held_amount'], 2),
                                'breakdown': {
                                    'egress': round(estimate['egress_earnings_net'], 2),
                                    'storage': round(estimate['storage_earnings_net'], 2),
                                    'repair': round(estimate['repair_earnings_net'], 2),
                                    'audit': round(estimate['audit_earnings_net'], 2)
                                },
                                'forecast_month_end': round(forecast_info['forecasted_payout'], 2) if forecast_info else None,
                                'confidence': round(forecast_info['confidence'], 2) if forecast_info else None
                            })
                        
                        payload = {"type": "earnings_data", "data": formatted_data}
                        
                        # Debug log to show what breakdown values are being sent
                        log.info(f"Sending earnings for period {period} with {len(formatted_data)} satellites")
                        for item in formatted_data:
                            breakdown = item.get('breakdown', {})
                            total_breakdown = sum(breakdown.values())
                            log.debug(
                                f"[{item['node_name']}] {item['satellite']}: "
                                f"total_net=${item['total_net']:.2f}, "
                                f"breakdown sum=${total_breakdown:.2f}"
                            )
                        
                        # Store in cache with period included in key
                        if 'earnings_cache' not in app_state:
                            app_state['earnings_cache'] = {}
                        app_state['earnings_cache'][cache_key] = payload
                        
                        await ws.send_json(payload)
                    
                    elif msg_type == 'get_earnings_history':
                        # Phase 5.3: Get earnings history
                        node_name = data.get('node_name')
                        satellite = data.get('satellite')
                        days = data.get('days', 30)
                        
                        log.info(f"Earnings history requested: node={node_name}, satellite={satellite}, days={days}")
                        
                        if not node_name:
                            await ws.send_json({"type": "error", "message": "node_name required"})
                            continue
                        
                        loop = asyncio.get_running_loop()
                        from .config import DATABASE_FILE
                        from .database import blocking_get_earnings_estimates
                        
                        history_data = await loop.run_in_executor(
                            app['db_executor'],
                            blocking_get_earnings_estimates,
                            DATABASE_FILE,
                            [node_name],
                            satellite,
                            None,  # period
                            days
                        )
                        
                        log.info(f"Earnings history query returned {len(history_data) if history_data else 0} records")
                        if history_data and len(history_data) > 0:
                            log.info(f"Sample record: period={history_data[0].get('period')}, total_net={history_data[0].get('total_earnings_net')}")
                        
                        payload = {"type": "earnings_history", "data": history_data}
                        await ws.send_json(payload)

                except Exception:
                    log.error("Could not parse websocket message:", exc_info=True)

    finally:
        if ws in app_state['websockets']:
            del app_state['websockets'][ws]
        log.info(f"WebSocket client disconnected. Total clients: {len(app_state['websockets'])}")
    return ws


def run_server(nodes_config: Dict[str, Any]):
    app = web.Application(middlewares=[cache_control_middleware])
    app['nodes'] = nodes_config

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', websocket_handler)

    static_path = os.path.join(os.path.dirname(__file__), 'static')
    app.router.add_static('/static/', path=static_path, name='static')

    log.info(f"Server starting on http://{SERVER_HOST}:{SERVER_PORT}")
    log.info(f"Static files version: {STATIC_VERSION}")
    log.info(f"Monitoring nodes: {list(app['nodes'].keys())}")
    web.run_app(app, host=SERVER_HOST, port=SERVER_PORT)
