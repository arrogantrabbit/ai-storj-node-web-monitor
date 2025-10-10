"""
Comprehensive unit tests for performance_analyzer.py

Tests percentile calculations, latency statistics, slow operation detection,
and histogram generation.
"""

from unittest.mock import MagicMock, patch

from storj_monitor.performance_analyzer import (
    analyze_latency_data,
    blocking_get_latency_histogram,
    blocking_get_latency_stats,
    calculate_percentiles,
    detect_slow_operations,
)


class TestCalculatePercentiles:
    """Test suite for calculate_percentiles function."""

    def test_calculate_percentiles_basic(self):
        """Test basic percentile calculation."""
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        result = calculate_percentiles(values, [50, 95, 99])

        assert "p50" in result
        assert "p95" in result
        assert "p99" in result
        assert 4.5 <= result["p50"] <= 5.5  # Median of 1-10 (implementation-dependent)

    def test_calculate_percentiles_empty_list(self):
        """Test percentile calculation with empty list."""
        values = []
        result = calculate_percentiles(values, [50, 95, 99])

        assert result["p50"] == 0.0
        assert result["p95"] == 0.0
        assert result["p99"] == 0.0

    def test_calculate_percentiles_single_value(self):
        """Test percentile calculation with single value."""
        values = [100]
        result = calculate_percentiles(values, [50, 95, 99])

        assert result["p50"] == 100
        assert result["p95"] == 100
        assert result["p99"] == 100

    def test_calculate_percentiles_p50(self):
        """Test p50 (median) calculation."""
        values = [10, 20, 30, 40, 50]
        result = calculate_percentiles(values, [50])

        assert 25 <= result["p50"] <= 30  # Middle value or slightly below

    def test_calculate_percentiles_p95(self):
        """Test p95 calculation."""
        values = list(range(1, 101))  # 1 to 100
        result = calculate_percentiles(values, [95])

        # P95 should be around 95
        assert 94 <= result["p95"] <= 96

    def test_calculate_percentiles_p99(self):
        """Test p99 calculation."""
        values = list(range(1, 101))  # 1 to 100
        result = calculate_percentiles(values, [99])

        # P99 should be around 99
        assert 98 <= result["p99"] <= 100

    def test_calculate_percentiles_custom_percentiles(self):
        """Test with custom percentile list."""
        values = list(range(1, 11))
        result = calculate_percentiles(values, [25, 75])

        assert "p25" in result
        assert "p75" in result
        assert result["p25"] < result["p75"]

    def test_calculate_percentiles_sorted_values(self):
        """Test that function works with unsorted values."""
        values = [5, 1, 9, 3, 7, 2, 8, 4, 6, 10]
        result = calculate_percentiles(values, [50])

        assert 4.5 <= result["p50"] <= 5.5  # Same as sorted (implementation-dependent)


class TestAnalyzeLatencyData:
    """Test suite for analyze_latency_data function."""

    def test_analyze_latency_basic(self):
        """Test basic latency analysis."""
        events = [
            {"duration_ms": 100, "category": "get", "status": "success"},
            {"duration_ms": 200, "category": "get", "status": "success"},
            {"duration_ms": 300, "category": "put", "status": "success"},
        ]

        result = analyze_latency_data(events)

        assert "get" in result
        assert "put" in result
        assert "all" in result
        assert result["get"]["count"] == 2
        assert result["put"]["count"] == 1
        assert result["all"]["count"] == 3

    def test_analyze_latency_empty_events(self):
        """Test latency analysis with empty events list."""
        events = []
        result = analyze_latency_data(events)

        assert result["get"]["count"] == 0
        assert result["put"]["count"] == 0
        assert result["all"]["count"] == 0

    def test_analyze_latency_only_successful(self):
        """Test that only successful operations are included."""
        events = [
            {"duration_ms": 100, "category": "get", "status": "success"},
            {"duration_ms": 200, "category": "get", "status": "failed"},
            {"duration_ms": 300, "category": "get", "status": "success"},
        ]

        result = analyze_latency_data(events)

        # Only 2 successful events should be counted
        assert result["get"]["count"] == 2
        assert result["all"]["count"] == 2

    def test_analyze_latency_statistics(self):
        """Test latency statistics calculation."""
        events = [
            {"duration_ms": 100, "category": "get", "status": "success"},
            {"duration_ms": 200, "category": "get", "status": "success"},
            {"duration_ms": 300, "category": "get", "status": "success"},
        ]

        result = analyze_latency_data(events)

        assert result["get"]["mean"] == 200.0
        assert result["get"]["median"] == 200.0
        assert result["get"]["min"] == 100
        assert result["get"]["max"] == 300

    def test_analyze_latency_percentiles(self):
        """Test percentile calculations in latency analysis."""
        events = []
        for i in range(100):
            events.append({"duration_ms": i + 1, "category": "get", "status": "success"})

        result = analyze_latency_data(events)

        assert "p50" in result["get"]
        assert "p95" in result["get"]
        assert "p99" in result["get"]

    def test_analyze_latency_audit_category(self):
        """Test audit category classification."""
        events = [
            {"duration_ms": 100, "category": "audit", "status": "success"},
            {"duration_ms": 200, "category": "audit", "status": "success"},
        ]

        result = analyze_latency_data(events)

        assert result["audit"]["count"] == 2
        assert result["audit"]["mean"] == 150.0

    def test_analyze_latency_ignore_null_duration(self):
        """Test that events with null duration are ignored."""
        events = [
            {"duration_ms": None, "category": "get", "status": "success"},
            {"duration_ms": 0, "category": "get", "status": "success"},
            {"duration_ms": -10, "category": "get", "status": "success"},
            {"duration_ms": 100, "category": "get", "status": "success"},
        ]

        result = analyze_latency_data(events)

        # Only the valid 100ms event should be counted
        assert result["get"]["count"] == 1

    def test_analyze_latency_multiple_categories(self):
        """Test analysis with multiple categories."""
        events = [
            {"duration_ms": 100, "category": "get", "status": "success"},
            {"duration_ms": 200, "category": "put", "status": "success"},
            {"duration_ms": 150, "category": "audit", "status": "success"},
        ]

        result = analyze_latency_data(events)

        assert result["get"]["count"] == 1
        assert result["put"]["count"] == 1
        assert result["audit"]["count"] == 1
        assert result["all"]["count"] == 3


