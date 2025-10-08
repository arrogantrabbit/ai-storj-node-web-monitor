# Database Concurrency Fix

## Problem Analysis

The application was experiencing widespread `sqlite3.OperationalError: unable to open database file` errors across multiple modules:
- Storage tracker
- Performance analyzer  
- Stats updater
- Database writer
- Financial tracker

These errors indicated that SQLite's default configuration with the existing thread pool settings (5 workers, 10-second timeouts) could not handle the concurrent load from all monitoring features.

## Root Causes

1. **Insufficient Thread Pool Size**: Only 5 workers for database operations
2. **Low Connection Timeouts**: 10-second timeouts too short for high load
3. **No Retry Logic**: Single-attempt operations failed on transient locks
4. **Sub-optimal SQLite Configuration**: Default settings not tuned for concurrency
5. **Connection Overhead**: Creating new connections for every operation

## Implemented Solutions

### 1. New Database Utilities Module (`storj_monitor/db_utils.py`)

Created a comprehensive utilities module providing:

#### Retry Decorator with Exponential Backoff
```python
@retry_on_db_lock(max_attempts=3, base_delay=0.5, max_delay=5.0)
```
- Automatically retries failed database operations
- Uses exponential backoff with jitter
- Only retries on lock/busy errors (not other errors)
- Provides detailed logging of retry attempts

#### Optimized Connection Factory
```python
get_optimized_connection(db_path, timeout=30.0, read_only=False)
```
SQLite optimizations applied:
- **WAL Mode**: `PRAGMA journal_mode=WAL` for better concurrency
- **Synchronous Mode**: `PRAGMA synchronous=NORMAL` (balanced safety/speed)
- **Cache Size**: `PRAGMA cache_size=-64000` (64MB cache)
- **Temp Store**: `PRAGMA temp_store=MEMORY` (faster temp operations)
- **Memory-Mapped I/O**: `PRAGMA mmap_size=33554432` (32MB mmap)
- **Busy Timeout**: Connection-level timeout to wait for locks

#### Connection Pool
- Reuses read connections to reduce overhead
- Configurable pool size (default: 5 connections)
- Automatic connection health checks
- Proper cleanup on shutdown

### 2. Updated Configuration (`storj_monitor/config.py`)

Added new database concurrency settings:
```python
DB_THREAD_POOL_SIZE = 10           # Doubled from 5 to 10 workers
DB_CONNECTION_TIMEOUT = 30.0       # Tripled from 10 to 30 seconds
DB_MAX_RETRIES = 3                 # Number of retry attempts
DB_RETRY_BASE_DELAY = 0.5          # Base delay between retries
DB_RETRY_MAX_DELAY = 5.0           # Maximum retry delay
DB_CONNECTION_POOL_SIZE = 5        # Connection pool for reads
```

### 3. Updated Database Module (`storj_monitor/database.py`)

Applied fixes to all database functions:
- Import retry decorator and connection factory
- Apply `@retry_on_db_lock` decorator to critical functions
- Replace `sqlite3.connect()` with `get_optimized_connection()`
- Use new timeout configuration values

Key functions updated:
- `blocking_db_batch_write()` - Event writer (most critical)
- `get_historical_stats()` - Stats queries
- `blocking_get_storage_history()` - Storage queries
- `blocking_get_latest_storage()` - Latest storage data
- All reputation, alert, insight, and earnings functions

### 4. Updated Performance Analyzer (`storj_monitor/performance_analyzer.py`)

- Applied retry logic to latency statistics functions
- Used optimized connections with increased timeouts
- `blocking_get_latency_stats()` now retries on lock errors
- `blocking_get_latency_histogram()` now retries on lock errors

### 5. Updated Task Manager (`storj_monitor/tasks.py`)

#### Initialization
- Initialize connection pool on startup
- Use `DB_THREAD_POOL_SIZE` for executor creation
- Log pool and executor configuration

#### Cleanup
- Properly cleanup connection pool on shutdown
- Close all pooled connections

## Benefits

### Immediate Improvements
1. **Higher Concurrency**: 2x more database workers (10 vs 5)
2. **Better Resilience**: 3x longer timeouts (30s vs 10s)
3. **Automatic Recovery**: Retry logic handles transient locks
4. **Reduced Errors**: Exponential backoff prevents thundering herd

### Performance Enhancements
1. **Faster Reads**: Connection pooling reduces overhead
2. **Better Write Performance**: Optimized SQLite settings
3. **Improved Cache Hit Rate**: 64MB cache vs default
4. **Reduced I/O**: Memory-mapped I/O for hot data

### Operational Benefits
1. **Detailed Logging**: Retry attempts logged with context
2. **Graceful Degradation**: System continues operating under high load
3. **No Data Loss**: WAL mode + retries prevent data loss
4. **Easy Tuning**: Centralized configuration values

## Configuration Tuning

The system can be further tuned based on workload:

### For Higher Concurrency
```python
DB_THREAD_POOL_SIZE = 15  # More workers
DB_CONNECTION_POOL_SIZE = 10  # Larger pool
```

### For Lower Latency
```python
DB_RETRY_BASE_DELAY = 0.1  # Faster retries
DB_MAX_RETRIES = 5  # More attempts
```

### For Resource-Constrained Systems
```python
DB_THREAD_POOL_SIZE = 5  # Fewer workers
DB_CONNECTION_TIMEOUT = 60.0  # Longer wait
```

## Monitoring

Watch for these log patterns to assess effectiveness:

### Success Indicators
```
[INFO] Database connection pool initialized with 5 connections
[INFO] Database thread pool initialized with 10 workers
[INFO] Successfully wrote 313 events to the database
```

### Retry Activity (Normal)
```
[WARNING] Database operation failed (attempt 1/3): database is locked. Retrying in 0.50s...
```

### Issues Requiring Attention
```
[ERROR] Database operation failed after 3 attempts: database is locked
```

If errors persist after 3 retries, consider:
1. Increasing `DB_THREAD_POOL_SIZE`
2. Increasing `DB_CONNECTION_TIMEOUT`
3. Increasing `DB_MAX_RETRIES`
4. Checking for long-running queries
5. Analyzing database size and I/O performance

## Testing Recommendations

1. **Load Testing**: Test with high event volume
2. **Concurrent Access**: Multiple clients requesting data simultaneously
3. **Long-Running Operations**: Ensure large queries don't block writers
4. **Failure Scenarios**: Simulate disk I/O issues
5. **Recovery Testing**: Verify retry logic handles transient failures

## Future Enhancements

Potential improvements if issues persist:
1. **Read Replicas**: Separate read-only database instances
2. **Query Optimization**: Add indexes, optimize slow queries
3. **Batch Optimization**: Larger batch sizes with checkpointing
4. **Alternative Database**: Consider PostgreSQL for very high loads
5. **Connection Pooling Library**: Use enterprise solution like SQLAlchemy

## References

- SQLite WAL Mode: https://www.sqlite.org/wal.html
- SQLite PRAGMA Statements: https://www.sqlite.org/pragma.html
- SQLite Best Practices: https://www.sqlite.org/bestpractice.html