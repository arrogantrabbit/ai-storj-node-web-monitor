# Network Resilience Implementation

This document describes the network resilience features implemented in the Storj Node Monitor to handle network interruptions gracefully.

## Overview

The application now features comprehensive network resilience with automatic reconnection, health monitoring, and graceful degradation when services become unavailable. Both the log forwarder connections and API client connections are protected against network interruptions.

## Key Features

### 1. **Automatic Reconnection**
- Both log forwarder and API connections automatically reconnect when interrupted
- Exponential backoff strategy prevents overwhelming disconnected services
- Configurable retry attempts and intervals

### 2. **Connection State Tracking**
- Real-time monitoring of connection states for all nodes
- States: `disconnected`, `connecting`, `connected`, `error`, `stopped`
- Broadcast to WebSocket clients every 10 seconds

### 3. **Health Monitoring**
- API clients perform periodic health checks (every 30 seconds)
- Automatic detection of connection degradation
- Proactive reconnection before complete failure

### 4. **Graceful Degradation**
- Application continues functioning when connections are lost
- Features requiring unavailable services are disabled automatically
- No crashes or data loss during network interruptions

### 5. **Transparent Recovery**
- Seamless reconnection without user intervention
- Automatic resumption of log streaming
- API queries resume when connection is restored

## Architecture

### Connection State Management

Connection states are tracked in `app_state['connection_states']` with the following structure:

```python
{
    'node_name': {
        'log_reader': {
            'state': 'connected',  # disconnected, connecting, connected, error, stopped
            'host': 'localhost',
            'port': 9001,
            'reconnect_attempts': 0,
            'last_error': None
        },
        'api_client': {
            'state': 'connected',
            'is_available': True,
            'last_error': None,
            'reconnect_attempts': 0,
            'last_successful_request': 1696800000.0
        }
    }
}
```

### Component Details

#### 1. **API Client (`storj_api_client.py`)**

**New Attributes:**
- `_health_check_task`: Background task for health monitoring
- `_reconnect_attempts`: Counter for reconnection attempts
- `_max_reconnect_attempts`: Maximum attempts before giving up (default: 10)
- `_health_check_interval`: Time between health checks (default: 30s)
- `_last_successful_request`: Timestamp of last successful API call
- `_connection_state`: Current connection state

**Key Methods:**
- `_connect()`: Establishes connection with retry logic
- `_health_check_loop()`: Background task for health monitoring
- `get_connection_state()`: Returns current connection state for monitoring

**Behavior:**
1. Initial connection attempt on startup
2. Periodic health checks every 30 seconds
3. Automatic reconnection on failure with exponential backoff
4. Maximum backoff of 5 minutes
5. Health check task runs for the lifetime of the client

**Reconnection Strategy:**
- Attempt 1: 2 seconds delay
- Attempt 2: 4 seconds delay
- Attempt 3: 8 seconds delay
- Attempt N: min(2^N, 300) seconds delay

#### 2. **Network Log Reader (`log_processor.py`)**

**Enhanced Features:**
- Connection state tracking and broadcasting
- Unlimited retry attempts (configurable)
- Exponential backoff (2s → 4s → 8s → ... → 60s max)
- Detailed error logging with attempt counts

**State Updates:**
The network log reader updates connection state in real-time:
- `connecting`: Attempting to establish connection
- `connected`: Successfully connected and receiving logs
- `disconnected`: Connection closed by remote
- `error`: Connection error occurred
- `stopped`: Task cancelled/stopped

**Behavior:**
1. Attempts connection on startup
2. On connection loss, waits with exponential backoff
3. Continues retrying indefinitely (unless cancelled)
4. Updates connection state for monitoring
5. Handles various error types gracefully

#### 3. **Connection Status Broadcaster (`tasks.py`)**

**Purpose:**
Broadcasts connection states to all WebSocket clients for real-time monitoring.

**Configuration:**
- Broadcast interval: 10 seconds
- Payload type: `connection_status`

**Payload Structure:**
```json
{
    "type": "connection_status",
    "states": {
        "node1": {
            "log_reader": {
                "state": "connected",
                "host": "192.168.1.100",
                "port": 9001,
                "reconnect_attempts": 0,
                "last_error": null
            },
            "api_client": {
                "state": "connected",
                "is_available": true,
                "last_error": null,
                "reconnect_attempts": 0,
                "last_successful_request": 1696800000.0
            }
        }
    }
}
```

## Usage

### Monitoring Connection States

WebSocket clients receive connection status updates every 10 seconds. Handle the `connection_status` message type to display connection health in the UI:

```javascript
websocket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'connection_status') {
        // Update UI with connection states
        Object.entries(data.states).forEach(([nodeName, states]) => {
            updateNodeConnectionStatus(nodeName, states);
        });
    }
};
```

### API Client Health Checks

API clients automatically perform health checks. No manual intervention required:

```python
# Client automatically maintains connection
client = StorjNodeAPIClient('node1', 'http://localhost:14002')
await client.start()  # Starts health check loop

# Use normally - automatic reconnection on failure
data = await client.get_dashboard()
```

### Network Log Reader

Network log readers automatically handle reconnection:

