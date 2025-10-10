"""
Comprehensive tests for configuration module.
"""

import os


def test_config_imports():
    """Test that config module can be imported."""
    from storj_monitor import config

    assert config is not None


def test_database_file_config():
    """Test DATABASE_FILE configuration."""
    from storj_monitor.config import DATABASE_FILE

    assert DATABASE_FILE is not None
    assert isinstance(DATABASE_FILE, str)


def test_satellite_names_config():
    """Test SATELLITE_NAMES configuration."""
    from storj_monitor.config import SATELLITE_NAMES

    assert isinstance(SATELLITE_NAMES, dict)
    assert len(SATELLITE_NAMES) > 0
    # Check known satellites
    assert "12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S" in SATELLITE_NAMES
    assert SATELLITE_NAMES["12EayRS2V1kEsWESU9QMRseFhdxYxKicsiFmxrsLZHeLUtdps3S"] == "us1"


def test_config_has_required_settings():
    """Test that config has all required settings."""
    from storj_monitor import config

    required_settings = [
        "DATABASE_FILE",
        "STATS_WINDOW_MINUTES",
        "HISTORICAL_HOURS_TO_SHOW",
        "DB_CONNECTION_TIMEOUT",
        "SERVER_HOST",
        "SERVER_PORT",
        "NODE_API_DEFAULT_PORT",
        "SATELLITE_NAMES",
    ]

    for setting in required_settings:
        assert hasattr(config, setting), f"Missing required config: {setting}"


def test_default_configuration_values():
    """Test that default config values are set correctly."""
    from storj_monitor import config

    # Network settings
    assert config.SERVER_HOST == "0.0.0.0"
    assert config.SERVER_PORT == 8765
    assert config.NODE_API_DEFAULT_PORT == 14002

    # Timing settings
    assert config.STATS_WINDOW_MINUTES == 60
    assert config.STATS_INTERVAL_SECONDS == 5
    assert config.PERFORMANCE_INTERVAL_SECONDS == 2
    assert config.HISTORICAL_HOURS_TO_SHOW == 6

    # Database settings
    assert config.DB_CONNECTION_TIMEOUT == 30.0
    assert config.DB_MAX_RETRIES == 3
    assert config.DB_THREAD_POOL_SIZE == 10

    # Retention settings
    assert config.DB_EVENTS_RETENTION_DAYS == 2
    assert config.DB_PRUNE_INTERVAL_HOURS == 6
    assert config.DB_HASHSTORE_RETENTION_DAYS == 180


def test_threshold_values_are_logical():
    """Test that threshold values make sense and have logical relationships."""
    from storj_monitor import config

    # Audit score thresholds (higher is better, so warning > critical)
    assert config.AUDIT_SCORE_WARNING > config.AUDIT_SCORE_CRITICAL
    assert config.AUDIT_SCORE_CRITICAL >= 0
    assert config.AUDIT_SCORE_WARNING <= 100

    # Suspension score threshold
    assert config.SUSPENSION_SCORE_CRITICAL >= 0
    assert config.SUSPENSION_SCORE_CRITICAL <= 100

    # Online score threshold
    assert config.ONLINE_SCORE_WARNING >= 0
    assert config.ONLINE_SCORE_WARNING <= 100

    # Storage thresholds (higher percent = more full, so critical > warning)
    assert config.STORAGE_CRITICAL_PERCENT > config.STORAGE_WARNING_PERCENT
    assert config.STORAGE_WARNING_PERCENT >= 0
    assert config.STORAGE_CRITICAL_PERCENT <= 100

    # Forecast thresholds (more urgent = fewer days, so critical < warning)
    assert config.STORAGE_FORECAST_CRITICAL_DAYS < config.STORAGE_FORECAST_WARNING_DAYS
    assert config.STORAGE_FORECAST_CRITICAL_DAYS > 0

    # Latency thresholds (higher is worse, so critical > warning)
    assert config.LATENCY_CRITICAL_MS > config.LATENCY_WARNING_MS
    assert config.LATENCY_WARNING_MS > 0