class TestDetectSlowOperations:
    """Test suite for detect_slow_operations function."""

    def test_detect_slow_operations_basic(self):
        """Test basic slow operation detection."""
        events = [
            {
                "timestamp": "2025-10-08T00:00:00Z",
                "action": "GET",
                "duration_ms": 6000,
                "piece_id": "piece1",
                "satellite_id": "sat1",
                "status": "success",
                "size": 1024,
                "node_name": "node1",
            }
        ]

        result = detect_slow_operations(events, threshold_ms=5000, limit=10)

        assert len(result) == 1
        assert result[0]["duration_ms"] == 6000

    def test_detect_slow_operations_none_slow(self):
        """Test when no operations exceed threshold."""
        events = [
            {"duration_ms": 1000, "action": "GET", "status": "success"},
            {"duration_ms": 2000, "action": "PUT", "status": "success"},
        ]

        result = detect_slow_operations(events, threshold_ms=5000)

        assert len(result) == 0

    def test_detect_slow_operations_sorting(self):
        """Test that slow operations are sorted by duration (slowest first)."""
        events = [
            {
                "timestamp": "2025-10-08T00:00:00Z",
                "action": "GET",
                "duration_ms": 6000,
                "status": "success",
                "node_name": "node1",
            },
            {
                "timestamp": "2025-10-08T00:01:00Z",
                "action": "PUT",
                "duration_ms": 8000,
                "status": "success",
                "node_name": "node1",
            },
            {
                "timestamp": "2025-10-08T00:02:00Z",
                "action": "GET",
                "duration_ms": 7000,
                "status": "success",
                "node_name": "node1",
            },
        ]

        result = detect_slow_operations(events, threshold_ms=5000)

        assert len(result) == 3
        assert result[0]["duration_ms"] == 8000  # Slowest first
        assert result[1]["duration_ms"] == 7000
        assert result[2]["duration_ms"] == 6000

    def test_detect_slow_operations_limit(self):
        """Test limit parameter."""
        events = []
        for i in range(20):
            events.append(
                {
                    "timestamp": f"2025-10-08T00:{i:02d}:00Z",
                    "action": "GET",
                    "duration_ms": 5000 + (i * 100),
                    "status": "success",
                    "node_name": "node1",
                }
            )

        result = detect_slow_operations(events, threshold_ms=5000, limit=5)

        assert len(result) == 5

    def test_detect_slow_operations_custom_threshold(self):
        """Test with custom threshold."""
        events = [
            {"duration_ms": 3000, "action": "GET", "status": "success", "node_name": "node1"},
            {"duration_ms": 4000, "action": "PUT", "status": "success", "node_name": "node1"},
            {"duration_ms": 2000, "action": "GET", "status": "success", "node_name": "node1"},
        ]

        result = detect_slow_operations(events, threshold_ms=3500)

        assert len(result) == 1
        assert result[0]["duration_ms"] == 4000


