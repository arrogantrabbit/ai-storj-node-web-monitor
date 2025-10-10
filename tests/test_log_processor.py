"""
Comprehensive tests for log processor module.
"""

import datetime
from unittest.mock import Mock

import geoip2.errors


def test_categorize_action():
    """Test action categorization."""
    from storj_monitor.log_processor import categorize_action

    # Test all action types
    assert categorize_action("GET") == "get"
    assert categorize_action("PUT") == "put"
    assert categorize_action("GET_AUDIT") == "audit"
    assert categorize_action("GET_REPAIR") == "get_repair"
    assert categorize_action("PUT_REPAIR") == "put_repair"
    assert categorize_action("DELETE") == "other"
    assert categorize_action("UNKNOWN") == "other"
    assert categorize_action("") == "other"


def test_parse_size_to_bytes_binary_units():
    """Test parsing size strings with binary units (KiB, MiB, GiB)."""
    from storj_monitor.log_processor import parse_size_to_bytes

    # Binary units (base 1024)
    assert parse_size_to_bytes("1 KiB") == 1024
    assert parse_size_to_bytes("1 MiB") == 1024**2
    assert parse_size_to_bytes("1 GiB") == 1024**3
    assert parse_size_to_bytes("1 TiB") == 1024**4

    # With decimals
    assert parse_size_to_bytes("7.05 GiB") == int(7.05 * (1024**3))
    assert parse_size_to_bytes("2.5 MiB") == int(2.5 * (1024**2))


def test_parse_size_to_bytes_decimal_units():
    """Test parsing size strings with decimal/SI units (KB, MB, GB)."""
    from storj_monitor.log_processor import parse_size_to_bytes

    # Decimal units (base 1000)
    assert parse_size_to_bytes("1 KB") == 1000
    assert parse_size_to_bytes("1 MB") == 1000**2
    assert parse_size_to_bytes("1 GB") == 1000**3
    assert parse_size_to_bytes("1 TB") == 1000**4

    # With decimals
    assert parse_size_to_bytes("7.05 GB") == int(7.05 * (1000**3))
    assert parse_size_to_bytes("181.90 GB") == int(181.90 * (1000**3))


def test_parse_size_to_bytes_edge_cases():
    """Test edge cases for size parsing."""
    from storj_monitor.log_processor import parse_size_to_bytes

    # No unit (assume bytes)
    assert parse_size_to_bytes("1024") == 1024
    assert parse_size_to_bytes("512") == 512

    # Case insensitive
    assert parse_size_to_bytes("1 gb") == 1000**3
    assert parse_size_to_bytes("1 GiB") == 1024**3

    # With spaces
    assert parse_size_to_bytes("  1  GB  ") == 1000**3

    # Invalid input
    assert parse_size_to_bytes("") == 0
    assert parse_size_to_bytes("invalid") == 0
    assert parse_size_to_bytes(None) == 0
    assert parse_size_to_bytes(123) == 0  # Not a string


def test_parse_duration_str_to_seconds_simple():
    """Test parsing simple duration strings."""
    from storj_monitor.log_processor import parse_duration_str_to_seconds

    # Simple seconds
    assert parse_duration_str_to_seconds("42.281s") == 42.281
    assert parse_duration_str_to_seconds("1s") == 1.0
    assert parse_duration_str_to_seconds("0.5s") == 0.5

    # Simple minutes
    assert parse_duration_str_to_seconds("1m") == 60.0
    assert parse_duration_str_to_seconds("2m") == 120.0

    # Simple hours
    assert parse_duration_str_to_seconds("1h") == 3600.0
    assert parse_duration_str_to_seconds("2h") == 7200.0


def test_parse_duration_str_to_seconds_complex():
    """Test parsing complex duration strings."""
    from storj_monitor.log_processor import parse_duration_str_to_seconds

    # Complex combinations
    assert parse_duration_str_to_seconds("1m37.535505102s") == 97.535505102
    assert parse_duration_str_to_seconds("1h30m") == 5400.0
    assert parse_duration_str_to_seconds("1h30m45s") == 5445.0
    assert parse_duration_str_to_seconds("2h15m30.5s") == 8130.5


