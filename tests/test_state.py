"""
Comprehensive unit tests for state module.

Tests IncrementalStats class and app_state structure.
"""

import datetime
import re
import time 
import pytest

from storj_monitor.state import IncrementalStats, app_state


class TestIncrementalStatsInitialization:
    """Test suite for IncrementalStats initialization."""

    def test_incremental_stats_default_values(self):
        """Test default values on initialization."""
        stats = IncrementalStats()

        assert stats.dl_success == 0
        assert stats.dl_fail == 0
        assert stats.ul_success == 0
        assert stats.ul_fail == 0
        assert stats.audit_success == 0
        assert stats.audit_fail == 0
        assert stats.total_dl_size == 0
        assert stats.total_ul_size == 0
        assert stats.live_dl_bytes == 0
        assert stats.live_ul_bytes == 0

    def test_incremental_stats_collections(self):
        """Test that collection fields are initialized."""
        stats = IncrementalStats()

        assert isinstance(stats.satellites, dict)
        assert len(stats.satellites) == 0
        assert isinstance(stats.error_agg, dict)
        assert isinstance(stats.hot_pieces, dict)


class TestGetOrCreateSatellite:
    """Test suite for get_or_create_satellite method."""

    def test_create_new_satellite(self):
        """Test creating new satellite stats."""
        stats = IncrementalStats()

        sat_stats = stats.get_or_create_satellite("test-satellite-id")

        assert sat_stats is not None
        assert "uploads" in sat_stats
        assert "downloads" in sat_stats
        assert sat_stats["uploads"] == 0
        assert sat_stats["downloads"] == 0
        assert sat_stats["audits"] == 0

    def test_get_existing_satellite(self):
        """Test getting existing satellite stats."""
        stats = IncrementalStats()

        # Create satellite
        sat_stats1 = stats.get_or_create_satellite("test-satellite-id")
        sat_stats1["uploads"] = 10

        # Get same satellite
        sat_stats2 = stats.get_or_create_satellite("test-satellite-id")

        assert sat_stats1 is sat_stats2
        assert sat_stats2["uploads"] == 10

    def test_multiple_satellites(self):
        """Test handling multiple satellites."""
        stats = IncrementalStats()

        sat1 = stats.get_or_create_satellite("sat-1")
        sat2 = stats.get_or_create_satellite("sat-2")

        assert sat1 is not sat2
        assert len(stats.satellites) == 2


class TestAddEventDownload:
    """Test suite for add_event with download events."""

    def test_add_download_success_event(self):
        """Test adding successful download event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 1024000,
            "piece_id": "test-piece",
            "location": {"country": "US"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.dl_success == 1
        assert stats.dl_fail == 0
        assert stats.total_dl_size == 1024000
        assert stats.satellites["test-sat"]["downloads"] == 1
        assert stats.satellites["test-sat"]["dl_success"] == 1

    def test_add_download_failed_event(self):
        """Test adding failed download event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "test-piece",
            "location": {"country": "US"},
            "error_reason": "piece not found",
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.dl_success == 0
        assert stats.dl_fail == 1
        assert len(stats.error_agg) > 0


class TestAddEventUpload:
    """Test suite for add_event with upload events."""

    def test_add_upload_success_event(self):
        """Test adding successful upload event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "put",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 2048000,
            "piece_id": "test-piece",
            "location": {"country": "CA"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.ul_success == 1
        assert stats.ul_fail == 0
        assert stats.total_ul_size == 2048000
        assert stats.satellites["test-sat"]["uploads"] == 1
        assert stats.countries_ul["CA"] == 2048000

    def test_add_upload_failed_event(self):
        """Test adding failed upload event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "put",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "test-piece",
            "location": {"country": "CA"},
            "error_reason": "disk full",
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.ul_success == 0
        assert stats.ul_fail == 1


class TestAddEventAudit:
    """Test suite for add_event with audit events."""

    def test_add_audit_success_event(self):
        """Test adding successful audit event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "audit",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 256000,
            "piece_id": "audit-piece",
            "location": {"country": "US"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.audit_success == 1
        assert stats.audit_fail == 0
        assert stats.satellites["test-sat"]["audits"] == 1
        assert stats.satellites["test-sat"]["audit_success"] == 1

    def test_add_audit_failed_event(self):
        """Test adding failed audit event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "audit",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "audit-piece",
            "location": {"country": "US"},
            "error_reason": "verification failed",
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.audit_success == 0
        assert stats.audit_fail == 1


