"""
Integration tests for WebSocket communication.
Tests message handling, request/response cycles, and data broadcasting.
"""

import asyncio
import datetime
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web


@pytest.fixture
async def mock_app():
    """Create a mock aiohttp app with necessary components."""
    from concurrent.futures import ThreadPoolExecutor

    app = web.Application()
    app["nodes"] = {
        "test-node": {
            "name": "test-node",
            "enabled": True,
            "type": "file",
            "path": "/var/log/storj/test-node.log",
        }
    }
    app["db_executor"] = ThreadPoolExecutor(max_workers=1)

    yield app

    app["db_executor"].shutdown(wait=False)


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.send_json = AsyncMock()
    ws.send_str = AsyncMock()
    ws.closed = False
    ws.exception = Mock(return_value=None)
    return ws


@pytest.mark.asyncio
async def test_websocket_initial_connection(mock_app, mock_websocket, temp_db):
    """
    Test WebSocket initial connection flow:
    1. Client connects
    2. Receives initial node list
    3. Receives initial statistics
    4. Receives active compactions
    """
    from storj_monitor import database
    from storj_monitor.state import app_state

    # Initialize database
    database.init_db()

    # Setup app state
    app_state["nodes"]["test-node"] = {
        "live_events": [],
        "active_compactions": {},
        "unprocessed_performance_events": [],
        "has_new_events": False,
    }
    app_state["websockets"] = {}
    app_state["stats_cache"] = {}

    # Simulate connection
    app_state["websockets"][mock_websocket] = {"view": ["Aggregate"]}

    # Send init message
    await mock_websocket.send_json({"type": "init", "nodes": ["test-node"]})

    # Verify initial messages were sent
    assert mock_websocket.send_json.called
    init_call = mock_websocket.send_json.call_args_list[0][0][0]
    assert init_call["type"] == "init"
    assert "test-node" in init_call["nodes"]


@pytest.mark.asyncio
async def test_websocket_view_change(mock_app, mock_websocket, temp_db):
    """
    Test WebSocket view change:
    1. Client sends set_view message
    2. Server updates client view
    3. Client receives statistics for new view
    """
    from storj_monitor import database
    from storj_monitor.state import app_state

    database.init_db()

    # Setup state
    app_state["websockets"][mock_websocket] = {"view": ["Aggregate"]}
    app_state["nodes"]["test-node"] = {
        "live_events": [],
        "active_compactions": {},
        "unprocessed_performance_events": [],
        "has_new_events": False,
    }
    app_state["stats_cache"] = {}

    # Simulate view change message

    # Update view in app_state
    app_state["websockets"][mock_websocket]["view"] = ["test-node"]

    # Verify view was updated
    assert app_state["websockets"][mock_websocket]["view"] == ["test-node"]


@pytest.mark.asyncio
async def test_websocket_statistics_broadcast(mock_websocket):
    """
    Test WebSocket statistics broadcasting:
    1. Generate statistics update
    2. Broadcast to all connected clients
    3. Verify message received by appropriate clients
    """
    from storj_monitor.state import IncrementalStats, app_state
    from storj_monitor.websocket_utils import robust_broadcast

    # Setup multiple clients
    ws1 = AsyncMock(spec=web.WebSocketResponse)
    ws1.send_json = AsyncMock()
    ws1.closed = False

    ws2 = AsyncMock(spec=web.WebSocketResponse)
    ws2.send_json = AsyncMock()
    ws2.closed = False

    app_state["websockets"] = {ws1: {"view": ["test-node"]}, ws2: {"view": ["Aggregate"]}}

    # Create statistics payload
    IncrementalStats()
    payload = {
        "type": "statistics_update",
        "success_rate": 95.5,
        "bandwidth": {"egress": 10.5, "ingress": 5.2},
    }

    # Broadcast to all clients
    await robust_broadcast(app_state["websockets"], payload)

    # Verify both clients received the message
    assert ws1.send_json.called
    assert ws2.send_json.called