def test_reputation_thresholds():
    """Test reputation alert thresholds."""
    from storj_monitor.config import (
        AUDIT_SCORE_CRITICAL,
        AUDIT_SCORE_WARNING,
        ONLINE_SCORE_WARNING,
        SUSPENSION_SCORE_CRITICAL,
    )

    # Verify values are reasonable
    assert AUDIT_SCORE_WARNING == 85.0
    assert AUDIT_SCORE_CRITICAL == 70.0
    assert SUSPENSION_SCORE_CRITICAL == 60.0
    assert ONLINE_SCORE_WARNING == 95.0


def test_storage_capacity_thresholds():
    """Test storage capacity thresholds."""
    from storj_monitor.config import (
        MIN_STORAGE_DATA_POINTS_FOR_FORECAST,
        STORAGE_CRITICAL_PERCENT,
        STORAGE_FORECAST_CRITICAL_DAYS,
        STORAGE_FORECAST_WARNING_DAYS,
        STORAGE_WARNING_PERCENT,
    )

    assert STORAGE_WARNING_PERCENT == 80
    assert STORAGE_CRITICAL_PERCENT == 95
    assert STORAGE_FORECAST_WARNING_DAYS == 30
    assert STORAGE_FORECAST_CRITICAL_DAYS == 7
    assert MIN_STORAGE_DATA_POINTS_FOR_FORECAST == 12


def test_performance_thresholds():
    """Test performance alert thresholds."""
    from storj_monitor.config import LATENCY_CRITICAL_MS, LATENCY_WARNING_MS

    assert LATENCY_WARNING_MS == 5000
    assert LATENCY_CRITICAL_MS == 10000


def test_anomaly_detection_config():
    """Test anomaly detection configuration."""
    from storj_monitor.config import (
        ALERT_COOLDOWN_MINUTES,
        ALERT_EVALUATION_INTERVAL_MINUTES,
        ANALYTICS_BASELINE_UPDATE_HOURS,
        ANOMALY_BASELINE_DAYS,
        ANOMALY_ZSCORE_THRESHOLD,
        ENABLE_ANOMALY_DETECTION,
    )

    assert isinstance(ENABLE_ANOMALY_DETECTION, bool)
    assert ANOMALY_ZSCORE_THRESHOLD == 3.0
    assert ANOMALY_BASELINE_DAYS == 7
    assert ALERT_EVALUATION_INTERVAL_MINUTES == 5
    assert ALERT_COOLDOWN_MINUTES == 15
    assert ANALYTICS_BASELINE_UPDATE_HOURS == 24


def test_notification_settings():
    """Test notification configuration."""
    from storj_monitor.config import (
        ENABLE_BROWSER_NOTIFICATIONS,
        ENABLE_EMAIL_NOTIFICATIONS,
        ENABLE_WEBHOOK_NOTIFICATIONS,
    )

    assert isinstance(ENABLE_BROWSER_NOTIFICATIONS, bool)
    assert isinstance(ENABLE_EMAIL_NOTIFICATIONS, bool)
    assert isinstance(ENABLE_WEBHOOK_NOTIFICATIONS, bool)


def test_email_config():
    """Test email configuration."""
    from storj_monitor.config import (
        EMAIL_SMTP_PORT,
        EMAIL_SMTP_SERVER,
        EMAIL_TO_ADDRESSES,
        EMAIL_USE_TLS,
    )

    assert isinstance(EMAIL_SMTP_SERVER, str)
    assert isinstance(EMAIL_SMTP_PORT, int)
    assert isinstance(EMAIL_USE_TLS, bool)
    assert isinstance(EMAIL_TO_ADDRESSES, list)


def test_webhook_config():
    """Test webhook configuration."""
    from storj_monitor.config import WEBHOOK_CUSTOM_URLS, WEBHOOK_DISCORD_URL, WEBHOOK_SLACK_URL

    assert isinstance(WEBHOOK_DISCORD_URL, str)
    assert isinstance(WEBHOOK_SLACK_URL, str)
    assert isinstance(WEBHOOK_CUSTOM_URLS, list)