class TestAddEventRepair:
    """Test suite for add_event with repair events."""

    def test_add_get_repair_success(self):
        """Test adding successful GET_REPAIR event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get_repair",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 512000,
            "piece_id": "repair-piece",
            "location": {"country": "DE"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.satellites["test-sat"]["get_repair"] == 1
        assert stats.satellites["test-sat"]["get_repair_success"] == 1
        assert stats.satellites["test-sat"]["total_get_repair_size"] == 512000

    def test_add_put_repair_success(self):
        """Test adding successful PUT_REPAIR event."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "put_repair",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 768000,
            "piece_id": "repair-piece",
            "location": {"country": "FR"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.satellites["test-sat"]["put_repair"] == 1
        assert stats.satellites["test-sat"]["put_repair_success"] == 1
        assert stats.satellites["test-sat"]["total_put_repair_size"] == 768000


class TestHotPiecesTracking:
    """Test suite for hot pieces tracking."""

    def test_hot_pieces_single_access(self):
        """Test hot pieces tracking with single access."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 1024000,
            "piece_id": "hot-piece-1",
            "location": {"country": "US"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert "hot-piece-1" in stats.hot_pieces
        assert stats.hot_pieces["hot-piece-1"]["count"] == 1
        assert stats.hot_pieces["hot-piece-1"]["size"] == 1024000

    def test_hot_pieces_multiple_accesses(self):
        """Test hot pieces tracking with multiple accesses."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Access same piece multiple times
        for i in range(5):
            event = {
                "category": "get",
                "status": "success",
                "satellite_id": "test-sat",
                "size": 1024000,
                "piece_id": "popular-piece",
                "location": {"country": "US"},
                "error_reason": None,
            }
            stats.add_event(event, TOKEN_REGEX)

        assert stats.hot_pieces["popular-piece"]["count"] == 5
        assert stats.hot_pieces["popular-piece"]["size"] == 5 * 1024000


class TestCountryStats:
    """Test suite for country statistics."""

    def test_country_download_stats(self):
        """Test download country statistics."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 1024000,
            "piece_id": "test-piece",
            "location": {"country": "US"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.countries_dl["US"] == 1024000

    def test_country_upload_stats(self):
        """Test upload country statistics."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "put",
            "status": "success",
            "satellite_id": "test-sat",
            "size": 2048000,
            "piece_id": "test-piece",
            "location": {"country": "CA"},
            "error_reason": None,
        }

        stats.add_event(event, TOKEN_REGEX)

        assert stats.countries_ul["CA"] == 2048000

    def test_country_multiple_countries(self):
        """Test statistics with multiple countries."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        countries = ["US", "CA", "DE", "FR", "UK"]

        for country in countries:
            event = {
                "category": "get",
                "status": "success",
                "satellite_id": "test-sat",
                "size": 1024000,
                "piece_id": "test-piece",
                "location": {"country": country},
                "error_reason": None,
            }
            stats.add_event(event, TOKEN_REGEX)

        assert len(stats.countries_dl) == 5
        for country in countries:
            assert stats.countries_dl[country] == 1024000


class TestUpdateLiveStats:
    """Test suite for update_live_stats method."""

    def test_update_live_stats_recent_events(self):
        """Test live stats update with recent events."""
        stats = IncrementalStats()

        now = datetime.datetime.now(datetime.timezone.utc)
        events = [
            {
                "ts_unix": now.timestamp(),
                "status": "success",
                "category": "get",
                "size": 1024000,
            },
            {
                "ts_unix": now.timestamp(),
                "status": "success",
                "category": "put",
                "size": 2048000,
            },
        ]

        stats.update_live_stats(events)

        assert stats.live_dl_bytes == 1024000
        assert stats.live_ul_bytes == 2048000

    def test_update_live_stats_old_events(self):
        """Test live stats ignores old events."""
        stats = IncrementalStats()

        now = datetime.datetime.now(datetime.timezone.utc)
        old_time = (now - datetime.timedelta(minutes=2)).timestamp()

        events = [
            {"ts_unix": old_time, "status": "success", "category": "get", "size": 1024000}
        ]

        stats.update_live_stats(events)

        # Old events should be ignored
        assert stats.live_dl_bytes == 0

    def test_update_live_stats_failed_events(self):
        """Test live stats ignores failed events."""
        stats = IncrementalStats()

        now = datetime.datetime.now(datetime.timezone.utc)
        events = [
            {"ts_unix": now.timestamp(), "status": "failed", "category": "get", "size": 1024000}
        ]

        stats.update_live_stats(events)

        assert stats.live_dl_bytes == 0


class TestToPayload:
    """Test suite for to_payload method."""

    def test_to_payload_basic_structure(self):
        """Test basic payload structure."""
        stats = IncrementalStats()
        payload = stats.to_payload()

        assert payload["type"] == "stats_update"
        assert "first_event_iso" in payload
        assert "last_event_iso" in payload
        assert "overall" in payload
        assert "satellites" in payload
        assert "transfer_sizes" in payload

    def test_to_payload_overall_stats(self):
        """Test overall statistics in payload."""
        stats = IncrementalStats()
        stats.dl_success = 100
        stats.dl_fail = 5
        stats.ul_success = 80
        stats.ul_fail = 3

        payload = stats.to_payload()

        assert payload["overall"]["dl_success"] == 100
        assert payload["overall"]["dl_fail"] == 5
        assert payload["overall"]["ul_success"] == 80
        assert payload["overall"]["ul_fail"] == 3

    def test_to_payload_bandwidth_calculation(self):
        """Test bandwidth calculation in payload."""
        stats = IncrementalStats()
        stats.live_dl_bytes = 6000000  # 6 MB in 1 minute
        stats.live_ul_bytes = 3000000  # 3 MB in 1 minute

        payload = stats.to_payload()

        # (bytes * 8) / (60 seconds * 1e6) = Mbps
        expected_egress = (6000000 * 8) / (60 * 1e6)
        expected_ingress = (3000000 * 8) / (60 * 1e6)

        assert payload["overall"]["avg_egress_mbps"] == pytest.approx(expected_egress, rel=0.01)
        assert payload["overall"]["avg_ingress_mbps"] == pytest.approx(expected_ingress, rel=0.01)

    def test_to_payload_satellites_sorted(self):
        """Test satellites are sorted by activity."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Add events for multiple satellites with different activity levels
        satellites = [("sat-low", 10), ("sat-high", 100), ("sat-medium", 50)]

        for sat_id, count in satellites:
            for _ in range(count):
                event = {
                    "category": "get",
                    "status": "success",
                    "satellite_id": sat_id,
                    "size": 1024,
                    "piece_id": "test",
                    "location": {"country": "US"},
                    "error_reason": None,
                }
                stats.add_event(event, TOKEN_REGEX)

        payload = stats.to_payload()

        # Satellites should be sorted by total activity (descending)
        assert payload["satellites"][0]["satellite_id"] == "sat-high"
        assert payload["satellites"][1]["satellite_id"] == "sat-medium"
        assert payload["satellites"][2]["satellite_id"] == "sat-low"

    def test_to_payload_transfer_sizes(self):
        """Test transfer size buckets in payload."""
        stats = IncrementalStats()

        # Manually set some bucket counts
        stats.dls_success["< 1 KB"] = 5
        stats.dls_success["1-4 KB"] = 10
        stats.uls_success["> 1 MB"] = 3

        payload = stats.to_payload()

        transfer_sizes = {item["bucket"]: item for item in payload["transfer_sizes"]}

        assert transfer_sizes["< 1 KB"]["downloads_success"] == 5
        assert transfer_sizes["1-4 KB"]["downloads_success"] == 10
        assert transfer_sizes["> 1 MB"]["uploads_success"] == 3

    def test_to_payload_with_historical_stats(self):
        """Test payload with historical stats."""
        stats = IncrementalStats()

        historical = [
            {"hour": "2025-01-08T10:00:00Z", "downloads": 100, "uploads": 50},
            {"hour": "2025-01-08T11:00:00Z", "downloads": 120, "uploads": 60},
        ]

        payload = stats.to_payload(historical)

        assert payload["historical_stats"] == historical

    def test_to_payload_error_categories(self):
        """Test error categories in payload."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Add failed events with same error
        for _ in range(5):
            event = {
                "category": "get",
                "status": "failed",
                "satellite_id": "test-sat",
                "size": 0,
                "piece_id": "test",
                "location": {"country": "US"},
                "error_reason": "context canceled",
            }
            stats.add_event(event, TOKEN_REGEX)

        payload = stats.to_payload()

        assert len(payload["error_categories"]) > 0
        assert payload["error_categories"][0]["count"] == 5

    def test_to_payload_top_pieces(self):
        """Test top pieces in payload."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Add multiple accesses to same piece
        for i in range(15):
            event = {
                "category": "get",
                "status": "success",
                "satellite_id": "test-sat",
                "size": 1024,
                "piece_id": "popular-piece",
                "location": {"country": "US"},
                "error_reason": None,
            }
            stats.add_event(event, TOKEN_REGEX)

        payload = stats.to_payload()

        assert len(payload["top_pieces"]) > 0
        assert payload["top_pieces"][0]["id"] == "popular-piece"
        assert payload["top_pieces"][0]["count"] == 15

    def test_to_payload_top_countries(self):
        """Test top countries in payload."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Add events from various countries
        countries = {"US": 5, "CA": 3, "DE": 2}

        for country, count in countries.items():
            for _ in range(count):
                event = {
                    "category": "get",
                    "status": "success",
                    "satellite_id": "test-sat",
                    "size": 1024000,
                    "piece_id": "test",
                    "location": {"country": country},
                    "error_reason": None,
                }
                stats.add_event(event, TOKEN_REGEX)

        payload = stats.to_payload()

        # Check top countries for downloads
        top_dl = payload["top_countries_dl"]
        assert len(top_dl) == 3
        assert top_dl[0]["country"] == "US"  # Most downloads


class TestErrorAggregation:
    """Test suite for error aggregation."""

    def test_aggregate_error_basic(self):
        """Test basic error aggregation."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "test",
            "location": {"country": "US"},
            "error_reason": "piece not found",
        }

        stats.add_event(event, TOKEN_REGEX)

        assert "piece not found" in stats.error_agg
        assert stats.error_agg["piece not found"]["count"] == 1

    def test_aggregate_error_with_ip_address(self):
        """Test error aggregation with IP addresses."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "test",
            "location": {"country": "US"},
            "error_reason": "connection failed to 192.168.1.1:8080",
        }

        stats.add_event(event, TOKEN_REGEX)

        # Should create template with placeholder
        assert len(stats.error_agg) > 0

    def test_aggregate_error_caching(self):
        """Test error template caching."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        # Add same error multiple times
        for _ in range(5):
            event = {
                "category": "get",
                "status": "failed",
                "satellite_id": "test-sat",
                "size": 0,
                "piece_id": "test",
                "location": {"country": "US"},
                "error_reason": "timeout error",
            }
            stats.add_event(event, TOKEN_REGEX)

        # Should be cached
        assert "timeout error" in stats.error_templates_cache
        # Should aggregate count
        assert stats.error_agg["timeout error"]["count"] == 5

    def test_aggregate_error_none_reason(self):
        """Test error aggregation with None reason."""
        stats = IncrementalStats()
        TOKEN_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b")

        event = {
            "category": "get",
            "status": "failed",
            "satellite_id": "test-sat",
            "size": 0,
            "piece_id": "test",
            "location": {"country": "US"},
            "error_reason": None,
        }

        # Should handle None gracefully
        stats.add_event(event, TOKEN_REGEX)
        assert len(stats.error_agg) == 0