def test_parse_duration_str_to_seconds_milliseconds():
    """Test parsing durations with milliseconds."""
    from storj_monitor.log_processor import parse_duration_str_to_seconds

    # Milliseconds
    assert parse_duration_str_to_seconds("500ms") == 0.5
    assert parse_duration_str_to_seconds("1500ms") == 1.5
    assert parse_duration_str_to_seconds("100ms") == 0.1

    # Mixed with other units
    assert parse_duration_str_to_seconds("1s500ms") == 1.5
    assert parse_duration_str_to_seconds("1m500ms") == 60.5


def test_parse_duration_str_to_seconds_edge_cases():
    """Test edge cases for duration parsing."""
    from storj_monitor.log_processor import parse_duration_str_to_seconds

    # Plain number (assume seconds)
    assert parse_duration_str_to_seconds("42.5") == 42.5
    assert parse_duration_str_to_seconds("100") == 100.0

    # Invalid input
    assert parse_duration_str_to_seconds("") is None
    assert parse_duration_str_to_seconds("invalid") is None
    assert parse_duration_str_to_seconds(None) is None
    assert parse_duration_str_to_seconds(123) is None  # Not a string


def test_get_size_bucket():
    """Test size bucket categorization."""
    from storj_monitor.log_processor import get_size_bucket

    # Test each bucket
    assert get_size_bucket(512) == "< 1 KB"
    assert get_size_bucket(2048) == "1-4 KB"
    assert get_size_bucket(8192) == "4-16 KB"
    assert get_size_bucket(32768) == "16-64 KB"
    assert get_size_bucket(131072) == "64-256 KB"
    assert get_size_bucket(524288) == "256 KB - 1 MB"
    assert get_size_bucket(2097152) == "> 1 MB"

    # Boundary cases
    assert get_size_bucket(1023) == "< 1 KB"
    assert get_size_bucket(1024) == "1-4 KB"
    assert get_size_bucket(1048575) == "256 KB - 1 MB"
    assert get_size_bucket(1048576) == "> 1 MB"


def test_get_size_bucket_caching():
    """Test that size bucket results are cached."""
    from storj_monitor.log_processor import _size_bucket_cache, get_size_bucket

    # Clear cache
    _size_bucket_cache.clear()

    # First call should cache
    result1 = get_size_bucket(1024)
    assert 1024 in _size_bucket_cache

    # Second call should use cache
    result2 = get_size_bucket(1024)
    assert result1 == result2


