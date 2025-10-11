import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from storj_monitor.log_processor import parse_log_line, log_processor_task
from storj_monitor.state import app_state


def pick_line(*contains: str) -> str:
    p = Path(__file__).parent / "fixtures" / "sample_logs.txt"
    with p.open() as f:
        for line in f:
            if all(c in line for c in contains):
                return line
    raise AssertionError(f"No sample log line found for: {contains}")


def test_parse_real_fixture_lines_basic(mock_geoip_reader):
    geoip_cache = {}

    # Typical GET downloaded
    dl = pick_line("INFO", "piecestore", "downloaded", '"Action": "GET"')
    r1 = parse_log_line(dl, "node1", mock_geoip_reader, geoip_cache)
    assert r1 and r1["type"] == "traffic_event"
    assert r1["data"]["category"] in ("get", "audit", "get_repair")

    # Typical PUT uploaded
    ul = pick_line("INFO", "piecestore", "uploaded", '"Action": "PUT"')
    r2 = parse_log_line(ul, "node1", mock_geoip_reader, geoip_cache)
    assert r2 and r2["type"] == "traffic_event"
    assert r2["data"]["category"] in ("put", "put_repair")

    # Hashstore compaction begin
    hb = pick_line("INFO", "hashstore", "beginning compaction")
    r3 = parse_log_line(hb, "node1", mock_geoip_reader, geoip_cache)
    assert r3 and r3["type"] == "hashstore_begin"

    # Hashstore compaction end
    he = pick_line("INFO", "hashstore", "finished compaction")
    r4 = parse_log_line(he, "node1", mock_geoip_reader, geoip_cache)
    assert r4 and r4["type"] == "hashstore_end"
    assert "data" in r4


@pytest.mark.asyncio
async def test_log_processor_task_consumes_fixture_lines(monkeypatch):
    # Minimal app and node state
    node_name = "node-a"
    app_state["nodes"][node_name] = {
        "live_events": [],
        "active_compactions": {},
        "unprocessed_performance_events": [],
        "has_new_events": False,
    }
    app_state["db_write_queue"] = asyncio.Queue()
    app_state["websocket_event_queue"] = []
    app_state["websocket_queue_lock"] = asyncio.Lock()
    app_state["websockets"] = {}

    # Fake geoip reader
    city_response = Mock()
    city_response.country.name = "United States"
    city_response.location.latitude = 37.0
    city_response.location.longitude = -122.0
    geoip_reader = Mock()
    geoip_reader.city.return_value = city_response

    # Build app dict
    app = {"geoip_reader": geoip_reader, "db_executor": None}

    # Lines: include "download started" (operation_start) then "downloaded" (traffic_event)
    start_line = pick_line("DEBUG", "piecestore", "download started")
    done_line = pick_line("INFO", "piecestore", "downloaded", '"Action": "GET"')

    q = asyncio.Queue()
    # Monkeypatch broadcast and DB write used by log_processor_task for hashstore paths (not used here)
    monkeypatch.setattr("storj_monitor.websocket_utils.robust_broadcast", AsyncMock(return_value=True))
    monkeypatch.setattr("storj_monitor.log_processor.blocking_write_hashstore_log", lambda *a, **k: True)
    # Server helper used when broadcasting compaction state
    monkeypatch.setattr("storj_monitor.server.get_active_compactions_payload", lambda: {})

    # Start the processor task
    task = asyncio.create_task(log_processor_task(app, node_name, q))
    try:
        await q.put((start_line, time.time()))
        await q.put((done_line, time.time()))
        # Allow task to process
        await asyncio.sleep(0.05)
        # We should have at least one live event appended
        assert app_state["nodes"][node_name]["live_events"], "Expected events to be processed"
    finally:
        task.cancel()
        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await task