"""
Integration tests for database migrations and schema creation.
Tests database creation from scratch and schema integrity.
"""

import builtins
import contextlib
import os
import sqlite3
import tempfile


def test_database_creation_from_scratch(temp_db):
    """
    Test database creation from scratch:
    1. Create new database
    2. Run init_db()
    3. Verify all tables exist
    4. Verify all indexes exist
    """
    from storj_monitor import database

    # Initialize database schema
    database.init_db()

    # Connect and verify
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Get all table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    # Expected tables
    expected_tables = [
        "alerts",
        "analytics_baselines",
        "app_persistent_state",
        "earnings_estimates",
        "events",
        "hashstore_compaction_history",
        "hourly_stats",
        "insights",
        "payout_history",
        "reputation_history",
        "storage_snapshots",
    ]

    for table in expected_tables:
        assert table in tables, f"Table '{table}' should exist"

    conn.close()


def test_all_tables_created(temp_db):
    """Test that all expected tables are created with correct schemas."""
    from storj_monitor import database

    database.init_db()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Test events table
    cursor.execute("PRAGMA table_info(events)")
    events_columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "id" in events_columns
    assert "timestamp" in events_columns
    assert "action" in events_columns
    assert "status" in events_columns
    assert "node_name" in events_columns
    assert "duration_ms" in events_columns

    # Test reputation_history table
    cursor.execute("PRAGMA table_info(reputation_history)")
    reputation_columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "id" in reputation_columns
    assert "timestamp" in reputation_columns
    assert "node_name" in reputation_columns
    assert "satellite" in reputation_columns
    assert "audit_score" in reputation_columns
    assert "suspension_score" in reputation_columns
    assert "online_score" in reputation_columns

    # Test storage_snapshots table
    cursor.execute("PRAGMA table_info(storage_snapshots)")
    storage_columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "id" in storage_columns
    assert "timestamp" in storage_columns
    assert "node_name" in storage_columns
    assert "total_bytes" in storage_columns
    assert "used_bytes" in storage_columns
    assert "available_bytes" in storage_columns

    # Test alerts table
    cursor.execute("PRAGMA table_info(alerts)")
    alerts_columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "id" in alerts_columns
    assert "timestamp" in alerts_columns
    assert "node_name" in alerts_columns
    assert "alert_type" in alerts_columns
    assert "severity" in alerts_columns
    assert "acknowledged" in alerts_columns

    # Test earnings_estimates table
    cursor.execute("PRAGMA table_info(earnings_estimates)")
    earnings_columns = {row[1]: row[2] for row in cursor.fetchall()}

    assert "id" in earnings_columns
    assert "timestamp" in earnings_columns
    assert "node_name" in earnings_columns
    assert "satellite" in earnings_columns
    assert "period" in earnings_columns
    assert "total_earnings_net" in earnings_columns

    conn.close()


def test_all_indexes_created(temp_db):
    """Test that all expected indexes are created."""
    from storj_monitor import database

    database.init_db()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Get all index names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
    indexes = [row[0] for row in cursor.fetchall()]

    # Expected indexes
    expected_indexes = [
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
    ]

    for index in expected_indexes:
        assert index in indexes, f"Index '{index}' should exist"

    conn.close()


def test_database_functional_after_creation(temp_db):
    """Test that database is fully functional after creation."""
    import datetime

    from storj_monitor import database

    database.init_db()

    # Test writing and reading events
    test_event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "action": "GET",
        "status": "success",
        "size": 1024000,
        "piece_id": "test-piece",
        "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "remote_ip": "192.168.1.1",
        "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
        "error_reason": None,
        "node_name": "test-node",
        "duration_ms": 150,
    }

    # Write event
    database.blocking_db_batch_write(temp_db, [test_event])

    # Read event
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events WHERE node_name = ?", ("test-node",))
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1

    # Test writing reputation data
    reputation_record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "satellite": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "audit_score": 1.0,
        "suspension_score": 1.0,
        "online_score": 1.0,
        "audit_success_count": 100,
        "audit_total_count": 100,
        "is_disqualified": False,
        "is_suspended": False,
    }

    success = database.blocking_write_reputation_history(temp_db, [reputation_record])
    assert success

    # Test writing storage snapshot
    storage_snapshot = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "total_bytes": 10000000000,
        "used_bytes": 5000000000,
        "available_bytes": 5000000000,
        "trash_bytes": 100000000,
        "used_percent": 50.0,
        "trash_percent": 1.0,
        "available_percent": 50.0,
    }

    success = database.blocking_write_storage_snapshot(temp_db, storage_snapshot)
    assert success

    # Test writing alert
    alert = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "node_name": "test-node",
        "alert_type": "test_alert",
        "severity": "warning",
        "title": "Test Alert",
        "message": "This is a test alert",
        "metadata": {"test": "value"},
    }

    success = database.blocking_write_alert(temp_db, alert)
    assert success


