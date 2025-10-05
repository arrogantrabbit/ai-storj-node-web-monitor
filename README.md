# Storagenode Pro Monitor

Storagenode Pro Monitor is a high-performance, real-time web dashboard for Storj node operators. It provides a detailed, live view of your node's activity, including traffic statistics, success rates, performance metrics, and a global traffic heatmap.

## Features

### Core Monitoring (Log-Based)
*   **Live Traffic Heatmap:** Visualize uploads, downloads, and audits in real-time on a world map.
*   **Comprehensive Stats:** Track success rates, data transfer volumes, and operation counts for downloads, uploads, and audits.
*   **Performance Charting:** Live and historical graphs for bandwidth, piece counts, and operational concurrency.
*   **Multi-Node Support:** Monitor multiple nodes simultaneously, either individually or as an aggregated view.
*   **Detailed Analysis:** View breakdowns by satellite, transfer size, top error types, and most frequently accessed pieces.
*   **Remote Monitoring:** The server can connect to remote log sources, enabling high-fidelity monitoring even when the dashboard is run on a separate machine.
*   **Efficient Architecture:** Built with modern Python `asyncio` and `watchdog` for minimal impact on your storagenode's performance.
*   **Historical Log Ingestion:** A one-time ingestion mode to pre-populate the database from existing log files, providing immediate historical context.

### Enhanced Monitoring (API-Based) ⭐ NEW
When the node API endpoint is provided or auto-discovered, additional features are automatically enabled:

*   **Reputation Monitoring:** Track audit, suspension, and online scores per satellite with automatic alerts for low scores that could lead to suspension or disqualification.
*   **Storage Capacity Tracking:** Monitor disk usage, growth rate, and forecast when your disk will be full to prevent downtime.
*   **Latency Analytics:** Track operation response times with p50/p95/p99 percentiles and identify slow operations.
*   **Proactive Alerting:** Receive warnings before critical issues occur (low reputation scores, high disk usage, performance degradation).

## Prerequisites

