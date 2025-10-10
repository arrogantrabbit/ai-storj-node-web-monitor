import asyncio
import logging
from typing import Optional

import aiohttp


log = logging.getLogger("StorjMonitor.WebsocketUtils")


async def safe_send_json(ws, payload):
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
    except (ConnectionResetError,
            aiohttp.client_exceptions.ClientConnectionResetError,
            RuntimeError,
            asyncio.CancelledError) as e:
        # Client disconnected or connection is closing - this is normal
        log.debug(f"Could not send broadcast to client (connection closing): {type(e).__name__}")
        return False
    except Exception as e:
        # Unexpected error - log it
        log.warning(f"Unexpected error sending WebSocket broadcast: {e}", exc_info=True)
        return False


async def robust_broadcast(websockets_dict, payload, node_name: Optional[str] = None):
    """
    Sends a JSON payload to all relevant WebSocket clients.
    Filters recipients if a specific node_name is provided.
    Handles connection errors gracefully without logging errors for normal disconnections.
    """
    # If this is a node-specific message, filter the recipients to those
    # viewing the specific node or the aggregate view.
    if node_name:
        recipients = {ws for ws, state in websockets_dict.items()
                      if state.get("view") and (state.get("view") == ["Aggregate"] or node_name in state.get("view"))
                      }
    else:  # Broadcast to all connected clients
        recipients = set(websockets_dict.keys())

    if not recipients:
        return

    # Create tasks for all send operations to run them concurrently
    tasks = [safe_send_json(ws, payload) for ws in recipients]

    if tasks:
        # Wait for all send operations to complete.
        # return_exceptions=True prevents one failed send from stopping others.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count successful sends for debugging
        successful = sum(1 for r in results if r is True)
        if successful < len(results):
            log.debug(f"Broadcast: {successful}/{len(results)} clients received message")
