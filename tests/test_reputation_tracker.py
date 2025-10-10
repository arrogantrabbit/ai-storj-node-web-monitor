"""
Comprehensive unit tests for reputation_tracker.py

Tests reputation tracking, score extraction, alert generation, and database storage.
"""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from storj_monitor.reputation_tracker import (
    _get_satellite_name,
    calculate_reputation_health_score,
    get_reputation_summary,
    track_reputation,
)


class TestTrackReputation:
    """Test suite for track_reputation function."""

    @pytest.mark.asyncio
    async def test_track_reputation_success_dict_format(self):
        """Test successful reputation tracking with dict format response."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        # Mock API client
        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value={
                "satellite-1": [
                    {
                        "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                        "url": "us1.storj.io:7777",
                        "disqualified": None,
                        "suspended": None,
                        "audit": {"score": 1.0, "successCount": 1000, "totalCount": 1000},
                        "suspension": {"score": 1.0},
                        "online": {"score": 1.0},
                    }
                ]
            }
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert result["node_name"] == node_name
        assert len(result["reputation_records"]) == 1
        assert result["reputation_records"][0]["audit_score"] == 100.0
        assert result["reputation_records"][0]["suspension_score"] == 100.0
        assert result["reputation_records"][0]["online_score"] == 100.0
        assert len(result["alerts"]) == 0

    @pytest.mark.asyncio
    async def test_track_reputation_success_list_format(self):
        """Test successful reputation tracking with list format response."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 0.95, "successCount": 950, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 0.98},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["reputation_records"]) == 1
        assert result["reputation_records"][0]["audit_score"] == 95.0
        assert result["reputation_records"][0]["online_score"] == 98.0

    @pytest.mark.asyncio
    async def test_track_reputation_no_data(self):
        """Test reputation tracking when API returns None."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(return_value=None)

        result = await track_reputation(app, node_name, api_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_reputation_empty_satellites(self):
        """Test reputation tracking with empty satellites list."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(return_value=[])

        result = await track_reputation(app, node_name, api_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_reputation_disqualified_alert(self):
        """Test alert generation for disqualified node."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "suspended": None,
                    "audit": {"score": 0.5, "successCount": 500, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Disqualified" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_suspended_alert(self):
        """Test alert generation for suspended node."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "audit": {"score": 0.8, "successCount": 800, "totalCount": 1000},
                    "suspension": {"score": 0.9},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Suspended" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_low_audit_score_warning(self):
        """Test warning alert for low audit score."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 0.97, "successCount": 970, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch("storj_monitor.reputation_tracker.AUDIT_SCORE_WARNING", 98.0):
                    result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "warning" and "Low Audit Score" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_critical_audit_score(self):
        """Test critical alert for very low audit score."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 0.95, "successCount": 950, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch("storj_monitor.reputation_tracker.AUDIT_SCORE_CRITICAL", 96.0):
                    result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Critical Audit Score" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_critical_suspension_score(self):
        """Test critical alert for low suspension score."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 1.0, "successCount": 1000, "totalCount": 1000},
                    "suspension": {"score": 0.95},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch("storj_monitor.reputation_tracker.SUSPENSION_SCORE_CRITICAL", 96.0):
                    result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Critical Suspension Score" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_low_online_score_warning(self):
        """Test warning alert for low online score."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 1.0, "successCount": 1000, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 0.95},
                }
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch("storj_monitor.reputation_tracker.ONLINE_SCORE_WARNING", 96.0):
                    result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "warning" and "Low Online Score" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_reputation_multiple_satellites(self):
        """Test reputation tracking with multiple satellites."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "sat-1",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 1.0, "successCount": 1000, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                },
                {
                    "id": "sat-2",
                    "url": "eu1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 0.98, "successCount": 980, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 0.99},
                },
            ]
        )

        with patch("storj_monitor.database.blocking_write_reputation_history"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                result = await track_reputation(app, node_name, api_client)

        assert result is not None
        assert len(result["reputation_records"]) == 2

    @pytest.mark.asyncio
    async def test_track_reputation_database_storage(self):
        """Test that reputation data is stored in database."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(
            return_value=[
                {
                    "id": "sat-1",
                    "url": "us1.storj.io:7777",
                    "disqualified": None,
                    "suspended": None,
                    "audit": {"score": 1.0, "successCount": 1000, "totalCount": 1000},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                }
            ]
        )

        with patch("asyncio.get_running_loop") as mock_loop:
            Mock()
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            result = await track_reputation(app, node_name, api_client)

        # Verify run_in_executor was called with the write function
        assert mock_loop.return_value.run_in_executor.called
        # Check that reputation records were created
        assert result is not None
        assert len(result["reputation_records"]) == 1
        assert result["reputation_records"][0]["node_name"] == node_name

    @pytest.mark.asyncio
    async def test_track_reputation_exception_handling(self):
        """Test exception handling in track_reputation."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(side_effect=Exception("API error"))

        result = await track_reputation(app, node_name, api_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_reputation_invalid_data_format(self):
        """Test handling of invalid satellite data format."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_satellites = AsyncMock(return_value="invalid")

        result = await track_reputation(app, node_name, api_client)
        assert result is None


