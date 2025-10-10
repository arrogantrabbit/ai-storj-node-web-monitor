"""
Comprehensive unit tests for storage_tracker.py

Tests storage tracking, growth rate calculation, capacity forecasting,
and alert generation.
"""

import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from storj_monitor.storage_tracker import _format_bytes, calculate_storage_forecast, track_storage


class TestTrackStorage:
    """Test suite for track_storage function."""

    @pytest.mark.asyncio
    async def test_track_storage_success(self):
        """Test successful storage tracking."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        # Mock API client
        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "nodeID": "test-node-id",
                "diskSpace": {
                    "used": 5368709120,  # ~5 GB
                    "available": 53687091200,  # ~50 GB (total allocated)
                    "trash": 104857600,  # ~100 MB
                },
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch(
                    "storj_monitor.storage_tracker.calculate_storage_forecast", return_value=None
                ):
                    result = await track_storage(app, node_name, api_client)

        assert result is not None
        assert result["node_name"] == node_name
        assert result["snapshot"]["total_bytes"] == 53687091200
        assert result["snapshot"]["used_bytes"] == 5368709120
        assert result["snapshot"]["trash_bytes"] == 104857600
        assert len(result["alerts"]) == 0

    @pytest.mark.asyncio
    async def test_track_storage_no_dashboard_data(self):
        """Test storage tracking when dashboard returns None."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(return_value=None)

        result = await track_storage(app, node_name, api_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_storage_invalid_disk_space_format(self):
        """Test storage tracking with invalid diskSpace format."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": "invalid"  # Should be dict
            }
        )

        result = await track_storage(app, node_name, api_client)
        assert result is None

    @pytest.mark.asyncio
    async def test_track_storage_percentage_calculations(self):
        """Test storage percentage calculations."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {
                    "used": 8000000000,  # 8 GB
                    "available": 10000000000,  # 10 GB total
                    "trash": 1000000000,  # 1 GB
                }
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with patch(
                    "storj_monitor.storage_tracker.calculate_storage_forecast", return_value=None
                ):
                    result = await track_storage(app, node_name, api_client)

        assert result is not None
        snapshot = result["snapshot"]
        assert snapshot["used_percent"] == 80.0  # 8/10 * 100
        assert snapshot["trash_percent"] == 10.0  # 1/10 * 100
        assert snapshot["available_percent"] == 20.0  # (10-8)/10 * 100

    @pytest.mark.asyncio
    async def test_track_storage_warning_alert(self):
        """Test warning alert for high disk usage."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {
                    "used": 8500000000,  # 85%
                    "available": 10000000000,
                    "trash": 100000000,
                }
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with (
                    patch(
                        "storj_monitor.storage_tracker.calculate_storage_forecast",
                        return_value=None,
                    ),
                    patch("storj_monitor.storage_tracker.STORAGE_WARNING_PERCENT", 80.0),
                ):
                    with patch("storj_monitor.storage_tracker.STORAGE_CRITICAL_PERCENT", 90.0):
                        result = await track_storage(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "warning" and "High Disk Usage" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_storage_critical_alert(self):
        """Test critical alert for very high disk usage."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {
                    "used": 9500000000,  # 95%
                    "available": 10000000000,
                    "trash": 100000000,
                }
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with (
                    patch(
                        "storj_monitor.storage_tracker.calculate_storage_forecast",
                        return_value=None,
                    ),
                    patch("storj_monitor.storage_tracker.STORAGE_CRITICAL_PERCENT", 90.0),
                ):
                    result = await track_storage(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Critical Disk Usage" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_storage_forecast_warning_alert(self):
        """Test warning alert for forecast predicting disk full soon."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {"used": 5000000000, "available": 10000000000, "trash": 100000000}
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        forecast_data = {"days_until_full": 25.0, "growth_rate_gb_per_day": 0.5}

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with (
                    patch(
                        "storj_monitor.storage_tracker.calculate_storage_forecast",
                        return_value=forecast_data,
                    ),
                    patch("storj_monitor.storage_tracker.STORAGE_FORECAST_WARNING_DAYS", 30),
                    patch("storj_monitor.storage_tracker.STORAGE_FORECAST_CRITICAL_DAYS", 7),
                ):
                    result = await track_storage(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "warning" and "Capacity Warning" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_storage_forecast_critical_alert(self):
        """Test critical alert for forecast predicting disk full very soon."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {"used": 9000000000, "available": 10000000000, "trash": 100000000}
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        forecast_data = {"days_until_full": 5.0, "growth_rate_gb_per_day": 0.2}

        with patch("storj_monitor.database.blocking_write_storage_snapshot"):
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
                with (
                    patch(
                        "storj_monitor.storage_tracker.calculate_storage_forecast",
                        return_value=forecast_data,
                    ),
                    patch("storj_monitor.storage_tracker.STORAGE_FORECAST_CRITICAL_DAYS", 7),
                ):
                    result = await track_storage(app, node_name, api_client)

        assert result is not None
        assert len(result["alerts"]) >= 1
        assert any(
            alert["severity"] == "critical" and "Full Soon" in alert["title"]
            for alert in result["alerts"]
        )

    @pytest.mark.asyncio
    async def test_track_storage_database_storage(self):
        """Test that storage data is stored in database."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(
            return_value={
                "diskSpace": {"used": 5000000000, "available": 10000000000, "trash": 100000000}
            }
        )
        api_client.get_satellites = AsyncMock(return_value={})

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            with patch(
                "storj_monitor.storage_tracker.calculate_storage_forecast", return_value=None
            ):
                result = await track_storage(app, node_name, api_client)

        # Verify run_in_executor was called
        assert mock_loop.return_value.run_in_executor.called
        # Check that snapshot was created
        assert result is not None
        assert result["snapshot"]["node_name"] == node_name

    @pytest.mark.asyncio
    async def test_track_storage_exception_handling(self):
        """Test exception handling in track_storage."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        api_client = AsyncMock()
        api_client.get_dashboard = AsyncMock(side_effect=Exception("API error"))

        result = await track_storage(app, node_name, api_client)
        assert result is None


class TestCalculateStorageForecast:
    """Test suite for calculate_storage_forecast function."""

    @pytest.mark.asyncio
    async def test_calculate_forecast_insufficient_data(self):
        """Test forecast calculation with insufficient data."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=[])
            result = await calculate_storage_forecast(app, node_name, 1000000000)

        assert result is None

    @pytest.mark.asyncio
    async def test_calculate_forecast_no_valid_data(self):
        """Test forecast calculation with no valid used_bytes data."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        history = [
            {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "used_bytes": None,  # From log-based snapshot
            },
            {
                "timestamp": (
                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
                ).isoformat(),
                "used_bytes": None,
            },
        ]

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=history)
            result = await calculate_storage_forecast(app, node_name, 1000000000)

        assert result is None

    @pytest.mark.asyncio
    async def test_calculate_forecast_success(self):
        """Test successful forecast calculation."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        now = datetime.datetime.now(datetime.timezone.utc)
        history = []

        # Generate 8 days of history with linear growth
        for i in range(8):
            history.append(
                {
                    "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
                    "used_bytes": 5000000000 - (i * 100000000),  # Growing by ~100MB/day
                }
            )

        current_available = 5000000000  # 5 GB free

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=history)
            result = await calculate_storage_forecast(app, node_name, current_available)

        assert result is not None
        assert "growth_rate_bytes_per_day" in result
        assert "growth_rate_gb_per_day" in result
        assert "days_until_full" in result
        assert "growth_rates" in result

    @pytest.mark.asyncio
    async def test_calculate_forecast_negative_growth(self):
        """Test forecast calculation with negative growth (shrinking)."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        now = datetime.datetime.now(datetime.timezone.utc)
        history = []

        # Generate history with shrinking usage
        for i in range(8):
            history.append(
                {
                    "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
                    "used_bytes": 5000000000 + (i * 100000000),  # Shrinking
                }
            )

        current_available = 5000000000

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=history)
            result = await calculate_storage_forecast(app, node_name, current_available)

        assert result is not None
        # With negative/zero growth, days_until_full should be None
        assert result["days_until_full"] is None
        assert result["growth_rate_bytes_per_day"] == 0

    @pytest.mark.asyncio
    async def test_calculate_forecast_multiple_time_windows(self):
        """Test forecast calculation includes multiple time windows."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        now = datetime.datetime.now(datetime.timezone.utc)
        history = []

        # Generate 31 days of history
        for i in range(31):
            history.append(
                {
                    "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
                    "used_bytes": 5000000000 + (i * 100000000),
                }
            )

        current_available = 5000000000

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=history)
            result = await calculate_storage_forecast(app, node_name, current_available)

        assert result is not None
        assert "growth_rates" in result
        assert "1d" in result["growth_rates"]
        assert "7d" in result["growth_rates"]
        assert "30d" in result["growth_rates"]

    @pytest.mark.asyncio
    async def test_calculate_forecast_uses_7d_primary(self):
        """Test that 7-day window is used as primary forecast."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        now = datetime.datetime.now(datetime.timezone.utc)
        history = []

        # Generate 8 days of history
        for i in range(8):
            history.append(
                {
                    "timestamp": (now - datetime.timedelta(days=i)).isoformat(),
                    "used_bytes": 5000000000 - (i * 100000000),
                }
            )

        current_available = 5000000000

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=history)
            result = await calculate_storage_forecast(app, node_name, current_available)

        assert result is not None
        # Primary rate should match 7d rate
        assert (
            result["growth_rate_bytes_per_day"]
            == result["growth_rates"]["7d"]["growth_rate_bytes_per_day"]
        )

    @pytest.mark.asyncio
    async def test_calculate_forecast_exception_handling(self):
        """Test exception handling in forecast calculation."""
        app = {"db_executor": Mock()}
        node_name = "Test-Node"

        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=Exception("Database error")
            )
            result = await calculate_storage_forecast(app, node_name, 1000000000)

        assert result is None


