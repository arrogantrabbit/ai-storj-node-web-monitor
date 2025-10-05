import os

# --- Configuration ---
GEOIP_DATABASE_PATH = 'GeoLite2-City.mmdb'
# The database file path.
# This is a relative path by default, so it will be created in the current
# working directory. The service files (systemd, rc.d) are configured to set
# the working directory to a data location like /var/lib/storj_monitor.
# It can be overridden with the STORJ_MONITOR_DB_PATH environment variable.
DATABASE_FILE = os.getenv('STORJ_MONITOR_DB_PATH', 'storj_stats.db')

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8765
STATS_WINDOW_MINUTES = 60
STATS_INTERVAL_SECONDS = 5
PERFORMANCE_INTERVAL_SECONDS = 2
WEBSOCKET_BATCH_INTERVAL_MS = 25  # Batch websocket events every 25ms (very frequent, small batches)
WEBSOCKET_BATCH_SIZE = 10  # Maximum events per batch (small batches for continuous flow)
DB_WRITE_BATCH_INTERVAL_SECONDS = 10
DB_QUEUE_MAX_SIZE = 30000
EXPECTED_DB_COLUMNS = 13  # Increased for node_name
HISTORICAL_HOURS_TO_SHOW = 6
MAX_GEOIP_CACHE_SIZE = 5000
HOURLY_AGG_INTERVAL_MINUTES = 10
DB_EVENTS_RETENTION_DAYS = 2  # New: How many days of event data to keep
DB_PRUNE_INTERVAL_HOURS = 6  # New: How often to run the pruner
DB_HASHSTORE_RETENTION_DAYS = 180  # How many days of hashstore compaction history to keep

# --- API Integration Configuration (Phase 1) ---
NODE_API_DEFAULT_PORT = 14002  # Default Storj node API port
NODE_API_TIMEOUT = 10  # seconds
NODE_API_POLL_INTERVAL = 300  # 5 minutes - how often to poll node API
ALLOW_REMOTE_API = True  # Allow API endpoints on remote hosts (set False for localhost-only)

# --- Reputation Alert Thresholds (Phase 1) ---
AUDIT_SCORE_WARNING = 85.0  # Yellow warning
AUDIT_SCORE_CRITICAL = 70.0  # Red alert
SUSPENSION_SCORE_CRITICAL = 60.0  # Red alert - risk of suspension
ONLINE_SCORE_WARNING = 95.0

# --- Global Constants ---
SATELLITE_NAMES = {'121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1',
                   '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1',
                   '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1',
                   '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake'}
