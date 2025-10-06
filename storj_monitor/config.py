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

# --- Database Concurrency Configuration ---
DB_THREAD_POOL_SIZE = 10  # Increased from 5 to handle concurrent load
DB_CONNECTION_TIMEOUT = 30.0  # Increased timeout for database operations (seconds)
DB_MAX_RETRIES = 3  # Number of retry attempts for locked database
DB_RETRY_BASE_DELAY = 0.5  # Base delay between retries (seconds)
DB_RETRY_MAX_DELAY = 5.0  # Maximum delay between retries (seconds)
DB_CONNECTION_POOL_SIZE = 5  # Connection pool size for read operations

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

# --- Storage Capacity Thresholds (Phase 2.2) ---
STORAGE_WARNING_PERCENT = 80  # 80% full
STORAGE_CRITICAL_PERCENT = 95  # 95% full
STORAGE_FORECAST_WARNING_DAYS = 30  # Alert if full within 30 days
STORAGE_FORECAST_CRITICAL_DAYS = 7  # Critical if full within 7 days

# --- Performance Thresholds (Phase 2.1) ---
LATENCY_WARNING_MS = 5000  # 5 seconds
LATENCY_CRITICAL_MS = 10000  # 10 seconds

# --- Analytics & Anomaly Detection Configuration (Phase 4) ---
ENABLE_ANOMALY_DETECTION = True  # Enable anomaly detection
ANOMALY_ZSCORE_THRESHOLD = 3.0  # Z-score threshold for anomaly detection
ANOMALY_BASELINE_DAYS = 7  # Days of historical data for baseline calculation
ALERT_EVALUATION_INTERVAL_MINUTES = 5  # How often to evaluate alert conditions
ALERT_COOLDOWN_MINUTES = 15  # Minimum time between duplicate alerts
ANALYTICS_BASELINE_UPDATE_HOURS = 24  # How often to update statistical baselines

# --- Notification Settings (Phase 4) ---
ENABLE_BROWSER_NOTIFICATIONS = True  # Enable browser push notifications
ENABLE_EMAIL_NOTIFICATIONS = False  # Enable email notifications (not implemented yet)
ENABLE_WEBHOOK_NOTIFICATIONS = False  # Enable webhook notifications (not implemented yet)

# --- Data Retention (Phase 4) ---
DB_ALERTS_RETENTION_DAYS = 90  # How many days of alert history to keep
DB_INSIGHTS_RETENTION_DAYS = 90  # How many days of insights to keep
DB_ANALYTICS_RETENTION_DAYS = 180  # How many days of analytics baselines to keep
# --- Financial Tracking Configuration (Phase 5) ---
ENABLE_FINANCIAL_TRACKING = True  # Enable financial tracking and earnings calculations
# NOTE: These prices are what the NODE OPERATOR receives (net), not gross amounts
# Storj pricing as of Dec 1st, 2023: Storage $1.50/TB-month, Egress/Repair/Audit $2.00/TB
PRICING_EGRESS_PER_TB = 2.00  # USD per TB egress (operator's share)
PRICING_STORAGE_PER_TB_MONTH = 1.50  # USD per TB-month storage (operator's share)
PRICING_REPAIR_PER_TB = 2.00  # USD per TB repair traffic (operator's share)
PRICING_AUDIT_PER_TB = 2.00  # USD per TB audit traffic (operator's share)
# These "operator share" values should be 1.0 because the prices above are already net to operator
OPERATOR_SHARE_EGRESS = 1.0  # 100% - price above is already operator's share
OPERATOR_SHARE_STORAGE = 1.0  # 100% - price above is already operator's share
OPERATOR_SHARE_REPAIR = 1.0  # 100% - price above is already operator's share
OPERATOR_SHARE_AUDIT = 1.0  # 100% - price above is already operator's share
HELD_AMOUNT_MONTHS_1_TO_3 = 0.75  # 75% held for months 1-3
HELD_AMOUNT_MONTHS_4_TO_6 = 0.50  # 50% held for months 4-6
HELD_AMOUNT_MONTHS_7_TO_9 = 0.25  # 25% held for months 7-9
HELD_AMOUNT_MONTHS_10_TO_15 = 0.00  # 0% held for months 10-15
HELD_AMOUNT_MONTH_16_PLUS = 0.00  # 0% held for month 16+, plus 50% of accumulated held amount returned
NODE_MONTHLY_COSTS = {}  # Optional: {'node_name': monthly_cost_usd} for profitability analysis
DB_EARNINGS_RETENTION_DAYS = 365  # How many days of earnings estimates to keep


# --- Global Constants ---
SATELLITE_NAMES = {'121RTSDpyNZVcEU84Ticf2L1ntiuUimbWgfATz21tuvgk3vzoA6': 'ap1',
                   '12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S': 'us1',
                   '12L9ZFwhzVpuEKMUNUqkaTLGzwY9G24tbiigLiXpmZWKwmcNDDs': 'eu1',
                   '1wFTAgs9DP5RSnCqKV1eLf6N9wtk4EAtmN5DpSxcs8EjT69tGE': 'saltlake'}