class TestFormatBytes:
    """Test suite for _format_bytes helper function."""

    def test_format_bytes_bytes(self):
        """Test formatting bytes."""
        assert _format_bytes(100) == "100.00 B"
        assert _format_bytes(512) == "512.00 B"

    def test_format_bytes_kilobytes(self):
        """Test formatting kilobytes."""
        assert _format_bytes(1024) == "1.00 KB"
        assert _format_bytes(2048) == "2.00 KB"

    def test_format_bytes_megabytes(self):
        """Test formatting megabytes."""
        assert _format_bytes(1048576) == "1.00 MB"
        assert _format_bytes(10485760) == "10.00 MB"

    def test_format_bytes_gigabytes(self):
        """Test formatting gigabytes."""
        assert _format_bytes(1073741824) == "1.00 GB"
        assert _format_bytes(5368709120) == "5.00 GB"

    def test_format_bytes_terabytes(self):
        """Test formatting terabytes."""
        assert _format_bytes(1099511627776) == "1.00 TB"
        assert _format_bytes(2199023255552) == "2.00 TB"

    def test_format_bytes_petabytes(self):
        """Test formatting petabytes."""
        assert _format_bytes(1125899906842624) == "1.00 PB"

    def test_format_bytes_zero(self):
        """Test formatting zero bytes."""
        assert _format_bytes(0) == "0.00 B"

    def test_format_bytes_decimal(self):
        """Test formatting with decimal values."""
        assert _format_bytes(1536) == "1.50 KB"  # 1.5 KB
        assert _format_bytes(2621440) == "2.50 MB"  # 2.5 MB