def test_data_retention_config():
    """Test data retention configuration."""
    from storj_monitor.config import (
        DB_ALERTS_RETENTION_DAYS,
        DB_ANALYTICS_RETENTION_DAYS,
        DB_EARNINGS_RETENTION_DAYS,
        DB_EVENTS_RETENTION_DAYS,
        DB_INSIGHTS_RETENTION_DAYS,
    )

    assert DB_EVENTS_RETENTION_DAYS == 2
    assert DB_ALERTS_RETENTION_DAYS == 90
    assert DB_INSIGHTS_RETENTION_DAYS == 90
    assert DB_ANALYTICS_RETENTION_DAYS == 180
    assert DB_EARNINGS_RETENTION_DAYS == 365


def test_financial_tracking_config():
    """Test financial tracking configuration."""
    from storj_monitor.config import (
        ENABLE_FINANCIAL_TRACKING,
        HELD_AMOUNT_MONTHS_1_TO_3,
        HELD_AMOUNT_MONTHS_4_TO_6,
        HELD_AMOUNT_MONTHS_7_TO_9,
        NODE_MONTHLY_COSTS,
        OPERATOR_SHARE_EGRESS,
        OPERATOR_SHARE_STORAGE,
        PRICING_AUDIT_PER_TB,
        PRICING_EGRESS_PER_TB,
        PRICING_REPAIR_PER_TB,
        PRICING_STORAGE_PER_TB_MONTH,
    )

    assert isinstance(ENABLE_FINANCIAL_TRACKING, bool)
    assert PRICING_EGRESS_PER_TB == 2.00
    assert PRICING_STORAGE_PER_TB_MONTH == 1.50
    assert PRICING_REPAIR_PER_TB == 2.00
    assert PRICING_AUDIT_PER_TB == 2.00
    assert OPERATOR_SHARE_EGRESS == 1.0
    assert OPERATOR_SHARE_STORAGE == 1.0
    assert HELD_AMOUNT_MONTHS_1_TO_3 == 0.75
    assert HELD_AMOUNT_MONTHS_4_TO_6 == 0.50
    assert HELD_AMOUNT_MONTHS_7_TO_9 == 0.25
    assert isinstance(NODE_MONTHLY_COSTS, dict)


def test_held_amount_progression():
    """Test that held amount percentages decrease over time."""
    from storj_monitor.config import (
        HELD_AMOUNT_MONTH_16_PLUS,
        HELD_AMOUNT_MONTHS_1_TO_3,
        HELD_AMOUNT_MONTHS_4_TO_6,
        HELD_AMOUNT_MONTHS_7_TO_9,
        HELD_AMOUNT_MONTHS_10_TO_15,
    )

    # Held amounts should decrease as node ages
    assert HELD_AMOUNT_MONTHS_1_TO_3 > HELD_AMOUNT_MONTHS_4_TO_6
    assert HELD_AMOUNT_MONTHS_4_TO_6 > HELD_AMOUNT_MONTHS_7_TO_9
    assert HELD_AMOUNT_MONTHS_7_TO_9 > HELD_AMOUNT_MONTHS_10_TO_15
    assert HELD_AMOUNT_MONTHS_10_TO_15 >= HELD_AMOUNT_MONTH_16_PLUS


def test_environment_variable_overrides():
    """Test that environment variables can override config values."""
    import importlib

    from storj_monitor import config

    # Test DATABASE_FILE override
    test_db_path = "/tmp/test_override.db"
    os.environ["STORJ_MONITOR_DB_PATH"] = test_db_path

    # Reload config to pick up environment variable
    importlib.reload(config)

    assert test_db_path == config.DATABASE_FILE

    # Cleanup
    del os.environ["STORJ_MONITOR_DB_PATH"]
    importlib.reload(config)


def test_websocket_config():
    """Test websocket configuration."""
    from storj_monitor.config import WEBSOCKET_BATCH_INTERVAL_MS, WEBSOCKET_BATCH_SIZE

    assert WEBSOCKET_BATCH_INTERVAL_MS == 25
    assert WEBSOCKET_BATCH_SIZE == 10
    assert WEBSOCKET_BATCH_INTERVAL_MS > 0
    assert WEBSOCKET_BATCH_SIZE > 0


def test_database_queue_config():
    """Test database queue configuration."""
    from storj_monitor.config import (
        DB_CONNECTION_POOL_SIZE,
        DB_QUEUE_MAX_SIZE,
        DB_WRITE_BATCH_INTERVAL_SECONDS,
    )

    assert DB_WRITE_BATCH_INTERVAL_SECONDS == 10
    assert DB_QUEUE_MAX_SIZE == 30000
    assert DB_CONNECTION_POOL_SIZE == 5


