import datetime

from storj_monitor.state import IncrementalStats, app_state


def test_error_aggregation_placeholder_types_and_ranges():
    stats = IncrementalStats()
    TOKEN_REGEX = app_state["TOKEN_REGEX"]

    # Aggregate two similar errors with varying numbers and IPs to exercise:
    # - template building
    # - address placeholder aggregation
    # - number placeholder min/max range update
    stats._aggregate_error("error at 1.2.3.4:1234 code 500 and token 42", TOKEN_REGEX)
    stats._aggregate_error("error at 1.2.3.5:1234 code 404 and token 43", TOKEN_REGEX)

    assert len(stats.error_agg) == 1
    template, data = next(iter(stats.error_agg.items()))
    assert data["count"] == 2
    placeholders = data["placeholders"]

    # Ensure we saw an address placeholder and at least one number placeholder with a range
    address_ph = next((ph for ph in placeholders if ph["type"] == "address"), None)
    number_ph = next((ph for ph in placeholders if ph["type"] == "number"), None)
    assert address_ph is not None
    assert number_ph is not None
    # After two updates with 500 and 404, min/max should bound the range
    assert number_ph["min"] <= 404
    assert number_ph["max"] >= 500


def test_to_payload_includes_countries_and_buckets_and_top_pieces():
    stats = IncrementalStats()
    TOKEN_REGEX = app_state["TOKEN_REGEX"]

    # Craft a few events to populate stats
    base_ts = datetime.datetime.now(datetime.timezone.utc)

    events = [
        # Successful GET from US increments dl_success and countries_dl
        {
            "category": "get",
            "status": "success",
            "satellite_id": "sat-1",
            "size": 2048,
            "piece_id": "p1",
            "location": {"country": "US"},
            "error_reason": None,
        },
        # Successful PUT from CA increments ul_success and countries_ul
        {
            "category": "put",
            "status": "success",
            "satellite_id": "sat-1",
            "size": 1024,
            "piece_id": "p2",
            "location": {"country": "CA"},
            "error_reason": None,
        },
        # Failed GET with error goes to error aggregation
        {
            "category": "get",
            "status": "failed",
            "satellite_id": "sat-1",
            "size": 512,
            "piece_id": "p1",
            "location": {"country": "US"},
            "error_reason": "timeout after 30s to 10.0.0.1:7777",
        },
    ]

    for e in events:
        stats.add_event(e, TOKEN_REGEX)

    payload = stats.to_payload([])

    assert payload["type"] == "stats_update"
    assert "overall" in payload and "satellites" in payload
    # Countries should appear
    assert any(c["country"] == "US" for c in payload["top_countries_dl"])
    assert any(c["country"] == "CA" for c in payload["top_countries_ul"])
    # Transfer sizes buckets present and carry counts/sizes keys
    assert payload["transfer_sizes"]
    assert {"downloads_success", "uploads_success", "downloads_success_size", "uploads_success_size"} <= set(
        payload["transfer_sizes"][0].keys()
    )
    # Hot pieces tracked
    assert any(tp["id"] == "p1" for tp in payload["top_pieces"])