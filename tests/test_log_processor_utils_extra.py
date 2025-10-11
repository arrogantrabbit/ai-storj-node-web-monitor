import pytest

from storj_monitor.log_processor import (
    parse_size_to_bytes,
    parse_duration_str_to_seconds,
    get_size_bucket,
    categorize_action,
)


def test_parse_size_to_bytes_decimal_and_binary_units():
    # Decimal (SI) units
    assert parse_size_to_bytes("7.05 GB") == int(7.05 * 1000**3)
    assert parse_size_to_bytes("181.90 GB") == int(181.90 * 1000**3)
    assert parse_size_to_bytes("1.5 MB") == int(1.5 * 1000**2)
    assert parse_size_to_bytes("999 KB") == 999_000

    # Binary (IEC) units
    assert parse_size_to_bytes("7.05 GiB") == int(7.05 * 1024**3)
    assert parse_size_to_bytes("512 MiB") == int(512 * 1024**2)
    assert parse_size_to_bytes("1 KiB") == 1024

    # Missing unit defaults to bytes
    assert parse_size_to_bytes("123") == 123

    # Weird casing and spacing
    assert parse_size_to_bytes("  2.5  gib ") == int(2.5 * 1024**3)

    # Unknown unit falls back reasonably to bytes of the parsed number
    assert parse_size_to_bytes("42 foo") == 42

    # Invalid inputs
    assert parse_size_to_bytes("") == 0
    assert parse_size_to_bytes(None) == 0  # type: ignore[arg-type]
    assert parse_size_to_bytes("not-a-size") == 0


def test_parse_duration_str_to_seconds_variants():
    # Mixed units
    assert parse_duration_str_to_seconds("1h2m3s") == pytest.approx(3723.0)
    assert parse_duration_str_to_seconds("1m37.5s") == pytest.approx(97.5)
    assert parse_duration_str_to_seconds("500ms") == pytest.approx(0.5)
    assert parse_duration_str_to_seconds("42.281s") == pytest.approx(42.281)

    # No unit should be interpreted as seconds (float parse)
    assert parse_duration_str_to_seconds("2") == pytest.approx(2.0)

    # Invalid
    assert parse_duration_str_to_seconds("") is None
    assert parse_duration_str_to_seconds("abc") is None
    assert parse_duration_str_to_seconds(None) is None  # type: ignore[arg-type]


def test_get_size_bucket_boundaries_and_cache():
    # Below 1 KB
    assert get_size_bucket(0) == "< 1 KB"
    assert get_size_bucket(1023) == "< 1 KB"

    # Exact thresholds and within ranges
    assert get_size_bucket(1024) == "1-4 KB"
    assert get_size_bucket(4095) == "1-4 KB"
    assert get_size_bucket(4096) == "4-16 KB"
    assert get_size_bucket(16383) == "4-16 KB"
    assert get_size_bucket(16384) == "16-64 KB"
    assert get_size_bucket(65535) == "16-64 KB"
    assert get_size_bucket(65536) == "64-256 KB"
    assert get_size_bucket(262143) == "64-256 KB"
    assert get_size_bucket(262144) == "256 KB - 1 MB"
    assert get_size_bucket(1048575) == "256 KB - 1 MB"
    assert get_size_bucket(1048576) == "> 1 MB"

    # Call again to exercise cache path
    assert get_size_bucket(1048576) == "> 1 MB"
    assert get_size_bucket(0) == "< 1 KB"


def test_categorize_action():
    assert categorize_action("GET_AUDIT") == "audit"
    assert categorize_action("GET_REPAIR") == "get_repair"
    assert categorize_action("PUT_REPAIR") == "put_repair"
    assert categorize_action("GET") == "get"
    assert categorize_action("PUT") == "put"
    assert categorize_action("UNKNOWN_ACTION") == "other"