1.  **Python Environment:** Python 3.9+ is required. The application uses `uv` (or `pip`) to manage its environment and will install dependencies automatically.
2.  **GeoIP Database:** The application uses the MaxMind GeoLite2 City database to map IP addresses to physical locations for the heatmap.
    *   Download the free database from the [MaxMind website](https://www.maxmind.com/en/geolite2/signup).
    *   After signing up, download the `GeoLite2-City.mmdb` file.
    *   Place the `GeoLite2-City.mmdb` file in the same top-level directory as the `storj_monitor` package.

---

## Installation & Usage

This application is designed to be run as a command-line tool. The recommended workflow is to use `uv tool install` to make it available on your system. This avoids creating `.venv` directories in your project folder.

### 1. Installation

From your project's root directory (the one containing `pyproject.toml`), run the following command:

```bash
# Install the tool using the current directory as the source
uv tool install .
```

`uv` will install the `storj-pro-monitor` and its dependencies into a managed environment. It may prompt you to add its tool `bin` directory to your shell's `PATH`. **This is a required one-time setup step.** Follow the instructions provided by `uv`. A common way to do this is to add the following line to your shell's startup file (e.g., `~/.zshrc`, `~/.bashrc`):

```bash
export PATH="$HOME/.uv/tools/bin:$PATH"
```

After updating your `PATH`, restart your terminal or source the startup file (e.g., `source ~/.zshrc`).

### 1a. Editable (Developer) Mode

If you are developing the tool and want your code changes to be reflected immediately without reinstalling, use the `-e` flag for an "editable" install:

```bash
# From your project's root directory
uv tool install -e .
```

### 2. Updating the Tool

If you have already installed the tool and need to apply updates (like this fix), you must reinstall it:

```bash
# From your project's root directory
uv tool install . --reinstall
```

### 3. Running the Monitor

Once installed, you can run the monitor from any directory.

#### Node Configuration Format

The `--node` parameter supports an extended syntax for enhanced monitoring features:

```
--node "NodeName:log_source[:api_endpoint]"
```

Where:
- `NodeName` is a friendly name for your node
- `log_source` is either:
  - A file path: `/path/to/storagenode.log`
  - A network address: `host:port` (for log forwarder)
- `api_endpoint` (optional) is the Storj node API URL (e.g., `http://localhost:14002`)

#### Local Monitoring

**Basic (Log Only):**
```bash
# Single node - log monitoring only
storj_monitor --node "My-Node:/path/to/storagenode.log"
```

**Enhanced (Log + API):**
```bash
# With auto-discovered API (tries http://localhost:14002)
storj_monitor --node "My-Node:/path/to/storagenode.log"

# With explicit API endpoint
storj_monitor --node "My-Node:/path/to/storagenode.log:http://localhost:14002"

# Multiple nodes with different API ports
storj_monitor \
  --node "Node1:/path/to/node1.log:http://localhost:14002" \
  --node "Node2:/path/to/node2.log:http://localhost:15002"
```

**API Auto-Discovery:**
For local nodes (file paths), the system automatically attempts to connect to the node API at `http://localhost:14002`. If successful, enhanced monitoring features are automatically enabled. No explicit API endpoint needed in most cases!

#### Remote Monitoring

The monitor can connect to a simple TCP log stream from another machine. This requires a log forwarding utility on the storagenode machine (see [`log_forwarder.md`](log_forwarder.md)) that streams new log lines to a network port.

**Basic (Log Only):**
```bash
# Remote log forwarder without API
storj_monitor --node "Remote-Node:192.168.1.100:9999"
```

**Enhanced (Log + API):**
```bash
# Remote log forwarder WITH API for enhanced features
storj_monitor --node "Remote-Node:192.168.1.100:9999:http://192.168.1.100:14002"

# Mixed local and remote nodes
storj_monitor \
  --node "Local-Node:/var/log/localnode.log" \
  --node "Remote-Node:192.168.1.100:9999:http://192.168.1.100:14002"
```

**Security Note for Remote APIs:**
The Storj node API does not require authentication but should not be exposed to the internet. For remote monitoring, consider:
- **VPN:** Connect monitoring machine and node via VPN
- **SSH Tunnel:** Create a secure tunnel: `ssh -L 14002:localhost:14002 user@nodehost`
- **Firewall:** Restrict API access to monitoring machine's IP

#### What Gets Enabled with API Access

| Feature | Log Only | Log + API |
|---------|----------|-----------|
| Live traffic heatmap | ✓ | ✓ |
| Success rates & bandwidth | ✓ | ✓ |
| Error analysis | ✓ | ✓ |
| Hashstore monitoring | ✓ | ✓ |
| **Reputation monitoring** | ✗ | ✓ |
| **Storage capacity tracking** | ✗ | ✓ |
| **Latency analytics** | Partial | ✓ |
| **Disk usage forecasting** | ✗ | ✓ |
| **Proactive alerts** | ✗ | ✓ |

After starting the server, view the dashboard by opening a web browser to `http://localhost:8765`.

### 4. One-Time Log Ingestion

To pre-populate the database with historical data, use the `--ingest-log` argument. This command parses an entire log file and exits without starting the web server.

```bash
# Example: Ingest a log file for a node named "My-Node"
storj_monitor --ingest-log "My-Node:/path/to/storagenode.log"
```

### 5. Enhanced Monitoring Setup

#### Verifying API Connection

When enhanced monitoring is enabled, you'll see these log messages on startup:

```
[INFO] [Node 1] API client connected to http://localhost:14002 (Node ID: abc123..., Wallet: 0xdef456..., Version: 1.137.5)
[INFO] Enhanced monitoring enabled for 1 node(s)
[INFO] Reputation polling task started
[INFO] Storage polling task started
```

If API is not available, you'll see:
```
[WARNING] [Node 1] Could not auto-discover API. Enhanced features disabled.
```

The monitor will continue working normally in log-only mode.

#### Monitoring Logs for Enhanced Features

With API enabled, watch for important alerts:

```bash
# Monitor for reputation warnings
[WARNING] [Node 1] WARNING: Low Audit Score on us1 - Audit score is 82.50% (threshold: 85.0%)

# Monitor for storage warnings
[WARNING] [Node 1] WARNING: High Disk Usage on Node 1 - Disk is 82.3% full (threshold: 80.0%)

# Monitor for capacity forecasts
[INFO] [Node 1] Growth rate: 10.5 GB/day, full in 38.2 days
```

#### Accessing Enhanced Data

**Via Database:**
```bash
# Check reputation scores
sqlite3 storj_stats.db "SELECT * FROM reputation_history ORDER BY timestamp DESC LIMIT 10;"

# Check storage capacity
sqlite3 storj_stats.db "SELECT * FROM storage_snapshots ORDER BY timestamp DESC LIMIT 10;"

# Check latency data
sqlite3 storj_stats.db "SELECT action, AVG(duration_ms) as avg_ms, MAX(duration_ms) as max_ms FROM events WHERE duration_ms IS NOT NULL GROUP BY action;"
```

**Via WebSocket API (programmatically):**
```javascript
// Get reputation data
ws.send(JSON.stringify({type: "get_reputation_data", view: ["My-Node"]}));

// Get storage data
ws.send(JSON.stringify({type: "get_storage_data", view: ["My-Node"]}));

// Get latency statistics
ws.send(JSON.stringify({type: "get_latency_stats", view: ["My-Node"], hours: 1}));
```

#### Configuration Options

Enhanced monitoring can be configured in `storj_monitor/config.py`:

```python
# API Configuration
NODE_API_DEFAULT_PORT = 14002
NODE_API_POLL_INTERVAL = 300  # Poll every 5 minutes
ALLOW_REMOTE_API = True  # Allow remote API endpoints

# Alert Thresholds
AUDIT_SCORE_WARNING = 85.0
AUDIT_SCORE_CRITICAL = 70.0
SUSPENSION_SCORE_CRITICAL = 60.0
STORAGE_WARNING_PERCENT = 80
STORAGE_CRITICAL_PERCENT = 95
LATENCY_WARNING_MS = 5000
LATENCY_CRITICAL_MS = 10000
```

## Credits

Created by Google Gemini Pro 2.5 and iterated on by Anthropic Opus 4.1, Google Gemini Pro 2.5, and Claude Sonnet 4.5 under the unrelenting guidance of Sir Arrogant Rabbit.


## How it looks:
<img width="903" height="1009" alt="image" src="https://github.com/user-attachments/assets/b07aaa7f-efd4-42f9-9de3-46b8b44f5411" />
<img width="905" height="1038" alt="image" src="https://github.com/user-attachments/assets/88be66e8-7151-4b04-8df0-eb8954cdc60d" />