def test_parse_log_line_download_success(mock_geoip_reader):
    """Test parsing successful download log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "ABCDEFGH123456789", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024000, "Remote Address": "192.168.1.1:12345", "duration": "1.5s"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "traffic_event"
    assert result["data"]["action"] == "GET"
    assert result["data"]["status"] == "success"
    assert result["data"]["size"] == 1024000
    assert result["data"]["satellite_id"] == "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S"
    assert result["data"]["node_name"] == "test-node"
    assert result["data"]["duration_ms"] == 1500


def test_parse_log_line_upload_success(mock_geoip_reader):
    """Test parsing successful upload log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore uploaded {"Piece ID": "TEST123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "PUT", "Size": 2048000, "Remote Address": "10.0.0.1:54321"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "traffic_event"
    assert result["data"]["action"] == "PUT"
    assert result["data"]["status"] == "success"
    assert result["data"]["category"] == "put"


def test_parse_log_line_audit(mock_geoip_reader):
    """Test parsing audit log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "AUDIT123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET_AUDIT", "Size": 256000, "Remote Address": "192.168.1.1:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["action"] == "GET_AUDIT"
    assert result["data"]["category"] == "audit"


def test_parse_log_line_repair_operations(mock_geoip_reader):
    """Test parsing repair operations."""
    from storj_monitor.log_processor import parse_log_line

    # GET_REPAIR
    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "REPAIR123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET_REPAIR", "Size": 512000, "Remote Address": "192.168.1.1:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["action"] == "GET_REPAIR"
    assert result["data"]["category"] == "get_repair"

    # PUT_REPAIR
    log_line2 = """2025-01-08T12:00:00.000Z INFO piecestore uploaded {"Piece ID": "REPAIR456", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "PUT_REPAIR", "Size": 512000, "Remote Address": "192.168.1.1:12345"}"""

    result2 = parse_log_line(log_line2, "test-node", mock_geoip_reader, geoip_cache)
    assert result2["data"]["category"] == "put_repair"


def test_parse_log_line_failed(mock_geoip_reader):
    """Test parsing failed operation log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z ERROR piecestore failed {"Piece ID": "FAILED123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 0, "Remote Address": "192.168.1.1:12345", "error": "piece not found"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["status"] == "failed"
    assert result["data"]["error_reason"] == "piece not found"


def test_parse_log_line_canceled(mock_geoip_reader):
    """Test parsing canceled operation log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore download canceled {"Piece ID": "CANCEL123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 0, "Remote Address": "192.168.1.1:12345", "reason": "context canceled"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["status"] == "canceled"
    assert result["data"]["error_reason"] == "context canceled"


def test_parse_log_line_geoip_lookup(mock_geoip_reader):
    """Test GeoIP lookup in log parsing."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "GEO123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "8.8.8.8:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert "location" in result["data"]
    assert result["data"]["location"]["country"] == "United States"
    assert result["data"]["location"]["lat"] == 37.7749
    assert result["data"]["location"]["lon"] == -122.4194
    assert result["data"]["remote_ip"] == "8.8.8.8"


def test_parse_log_line_geoip_cache(mock_geoip_reader):
    """Test that GeoIP results are cached."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "CACHE123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "1.2.3.4:12345"}"""

    geoip_cache = {}

    # First call should cache
    parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)
    assert "1.2.3.4" in geoip_cache

    # Second call should use cache (mock won't be called again)
    result2 = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)
    assert result2["data"]["location"] == geoip_cache["1.2.3.4"]


def test_parse_log_line_geoip_not_found():
    """Test GeoIP lookup when address not found."""
    from storj_monitor.log_processor import parse_log_line

    # Mock reader that raises AddressNotFoundError
    mock_reader = Mock()
    mock_reader.city.side_effect = geoip2.errors.AddressNotFoundError("Address not found")

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "NOTFOUND", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "0.0.0.0:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_reader, geoip_cache)

    assert result is not None
    assert result["data"]["location"]["country"] == "Unknown"
    assert result["data"]["location"]["lat"] is None
    assert result["data"]["location"]["lon"] is None


