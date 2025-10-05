# Storage Data from Historical Logs - Implementation Summary

## Problem
When ingesting historical data from log files, the Storage Health & Capacity section was empty because:
1. Storage data comes from the Storj Node API (polled every 5 minutes)
2. During historical log ingestion, there's no API connection
3. Only live monitoring had storage data

## Solution
Extract storage capacity information directly from DEBUG-level log entries, which contain the `Available Space` field in every upload/download operation.

## Implementation Details

### 1. Log Parser Enhancement ([`log_processor.py`](storj_monitor/log_processor.py:186))
- Modified `parse_log_line()` to extract `Available Space` from operation start messages
- Added field to operation_start return value

### 2. Storage Sampling Logic ([`log_processor.py`](storj_monitor/log_processor.py:260))
- Implemented intelligent sampling to avoid database spam
- **Sampling Strategy:**
  - Sample every 5 minutes (300 seconds)
  - Only write if space changed by >1GB (significant change detection)
  - Prevents thousands of identical writes during log ingestion

### 3. Storage Snapshot Creation
When a sample is taken, creates a partial storage snapshot:
```python
{
    'timestamp': parsed['timestamp'],
    'node_name': node_name,
    'available_bytes': available_space,  # From logs
    'total_bytes': None,  # Unknown from logs
    'used_bytes': None,   # Unknown from logs
    'trash_bytes': None,  # Unknown from logs
    'used_percent': None, # Cannot calculate without total
    'trash_percent': None,# Unknown from logs
    'available_percent': None, # Cannot calculate without total
    'source': 'logs'  # Metadata to identify source
}
```

### 4. Database Support ([`database.py`](storj_monitor/database.py:845))
- Modified `blocking_write_storage_snapshot()` to handle partial snapshots
- Changed all required fields to optional using `.get()`
- Added source metadata logging for debugging

### 5. Frontend Compatibility
- Frontend already handles missing data gracefully
- Displays "N/A" for unavailable fields (like used_percent)
- Shows available space when present
- No frontend changes needed!

## Benefits

✅ **Historical Data Support**: Storage capacity visible during historical log ingestion  
✅ **Intelligent Sampling**: Prevents database bloat with smart filtering  
✅ **API Compatibility**: Works alongside API-based storage tracking  
✅ **Graceful Degradation**: Shows partial data when full data unavailable  
✅ **No Breaking Changes**: Existing functionality preserved  

## Example Log Entry
```
2025-10-04T23:06:52-07:00 DEBUG piecestore upload started 
{"Process": "storagenode", "Piece ID": "3K6MV...", "Satellite ID": "12Eay...", 
 "Action": "PUT", "Remote Address": "79.127.205.230:40844", 
 "Available Space": 14540395224064}  // ← Extracted!
```

## Limitations
- Only tracks `available_bytes` from logs (not total/used/trash)
- Cannot calculate percentages without total capacity
- Requires DEBUG-level logging to be enabled
- 5-minute sampling may miss rapid changes

## Testing
To verify:
1. Ingest historical log file with DEBUG entries
2. Check Storage Health & Capacity card
3. Should show available space from logs
4. Database should contain log-based snapshots with `source='logs'`

## Future Enhancements
- Could parse total capacity from node startup messages
- Could correlate with API data for complete picture
- Could add trend analysis for log-based data