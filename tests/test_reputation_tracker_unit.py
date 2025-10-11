import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.reputation_tracker import (
    track_reputation,
    calculate_reputation_health_score,
    _get_satellite_name,
)


@pytest.mark.asyncio
async def test_track_reputation_list_format_generates_records_and_alerts():
    class DummyAPI:
        async def get_satellites(self):
            return [
                {
                    "id": "sat-1234567890",
                    "audit": {"score": 0.84, "successCount": 84, "totalCount": 100},
                    "suspension": {"score": 1.0},
                    "online": {"score": 1.0},
                    "disqualified": None,
                    "suspended": None,
                }
            ]

    app = {"db_executor": object()}

    with patch("storj_monitor.database.blocking_write_reputation_history", return_value=True):
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            result = await track_reputation(app, "test-node", DummyAPI())

    assert result is not None
    assert result["node_name"] == "test-node"
    assert len(result["reputation_records"]) == 1
    # Below-warning audit score should produce at least one alert (warning/critical depends on config)
    assert len(result["alerts"]) >= 1


@pytest.mark.asyncio
async def test_track_reputation_dict_format_supported():
    class DummyAPI:
        async def get_satellites(self):
            return {
                "group1": [
                    {
                        "id": "sat-abcdef123456",
                        "audit": {"score": 0.99, "successCount": 99, "totalCount": 100},
                        "suspension": {"score": 1.0},
                        "online": {"score": 1.0},
                        "disqualified": None,
                        "suspended": None,
                    }
                ]
            }

    app = {"db_executor": object()}

    with patch("storj_monitor.database.blocking_write_reputation_history", return_value=True):
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            result = await track_reputation(app, "test-node", DummyAPI())

    assert result is not None
    assert len(result["reputation_records"]) == 1
    # High scores -> no alerts expected
    assert len(result["alerts"]) == 0


def test_calculate_reputation_health_score_and_satellite_name_fallback():
    # Weighted: 0.4*80 + 0.3*90 + 0.3*100 = 89.0
    score = calculate_reputation_health_score(
        {"audit_score": 80, "suspension_score": 90, "online_score": 100}
    )
    assert score == 89.0

    # Fallback name should be first 12 chars + "..."
    assert _get_satellite_name("abc1234567890") == "abc123456789..."