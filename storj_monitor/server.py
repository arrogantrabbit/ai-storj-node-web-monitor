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
from .websocket_utils import robust_broadcast

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

    node_names = list(app['nodes'].keys())
    await ws.send_json({"type": "init", "nodes": node_names})
    await send_initial_stats(app, ws, ["Aggregate"])
    await ws.send_json(get_active_compactions_payload())
    
    # Send current connection states for network nodes
    if 'connection_states' in app_state:
        for node_name, state_data in app_state['connection_states'].items():
            await ws.send_json({
                'type': 'connection_status',
                'node_name': node_name,
                'state': state_data['state'],
                'host': state_data['host'],
                'port': state_data['port'],
                'error': state_data.get('error')
            })

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