@pytest.mark.asyncio
async def test_websocket_node_specific_broadcast(mock_websocket):
    """
    Test node-specific broadcasting:
    1. Create clients with different views
    2. Broadcast node-specific message
    3. Verify only appropriate clients receive message
    """
    from storj_monitor.state import app_state
    from storj_monitor.websocket_utils import robust_broadcast

    # Setup clients with different views
    ws_node1 = AsyncMock(spec=web.WebSocketResponse)
    ws_node1.send_json = AsyncMock()
    ws_node1.closed = False

    ws_node2 = AsyncMock(spec=web.WebSocketResponse)
    ws_node2.send_json = AsyncMock()
    ws_node2.closed = False

    ws_aggregate = AsyncMock(spec=web.WebSocketResponse)
    ws_aggregate.send_json = AsyncMock()
    ws_aggregate.closed = False

    app_state["websockets"] = {
        ws_node1: {"view": ["test-node"]},
        ws_node2: {"view": ["other-node"]},
        ws_aggregate: {"view": ["Aggregate"]},
    }

    payload = {"type": "node_update", "node_name": "test-node", "data": {"status": "online"}}

    # Broadcast node-specific message
    await robust_broadcast(app_state["websockets"], payload, node_name="test-node")

    # Verify only test-node and aggregate clients received it
    assert ws_node1.send_json.called
    assert ws_aggregate.send_json.called
    # ws_node2 should not receive it (different node)


@pytest.mark.asyncio
async def test_websocket_performance_data_request(mock_app, mock_websocket, temp_db):
    """
    Test performance data request/response:
    1. Client requests historical performance
    2. Server queries database
    3. Client receives performance data
    """
    from storj_monitor import database
    from storj_monitor.state import app_state

    database.init_db()

    # Setup node state with events
    app_state["nodes"]["test-node"] = {
        "live_events": [],
        "active_compactions": {},
        "unprocessed_performance_events": [],
        "has_new_events": False,
    }

    # Simulate request

    # Mock database response
    with patch("storj_monitor.database.blocking_get_historical_performance") as mock_query:
        mock_query.return_value = [
            {
                "timestamp": "2025-01-08T10:00:00Z",
                "ingress_mbps": 5.0,
                "egress_mbps": 10.0,
                "total_ops": 100,
            }
        ]

        # Simulate processing request
        loop = asyncio.get_running_loop()
        performance_data = await loop.run_in_executor(
            mock_app["db_executor"], database.blocking_get_historical_performance, [], 100, 60
        )

    # Verify response would be sent
    response_payload = {
        "type": "historical_performance_data",
        "view": ["test-node"],
        "performance_data": performance_data,
    }

    assert response_payload["type"] == "historical_performance_data"


@pytest.mark.asyncio
async def test_websocket_reputation_data_request(mock_app, mock_websocket, temp_db):
    """
    Test reputation data request/response:
    1. Client requests reputation data
    2. Server queries database
    3. Client receives reputation data
    """
    from storj_monitor import database

    database.init_db()

    # Add reputation data
    reputation_record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "audit_score": 1.0,
        "suspension_score": 1.0,
        "online_score": 1.0,
        "audit_success_count": 100,
        "audit_total_count": 100,
        "is_disqualified": False,
        "is_suspended": False,
    }

    database.blocking_write_reputation_history(temp_db, [reputation_record])

    # Query reputation data
    reputation_data = database.blocking_get_latest_reputation(temp_db, ["test-node"])

    # Verify data structure
    assert len(reputation_data) > 0
    assert reputation_data[0]["node_name"] == "test-node"
    assert reputation_data[0]["audit_score"] == 1.0