def test_parse_log_line_operation_start(mock_geoip_reader):
    """Test parsing operation start (DEBUG) log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z DEBUG piecestore download started {"Piece ID": "START123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Available Space": 5000000000}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "operation_start"
    assert result["piece_id"] == "START123"
    assert result["action"] == "GET"
    assert result["available_space"] == 5000000000


def test_parse_log_line_hashstore_begin(mock_geoip_reader):
    """Test parsing hashstore compaction begin log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO hashstore	beginning compaction	{"satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "store": "pieces"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "hashstore_begin"
    assert "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S:pieces" in result["key"]


def test_parse_log_line_hashstore_end(mock_geoip_reader):
    """Test parsing hashstore compaction end log."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO hashstore	finished compaction	{"satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "store": "pieces", "duration": "2m30s", "stats": {"DataReclaimed": "100 MB", "DataRewritten": "50 MB", "Table": {"Load": 0.75}, "TrashPercent": 0.05}}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "hashstore_end"
    assert "data" in result
    assert result["data"]["node_name"] == "test-node"
    assert result["data"]["duration"] == 150.0  # 2m30s = 150 seconds
    assert result["data"]["data_reclaimed_bytes"] == 100000000  # 100 MB
    assert result["data"]["table_load"] == 75.0
    assert result["data"]["trash_percent"] == 5.0


def test_parse_log_line_malformed():
    """Test that malformed logs don't crash parser."""
    from storj_monitor.log_processor import parse_log_line

    mock_reader = Mock()
    geoip_cache = {}

    # Not a valid log line
    result = parse_log_line("This is not a valid log line", "test-node", mock_reader, geoip_cache)
    assert result is None

    # Missing JSON
    result = parse_log_line(
        "2025-01-08T12:00:00.000Z INFO piecestore", "test-node", mock_reader, geoip_cache
    )
    assert result is None

    # Invalid JSON
    result = parse_log_line(
        "2025-01-08T12:00:00.000Z INFO piecestore {invalid json}",
        "test-node",
        mock_reader,
        geoip_cache,
    )
    assert result is None

    # Missing required fields
    result = parse_log_line(
        '2025-01-08T12:00:00.000Z INFO piecestore {"Action": "GET"}',
        "test-node",
        mock_reader,
        geoip_cache,
    )
    assert result is None


def test_parse_log_line_irrelevant():
    """Test that irrelevant log lines are ignored."""
    from storj_monitor.log_processor import parse_log_line

    mock_reader = Mock()
    geoip_cache = {}

    # Not piecestore or hashstore log
    result = parse_log_line(
        "2025-01-08T12:00:00.000Z INFO server started", "test-node", mock_reader, geoip_cache
    )
    assert result is None

    # No recognized log level
    result = parse_log_line(
        '2025-01-08T12:00:00.000Z TRACE piecestore {"test": "data"}',
        "test-node",
        mock_reader,
        geoip_cache,
    )
    assert result is None


def test_parse_log_line_with_duration_in_json(mock_geoip_reader):
    """Test parsing log with duration in JSON field."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "DUR123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "192.168.1.1:12345", "duration": "2m15.5s"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["duration_ms"] == 135500  # 2m15.5s = 135.5 seconds = 135500ms


def test_parse_log_line_timestamp_parsing(mock_geoip_reader):
    """Test that timestamps are parsed correctly to UTC."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:30:45.123Z INFO piecestore downloaded {"Piece ID": "TIME123", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "192.168.1.1:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    timestamp = result["data"]["timestamp"]
    assert timestamp.tzinfo == datetime.timezone.utc
    assert timestamp.hour == 12
    assert timestamp.minute == 30
    assert timestamp.second == 45


def test_parse_log_line_missing_fields(mock_geoip_reader):
    """Test parsing log with missing optional fields."""
    from storj_monitor.log_processor import parse_log_line

    # Missing duration (should still parse)
    log_line = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "NODUR", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "192.168.1.1:12345"}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["data"]["duration_ms"] is None


def test_parse_size_to_bytes_petabytes():
    """Test parsing petabyte sizes."""
    from storj_monitor.log_processor import parse_size_to_bytes

    assert parse_size_to_bytes("1 PB") == 1000**5
    assert parse_size_to_bytes("1 PiB") == 1024**5
    assert parse_size_to_bytes("2.5 PB") == int(2.5 * (1000**5))


def test_parse_duration_hours_minutes_seconds():
    """Test parsing all time units together."""
    from storj_monitor.log_processor import parse_duration_str_to_seconds

    # All units present
    duration = parse_duration_str_to_seconds("2h30m45.5s")
    expected = (2 * 3600) + (30 * 60) + 45.5
    assert duration == expected

    # With milliseconds
    duration = parse_duration_str_to_seconds("1h15m30s500ms")
    expected = 3600 + (15 * 60) + 30 + 0.5
    assert duration == expected


def test_categorize_action_case_sensitivity():
    """Test that action categorization is case-sensitive where needed."""
    from storj_monitor.log_processor import categorize_action

    # Should handle exact matches
    assert categorize_action("GET") == "get"
    assert categorize_action("PUT") == "put"

    # Contains checks should still work
    assert categorize_action("SOMETHING_GET") == "get"
    assert categorize_action("SOMETHING_PUT") == "put"


