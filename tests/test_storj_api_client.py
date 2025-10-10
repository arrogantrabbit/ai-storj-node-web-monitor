"""
Comprehensive unit tests for storj_api_client.py

Tests API client initialization, all API methods, error handling, and timeouts.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from storj_monitor.storj_api_client import (
    StorjNodeAPIClient,
    _is_localhost,
    auto_discover_api_endpoint,
    cleanup_api_clients,
    setup_api_client,
)


class TestStorjNodeAPIClient:
    """Test suite for StorjNodeAPIClient class."""

    @pytest.mark.asyncio
    async def test_api_client_initialization(self):
        """Test API client initializes with correct parameters."""
        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002", timeout=10)

        assert client.node_name == "Test-Node"
        assert client.api_endpoint == "http://localhost:14002"
        assert client.timeout == 10
        assert client.session is None
        assert client.is_available is False
        assert client._last_error is None

    @pytest.mark.asyncio
    async def test_api_client_strips_trailing_slash(self):
        """Test API endpoint strips trailing slash."""
        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002/")
        assert client.api_endpoint == "http://localhost:14002"

    @pytest.mark.asyncio
    async def test_start_success(self):
        """Test successful API client start."""
        # Mock successful dashboard response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "nodeID": "test-node-id-12345",
                "wallet": "0x1234567890abcdef",
                "version": "v1.95.1",
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
            await client.start()

            assert client.is_available is True
            assert client.session is not None

    @pytest.mark.asyncio
    async def test_start_connection_failure(self):
        """Test API client start with connection failure."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            # Create mock session that will be returned by ClientSession()
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_session.close = AsyncMock()

            # Mock context manager that raises error on __aenter__
            mock_context = AsyncMock()
            mock_context.__aenter__.side_effect = aiohttp.ClientError("Connection refused")
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.get.return_value = mock_context

            client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
            await client.start()

            # When _get catches the exception, it returns None, which causes start() to mark as unavailable
            # but _last_error may not be set if the exception is caught in _get
            assert client.is_available is False
            # The client should have attempted to connect
            assert mock_session.get.called

    @pytest.mark.asyncio
    async def test_start_invalid_response_format(self):
        """Test API client start with invalid response format."""
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session

            # Mock response without nodeID
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"invalid": "data"})

            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_response
            mock_session.get.return_value = mock_context

            client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
            await client.start()

            assert client.is_available is False

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test API client cleanup."""
        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = AsyncMock()

        await client.stop()

        assert client.session.close.called

    @pytest.mark.asyncio
    async def test_get_dashboard_success(self):
        """Test successful dashboard retrieval."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "nodeID": "test-node-id",
                "diskSpace": {"used": 5368709120, "available": 53687091200, "trash": 104857600},
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()

        assert result is not None
        assert "nodeID" in result
        assert "diskSpace" in result
        assert result["diskSpace"]["used"] == 5368709120

    @pytest.mark.asyncio
    async def test_get_dashboard_no_session(self):
        """Test dashboard retrieval without active session."""
        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_dashboard_connection_error(self):
        """Test dashboard retrieval with connection error."""
        mock_session = AsyncMock()
        mock_session.get.side_effect = aiohttp.ClientError("Connection failed")

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_dashboard_timeout(self):
        """Test dashboard retrieval with timeout."""
        mock_session = AsyncMock()
        mock_session.get.side_effect = asyncio.TimeoutError()

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_dashboard_http_error(self):
        """Test dashboard retrieval with HTTP error status."""
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_response
        mock_session.get.return_value = mock_context

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_dashboard_invalid_json(self):
        """Test dashboard retrieval with invalid JSON response."""
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(side_effect=ValueError("Invalid JSON"))

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_response
        mock_session.get.return_value = mock_context

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_dashboard_null_response(self):
        """Test dashboard retrieval with null JSON response."""
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=None)

        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_response
        mock_session.get.return_value = mock_context

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_dashboard()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_satellites_success(self):
        """Test successful satellites retrieval."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "satellite-id-1": [
                    {
                        "id": "satellite-id-1",
                        "url": "us1.storj.io:7777",
                        "audit": {"score": 1.0},
                        "suspension": {"score": 1.0},
                        "online": {"score": 1.0},
                    }
                ]
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_satellites()

        assert result is not None
        assert "satellite-id-1" in result

    @pytest.mark.asyncio
    async def test_get_satellite_info_success(self):
        """Test successful satellite info retrieval."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "id": "satellite-id-1",
                "url": "us1.storj.io:7777",
                "audit": {"score": 1.0, "successCount": 100, "totalCount": 100},
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_satellite_info("satellite-id-1")

        assert result is not None
        assert result["id"] == "satellite-id-1"

    @pytest.mark.asyncio
    async def test_get_estimated_payout_success(self):
        """Test successful estimated payout retrieval."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "currentMonth": {
                    "egressBandwidth": 10737418240,
                    "egressBandwidthPayout": 20.00,
                    "diskSpace": 107374182400,
                    "diskSpacePayout": 1.50,
                }
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_estimated_payout()

        assert result is not None
        assert "currentMonth" in result
        assert result["currentMonth"]["egressBandwidthPayout"] == 20.00

    @pytest.mark.asyncio
    async def test_get_payout_paystubs_success(self):
        """Test successful payout paystubs retrieval."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value=[{"satelliteID": "sat-1", "period": "2025-10", "paid": 28112100}]
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        result = await client.get_payout_paystubs("2025-10")

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["paid"] == 28112100

    @pytest.mark.asyncio
    async def test_get_adds_trailing_slash(self):
        """Test _get method adds trailing slash to path."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"data": "test"})

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
            )
        )

        client = StorjNodeAPIClient("Test-Node", "http://localhost:14002")
        client.session = mock_session

        await client._get("/api/test")

        # Verify the URL had trailing slash
        call_args = mock_session.get.call_args
        assert call_args[0][0] == "http://localhost:14002/api/test/"


class TestAutoDiscoveryFunctions:
    """Test suite for API endpoint auto-discovery functions."""

    @pytest.mark.asyncio
    async def test_auto_discover_with_explicit_endpoint(self):
        """Test auto-discovery with explicit API endpoint."""
        node_config = {
            "name": "Test-Node",
            "type": "file",
            "api_endpoint": "http://localhost:14002",
        }

        result = await auto_discover_api_endpoint(node_config)
        assert result == "http://localhost:14002"

    @pytest.mark.asyncio
    async def test_auto_discover_local_file_success(self):
        """Test auto-discovery for local file node."""
        node_config = {"name": "Test-Node", "type": "file"}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"nodeID": "test-id"})

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_class.return_value.__aexit__ = AsyncMock()

            mock_session.get = MagicMock(
                return_value=MagicMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )

            result = await auto_discover_api_endpoint(node_config)
            assert result == "http://localhost:14002"

    @pytest.mark.asyncio
    async def test_auto_discover_local_file_failure(self):
        """Test auto-discovery failure for local file node."""
        node_config = {"name": "Test-Node", "type": "file"}

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_class.return_value.__aexit__ = AsyncMock()

            mock_session.get = MagicMock(side_effect=aiohttp.ClientError())

            result = await auto_discover_api_endpoint(node_config)
            assert result is None

    @pytest.mark.asyncio
    @patch("storj_monitor.storj_api_client.ALLOW_REMOTE_API", True)
    async def test_auto_discover_remote_network_success(self):
        """Test auto-discovery for remote network node."""
        node_config = {"name": "Test-Node", "type": "network", "host": "192.168.1.100"}

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"nodeID": "test-id"})

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_class.return_value.__aexit__ = AsyncMock()

            mock_session.get = MagicMock(
                return_value=MagicMock(
                    __aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()
                )
            )

            result = await auto_discover_api_endpoint(node_config)
            assert result == "http://192.168.1.100:14002"

    @pytest.mark.asyncio
    @patch("storj_monitor.storj_api_client.ALLOW_REMOTE_API", False)
    async def test_auto_discover_remote_disabled(self):
        """Test auto-discovery when remote API is disabled."""
        node_config = {"name": "Test-Node", "type": "network", "host": "192.168.1.100"}

        result = await auto_discover_api_endpoint(node_config)
        assert result is None

    def test_is_localhost_true(self):
        """Test _is_localhost for valid localhost addresses."""
        assert _is_localhost("localhost") is True
        assert _is_localhost("127.0.0.1") is True
        assert _is_localhost("::1") is True
        assert _is_localhost("0.0.0.0") is True

    def test_is_localhost_false(self):
        """Test _is_localhost for non-localhost addresses."""
        assert _is_localhost("192.168.1.100") is False
        assert _is_localhost("example.com") is False


class TestSetupAndCleanup:
    """Test suite for setup and cleanup functions."""

    @pytest.mark.asyncio
    async def test_setup_api_client_success(self):
        """Test successful API client setup."""
        app = {}

        with patch("storj_monitor.storj_api_client.StorjNodeAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.is_available = True
            mock_client.start = AsyncMock()
            mock_client_class.return_value = mock_client

            result = await setup_api_client(app, "Test-Node", "http://localhost:14002")

            assert result is not None
            assert result.is_available is True
            assert "api_clients" in app
            assert "Test-Node" in app["api_clients"]

    @pytest.mark.asyncio
    async def test_setup_api_client_failure(self):
        """Test API client setup failure."""
        app = {}

        with patch("storj_monitor.storj_api_client.StorjNodeAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.is_available = False
            mock_client._last_error = "Connection refused"
            mock_client.start = AsyncMock()
            mock_client_class.return_value = mock_client

            result = await setup_api_client(app, "Test-Node", "http://localhost:14002")

            assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_api_clients(self):
        """Test API clients cleanup."""
        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()

        app = {"api_clients": {"node1": mock_client1, "node2": mock_client2}}

        await cleanup_api_clients(app)

        assert mock_client1.stop.called
        assert mock_client2.stop.called

    @pytest.mark.asyncio
    async def test_cleanup_api_clients_empty(self):
        """Test cleanup with no API clients."""
        app = {}

        # Should not raise any errors
        await cleanup_api_clients(app)
