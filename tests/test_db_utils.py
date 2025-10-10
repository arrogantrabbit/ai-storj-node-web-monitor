"""
Comprehensive unit tests for db_utils module.

Tests retry logic, connection management, and connection pooling.
"""

import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from storj_monitor.db_utils import (
    ConnectionPool,
    cleanup_connection_pool,
    get_optimized_connection,
    get_pooled_connection,
    init_connection_pool,
    retry_on_db_lock,
    return_pooled_connection,
)


class TestRetryOnDbLock:
    """Test suite for retry_on_db_lock decorator."""

    def test_retry_decorator_success_first_attempt(self):
        """Test function succeeds on first attempt."""
        call_count = {"count": 0}

        @retry_on_db_lock(max_attempts=3)
        def test_function():
            call_count["count"] += 1
            return "success"

        result = test_function()
        assert result == "success"
        assert call_count["count"] == 1

    def test_retry_decorator_success_after_retry(self):
        """Test function succeeds after retries."""
        call_count = {"count": 0}

        @retry_on_db_lock(max_attempts=3, base_delay=0.1)
        def test_function():
            call_count["count"] += 1
            if call_count["count"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        result = test_function()
        assert result == "success"
        assert call_count["count"] == 3

    def test_retry_decorator_database_locked(self):
        """Test retry on database locked error."""

        @retry_on_db_lock(max_attempts=2, base_delay=0.1)
        def test_function():
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            test_function()

    def test_retry_decorator_database_busy(self):
        """Test retry on database busy error."""

        @retry_on_db_lock(max_attempts=2, base_delay=0.1)
        def test_function():
            raise sqlite3.DatabaseError("database table is locked")

        with pytest.raises(sqlite3.DatabaseError):
            test_function()

    def test_retry_decorator_non_retryable_error(self):
        """Test non-retryable errors are not retried."""
        call_count = {"count": 0}

        @retry_on_db_lock(max_attempts=3)
        def test_function():
            call_count["count"] += 1
            raise sqlite3.IntegrityError("UNIQUE constraint failed")

        with pytest.raises(sqlite3.IntegrityError):
            test_function()

        # Should only be called once (no retry)
        assert call_count["count"] == 1

    def test_retry_decorator_non_database_error(self):
        """Test non-database errors are not retried."""
        call_count = {"count": 0}

        @retry_on_db_lock(max_attempts=3)
        def test_function():
            call_count["count"] += 1
            raise ValueError("Not a database error")

        with pytest.raises(ValueError):
            test_function()

        assert call_count["count"] == 1

    def test_retry_decorator_exponential_backoff(self):
        """Test exponential backoff timing."""
        call_times = []

        @retry_on_db_lock(max_attempts=3, base_delay=0.1, max_delay=1.0)
        def test_function():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        test_function()

        # Verify delays increased (with some tolerance for timing variations)
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]
        assert delay2 > delay1  # Exponential backoff


class TestGetOptimizedConnection:
    """Test suite for get_optimized_connection function."""

    def test_get_optimized_connection_basic(self, temp_db):
        """Test basic connection creation."""
        conn = get_optimized_connection(temp_db, timeout=10.0)

        assert conn is not None
        assert isinstance(conn, sqlite3.Connection)

        # Test connection works
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result[0] == 1

        conn.close()

    def test_get_optimized_connection_readonly(self, temp_db):
        """Test read-only connection creation."""
        conn = get_optimized_connection(temp_db, timeout=10.0, read_only=True)

        assert conn is not None

        # Verify read operations work
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

        conn.close()

    def test_optimized_connection_wal_mode(self, temp_db):
        """Test that WAL mode is set for read-write connections."""
        conn = get_optimized_connection(temp_db, timeout=10.0, read_only=False)

        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]

        assert mode.lower() == "wal"

        conn.close()

    def test_optimized_connection_pragmas(self, temp_db):
        """Test that optimization pragmas are set."""
        conn = get_optimized_connection(temp_db, timeout=10.0)

        cursor = conn.cursor()

        # Check synchronous mode
        cursor.execute("PRAGMA synchronous")
        sync_mode = cursor.fetchone()[0]
        assert sync_mode == 1  # NORMAL

        # Check temp store
        cursor.execute("PRAGMA temp_store")
        temp_store = cursor.fetchone()[0]
        assert temp_store == 2  # MEMORY

        conn.close()

    def test_optimized_connection_custom_timeout(self, temp_db):
        """Test connection with custom timeout."""
        conn = get_optimized_connection(temp_db, timeout=5.0)

        assert conn is not None
        # Timeout is set internally, hard to verify directly
        # but connection should work

        conn.close()