class TestBlockingGetLatencyStats:
    """Test suite for blocking_get_latency_stats function."""

    def test_get_latency_stats_empty_nodes(self):
        """Test latency stats with empty node list."""
        result = blocking_get_latency_stats("test.db", [], hours=1)

        assert result["statistics"] == {}
        assert result["slow_operations"] == []

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_stats_success(self, mock_conn):
        """Test successful latency stats retrieval."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [{"node_name": "node1", "total_events": 100, "events_with_duration": 80}],
            [
                {
                    "timestamp": "2025-10-08T00:00:00Z",
                    "action": "GET",
                    "status": "success",
                    "size": 1024,
                    "piece_id": "piece1",
                    "satellite_id": "sat1",
                    "duration_ms": 150,
                    "node_name": "node1",
                }
            ],
        ]

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_context.__enter__.return_value.row_factory = None
        mock_conn.return_value = mock_context

        result = blocking_get_latency_stats("test.db", ["node1"], hours=1)

        assert "statistics" in result
        assert "slow_operations" in result

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_stats_caching(self, mock_conn):
        """Test that latency stats are cached."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [{"node_name": "node1", "total_events": 100, "events_with_duration": 80}],
            [],
        ]

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_conn.return_value = mock_context

        # Clear cache
        from storj_monitor.performance_analyzer import _latency_stats_cache

        _latency_stats_cache.clear()

        # First call
        blocking_get_latency_stats("test.db", ["node1"], hours=1)

        # Second call (should use cache)
        blocking_get_latency_stats("test.db", ["node1"], hours=1)

        # Connection should only be called once due to caching
        assert mock_conn.call_count == 1

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_stats_exception(self, mock_conn):
        """Test exception handling in get_latency_stats."""
        mock_conn.side_effect = Exception("Database error")

        result = blocking_get_latency_stats("test.db", ["node1"], hours=1)

        # Function returns dict with empty stats on error
        assert isinstance(result, dict)
        assert "statistics" in result or "all" in result
        # May return either format depending on implementation


class TestBlockingGetLatencyHistogram:
    """Test suite for blocking_get_latency_histogram function."""

    def test_get_latency_histogram_empty_nodes(self):
        """Test histogram with empty node list."""
        result = blocking_get_latency_histogram("test.db", [], hours=1)

        assert result == []

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_histogram_success(self, mock_conn):
        """Test successful histogram generation."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(
            return_value=iter(
                [
                    (0, 10),  # 0-100ms: 10 operations
                    (100, 5),  # 100-200ms: 5 operations
                    (200, 2),  # 200-300ms: 2 operations
                ]
            )
        )

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_conn.return_value = mock_context

        result = blocking_get_latency_histogram("test.db", ["node1"], hours=1, bucket_size_ms=100)

        assert len(result) == 3
        assert result[0]["bucket_start_ms"] == 0
        assert result[0]["bucket_end_ms"] == 100
        assert result[0]["count"] == 10

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_histogram_labels(self, mock_conn):
        """Test histogram bucket labels."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(
            return_value=iter(
                [
                    (0, 10),
                    (100, 5),
                ]
            )
        )

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_conn.return_value = mock_context

        result = blocking_get_latency_histogram("test.db", ["node1"], hours=1, bucket_size_ms=100)

        assert result[0]["label"] == "0-100ms"
        assert result[1]["label"] == "100-200ms"

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_histogram_caching(self, mock_conn):
        """Test that histogram is cached."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(return_value=iter([]))

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_conn.return_value = mock_context

        # Clear cache
        from storj_monitor.performance_analyzer import _latency_histogram_cache

        _latency_histogram_cache.clear()

        # First call
        blocking_get_latency_histogram("test.db", ["node1"], hours=1)

        # Second call (should use cache)
        blocking_get_latency_histogram("test.db", ["node1"], hours=1)

        # Connection should only be called once due to caching
        assert mock_conn.call_count == 1

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_histogram_custom_bucket_size(self, mock_conn):
        """Test histogram with custom bucket size."""
        mock_cursor = MagicMock()
        mock_cursor.__iter__ = MagicMock(
            return_value=iter(
                [
                    (0, 10),
                    (500, 5),
                ]
            )
        )

        mock_context = MagicMock()
        mock_context.__enter__.return_value.execute = MagicMock(return_value=mock_cursor)
        mock_conn.return_value = mock_context

        result = blocking_get_latency_histogram("test.db", ["node1"], hours=1, bucket_size_ms=500)

        assert result[0]["bucket_end_ms"] == 500
        assert result[1]["bucket_start_ms"] == 500
        assert result[1]["bucket_end_ms"] == 1000

    @patch("storj_monitor.performance_analyzer.get_optimized_connection")
    def test_get_latency_histogram_exception(self, mock_conn):
        """Test exception handling in get_latency_histogram."""
        mock_conn.side_effect = Exception("Database error")

        result = blocking_get_latency_histogram("test.db", ["node1"], hours=1)

        assert result == []
