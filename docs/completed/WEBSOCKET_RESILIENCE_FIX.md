# WebSocket Resilience Fix

## Problem

The server was logging errors when clients disconnected while the server was trying to send data:

```
aiohttp.client_exceptions.ClientConnectionResetError: Cannot write to closing transport
```

This occurred at various points in the code (lines 225, 292, 557 in server.py) when attempting to send WebSocket messages to clients whose connections were closing or already closed.

## Root Cause

The original code was calling `ws.send_json()` directly without checking if the connection was still valid or handling the normal case of clients disconnecting.

## Solution

### 1. Created Safe Send Wrapper Function

Added `safe_send_json()` function in both:
- `storj_monitor/server.py`
- `storj_monitor/websocket_utils.py`

This function:
- Checks if the WebSocket is closed before attempting to send
- Catches connection-related exceptions:
  - `ConnectionResetError`
  - `aiohttp.client_exceptions.ClientConnectionResetError`
  - `RuntimeError`
  - `asyncio.CancelledError`
- Logs these as debug messages (not errors) since they're normal occurrences
- Returns a boolean indicating success/failure

### 2. Updated All WebSocket Send Operations

Replaced all `ws.send_json()` calls with `safe_send_json(ws, ...)` throughout:
- `storj_monitor/server.py`: ~40+ occurrences
- Initial connection messages
- View changes
- All data request responses
- Error messages

### 3. Enhanced Broadcast Function

Updated `robust_broadcast()` in `websocket_utils.py`:
- Now uses `safe_send_json()` for each client
- Tracks successful sends
- Logs statistics when some clients don't receive messages
- Handles concurrent sends gracefully

## Benefits

1. **Cleaner Logs**: No more error messages for normal disconnections
2. **More Resilient**: Server continues operating even when clients disconnect mid-send
3. **Better Debugging**: Debug-level logging still available if needed
4. **Graceful Degradation**: Failed sends don't affect other clients or server operation
5. **No Performance Impact**: Uses the same async patterns, just with better error handling

## Testing

The fix handles these scenarios:
- Client disconnects while server is preparing data
- Client disconnects during data transmission
- Client connection closes between messages
- Multiple simultaneous client disconnections
- Network interruptions during broadcast

## Files Modified

- `storj_monitor/server.py`: Added `safe_send_json()` and replaced all send operations
- `storj_monitor/websocket_utils.py`: Added `safe_send_json()` and updated `robust_broadcast()`