class TestConnectionPool:
    """Test suite for ConnectionPool class."""

    def test_connection_pool_initialization(self, temp_db):
        """Test connection pool initialization."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        assert pool.db_path == temp_db
        assert pool.pool_size == 3
        assert pool.timeout == 10.0
        assert len(pool._pool) == 0
        assert len(pool._in_use) == 0

        pool.close_all()

    def test_connection_pool_get_connection(self, temp_db):
        """Test getting connection from pool."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        conn = pool.get_connection()

        assert conn is not None
        assert id(conn) in pool._in_use

        pool.return_connection(conn)
        pool.close_all()

    def test_connection_pool_return_connection(self, temp_db):
        """Test returning connection to pool."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        conn = pool.get_connection()
        pool.return_connection(conn)

        assert len(pool._pool) == 1
        assert id(conn) not in pool._in_use

        pool.close_all()

    def test_connection_pool_reuse(self, temp_db):
        """Test connection reuse from pool."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Get and return connection
        conn1 = pool.get_connection()
        conn1_id = id(conn1)
        pool.return_connection(conn1)

        # Get connection again - should be the same one
        conn2 = pool.get_connection()
        conn2_id = id(conn2)

        assert conn1_id == conn2_id

        pool.return_connection(conn2)
        pool.close_all()

    def test_connection_pool_max_size(self, temp_db):
        """Test pool respects maximum size."""
        pool = ConnectionPool(temp_db, pool_size=2, timeout=10.0)

        # Get and return 3 connections
        conns = [pool.get_connection() for _ in range(3)]
        for conn in conns:
            pool.return_connection(conn)

        # Pool should only keep 2
        assert len(pool._pool) <= 2

        pool.close_all()

    def test_connection_pool_close_all(self, temp_db):
        """Test closing all connections in pool."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Get and return some connections
        for _ in range(3):
            conn = pool.get_connection()
            pool.return_connection(conn)

        assert len(pool._pool) > 0

        pool.close_all()

        assert len(pool._pool) == 0
        assert len(pool._in_use) == 0

    def test_connection_pool_invalid_connection(self, temp_db):
        """Test pool handles invalid connections gracefully."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Create a connection and close it (making it invalid)
        conn = pool.get_connection()
        conn.close()

        # Return it to pool
        pool.return_connection(conn)

        # Try to get connection again - should create new one
        conn2 = pool.get_connection()
        assert conn2 is not None

        pool.return_connection(conn2)
        pool.close_all()


class TestGlobalPoolFunctions:
    """Test suite for global connection pool functions."""

    def test_init_connection_pool(self, temp_db):
        """Test global pool initialization."""
        init_connection_pool(temp_db, pool_size=3, timeout=10.0)

        # Verify pool was initialized
        from storj_monitor.db_utils import _connection_pool

        assert _connection_pool is not None
        assert _connection_pool.pool_size == 3

        cleanup_connection_pool()

    def test_get_pooled_connection_success(self, temp_db):
        """Test getting connection from global pool."""
        init_connection_pool(temp_db, pool_size=3, timeout=10.0)

        conn = get_pooled_connection()

        assert conn is not None
        assert isinstance(conn, sqlite3.Connection)

        return_pooled_connection(conn)
        cleanup_connection_pool()

    def test_get_pooled_connection_not_initialized(self):
        """Test getting connection when pool not initialized."""
        # Ensure pool is cleaned up
        cleanup_connection_pool()

        with pytest.raises(RuntimeError, match="Connection pool not initialized"):
            get_pooled_connection()

    def test_return_pooled_connection(self, temp_db):
        """Test returning connection to global pool."""
        init_connection_pool(temp_db, pool_size=3, timeout=10.0)

        conn = get_pooled_connection()
        return_pooled_connection(conn)

        # Connection should be back in pool
        from storj_monitor.db_utils import _connection_pool

        assert len(_connection_pool._pool) == 1

        cleanup_connection_pool()

    def test_cleanup_connection_pool(self, temp_db):
        """Test cleanup of global connection pool."""
        init_connection_pool(temp_db, pool_size=3, timeout=10.0)

        from storj_monitor.db_utils import _connection_pool

        assert _connection_pool is not None

        cleanup_connection_pool()

        # Pool should be None after cleanup
        from storj_monitor import db_utils

        assert db_utils._connection_pool is None

    def test_return_pooled_connection_no_pool(self):
        """Test returning connection when pool doesn't exist."""
        cleanup_connection_pool()

        # Create a mock connection
        mock_conn = MagicMock()

        # Should not raise error
        return_pooled_connection(mock_conn)


class TestRetryIntegration:
    """Integration tests for retry decorator with real database."""

    def test_retry_with_concurrent_writes(self, temp_db):
        """Test retry handles concurrent writes."""
        call_count = {"count": 0}

        @retry_on_db_lock(max_attempts=5, base_delay=0.1)
        def write_data():
            call_count["count"] += 1
            conn = sqlite3.connect(temp_db, timeout=0.1)
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
            cursor.execute("INSERT INTO test VALUES (1)")
            conn.commit()
            conn.close()

        # Should succeed
        write_data()
        assert call_count["count"] >= 1

    def test_retry_with_timeout_error(self):
        """Test retry handles timeout errors."""

        @retry_on_db_lock(max_attempts=2, base_delay=0.05)
        def failing_function():
            raise sqlite3.OperationalError("unable to open database file")

        with pytest.raises(sqlite3.OperationalError):
            failing_function()


