# Storagenode Pro Monitor

Storagenode Pro Monitor is a high-performance, real-time web dashboard for Storj node operators. It provides a detailed, live view of your node's activity, including traffic statistics, success rates, performance metrics, and a global traffic heatmap.

## Features

*   **Live Traffic Heatmap:** Visualize uploads, downloads, and audits in real-time on a world map.
*   **Comprehensive Stats:** Track success rates, data transfer volumes, and operation counts for downloads, uploads, and audits.
*   **Performance Charting:** Live and historical graphs for bandwidth, piece counts, and operational concurrency.
*   **Multi-Node Support:** Monitor multiple nodes simultaneously, either individually or as an aggregated view.
*   **Detailed Analysis:** View breakdowns by satellite, transfer size, top error types, and most frequently accessed pieces.
*   **Remote Monitoring:** A purpose-built, lightweight log forwarder enables smooth, high-fidelity monitoring even when the dashboard is run on a separate machine.
*   **Efficient Architecture:** Built with modern Python `asyncio` and `watchdog` for minimal impact on your storagenode's performance.
*   **Historical Log Ingestion:** A one-time ingestion mode to pre-populate the database from existing log files, providing immediate historical context.

## Prerequisites

1.  **Python Environment:** Python 3.9+ is required. The scripts are self-bootstrapping and will install dependencies automatically when run with a compatible tool like `uv` or `pip`.
2.  **GeoIP Database:** The application uses the MaxMind GeoLite2 City database to map IP addresses to physical locations for the heatmap.
    *   Download the free database from the [MaxMind website](https://www.maxmind.com/en/geolite2/signup).
    *   After signing up, download the `GeoLite2-City.mmdb` file.
    *   Place the `GeoLite2-City.mmdb` file in the same directory as `websies.py`.

---

## Getting Started: Basic Local Monitoring

This is the simplest way to run the monitor, with the dashboard running on the same machine as your storagenode.

1.  **Arrange Files:** Place `websies.py`, `index.html`, and the `GeoLite2-City.mmdb` file you downloaded into the same directory.

2.  **Run the Application:** Open a terminal and run the main application script. You must specify a unique name and the full path to your node's log file for each node you wish to monitor.

    ```bash
    # For a single node
    uv run websies.py --node "My-Node:/path/to/storagenode.log"

    # For multiple nodes on the same machine
    uv run websies.py --node "Node1:/path/to/node1.log" --node "Node2:/path/to/node2.log"
    ```
    *The server will start and automatically install its dependencies.*

3.  **View the Dashboard:** Open a web browser and navigate to `http://localhost:8765`.

---

## Advanced Usage

### One-Time Log Ingestion

To start the monitor with a rich set of historical data instead of a blank database, you can perform a one-time ingestion of your existing storagenode log files.

This mode will parse an entire log file, extract all relevant traffic and hashstore compaction events, and write them directly to the database. It will then pre-calculate the hourly statistics for historical charts. The script exits upon completion without starting the web server.

**To ingest a log file, use the `--ingest-log` argument:**

```bash
# Example: Ingest a log file for a node named "My-Node"
uv run websies.py --ingest-log "My-Node:/path/to/storagenode.log"
```

Run this command for each node you plan to monitor. Once the ingestion is complete, you can start the monitor in the normal server mode, and all your historical data will be immediately visible in the dashboard.

### Remote Monitoring with Log Forwarder

If you want to run the dashboard on a separate server (e.g., a home server or VM) and monitor storagenodes running elsewhere, using the log forwarder is the recommended approach.

While mounting the log file directory via NFS is an option, network latency and filesystem caching can batch log updates. This disrupts the high-resolution event timing required for the dashboard's smooth animations. The `log_forwarder.py` utility solves this problem by tailing the log file locally and streaming new entries with high-precision timestamps over a simple TCP connection.

#### Step 1: On Each Storagenode Machine

Run the lightweight `log_forwarder.py` script. It will watch the log file and listen for a connection from your main monitoring server.

```bash
# Example for a storagenode
uv run log_forwarder.py --log-file /path/to/storagenode.log --port 9999
```
*   You can choose any available port.
*   Ensure this port is accessible from your monitoring server (you may need to configure a firewall rule).

#### Step 2: On the Monitoring Server

Run the main `websies.py` application, but instead of a file path, provide the IP address and port of the storagenode running the forwarder.

```bash
# Example connecting to a storagenode at 192.168.1.100
uv run websies.py --node "Remote-Node:192.168.1.100:9999"
```

You can seamlessly monitor a mix of local and remote nodes:
```bash
uv run websies.py \
  --node "Local-Node:/var/log/localnode.log" \
  --node "Remote-Node-1:192.168.1.100:9999" \
  --node "Remote-Node-2:192.168.1.101:9999"
```

## Credits

Created by Google Gemini Pro 2.5 and iterated on by Anthropic Opus 4.1 and Google Gemini Pro 2.5 under the unrelenting guidance of Sir Arrogant Rabbit.


## How it looks:
<img width="903" height="1009" alt="image" src="https://github.com/user-attachments/assets/b07aaa7f-efd4-42f9-9de3-46b8b44f5411" />
<img width="905" height="1038" alt="image" src="https://github.com/user-attachments/assets/88be66e8-7151-4b04-8df0-eb8954cdc60d" />