def test_size_bucket_zero_and_negative():
    """Test size bucket with edge case values."""
    from storj_monitor.log_processor import get_size_bucket

    assert get_size_bucket(0) == "< 1 KB"
    # Negative sizes shouldn't normally occur but should not crash
    assert get_size_bucket(-1) == "< 1 KB"


def test_parse_log_line_multiple_logs_in_sequence(mock_geoip_reader):
    """Test parsing multiple sequential logs."""
    from storj_monitor.log_processor import parse_log_line

    geoip_cache = {}

    logs = [
        """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "SEQ1", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "192.168.1.1:12345"}""",
        """2025-01-08T12:00:01.000Z INFO piecestore uploaded {"Piece ID": "SEQ2", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "PUT", "Size": 2048, "Remote Address": "192.168.1.2:12345"}""",
        """2025-01-08T12:00:02.000Z INFO piecestore downloaded {"Piece ID": "SEQ3", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET_AUDIT", "Size": 512, "Remote Address": "192.168.1.3:12345"}""",
    ]

    results = [parse_log_line(log, "test-node", mock_geoip_reader, geoip_cache) for log in logs]

    assert all(r is not None for r in results)
    assert results[0]["data"]["action"] == "GET"
    assert results[1]["data"]["action"] == "PUT"
    assert results[2]["data"]["action"] == "GET_AUDIT"


def test_parse_hashstore_stats_extraction(mock_geoip_reader):
    """Test detailed extraction of hashstore statistics."""
    from storj_monitor.log_processor import parse_log_line

    log_line = """2025-01-08T12:00:00.000Z INFO hashstore	finished compaction	{"satellite": "test-sat", "store": "trash", "duration": "5m", "stats": {"DataReclaimed": "1.5 GiB", "DataRewritten": "500 MiB", "Table": {"Load": 0.85}, "TrashPercent": 0.12}}"""

    geoip_cache = {}
    result = parse_log_line(log_line, "test-node", mock_geoip_reader, geoip_cache)

    assert result is not None
    assert result["type"] == "hashstore_end"
    assert result["data"]["store"] == "trash"
    assert result["data"]["duration"] == 300.0  # 5 minutes
    assert result["data"]["data_reclaimed_bytes"] > 0
    assert result["data"]["data_rewritten_bytes"] > 0
    assert result["data"]["table_load"] == 85.0
    assert result["data"]["trash_percent"] == 12.0


def test_parse_log_line_with_various_ip_formats(mock_geoip_reader):
    """Test parsing logs with different IP address formats."""
    from storj_monitor.log_processor import parse_log_line

    geoip_cache = {}

    # IPv4 with port
    log1 = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "IP1", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "192.168.1.1:12345"}"""
    result1 = parse_log_line(log1, "test-node", mock_geoip_reader, geoip_cache)
    assert result1["data"]["remote_ip"] == "192.168.1.1"

    # Different port
    log2 = """2025-01-08T12:00:00.000Z INFO piecestore downloaded {"Piece ID": "IP2", "Satellite ID": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", "Action": "GET", "Size": 1024, "Remote Address": "10.0.0.1:54321"}"""
    result2 = parse_log_line(log2, "test-node", mock_geoip_reader, geoip_cache)
    assert result2["data"]["remote_ip"] == "10.0.0.1"


def test_parse_size_with_different_separators():
    """Test parsing sizes with different formats."""
    from storj_monitor.log_processor import parse_size_to_bytes

    # Different spacing
    assert parse_size_to_bytes("1GB") == 1000**3
    assert parse_size_to_bytes("1 GB") == 1000**3
    assert parse_size_to_bytes("1  GB") == 1000**3

    # Mixed case
    assert parse_size_to_bytes("1Gb") == 1000**3
    assert parse_size_to_bytes("1gB") == 1000**3