```python
# Reader automatically reconnects on disconnection
task = asyncio.create_task(
    network_log_reader_task('node1', '192.168.1.100', 9001, queue)
)
# Connection state tracked in app_state['connection_states']
```

## Configuration

### API Client Settings

Located in `storj_api_client.py`:

```python
_max_reconnect_attempts = 10        # Max attempts per cycle (0 = unlimited)
_health_check_interval = 30         # Health check interval in seconds
timeout = NODE_API_TIMEOUT          # Request timeout from config
```

### Network Log Reader Settings

Located in `log_processor.py`:

```python
max_backoff = 60                    # Maximum backoff time in seconds
max_reconnect_attempts = 0          # 0 = unlimited retries
backoff = 2                         # Initial backoff in seconds
```

### Connection Status Broadcaster

Located in `tasks.py`:

```python
broadcast_interval = 10             # Broadcast interval in seconds
```

## Error Handling

### Connection Errors

**Log Forwarder:**
- `ConnectionRefusedError`: Service not running
- `OSError`: Network error (unreachable, etc.)
- `asyncio.TimeoutError`: Connection timeout
- Generic `Exception`: Unexpected errors

**API Client:**
- `asyncio.TimeoutError`: Request timeout
- `aiohttp.ClientError`: HTTP client errors
- HTTP 5xx: Server errors trigger reconnection
- Generic `Exception`: Unexpected errors

### Recovery Strategies

1. **Immediate Retry**: For transient errors
2. **Exponential Backoff**: For persistent connection failures
3. **Health Check Recovery**: For degraded but not completely failed connections
4. **Manual Intervention**: Only needed if max attempts exceeded (API client only)

## Best Practices

### 1. **Monitoring**
- Watch connection states in the UI
- Set up alerts for prolonged disconnections
- Monitor reconnection attempt counts

### 2. **Configuration**
- Adjust backoff intervals based on network conditions
- Set appropriate health check intervals
- Configure max attempts for critical services

### 3. **Deployment**
- Ensure log forwarders start before the monitor
- Use systemd or similar for automatic service restart
- Configure network timeouts appropriately

### 4. **Troubleshooting**
- Check connection state broadcasts for error messages
- Review logs for reconnection attempts
- Verify firewall rules and network connectivity

## Testing Scenarios

### Scenario 1: Log Forwarder Restart

**Test:**
1. Monitor running and connected
2. Restart log forwarder service
3. Observe reconnection

**Expected Behavior:**
- Connection state changes: `connected` → `disconnected` → `connecting` → `connected`
- Logs resume streaming automatically
- No data loss (logs are buffered by forwarder)

### Scenario 2: Network Interruption

**Test:**
1. Monitor and API connected
2. Temporarily disable network (e.g., `sudo ifconfig eth0 down`)
3. Re-enable network
4. Observe recovery

**Expected Behavior:**
- Both log reader and API client detect disconnection
- Exponential backoff begins
- Automatic reconnection when network restored
- Services resume normally

### Scenario 3: API Unavailability

**Test:**
1. Stop Storj node (API becomes unavailable)
2. Monitor continues running
3. Restart Storj node
4. Observe recovery

**Expected Behavior:**
- API client marks service as unavailable
- Monitor continues with log-only data
- Health checks detect API recovery
- Enhanced features automatically re-enabled

### Scenario 4: Prolonged Disconnection

**Test:**
1. Keep service down for extended period
2. Monitor continues running
3. Observe backoff behavior

**Expected Behavior:**
- Backoff reaches maximum (60s for log reader, 300s for API)
- Reconnection attempts continue
- Application remains stable
- UI shows disconnected state

## Metrics and Monitoring

### Key Metrics to Track

1. **Connection Uptime**: Percentage of time services are connected
2. **Reconnection Frequency**: How often reconnections occur
3. **Reconnection Duration**: Time to re-establish connection
4. **Failed Attempts**: Number of failed reconnection attempts
5. **Health Check Success Rate**: Percentage of successful health checks

### Logging

**Connection Events:**
```
INFO: [node1] Connected to remote log source at 192.168.1.100:9001
WARNING: [node1] Connection to 192.168.1.100:9001 closed by remote end
ERROR: [node1] Cannot connect to 192.168.1.100:9001: [Errno 111] Connection refused. Attempt 3. Retrying in 8s
INFO: [node1] API client connected to http://localhost:14002 (Node ID: 12AB...34CD)
WARNING: [node1] API health check failed: Connection timeout
INFO: [node1] API reconnection attempt 2/10 in 4s
```

## Future Enhancements

Potential improvements for network resilience:

1. **Adaptive Backoff**: Adjust backoff based on connection stability
2. **Circuit Breaker Pattern**: Fail fast after repeated failures
3. **Connection Pooling**: Reuse connections when possible
4. **Metrics Dashboard**: Visualize connection health over time
5. **Alerting**: Notify on prolonged disconnections
6. **Graceful Shutdown**: Drain connections before stopping

## Conclusion

The network resilience implementation ensures the Storj Node Monitor remains operational during network interruptions, automatically recovering when services become available again. The system is designed for zero-intervention operation while providing full visibility into connection states for monitoring and troubleshooting.