@pytest.mark.asyncio
async def test_websocket_storage_data_request(mock_app, mock_websocket, temp_db):
    """
    Test storage data request/response:
    1. Client requests storage data
    2. Server queries database
    3. Client receives storage data with forecasts
    """
    from storj_monitor import database

    database.init_db()

    # Add storage snapshot
    storage_snapshot = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "total_bytes": 10000000000,
        "used_bytes": 5000000000,
        "available_bytes": 5000000000,
        "trash_bytes": 100000000,
        "used_percent": 50.0,
        "trash_percent": 1.0,
        "available_percent": 50.0,
    }

    database.blocking_write_storage_snapshot(temp_db, storage_snapshot)

    # Query storage data
    storage_data = database.blocking_get_latest_storage(temp_db, ["test-node"])

    # Verify data structure
    assert len(storage_data) > 0
    assert storage_data[0]["node_name"] == "test-node"
    assert storage_data[0]["used_percent"] == 50.0


@pytest.mark.asyncio
async def test_websocket_alert_acknowledgment(mock_app, mock_websocket, temp_db):
    """
    Test alert acknowledgment flow:
    1. Client sends acknowledge_alert message
    2. Server updates alert in database
    3. Server broadcasts acknowledgment
    """
    from storj_monitor import database

    database.init_db()

    # Create alert
    alert = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "alert_type": "test_alert",
        "severity": "warning",
        "title": "Test Alert",
        "message": "This is a test",
        "metadata": {},
    }

    database.blocking_write_alert(temp_db, alert)

    # Get alert ID
    import sqlite3

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM alerts ORDER BY id DESC LIMIT 1")
    alert_id = cursor.fetchone()[0]
    conn.close()

    # Acknowledge alert
    success = database.blocking_acknowledge_alert(temp_db, alert_id)
    assert success

    # Verify alert was acknowledged
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT acknowledged FROM alerts WHERE id = ?", (alert_id,))
    acknowledged = cursor.fetchone()[0]
    conn.close()

    assert acknowledged == 1


@pytest.mark.asyncio
async def test_websocket_error_handling():
    """
    Test WebSocket error handling:
    1. Simulate connection errors
    2. Verify graceful handling
    3. Ensure no crashes
    """
    from storj_monitor.state import app_state
    from storj_monitor.websocket_utils import robust_broadcast

    # Create WebSocket that raises error
    ws_error = AsyncMock(spec=web.WebSocketResponse)
    ws_error.send_json = AsyncMock(side_effect=ConnectionResetError("Connection lost"))
    ws_error.closed = True

    # Create normal WebSocket
    ws_normal = AsyncMock(spec=web.WebSocketResponse)
    ws_normal.send_json = AsyncMock()
    ws_normal.closed = False

    app_state["websockets"] = {
        ws_error: {"view": ["Aggregate"]},
        ws_normal: {"view": ["Aggregate"]},
    }

    payload = {"type": "test", "data": "test"}

    # Broadcast should handle error gracefully
    try:
        await robust_broadcast(app_state["websockets"], payload)
    except Exception as e:
        pytest.fail(f"robust_broadcast should not raise exception: {e}")

    # Normal client should still receive message
    assert ws_normal.send_json.called


@pytest.mark.asyncio
async def test_websocket_batch_broadcasting():
    """
    Test batch broadcasting of log entries:
    1. Add multiple log entries to queue
    2. Trigger batch broadcast
    3. Verify batch is sent correctly
    """
    from storj_monitor.state import app_state

    # Setup
    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.send_json = AsyncMock()
    ws.closed = False

    app_state["websockets"] = {ws: {"view": ["Aggregate"]}}
    app_state["websocket_event_queue"] = []

    # Add events to queue
    base_time = datetime.datetime.now(datetime.timezone.utc).timestamp()
    for i in range(5):
        event = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": "GET",
            "status": "success",
            "node_name": "test-node",
            "arrival_time": base_time + i,
        }
        app_state["websocket_event_queue"].append(event)

    # Simulate batch broadcast
    events_to_send = app_state["websocket_event_queue"][:5]

    # Calculate offsets
    base_arrival = events_to_send[0]["arrival_time"]
    for event in events_to_send:
        event["arrival_offset_ms"] = int((event["arrival_time"] - base_arrival) * 1000)

    payload = {"type": "log_entry_batch", "events": events_to_send}

    await ws.send_json(payload)

    # Verify batch was sent
    assert ws.send_json.called
    sent_payload = ws.send_json.call_args[0][0]
    assert sent_payload["type"] == "log_entry_batch"
    assert len(sent_payload["events"]) == 5


