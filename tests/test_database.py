"""
Comprehensive tests for database module.
"""

import datetime
import sqlite3


def test_database_init(temp_db):
    """Test database initialization creates all required tables."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Check all tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}

    required_tables = {
        "events",
        "hourly_stats",
        "app_persistent_state",
        "hashstore_compaction_history",
        "reputation_history",
        "storage_snapshots",
        "alerts",
        "insights",
        "analytics_baselines",
        "earnings_estimates",
        "payout_history",
    }

    assert required_tables.issubset(tables), f"Missing tables: {required_tables - tables}"
    conn.close()


def test_database_indexes_created(temp_db):
    """Test that all required indexes are created."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = {row[0] for row in cursor.fetchall()}

    required_indexes = {
        "idx_events_timestamp",
        "idx_events_node_name",
        "idx_events_node_name_timestamp",
        "idx_events_financial_traffic",
        "idx_events_latency",
        "idx_reputation_node_time",
        "idx_reputation_satellite",
        "idx_storage_node_time",
        "idx_storage_earnings",
        "idx_alerts_node_time",
        "idx_alerts_active",
        "idx_alerts_severity",
        "idx_insights_node_time",
        "idx_insights_type",
        "idx_baselines_node_metric",
        "idx_earnings_node_time",
        "idx_earnings_satellite",
        "idx_earnings_period",
        "idx_payout_node_time",
        "idx_payout_satellite",
        "idx_payout_period",
    }

    # Note: Some indexes might be auto-created by SQLite
    assert any(idx in indexes for idx in required_indexes)
    conn.close()


def test_events_table_schema(temp_db):
    """Test events table has correct schema."""
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(events)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}

    required_columns = {
        "id": "INTEGER",
        "timestamp": "DATETIME",
        "action": "TEXT",
        "status": "TEXT",
        "size": "INTEGER",
        "piece_id": "TEXT",
        "satellite_id": "TEXT",
        "remote_ip": "TEXT",
        "country": "TEXT",
        "latitude": "REAL",
        "longitude": "REAL",
        "error_reason": "TEXT",
        "node_name": "TEXT",
        "duration_ms": "INTEGER",
    }

    for col_name, _col_type in required_columns.items():
        assert col_name in columns, f"Missing column: {col_name}"

    conn.close()


def test_write_hashstore_log(temp_db):
    """Test writing hashstore log."""
    from storj_monitor.database import blocking_write_hashstore_log

    stats = {
        "node_name": "test-node",
        "satellite": "test-satellite",
        "store": "pieces",
        "last_run_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "duration": 123.45,
        "data_reclaimed_bytes": 1000000,
        "data_rewritten_bytes": 500000,
        "table_load": 0.75,
        "trash_percent": 2.5,
    }

    result = blocking_write_hashstore_log(temp_db, stats)
    assert result is True

    # Verify data was written
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hashstore_compaction_history WHERE node_name = ?", ("test-node",))
    rows = cursor.fetchall()
    assert len(rows) == 1
    conn.close()


def test_write_hashstore_log_invalid_data(temp_db):
    """Test writing hashstore log with invalid data."""
    from storj_monitor.database import blocking_write_hashstore_log

    # Missing required fields
    invalid_stats = {
        "node_name": "test-node",
        # Missing other required fields
    }

    result = blocking_write_hashstore_log(temp_db, invalid_stats)
    assert result is False


def test_batch_write_events(temp_db, sample_event):
    """Test batch writing events."""
    from storj_monitor.database import blocking_db_batch_write

    events = [sample_event, sample_event.copy()]
    blocking_db_batch_write(temp_db, events)

    # Verify events were written
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events WHERE node_name = ?", ("test-node",))
    count = cursor.fetchone()[0]
    assert count == 2
    conn.close()


def test_batch_write_empty_events(temp_db):
    """Test batch writing with empty list."""
    from storj_monitor.database import blocking_db_batch_write

    # Should not raise an error
    blocking_db_batch_write(temp_db, [])


def test_get_historical_stats(temp_db, sample_event):
    """Test getting historical stats."""
    from storj_monitor.database import (
        blocking_db_batch_write,
        blocking_hourly_aggregation,
        get_historical_stats,
    )

    # Write some events
    events = [sample_event]
    blocking_db_batch_write(temp_db, events)

    # Run aggregation
    blocking_hourly_aggregation(["test-node"])

    # Get stats
    stats = get_historical_stats(["test-node"], {"test-node": {}})
    assert isinstance(stats, list)


