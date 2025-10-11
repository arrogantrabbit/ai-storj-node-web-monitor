import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from storj_monitor.alert_manager import AlertManager
from storj_monitor.state import IncrementalStats


@pytest.mark.asyncio
async def test_storage_alerts_computed_percent_warning_when_used_percent_missing():
    # Arrange: used_percent missing but used_bytes/total_bytes present -> compute percent path
    app = {"db_executor": object()}
    am = AlertManager(app, analytics_engine=None, anomaly_detector=None)

    storage_data = {
        "used_percent": None,
        "used_bytes": 85_000_000,
        "total_bytes": 100_000_000,  # 85%
    }

    with patch.object(am, "generate_alert", new=AsyncMock()) as mock_gen:
        # Act
        await am.evaluate_storage_alerts("node-z", storage_data)

        # Assert: warning alert generated (default warning threshold = 80%)
        assert mock_gen.called
        args = mock_gen.call_args_list[0][0]
        assert args[1] == "storage_warning"
        assert args[2] == "warning"


@pytest.mark.asyncio
async def test_storage_alerts_skips_when_insufficient_data_for_computation():
    # Arrange: missing used_percent and no computable used/total -> path should early-return without alerts
    app = {"db_executor": object()}
    am = AlertManager(app, analytics_engine=None, anomaly_detector=None)

    storage_data = {
        "used_percent": None,
        # No used_bytes / total_bytes provided (or invalid)
    }

    with patch.object(am, "generate_alert", new=AsyncMock()) as mock_gen:
        # Act
        await am.evaluate_storage_alerts("node-z", storage_data)

        # Assert: no alert generated
        assert mock_gen.called is False


def test_update_live_stats_counts_recent_events_only():
    stats = IncrementalStats()

    now = time.time()
    # Recent successful GET and PUT should be counted
    recent_get = {
        "ts_unix": now - 5,
        "status": "success",
        "category": "get",
        "size": 1024,
    }
    recent_put = {
        "ts_unix": now - 10,
        "status": "success",
        "category": "put",
        "size": 2048,
    }
    # Old event beyond 60s should be ignored
    old_event = {
        "ts_unix": now - 120,
        "status": "success",
        "category": "get",
        "size": 999999,
    }
    stats.update_live_stats([recent_get, recent_put, old_event])

    assert stats.live_dl_bytes == 1024
    assert stats.live_ul_bytes == 2048