class TestAppState:
    """Test suite for app_state structure."""

    def test_app_state_structure(self):
        """Test that app_state has required keys."""
        required_keys = [
            "websockets",
            "nodes",
            "geoip_cache",
            "db_write_lock",
            "db_write_queue",
            "stats_cache",
            "incremental_stats",
            "websocket_event_queue",
            "websocket_queue_lock",
            "TOKEN_REGEX",
            "connection_states",
        ]

        for key in required_keys:
            assert key in app_state, f"Missing required key: {key}"

    def test_app_state_websockets(self):
        """Test websockets state structure."""
        assert isinstance(app_state["websockets"], dict)

    def test_app_state_nodes(self):
        """Test nodes state structure."""
        assert isinstance(app_state["nodes"], dict)

    def test_app_state_token_regex(self):
        """Test TOKEN_REGEX is compiled pattern."""
        assert isinstance(app_state["TOKEN_REGEX"], re.Pattern)

        # Test it matches IP addresses
        match = app_state["TOKEN_REGEX"].search("192.168.1.1:8080")
        assert match is not None

        # Test it matches numbers
        match = app_state["TOKEN_REGEX"].search("Error code 404")
        assert match is not None


class TestIncrementalStatsIntegration:
    """Integration tests for IncrementalStats with real event flow."""

    def test_complete_event_flow(self):
        """Test processing a sequence of mixed events."""
        stats = IncrementalStats()
        TOKEN_REGEX = app_state["TOKEN_REGEX"]

        events = [
            {
                "category": "get",
                "status": "success",
                "satellite_id": "sat-1",
                "size": 1024000,
                "piece_id": "piece-1",
                "location": {"country": "US"},
                "error_reason": None,
            },
            {
                "category": "put",
                "status": "success",
                "satellite_id": "sat-1",
                "size": 2048000,
                "piece_id": "piece-2",
                "location": {"country": "CA"},
                "error_reason": None,
            },
            {
                "category": "audit",
                "status": "success",
                "satellite_id": "sat-1",
                "size": 256000,
                "piece_id": "audit-1",
                "location": {"country": "US"},
                "error_reason": None,
            },
            {
                "category": "get",
                "status": "failed",
                "satellite_id": "sat-1",
                "size": 0,
                "piece_id": "piece-3",
                "location": {"country": "DE"},
                "error_reason": "timeout",
            },
        ]

        for event in events:
            stats.add_event(event, TOKEN_REGEX)

        # Verify all events processed
        assert stats.dl_success == 1
        assert stats.dl_fail == 1
        assert stats.ul_success == 1
        assert stats.audit_success == 1

        # Verify satellite stats
        sat_stats = stats.satellites["sat-1"]
        assert sat_stats["downloads"] == 2  # 1 success + 1 fail
        assert sat_stats["uploads"] == 1
        assert sat_stats["audits"] == 1

    def test_multiple_satellites_stats(self):
        """Test statistics with multiple satellites."""
        stats = IncrementalStats()
        TOKEN_REGEX = app_state["TOKEN_REGEX"]

        satellites = ["sat-1", "sat-2", "sat-3"]

        for sat in satellites:
            for i in range(5):
                event = {
                    "category": "get" if i % 2 == 0 else "put",
                    "status": "success",
                    "satellite_id": sat,
                    "size": 1024000,
                    "piece_id": f"piece-{i}",
                    "location": {"country": "US"},
                    "error_reason": None,
                }
                stats.add_event(event, TOKEN_REGEX)

        assert len(stats.satellites) == 3

        payload = stats.to_payload()
        assert len(payload["satellites"]) == 3

    def test_payload_generation_performance(self):
        """Test payload generation with many events."""
        stats = IncrementalStats()
        TOKEN_REGEX = app_state["TOKEN_REGEX"]

        # Add 100 events
        for i in range(100):
            event = {
                "category": "get" if i % 3 == 0 else "put",
                "status": "success" if i % 10 != 0 else "failed",
                "satellite_id": f"sat-{i % 5}",
                "size": 1024 * (i + 1),
                "piece_id": f"piece-{i}",
                "location": {"country": ["US", "CA", "DE"][i % 3]},
                "error_reason": "error" if i % 10 == 0 else None,
            }
            stats.add_event(event, TOKEN_REGEX)

        # Generate payload (should not take too long)
        start_time = time.time()
        payload = stats.to_payload()
        elapsed = time.time() - start_time

        assert elapsed < 1.0  # Should be fast
        assert payload["type"] == "stats_update"
        assert len(payload["satellites"]) == 5