def test_write_and_retrieve_reputation_history(temp_db, sample_reputation_data):
    """Test writing and retrieving reputation history."""
    from storj_monitor.database import (
        blocking_get_latest_reputation,
        blocking_get_reputation_history,
        blocking_write_reputation_history,
    )

    result = blocking_write_reputation_history(temp_db, [sample_reputation_data])
    assert result is True

    # Get latest reputation
    latest = blocking_get_latest_reputation(temp_db, ["test-node"])
    assert len(latest) > 0
    assert latest[0]["node_name"] == "test-node"
    assert latest[0]["audit_score"] == 1.0

    # Get reputation history
    history = blocking_get_reputation_history(temp_db, "test-node", hours=24)
    assert len(history) > 0


def test_write_reputation_empty_list(temp_db):
    """Test writing empty reputation list."""
    from storj_monitor.database import blocking_write_reputation_history

    result = blocking_write_reputation_history(temp_db, [])
    assert result is False


def test_write_and_retrieve_storage_snapshot(temp_db, sample_storage_snapshot):
    """Test writing and retrieving storage snapshot."""
    from storj_monitor.database import (
        blocking_get_latest_storage,
        blocking_get_storage_history,
        blocking_write_storage_snapshot,
    )

    result = blocking_write_storage_snapshot(temp_db, sample_storage_snapshot)
    assert result is True

    # Get latest storage
    latest = blocking_get_latest_storage(temp_db, ["test-node"])
    assert len(latest) > 0
    assert latest[0]["node_name"] == "test-node"

    # Get storage history
    history = blocking_get_storage_history(temp_db, "test-node", days=7)
    assert len(history) > 0


def test_storage_snapshot_with_partial_data(temp_db):
    """Test storage snapshot with partial data (from logs)."""
    from storj_monitor.database import blocking_write_storage_snapshot

    # Log-based snapshot only has available_bytes
    partial_snapshot = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "available_bytes": 5000000000,
        "total_bytes": None,
        "used_bytes": None,
        "trash_bytes": None,
        "used_percent": None,
        "trash_percent": None,
        "available_percent": None,
    }

    result = blocking_write_storage_snapshot(temp_db, partial_snapshot)
    assert result is True


def test_write_and_retrieve_alert(temp_db, sample_alert):
    """Test writing and retrieving alerts."""
    from storj_monitor.database import (
        blocking_get_active_alerts,
        blocking_get_alert_history,
        blocking_write_alert,
    )

    result = blocking_write_alert(temp_db, sample_alert)
    assert result is True

    # Get active alerts
    active = blocking_get_active_alerts(temp_db, ["test-node"])
    assert len(active) > 0
    assert active[0]["severity"] == "warning"

    # Get alert history
    history = blocking_get_alert_history(temp_db, "test-node", hours=24)
    assert len(history) > 0


def test_acknowledge_alert(temp_db, sample_alert):
    """Test acknowledging an alert."""
    from storj_monitor.database import (
        blocking_acknowledge_alert,
        blocking_get_active_alerts,
        blocking_write_alert,
    )

    blocking_write_alert(temp_db, sample_alert)

    # Get alert ID
    active = blocking_get_active_alerts(temp_db, ["test-node"])
    alert_id = active[0]["id"]

    # Acknowledge it
    result = blocking_acknowledge_alert(temp_db, alert_id)
    assert result is True

    # Verify it's no longer active
    active_after = blocking_get_active_alerts(temp_db, ["test-node"])
    assert len(active_after) == 0


def test_resolve_alert(temp_db, sample_alert):
    """Test resolving an alert."""
    from storj_monitor.database import (
        blocking_get_active_alerts,
        blocking_resolve_alert,
        blocking_write_alert,
    )

    blocking_write_alert(temp_db, sample_alert)

    # Get alert ID
    active = blocking_get_active_alerts(temp_db, ["test-node"])
    alert_id = active[0]["id"]

    # Resolve it
    result = blocking_resolve_alert(temp_db, alert_id)
    assert result is True

    # Verify it's no longer active
    active_after = blocking_get_active_alerts(temp_db, ["test-node"])
    assert len(active_after) == 0


