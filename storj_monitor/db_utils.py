"""
Database Utilities

Provides retry logic, connection management, and optimization utilities
for handling SQLite database operations under high concurrency.
"""

import builtins
import contextlib
import functools
import logging
import sqlite3
import time
from typing import Any, Callable

log = logging.getLogger("StorjMonitor.DbUtils")


def retry_on_db_lock(max_attempts: int = 3, base_delay: float = 0.5, max_delay: float = 5.0):
    """
    Decorator to retry database operations on lock/busy errors with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = base_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                    last_exception = e
                    error_msg = str(e).lower()

                    # Only retry on lock/busy errors, not other operational errors
                    if any(
                        err in error_msg
                        for err in ["locked", "busy", "unable to open", "database is locked"]
                    ):
                        if attempt < max_attempts:
                            log.warning(
                                f"Database operation failed (attempt {attempt}/{max_attempts}): {e}. "
                                f"Retrying in {delay:.2f}s..."
                            )
                            time.sleep(delay)
                            # Exponential backoff with jitter
                            delay = min(delay * 2 + (time.time() % 0.1), max_delay)
                        else:
                            log.error(
                                f"Database operation failed after {max_attempts} attempts: {e}",
                                exc_info=True,
                            )
                    else:
                        # Non-retryable error, raise immediately
                        raise
                except Exception:
                    # Non-database errors should not be retried
                    raise

            # If we exhausted all retries, raise the last exception
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def get_optimized_connection(
    db_path: str, timeout: float = 30.0, read_only: bool = False
) -> sqlite3.Connection:
    """
    Create an optimized SQLite connection with recommended settings for concurrency.

    Args:
        db_path: Path to database file
        timeout: Connection timeout in seconds
        read_only: Whether this is a read-only connection

    Returns:
        Configured SQLite connection
    """
    if read_only:
        # Use URI for read-only mode
        import os

        abs_path = os.path.abspath(db_path)
        uri = f"file:{abs_path}?mode=ro"
        conn = sqlite3.connect(uri, timeout=timeout, detect_types=0, uri=True)
    else:
        # Use regular path for read-write mode
        conn = sqlite3.connect(db_path, timeout=timeout, detect_types=0)

    # Optimize for concurrency and performance
    cursor = conn.cursor()

    # WAL mode for better concurrency (persistent setting)
    if not read_only:
        cursor.execute("PRAGMA journal_mode=WAL;")

    # Synchronous mode: NORMAL is a good balance (FULL is too slow, OFF is risky)
    cursor.execute("PRAGMA synchronous=NORMAL;")

    # Increase cache size (in KB, negative means KB not pages)
    cursor.execute("PRAGMA cache_size=-64000;")  # 64MB cache

    # Use memory for temp store (faster)
    cursor.execute("PRAGMA temp_store=MEMORY;")

    # Enable memory-mapped I/O for better performance (32MB)
    cursor.execute("PRAGMA mmap_size=33554432;")

    # Optimize page size (only effective on new databases, but doesn't hurt)
    cursor.execute("PRAGMA page_size=4096;")

    # Set busy timeout at connection level as well
    cursor.execute(f"PRAGMA busy_timeout={int(timeout * 1000)};")

    log.debug(f"Created optimized SQLite connection (timeout={timeout}s, read_only={read_only})")

    return conn


class ConnectionPool:
    """
    Simple connection pool for read operations to reduce connection overhead.
    """

    def __init__(self, db_path: str, pool_size: int = 5, timeout: float = 30.0):
        self.db_path = db_path
        self.pool_size = pool_size
        self.timeout = timeout
        self._pool = []
        self._in_use = set()

        log.info(f"Initializing connection pool with {pool_size} connections")

    def get_connection(self) -> sqlite3.Connection:
        """Get a connection from the pool or create a new one."""
        # Try to get an available connection from pool
        while self._pool:
            conn = self._pool.pop()
            try:
                # Test if connection is still valid
                conn.execute("SELECT 1")
                self._in_use.add(id(conn))
                return conn
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                # Connection is invalid, close it
                with contextlib.suppress(builtins.BaseException):
                    conn.close()

        # No available connections, create new one
        conn = get_optimized_connection(self.db_path, self.timeout, read_only=True)
        self._in_use.add(id(conn))
        return conn

    def return_connection(self, conn: sqlite3.Connection):
        """Return a connection to the pool."""
        conn_id = id(conn)
        if conn_id in self._in_use:
            self._in_use.remove(conn_id)

            if len(self._pool) < self.pool_size:
                self._pool.append(conn)
            else:
                # Pool is full, close the connection
                with contextlib.suppress(builtins.BaseException):
                    conn.close()

    def close_all(self):
        """Close all connections in the pool."""
        log.info("Closing all connections in pool")
        for conn in self._pool:
            with contextlib.suppress(builtins.BaseException):
                conn.close()
        self._pool.clear()
        self._in_use.clear()


# Global connection pool instance (initialized by app)
_connection_pool = None


def init_connection_pool(db_path: str, pool_size: int = 5, timeout: float = 30.0):
    """Initialize the global connection pool."""
    global _connection_pool
    _connection_pool = ConnectionPool(db_path, pool_size, timeout)
    log.info("Connection pool initialized")


def get_pooled_connection() -> sqlite3.Connection:
    """Get a connection from the global pool."""
    if _connection_pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_connection_pool() first.")
    return _connection_pool.get_connection()


def return_pooled_connection(conn: sqlite3.Connection):
    """Return a connection to the global pool."""
    if _connection_pool is not None:
        _connection_pool.return_connection(conn)


def cleanup_connection_pool():
    """Cleanup the global connection pool."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.close_all()
        _connection_pool = None