def test_geoip_config():
    """Test GeoIP configuration."""
    from storj_monitor.config import GEOIP_DATABASE_PATH, MAX_GEOIP_CACHE_SIZE

    assert isinstance(GEOIP_DATABASE_PATH, str)
    assert GEOIP_DATABASE_PATH == "GeoLite2-City.mmdb"
    assert MAX_GEOIP_CACHE_SIZE == 5000


def test_api_integration_config():
    """Test API integration configuration."""
    from storj_monitor.config import (
        ALLOW_REMOTE_API,
        NODE_API_DEFAULT_PORT,
        NODE_API_POLL_INTERVAL,
        NODE_API_TIMEOUT,
    )

    assert NODE_API_DEFAULT_PORT == 14002
    assert NODE_API_TIMEOUT == 10
    assert NODE_API_POLL_INTERVAL == 300
    assert isinstance(ALLOW_REMOTE_API, bool)


def test_aggregation_interval():
    """Test hourly aggregation interval."""
    from storj_monitor.config import HOURLY_AGG_INTERVAL_MINUTES

    assert HOURLY_AGG_INTERVAL_MINUTES == 10
    assert HOURLY_AGG_INTERVAL_MINUTES > 0


def test_expected_db_columns():
    """Test expected database columns configuration."""
    from storj_monitor.config import EXPECTED_DB_COLUMNS

    assert EXPECTED_DB_COLUMNS == 13


def test_db_retry_configuration():
    """Test database retry configuration."""
    from storj_monitor.config import DB_MAX_RETRIES, DB_RETRY_BASE_DELAY, DB_RETRY_MAX_DELAY

    assert DB_MAX_RETRIES == 3
    assert DB_RETRY_BASE_DELAY == 0.5
    assert DB_RETRY_MAX_DELAY == 5.0
    assert DB_RETRY_BASE_DELAY < DB_RETRY_MAX_DELAY
    assert DB_MAX_RETRIES > 0


def test_pricing_values_are_positive():
    """Test that all pricing values are positive."""
    from storj_monitor.config import (
        PRICING_AUDIT_PER_TB,
        PRICING_EGRESS_PER_TB,
        PRICING_REPAIR_PER_TB,
        PRICING_STORAGE_PER_TB_MONTH,
    )

    assert PRICING_EGRESS_PER_TB > 0
    assert PRICING_STORAGE_PER_TB_MONTH > 0
    assert PRICING_REPAIR_PER_TB > 0
    assert PRICING_AUDIT_PER_TB > 0


def test_operator_shares_are_valid():
    """Test that operator share values are between 0 and 1."""
    from storj_monitor.config import (
        OPERATOR_SHARE_AUDIT,
        OPERATOR_SHARE_EGRESS,
        OPERATOR_SHARE_REPAIR,
        OPERATOR_SHARE_STORAGE,
    )

    assert 0 <= OPERATOR_SHARE_EGRESS <= 1
    assert 0 <= OPERATOR_SHARE_STORAGE <= 1
    assert 0 <= OPERATOR_SHARE_REPAIR <= 1
    assert 0 <= OPERATOR_SHARE_AUDIT <= 1


def test_held_amounts_are_valid_percentages():
    """Test that held amount values are between 0 and 1."""
    from storj_monitor.config import (
        HELD_AMOUNT_MONTH_16_PLUS,
        HELD_AMOUNT_MONTHS_1_TO_3,
        HELD_AMOUNT_MONTHS_4_TO_6,
        HELD_AMOUNT_MONTHS_7_TO_9,
        HELD_AMOUNT_MONTHS_10_TO_15,
    )

    assert 0 <= HELD_AMOUNT_MONTHS_1_TO_3 <= 1
    assert 0 <= HELD_AMOUNT_MONTHS_4_TO_6 <= 1
    assert 0 <= HELD_AMOUNT_MONTHS_7_TO_9 <= 1
    assert 0 <= HELD_AMOUNT_MONTHS_10_TO_15 <= 1
    assert 0 <= HELD_AMOUNT_MONTH_16_PLUS <= 1