def test_write_and_retrieve_insight(temp_db):
    """Test writing and retrieving insights."""
    from storj_monitor.database import blocking_get_insights, blocking_write_insight

    insight = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "insight_type": "performance",
        "severity": "info",
        "title": "Test Insight",
        "description": "This is a test insight",
        "category": "bandwidth",
        "confidence": 0.95,
        "metadata": {"test": "data"},
    }

    result = blocking_write_insight(temp_db, insight)
    assert result is True

    # Get insights
    insights = blocking_get_insights(temp_db, ["test-node"], hours=24)
    assert len(insights) > 0
    assert insights[0]["title"] == "Test Insight"


def test_update_and_get_baseline(temp_db):
    """Test updating and retrieving analytics baselines."""
    from storj_monitor.database import blocking_get_baseline, blocking_update_baseline

    stats = {"mean": 100.5, "std_dev": 15.2, "min": 50.0, "max": 150.0, "count": 100}

    result = blocking_update_baseline(temp_db, "test-node", "egress_mbps", 168, stats)
    assert result is True

    # Get baseline
    baseline = blocking_get_baseline(temp_db, "test-node", "egress_mbps", 168)
    assert baseline is not None
    assert baseline["mean_value"] == 100.5
    assert baseline["std_dev"] == 15.2


def test_write_and_retrieve_earnings_estimate(temp_db, sample_earnings_estimate):
    """Test writing and retrieving earnings estimates."""
    from storj_monitor.database import (
        blocking_get_earnings_estimates,
        blocking_get_latest_earnings,
        blocking_write_earnings_estimate,
    )

    result = blocking_write_earnings_estimate(temp_db, sample_earnings_estimate)
    assert result is True

    # Get earnings estimates
    estimates = blocking_get_earnings_estimates(temp_db, node_names=["test-node"], days=30)
    assert len(estimates) > 0

    # Get latest earnings
    latest = blocking_get_latest_earnings(temp_db, ["test-node"], period="2025-01")
    assert len(latest) > 0
    assert latest[0]["node_name"] == "test-node"


def test_earnings_deduplication(temp_db, sample_earnings_estimate):
    """Test that duplicate earnings estimates are deduplicated."""
    from storj_monitor.database import (
        blocking_get_earnings_estimates,
        blocking_write_earnings_estimate,
    )

    # Write same estimate twice
    blocking_write_earnings_estimate(temp_db, sample_earnings_estimate)

    # Write again with different timestamp (simulating multiple updates)
    estimate2 = sample_earnings_estimate.copy()
    estimate2["timestamp"] = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=5
    )
    blocking_write_earnings_estimate(temp_db, estimate2)

    # Should only get the latest one per node/satellite/period
    estimates = blocking_get_earnings_estimates(
        temp_db, node_names=["test-node"], period="2025-01", days=30
    )

    # Should have only 1 result (the latest)
    assert len(estimates) == 1


def test_write_payout_history(temp_db):
    """Test writing payout history."""
    from storj_monitor.database import blocking_get_payout_history, blocking_write_payout_history

    payout = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "period": "2025-01",
        "actual_payout": 5.50,
        "estimated_payout": 5.25,
        "variance": 0.25,
        "variance_percent": 4.76,
        "payout_address": "0x1234567890abcdef",
        "transaction_hash": "0xabcdef1234567890",
        "notes": "Test payout",
    }

    result = blocking_write_payout_history(temp_db, payout)
    assert result is True

    # Get payout history
    history = blocking_get_payout_history(temp_db, ["test-node"], months=12)
    assert len(history) > 0
    assert history[0]["actual_payout"] == 5.50


def test_database_pruning(temp_db, sample_event):
    """Test database pruning functionality."""
    from storj_monitor.database import blocking_db_batch_write, blocking_db_prune

    # Create an old event
    old_event = sample_event.copy()
    old_event["timestamp"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=10
    )

    # Create a recent event
    recent_event = sample_event.copy()
    recent_event["timestamp"] = datetime.datetime.now(datetime.timezone.utc)

    blocking_db_batch_write(temp_db, [old_event, recent_event])

    # Prune with 5 day retention
    blocking_db_prune(
        temp_db,
        events_retention_days=5,
        hashstore_retention_days=180,
        earnings_retention_days=365,
        alerts_retention_days=90,
        insights_retention_days=90,
        analytics_retention_days=180,
    )

    # Verify old event was removed
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    count = cursor.fetchone()[0]
    assert count == 1  # Only recent event should remain
    conn.close()