def test_wal_mode_enabled(temp_db):
    """Test that WAL mode is properly enabled for better concurrency."""
    from storj_monitor import database

    database.init_db()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]

    assert mode.lower() == "wal", f"Journal mode should be WAL, got {mode}"

    conn.close()


def test_database_migration_adds_missing_columns():
    """Test that database migration adds missing columns to existing tables."""
    import os
    import tempfile

    from storj_monitor import database

    # Create a temporary database file WITHOUT calling init_db()
    fd, temp_db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        # Create database with old schema (without duration_ms)
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # Create old events table without duration_ms
        cursor.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME,
                action TEXT,
                status TEXT,
                size INTEGER,
                piece_id TEXT,
                satellite_id TEXT,
                remote_ip TEXT,
                country TEXT,
                latitude REAL,
                longitude REAL,
                error_reason TEXT,
                node_name TEXT
            )
        """)

        conn.commit()
        conn.close()

        # Now run init_db which should detect and add the missing column
        # Temporarily patch DATABASE_FILE to use our temp db
        old_db_file = database.DATABASE_FILE
        database.DATABASE_FILE = temp_db_path
        database.init_db()
        database.DATABASE_FILE = old_db_file

        # Verify duration_ms column was added
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(events)")
        columns = [col[1] for col in cursor.fetchall()]
        conn.close()

        assert "duration_ms" in columns, "duration_ms column should be added during migration"
    finally:
        # Clean up temp file
        with contextlib.suppress(builtins.BaseException):
            os.unlink(temp_db_path)


def test_composite_keys_and_constraints(temp_db):
    """Test that composite primary keys and constraints are properly set."""
    from storj_monitor import database

    database.init_db()

    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Test hourly_stats composite primary key
    cursor.execute("PRAGMA table_info(hourly_stats)")
    hourly_stats_info = cursor.fetchall()

    # Check that both hour_timestamp and node_name are part of the key
    pk_columns = [row[1] for row in hourly_stats_info if row[5] > 0]
    assert "hour_timestamp" in pk_columns
    assert "node_name" in pk_columns

    # Test analytics_baselines unique constraint
    cursor.execute("PRAGMA index_list(analytics_baselines)")
    indexes = cursor.fetchall()

    # Should have a unique index on node_name, metric_name, window_hours
    unique_indexes = [idx for idx in indexes if idx[2] == 1]  # unique flag
    assert len(unique_indexes) > 0, "Should have unique constraint on analytics_baselines"

    conn.close()


def test_foreign_key_relationships():
    """Test that foreign key relationships are properly defined (if any)."""
    # Note: Current schema doesn't use foreign keys, but this test
    # ensures we can add them in the future if needed
    pass


def test_database_pruning_functionality(temp_db):
    """Test that database pruning removes old data correctly."""
    import datetime

    from storj_monitor import database

    database.init_db()

    # Insert old events
    old_timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=100)
    old_event = {
        "timestamp": old_timestamp,
        "action": "GET",
        "status": "success",
        "size": 1024000,
        "piece_id": "old-piece",
        "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "remote_ip": "192.168.1.1",
        "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
        "error_reason": None,
        "node_name": "test-node",
        "duration_ms": 150,
    }

    # Insert recent events
    recent_timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    recent_event = old_event.copy()
    recent_event["timestamp"] = recent_timestamp
    recent_event["piece_id"] = "recent-piece"

    database.blocking_db_batch_write(temp_db, [old_event, recent_event])

    # Verify both events exist
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    count_before = cursor.fetchone()[0]
    assert count_before == 2
    conn.close()

    # Run pruning with 30 day retention
    database.blocking_db_prune(
        temp_db,
        events_retention_days=30,
        hashstore_retention_days=30,
        earnings_retention_days=90,
        alerts_retention_days=90,
        insights_retention_days=90,
        analytics_retention_days=90,
    )

    # Verify old event was pruned but recent event remains
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    count_after = cursor.fetchone()[0]

    assert count_after == 1, "Old event should be pruned"

    cursor.execute("SELECT piece_id FROM events")
    remaining_piece = cursor.fetchone()[0]
    assert remaining_piece == "recent-piece", "Recent event should remain"

    conn.close()


def test_hourly_aggregation_creates_stats(temp_db):
    """Test that hourly aggregation properly creates statistics."""
    import datetime

    from storj_monitor import database

    database.init_db()

    # Create events for current hour
    now = datetime.datetime.now(datetime.timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)

    events = []
    for i in range(10):
        event = {
            "timestamp": hour_start + datetime.timedelta(minutes=i),
            "action": "GET" if i % 2 == 0 else "PUT",
            "status": "success" if i < 8 else "failed",
            "size": 1024000,
            "piece_id": f"piece-{i}",
            "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
            "remote_ip": "192.168.1.1",
            "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
            "error_reason": None if i < 8 else "context canceled",
            "node_name": "test-node",
            "duration_ms": 150,
        }
        events.append(event)

    database.blocking_db_batch_write(temp_db, events)

    # Run hourly aggregation
    database.blocking_hourly_aggregation(["test-node"])

    # Verify stats were created
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM hourly_stats
        WHERE node_name = 'test-node'
        ORDER BY hour_timestamp DESC
        LIMIT 1
    """)

    stats = cursor.fetchone()
    assert stats is not None, "Hourly stats should be created"

    # Verify counts (5 GET, 5 PUT, 8 success, 2 failed)
    assert stats["dl_success"] + stats["dl_fail"] == 5  # GET operations
    assert stats["ul_success"] + stats["ul_fail"] == 5  # PUT operations

    conn.close()