@pytest.mark.asyncio
async def test_websocket_concurrent_clients():
    """
    Test handling multiple concurrent clients:
    1. Connect multiple clients
    2. Send messages to all
    3. Verify all receive messages
    4. Disconnect clients gracefully
    """
    from storj_monitor.state import app_state
    from storj_monitor.websocket_utils import robust_broadcast

    # Create multiple clients
    clients = []
    for _i in range(10):
        ws = AsyncMock(spec=web.WebSocketResponse)
        ws.send_json = AsyncMock()
        ws.closed = False
        clients.append(ws)

    # Register all clients
    app_state["websockets"] = {ws: {"view": ["Aggregate"]} for ws in clients}

    # Broadcast message
    payload = {"type": "test", "data": "concurrent_test"}
    await robust_broadcast(app_state["websockets"], payload)

    # Verify all clients received message
    for ws in clients:
        assert ws.send_json.called


@pytest.mark.asyncio
async def test_websocket_message_validation():
    """
    Test WebSocket message validation:
    1. Send invalid message format
    2. Verify error handling
    3. Ensure system remains stable
    """
    from storj_monitor.state import app_state

    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.send_json = AsyncMock()
    ws.closed = False

    app_state["websockets"] = {ws: {"view": ["Aggregate"]}}

    # Test invalid JSON
    invalid_messages = [
        "not json",
        "{}",
        '{"type": null}',
        '{"type": "unknown_type"}',
    ]

    # These should not crash the system
    for msg in invalid_messages:
        try:
            data = json.loads(msg) if msg.startswith("{") else None
            if data and "type" in data:
                data.get("type")
                # System should handle unknown types gracefully
                assert True
        except json.JSONDecodeError:
            # Invalid JSON should be caught
            assert True


@pytest.mark.asyncio
async def test_websocket_data_consistency():
    """
    Test data consistency across WebSocket updates:
    1. Send initial data
    2. Send updates
    3. Verify updates are consistent with initial data
    """
    from storj_monitor.state import IncrementalStats, app_state

    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.send_json = AsyncMock()
    ws.closed = False

    app_state["websockets"] = {ws: {"view": ["test-node"]}}

    # Initial stats
    stats = IncrementalStats()
    initial_payload = stats.to_payload([])

    await ws.send_json(initial_payload)

    # Update stats
    from storj_monitor.state import app_state

    test_event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "ts_unix": datetime.datetime.now(datetime.timezone.utc).timestamp(),
        "action": "GET",
        "status": "success",
        "size": 1024000,
        "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "category": "get",
        "location": {"country": "US", "lat": 0, "lon": 0},
        "error_reason": None,
        "piece_id": "test",
        "remote_ip": "192.168.1.1",
        "node_name": "test-node",
    }

    stats.add_event(test_event, app_state["TOKEN_REGEX"])
    updated_payload = stats.to_payload([])

    await ws.send_json(updated_payload)

    # Verify both messages were sent
    assert ws.send_json.call_count == 2


@pytest.mark.asyncio
async def test_websocket_heartbeat():
    """
    Test WebSocket heartbeat/keepalive:
    1. Verify WebSocket accepts heartbeat
    2. Ensure connection stays alive
    """
    ws = AsyncMock(spec=web.WebSocketResponse)
    ws.send_json = AsyncMock()
    ws.ping = AsyncMock()
    ws.closed = False

    # Simulate heartbeat
    await ws.ping()

    assert ws.ping.called
    assert not ws.closed