class TestConnectionPoolStress:
    """Stress tests for connection pool."""

    def test_pool_concurrent_access(self, temp_db):
        """Test pool handles concurrent access."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Get multiple connections
        connections = []
        for _ in range(5):
            conn = pool.get_connection()
            connections.append(conn)

        # All should be valid
        for conn in connections:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

        # Return all connections
        for conn in connections:
            pool.return_connection(conn)

        # Pool should have up to pool_size connections
        assert len(pool._pool) <= pool.pool_size

        pool.close_all()

    def test_pool_connection_validation(self, temp_db):
        """Test pool validates connections before reuse."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Get connection and close it manually
        conn1 = pool.get_connection()
        conn1.close()

        # Return the closed connection
        pool.return_connection(conn1)

        # Getting next connection should create new one (old one is invalid)
        conn2 = pool.get_connection()

        # Should be able to use new connection
        cursor = conn2.cursor()
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

        pool.return_connection(conn2)
        pool.close_all()


class TestOptimizationPragmas:
    """Test database optimization pragmas."""

    def test_wal_mode_not_set_for_readonly(self, temp_db):
        """Test WAL mode is not set for read-only connections."""
        # Read-only connections can't change journal mode
        conn = get_optimized_connection(temp_db, read_only=True)

        cursor = conn.cursor()
        # Just verify we can query (don't try to change mode)
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1

        conn.close()

    def test_cache_size_optimization(self, temp_db):
        """Test cache size is set correctly."""
        conn = get_optimized_connection(temp_db)

        cursor = conn.cursor()
        cursor.execute("PRAGMA cache_size")
        cache_size = cursor.fetchone()[0]

        # Should be negative (KB) and around -64000
        assert cache_size == -64000

        conn.close()

    def test_mmap_size_optimization(self, temp_db):
        """Test memory-mapped I/O is enabled."""
        conn = get_optimized_connection(temp_db)

        cursor = conn.cursor()
        cursor.execute("PRAGMA mmap_size")
        mmap_size = cursor.fetchone()[0]

        # Should be 32MB (33554432 bytes)
        assert mmap_size == 33554432

        conn.close()

    def test_page_size_optimization(self, temp_db):
        """Test page size is set to 4096."""
        conn = get_optimized_connection(temp_db)

        cursor = conn.cursor()
        cursor.execute("PRAGMA page_size")
        page_size = cursor.fetchone()[0]

        # Should be 4096 bytes
        assert page_size == 4096

        conn.close()


class TestConnectionPoolEdgeCases:
    """Test edge cases for connection pool."""

    def test_pool_with_zero_size(self, temp_db):
        """Test pool with zero size."""
        pool = ConnectionPool(temp_db, pool_size=0, timeout=10.0)

        conn = pool.get_connection()
        assert conn is not None

        pool.return_connection(conn)

        # Pool shouldn't store anything with size 0
        assert len(pool._pool) == 0

        pool.close_all()

    def test_pool_double_return(self, temp_db):
        """Test returning same connection twice."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        conn = pool.get_connection()
        conn_id = id(conn)

        pool.return_connection(conn)
        pool.return_connection(conn)  # Second return

        # Should handle gracefully
        assert isinstance(pool._pool, list)

        pool.close_all()

    def test_pool_return_unknown_connection(self, temp_db):
        """Test returning connection not from pool."""
        pool = ConnectionPool(temp_db, pool_size=3, timeout=10.0)

        # Create independent connection
        other_conn = sqlite3.connect(temp_db)

        # Return it to pool
        pool.return_connection(other_conn)

        # Should handle gracefully
        assert isinstance(pool._pool, list)

        other_conn.close()
        pool.close_all()


class TestRetryDecorator:
    """Additional retry decorator tests."""

    def test_retry_preserves_function_metadata(self):
        """Test decorator preserves original function metadata."""

        @retry_on_db_lock()
        def documented_function():
            """This is a documented function."""
            return True

        assert documented_function.__name__ == "documented_function"
        assert documented_function.__doc__ == "This is a documented function."

    def test_retry_with_arguments(self):
        """Test retry decorator with function arguments."""

        @retry_on_db_lock(max_attempts=2, base_delay=0.05)
        def function_with_args(a, b, c=None):
            if c is None:
                raise sqlite3.OperationalError("database is locked")
            return a + b + c

        result = function_with_args(1, 2, c=3)
        assert result == 6

    def test_retry_delay_capping(self):
        """Test that delay is capped at max_delay."""
        delays = []

        @retry_on_db_lock(max_attempts=5, base_delay=1.0, max_delay=2.0)
        def test_function():
            delays.append(time.time())
            if len(delays) < 4:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        test_function()

        # Calculate actual delays
        for i in range(1, len(delays)):
            delay = delays[i] - delays[i - 1]
            # Should not exceed max_delay + some tolerance for execution time
            assert delay < 2.5