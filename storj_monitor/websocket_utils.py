import asyncio
import logging
from typing import Optional

from .state import app_state

log = logging.getLogger("StorjMonitor.WebsocketUtils")


async def robust_broadcast(websockets_dict, payload, node_name: Optional[str] = None):
    """
    Sends a JSON payload to all relevant WebSocket clients.
    Filters recipients if a specific node_name is provided.
    """
    tasks = []
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

    for ws in recipients:
        try:
            # Create a task for each send operation to run them concurrently
            task = asyncio.create_task(ws.send_json(payload))
            tasks.append(task)
        except (ConnectionResetError, asyncio.CancelledError):
            # These exceptions are expected if a client disconnects abruptly.
            # No need to log an error.
            pass
        except Exception:
            log.error("An unexpected error occurred during websocket broadcast preparation:", exc_info=True)

    if tasks:
        # Wait for all send operations to complete.
        # return_exceptions=True prevents one failed send from stopping others.
        await asyncio.gather(*tasks, return_exceptions=True)