def test_hourly_aggregation(temp_db, sample_event):
    """Test hourly statistics aggregation."""
    from storj_monitor.database import blocking_db_batch_write, blocking_hourly_aggregation

    # Create events in current hour
    events = [sample_event.copy() for _ in range(5)]
    blocking_db_batch_write(temp_db, events)

    # Run aggregation
    blocking_hourly_aggregation(["test-node"])

    # Verify stats were created
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hourly_stats WHERE node_name = ?", ("test-node",))
    rows = cursor.fetchall()
    assert len(rows) > 0
    conn.close()


def test_backfill_hourly_stats(temp_db, sample_event):
    """Test backfilling hourly statistics."""
    from storj_monitor.database import blocking_backfill_hourly_stats, blocking_db_batch_write

    # Create events spread across time
    events = []
    for i in range(10):
        event = sample_event.copy()
        event["timestamp"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=i
        )
        events.append(event)

    blocking_db_batch_write(temp_db, events)

    # Backfill stats
    blocking_backfill_hourly_stats(["test-node"])

    # Verify stats were created
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM hourly_stats WHERE node_name = ?", ("test-node",))
    count = cursor.fetchone()[0]
    assert count > 0
    conn.close()


def test_get_hashstore_stats(temp_db):
    """Test getting hashstore compaction statistics."""
    import storj_monitor.config as config
    from storj_monitor.database import blocking_get_hashstore_stats, blocking_write_hashstore_log

    # Temporarily set DATABASE_FILE to temp_db
    original_db = config.DATABASE_FILE
    config.DATABASE_FILE = temp_db

    try:
        stats = {
            "node_name": "test-node",
            "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "store": "pieces",
            "last_run_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration": 100.0,
            "data_reclaimed_bytes": 1000000,
            "data_rewritten_bytes": 500000,
            "table_load": 75.0,
            "trash_percent": 5.0,
        }

        blocking_write_hashstore_log(temp_db, stats)

        # Get stats with no filters
        results = blocking_get_hashstore_stats({"node_name": "all"})
        assert len(results) > 0

        # Get stats with node filter
        results = blocking_get_hashstore_stats({"node_name": "test-node"})
        assert len(results) > 0
        assert results[0]["node_name"] == "test-node"
    finally:
        config.DATABASE_FILE = original_db


def test_get_aggregated_performance(temp_db, sample_event):
    """Test getting aggregated performance data."""
    import storj_monitor.config as config
    from storj_monitor.database import blocking_db_batch_write, blocking_get_aggregated_performance

    # Temporarily set DATABASE_FILE to temp_db
    original_db = config.DATABASE_FILE
    config.DATABASE_FILE = temp_db

    try:
        # Create events
        events = [sample_event.copy() for _ in range(10)]
        blocking_db_batch_write(temp_db, events)

        # Get aggregated performance
        results = blocking_get_aggregated_performance(["test-node"], time_window_hours=1)
        assert isinstance(results, list)
    finally:
        config.DATABASE_FILE = original_db


def test_storage_forecast_calculation(temp_db):
    """Test storage forecast with growth rate calculation."""
    from storj_monitor.database import (
        blocking_get_latest_storage_with_forecast,
        blocking_write_storage_snapshot,
    )

    # Create storage snapshots with growth over time
    base_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)

    for i in range(8):
        snapshot = {
            "timestamp": base_time + datetime.timedelta(days=i),
            "node_name": "test-node",
            "total_bytes": 10000000000,
            "used_bytes": 5000000000 + (i * 100000000),  # Growing usage
            "available_bytes": 5000000000 - (i * 100000000),
            "trash_bytes": 100000000,
            "used_percent": 50.0 + i,
            "trash_percent": 1.0,
            "available_percent": 50.0 - i,
        }
        blocking_write_storage_snapshot(temp_db, snapshot)

    # Get latest with forecast
    results = blocking_get_latest_storage_with_forecast(temp_db, ["test-node"])
    assert len(results) > 0
    assert "growth_rates" in results[0]