def test_backfill_hourly_stats(temp_db):
    """Test that backfill properly aggregates historical data."""
    import datetime

    from storj_monitor import database

    database.init_db()

    # Create events spanning multiple hours in the past
    base_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)

    events = []
    for hour in range(5):
        for i in range(10):
            event = {
                "timestamp": base_time + datetime.timedelta(hours=hour, minutes=i),
                "action": "GET",
                "status": "success",
                "size": 1024000,
                "piece_id": f"piece-h{hour}-{i}",
                "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
                "remote_ip": "192.168.1.1",
                "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
                "error_reason": None,
                "node_name": "test-node",
                "duration_ms": 150,
            }
            events.append(event)

    database.blocking_db_batch_write(temp_db, events)

    # Run backfill
    database.blocking_backfill_hourly_stats(["test-node"])

    # Verify stats were created for all hours
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) FROM hourly_stats
        WHERE node_name = 'test-node'
    """)

    stats_count = cursor.fetchone()[0]
    assert stats_count >= 5, f"Should have stats for at least 5 hours, got {stats_count}"

    conn.close()


def test_database_connection_pool():
    """Test that database connection pool is properly initialized."""
    from storj_monitor import database
    from storj_monitor.db_utils import cleanup_connection_pool, get_optimized_connection

    # Create temporary database
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        database.init_db()

        # Initialize connection pool
        from storj_monitor.db_utils import init_connection_pool

        init_connection_pool(path, pool_size=3, timeout=5)

        # Get connection from pool
        conn1 = get_optimized_connection(path)
        assert conn1 is not None
        conn1.close()

        # Get another connection
        conn2 = get_optimized_connection(path)
        assert conn2 is not None
        conn2.close()

        # Cleanup pool
        cleanup_connection_pool()

    finally:
        with contextlib.suppress(builtins.BaseException):
            os.unlink(path)


def test_data_integrity_after_schema_upgrade(temp_db):
    """Test that existing data remains intact after schema upgrades."""
    import datetime

    from storj_monitor import database

    # Create database and add some data
    database.init_db()

    test_event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "action": "GET",
        "status": "success",
        "size": 1024000,
        "piece_id": "integrity-test",
        "satellite_id": "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S",
        "remote_ip": "192.168.1.1",
        "location": {"country": "US", "lat": 37.7749, "lon": -122.4194},
        "error_reason": None,
        "node_name": "test-node",
        "duration_ms": 150,
    }

    database.blocking_db_batch_write(temp_db, [test_event])

    # Re-run init_db (simulating an upgrade)
    database.init_db()

    # Verify data is still there
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT piece_id FROM events WHERE piece_id = 'integrity-test'")
    result = cursor.fetchone()

    assert result is not None, "Data should survive schema upgrade"
    assert result[0] == "integrity-test"

    conn.close()
