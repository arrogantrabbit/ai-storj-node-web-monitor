import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from collections import Counter
import heapq

# Incremental Stats Accumulator
@dataclass
class IncrementalStats:
    """Maintains running statistics that can be updated incrementally."""
    # Overall counters
    dl_success: int = 0
    dl_fail: int = 0
    ul_success: int = 0
    ul_fail: int = 0
    audit_success: int = 0
    audit_fail: int = 0
    total_dl_size: int = 0
    total_ul_size: int = 0

    # Live stats (last minute)
    live_dl_bytes: int = 0
    live_ul_bytes: int = 0

    # Satellite stats
    satellites: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Country stats
    countries_dl: Counter = field(default_factory=Counter)
    countries_ul: Counter = field(default_factory=Counter)

    # Transfer size buckets
    dls_success: Counter = field(default_factory=Counter)
    dls_failed: Counter = field(default_factory=Counter)
    uls_success: Counter = field(default_factory=Counter)
    uls_failed: Counter = field(default_factory=Counter)

    # Error aggregation
    error_agg: Dict[str, Dict] = field(default_factory=dict)
    error_templates_cache: Dict[str, tuple] = field(default_factory=dict)

    # Hot pieces tracking
    hot_pieces: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Last processed event index per node for incremental updates
    last_processed_indices: Dict[str, int] = field(default_factory=dict)

    def get_or_create_satellite(self, sat_id: str) -> Dict[str, int]:
        """Get or create satellite stats."""
        if sat_id not in self.satellites:
            self.satellites[sat_id] = {
                'uploads': 0, 'downloads': 0, 'audits': 0,
                'ul_success': 0, 'dl_success': 0, 'audit_success': 0,
                'total_upload_size': 0, 'total_download_size': 0
            }
        return self.satellites[sat_id]

    def add_event(self, event: Dict[str, Any], TOKEN_REGEX: re.Pattern):
        """Add a single event to the running statistics."""
        from .log_processor import get_size_bucket

        # Extract event data
        category = event['category']
        status = event['status']
        sat_id = event['satellite_id']
        size = event['size']

        sat_stats = self.get_or_create_satellite(sat_id)
        is_success = status == 'success'

        # Update stats based on category
        if category == 'audit':
            sat_stats['audits'] += 1
            if is_success:
                self.audit_success += 1
                sat_stats['audit_success'] += 1
            else:
                self.audit_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)

        elif category == 'get':
            sat_stats['downloads'] += 1

            # Update hot pieces
            piece_id = event['piece_id']
            if piece_id not in self.hot_pieces:
                self.hot_pieces[piece_id] = {'count': 0, 'size': 0}
            hot_piece = self.hot_pieces[piece_id]
            hot_piece['count'] += 1
            hot_piece['size'] += size

            # Update country stats
            country = event['location']['country']
            if country:
                self.countries_dl[country] += size

            if is_success:
                self.dl_success += 1
                sat_stats['dl_success'] += 1
                sat_stats['total_download_size'] += size
                self.total_dl_size += size
                size_bucket = get_size_bucket(size)
                self.dls_success[size_bucket] += 1
            else:
                self.dl_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)
                size_bucket = get_size_bucket(size)
                self.dls_failed[size_bucket] += 1

        elif category == 'put':
            sat_stats['uploads'] += 1

            # Update country stats
            country = event['location']['country']
            if country:
                self.countries_ul[country] += size

            if is_success:
                self.ul_success += 1
                sat_stats['ul_success'] += 1
                sat_stats['total_upload_size'] += size
                self.total_ul_size += size
                size_bucket = get_size_bucket(size)
                self.uls_success[size_bucket] += 1
            else:
                self.ul_fail += 1
                self._aggregate_error(event['error_reason'], TOKEN_REGEX)
                size_bucket = get_size_bucket(size)
                self.uls_failed[size_bucket] += 1

    def _aggregate_error(self, reason: str, TOKEN_REGEX: re.Pattern):
        """Aggregate error reasons efficiently with optimized template building."""
        if not reason:
            return

        if reason in self.error_templates_cache:
            template, tokens = self.error_templates_cache[reason]
        else:
            matches = TOKEN_REGEX.finditer(reason)
            try:
                first_match = next(matches)
                tokens = [first_match.group(0)]
            except StopIteration:
                if len(self.error_templates_cache) < 1000: self.error_templates_cache[reason] = (reason, [])
                template, tokens = reason, []
            else:
                template_parts = [reason[:first_match.start()], '#']
                last_end = first_match.end()
                for match in matches:
                    start = match.start()
                    if start > last_end: template_parts.append(reason[last_end:start])
                    template_parts.append('#')
                    tokens.append(match.group(0))
                    last_end = match.end()
                if last_end < len(reason): template_parts.append(reason[last_end:])
                template = "".join(template_parts)
                if len(self.error_templates_cache) < 1000: self.error_templates_cache[reason] = (template, tokens)

        if template not in self.error_agg:
            placeholders = []
            for token in tokens:
                if '.' in token or ':' in token:
                    placeholders.append({'type': 'address', 'seen': {token}})
                else:
                    try:
                        num = int(token)
                        placeholders.append({'type': 'number', 'min': num, 'max': num})
                    except ValueError:
                        placeholders.append({'type': 'string', 'seen': {token}})
            self.error_agg[template] = {'count': 1, 'placeholders': placeholders}
        else:
            agg_item = self.error_agg[template]
            agg_item['count'] += 1
            if len(tokens) == len(agg_item['placeholders']):
                for i, token in enumerate(tokens):
                    ph = agg_item['placeholders'][i]
                    if ph['type'] == 'address':
                        if len(ph['seen']) < 100: ph['seen'].add(token)
                    elif ph['type'] == 'number':
                        try:
                            num = int(token)
                            if num < ph['min']: ph['min'] = num
                            elif num > ph['max']: ph['max'] = num
                        except ValueError: pass

    def update_live_stats(self, events: list[dict[str, any]]):
        """Update live stats for the last minute."""
        import time
        one_min_ago = time.time() - 60
        self.live_dl_bytes, self.live_ul_bytes = 0, 0
        for event in events:
            if event['ts_unix'] > one_min_ago and event['status'] == 'success':
                if event['category'] == 'get': self.live_dl_bytes += event['size']
                elif event['category'] == 'put': self.live_ul_bytes += event['size']

    def to_payload(self, historical_stats: list[dict] = None) -> dict[str, any]:
        """Convert stats to a JSON payload with sliding time window."""
        import datetime
        from .config import STATS_WINDOW_MINUTES
        
        # Calculate time range based on sliding window, not tracked events
        last_event_ts = datetime.datetime.now(datetime.timezone.utc)
        first_event_ts = last_event_ts - datetime.timedelta(minutes=STATS_WINDOW_MINUTES)
        
        avg_egress_mbps = (self.live_dl_bytes * 8) / (60 * 1e6)
        avg_ingress_mbps = (self.live_ul_bytes * 8) / (60 * 1e6)
        satellites = sorted([{'satellite_id': k, **v} for k, v in self.satellites.items()],
                            key=lambda x: x['uploads'] + x['downloads'], reverse=True)
        all_buckets = ["< 1 KB", "1-4 KB", "4-16 KB", "16-64 KB", "64-256 KB", "256 KB - 1 MB", "> 1 MB"]
        transfer_sizes = [{'bucket': b, 'downloads_success': self.dls_success[b], 'downloads_failed': self.dls_failed[b],
                           'uploads_success': self.uls_success[b], 'uploads_failed': self.uls_failed[b]} for b in all_buckets]
        sorted_errors = sorted(self.error_agg.items(), key=lambda item: item[1]['count'], reverse=True)
        final_errors = []
        for template, data in sorted_errors[:10]:
            final_msg = template
            if 'placeholders' in data:
                for ph_data in data['placeholders']:
                    if ph_data['type'] == 'number':
                        min_val, max_val = ph_data['min'], ph_data['max']
                        range_str = str(min_val) if min_val == max_val else f"({min_val}..{max_val})"
                        final_msg = final_msg.replace('#', range_str, 1)
                    elif ph_data['type'] == 'address':
                        count = len(ph_data['seen'])
                        range_str = f"[{count} unique address{'es' if count > 1 else ''}]"
                        final_msg = final_msg.replace('#', range_str, 1)
            final_errors.append({'reason': final_msg, 'count': data['count']})
        top_pieces = [{'id': k, 'count': v['count'], 'size': v['size']} for k, v in
                      heapq.nlargest(10, self.hot_pieces.items(), key=lambda item: item[1]['count'])]
        return {"type": "stats_update",
                "first_event_iso": first_event_ts.isoformat(),
                "last_event_iso": last_event_ts.isoformat(),
                "overall": {"dl_success": self.dl_success, "dl_fail": self.dl_fail, "ul_success": self.ul_success,
                            "ul_fail": self.ul_fail, "audit_success": self.audit_success, "audit_fail": self.audit_fail,
                            "avg_egress_mbps": avg_egress_mbps, "avg_ingress_mbps": avg_ingress_mbps},
                "satellites": satellites, "transfer_sizes": transfer_sizes, "historical_stats": historical_stats or [],
                "error_categories": final_errors, "top_pieces": top_pieces,
                "top_countries_dl": [{'country': k, 'size': v} for k, v in self.countries_dl.most_common(10) if k],
                "top_countries_ul": [{'country': k, 'size': v} for k, v in self.countries_ul.most_common(10) if k]}


# --- In-Memory State ---
app_state: Dict[str, Any] = {
    'websockets': {},  # {ws: {"view": ["Aggregate"]}}
    'nodes': {},  # { "node_name": NodeState }
    'geoip_cache': {},
    'db_write_lock': asyncio.Lock(),  # Lock to serialize DB write operations
    'db_write_queue': asyncio.Queue(), # size is set in config
    'stats_cache': {},  # Cache for pre-computed stats payloads
    'incremental_stats': {},  # New: { view_tuple: IncrementalStats }
    'websocket_event_queue': [],  # Queue for batching websocket events
    'websocket_queue_lock': asyncio.Lock(),  # Lock for websocket queue operations
    'TOKEN_REGEX': re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\b\d+\b'),  # Pre-compiled regex
}