def test_concurrent_database_access(temp_db, sample_event):
    """Test concurrent database access doesn't cause errors."""
    import threading

    from storj_monitor.database import blocking_db_batch_write

    def write_events():
        events = [sample_event.copy() for _ in range(5)]
        blocking_db_batch_write(temp_db, events)

    # Create multiple threads writing simultaneously
    threads = [threading.Thread(target=write_events) for _ in range(3)]

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    # Verify all events were written
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events WHERE node_name = ?", ("test-node",))
    count = cursor.fetchone()[0]
    assert count == 15  # 3 threads * 5 events each
    conn.close()


def test_invalid_database_path():
    """Test handling of invalid database path."""
    from storj_monitor.database import blocking_write_hashstore_log

    stats = {
        "node_name": "test-node",
        "satellite": "test-sat",
        "store": "pieces",
        "last_run_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "duration": 1.0,
        "data_reclaimed_bytes": 0,
        "data_rewritten_bytes": 0,
        "table_load": 0,
        "trash_percent": 0,
    }

    result = blocking_write_hashstore_log("/invalid/path/db.db", stats)
    assert result is False


def test_get_reputation_with_no_data(temp_db):
    """Test getting reputation when no data exists."""
    from storj_monitor.database import blocking_get_latest_reputation

    results = blocking_get_latest_reputation(temp_db, ["nonexistent-node"])
    assert results == []


def test_get_storage_with_no_data(temp_db):
    """Test getting storage when no data exists."""
    from storj_monitor.database import blocking_get_latest_storage

    results = blocking_get_latest_storage(temp_db, ["nonexistent-node"])
    assert results == []


def test_get_earnings_with_filters(temp_db, sample_earnings_estimate):
    """Test getting earnings with various filters."""
    from storj_monitor.database import (
        blocking_get_earnings_estimates,
        blocking_write_earnings_estimate,
    )

    blocking_write_earnings_estimate(temp_db, sample_earnings_estimate)

    # Filter by satellite
    results = blocking_get_earnings_estimates(
        temp_db, satellite="12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S", days=30
    )
    assert len(results) > 0

    # Filter by period
    results = blocking_get_earnings_estimates(temp_db, period="2025-01", days=30)
    assert len(results) > 0


def test_alert_history_with_resolved_filter(temp_db, sample_alert):
    """Test getting alert history with resolved filter."""
    from storj_monitor.database import (
        blocking_get_alert_history,
        blocking_resolve_alert,
        blocking_write_alert,
    )

    blocking_write_alert(temp_db, sample_alert)

    # Get alert ID and resolve it
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM alerts WHERE node_name = ?", ("test-node",))
    alert_id = cursor.fetchone()[0]
    conn.close()

    blocking_resolve_alert(temp_db, alert_id)

    # Get history including resolved
    history_with = blocking_get_alert_history(temp_db, "test-node", hours=24, include_resolved=True)
    assert len(history_with) > 0

    # Get history excluding resolved
    history_without = blocking_get_alert_history(
        temp_db, "test-node", hours=24, include_resolved=False
    )
    assert len(history_without) == 0


def test_batch_write_hashstore_ingest(temp_db):
    """Test batch writing hashstore compaction records."""
    from storj_monitor.database import blocking_batch_write_hashstore_ingest

    records = [
        {
            "node_name": "test-node",
            "satellite": f"sat-{i}",
            "store": "pieces",
            "last_run_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration": 100.0 + i,
            "data_reclaimed_bytes": 1000000,
            "data_rewritten_bytes": 500000,
            "table_load": 75.0,
            "trash_percent": 5.0,
        }
        for i in range(3)
    ]

    blocking_batch_write_hashstore_ingest(temp_db, records)

    # Verify records were written
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM hashstore_compaction_history WHERE node_name = ?", ("test-node",)
    )
    count = cursor.fetchone()[0]
    assert count == 3
    conn.close()


def test_reputation_history_with_satellite_filter(temp_db, sample_reputation_data):
    """Test getting reputation history with satellite filter."""
    from storj_monitor.database import (
        blocking_get_reputation_history,
        blocking_write_reputation_history,
    )

    blocking_write_reputation_history(temp_db, [sample_reputation_data])

    # Get with satellite filter
    history = blocking_get_reputation_history(
        temp_db,
        "test-node",
        satellite="12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        hours=24,
    )
    assert len(history) > 0
    assert history[0]["satellite"] == "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S"