class TestCalculateHealthScore:
    """Test suite for calculate_reputation_health_score function."""

    def test_calculate_health_score_perfect(self):
        """Test health score calculation with perfect scores."""
        reputation_data = {"audit_score": 100, "suspension_score": 100, "online_score": 100}

        score = calculate_reputation_health_score(reputation_data)
        assert score == 100.0

    def test_calculate_health_score_mixed(self):
        """Test health score calculation with mixed scores."""
        reputation_data = {"audit_score": 95, "suspension_score": 98, "online_score": 97}

        score = calculate_reputation_health_score(reputation_data)
        # (95 * 0.4) + (98 * 0.3) + (97 * 0.3) = 38 + 29.4 + 29.1 = 96.5
        assert score == 96.5

    def test_calculate_health_score_low(self):
        """Test health score calculation with low scores."""
        reputation_data = {"audit_score": 85, "suspension_score": 90, "online_score": 88}

        score = calculate_reputation_health_score(reputation_data)
        # (85 * 0.4) + (90 * 0.3) + (88 * 0.3) = 34 + 27 + 26.4 = 87.4
        assert score == 87.4

    def test_calculate_health_score_missing_data(self):
        """Test health score calculation with missing data."""
        reputation_data = {}

        score = calculate_reputation_health_score(reputation_data)
        # Defaults to 100 for missing values
        assert score == 100.0

    def test_calculate_health_score_partial_data(self):
        """Test health score calculation with partial data."""
        reputation_data = {"audit_score": 90}

        score = calculate_reputation_health_score(reputation_data)
        # (90 * 0.4) + (100 * 0.3) + (100 * 0.3) = 36 + 30 + 30 = 96
        assert score == 96.0


class TestGetReputationSummary:
    """Test suite for get_reputation_summary function."""

    @pytest.mark.asyncio
    async def test_get_reputation_summary_success(self):
        """Test successful reputation summary retrieval."""
        app = {"db_executor": Mock(), "api_clients": {"node1": Mock(), "node2": Mock()}}

        mock_summaries = [
            {
                "node_name": "node1",
                "satellite": "sat-1",
                "audit_score": 100,
                "suspension_score": 100,
                "online_score": 100,
            }
        ]

        with (
            patch(
                "storj_monitor.database.blocking_get_latest_reputation", return_value=mock_summaries
            ),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_summaries)
            result = await get_reputation_summary(app)

        assert len(result) == 1
        assert "health_score" in result[0]
        assert result[0]["health_score"] == 100.0

    @pytest.mark.asyncio
    async def test_get_reputation_summary_specific_nodes(self):
        """Test reputation summary for specific nodes."""
        app = {"db_executor": Mock()}
        node_names = ["node1", "node2"]

        mock_summaries = [
            {
                "node_name": "node1",
                "satellite": "sat-1",
                "audit_score": 98,
                "suspension_score": 99,
                "online_score": 97,
            }
        ]

        with (
            patch(
                "storj_monitor.database.blocking_get_latest_reputation", return_value=mock_summaries
            ),
            patch("asyncio.get_running_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_summaries)
            result = await get_reputation_summary(app, node_names)

        assert len(result) == 1
        assert result[0]["node_name"] == "node1"
        assert "health_score" in result[0]


class TestGetSatelliteName:
    """Test suite for _get_satellite_name helper function."""

    def test_get_satellite_name_known(self):
        """Test getting name for known satellite."""
        with patch(
            "storj_monitor.config.SATELLITE_NAMES",
            {"12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S": "US1"},
        ):
            name = _get_satellite_name("12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S")
            assert name == "US1"

    def test_get_satellite_name_unknown(self):
        """Test getting name for unknown satellite."""
        with patch("storj_monitor.config.SATELLITE_NAMES", {}):
            sat_id = "unknown-satellite-id-12345"
            name = _get_satellite_name(sat_id)
            assert name == "unknown-sate..."

    def test_get_satellite_name_short_id(self):
        """Test getting name for short satellite ID."""
        with patch("storj_monitor.config.SATELLITE_NAMES", {}):
            sat_id = "short"
            name = _get_satellite_name(sat_id)
            assert "..." in name
