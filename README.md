# Storagenode Pro Monitor

Storagenode Pro Monitor is a high-performance, real-time web dashboard for Storj node operators. It provides a detailed, live view of your node's activity, including traffic statistics, success rates, performance metrics, and a global traffic heatmap.

## Features

*   **Live Traffic Heatmap:** Visualize uploads, downloads, and audits in real-time on a world map.
*   **Comprehensive Stats:** Track success rates, data transfer volumes, and operation counts for downloads, uploads, and audits.
*   **Performance Charting:** Live and historical graphs for bandwidth, piece counts, and operational concurrency.
*   **Multi-Node Support:** Monitor multiple nodes simultaneously, either individually or as an aggregated view.
*   **Detailed Analysis:** View breakdowns by satellite, transfer size, top error types, and most frequently accessed pieces.
*   **Remote Monitoring:** The server can connect to remote log sources, enabling high-fidelity monitoring even when the dashboard is run on a separate machine.
*   **Efficient Architecture:** Built with modern Python `asyncio` and `watchdog` for minimal impact on your storagenode's performance.
*   **Historical Log Ingestion:** A one-time ingestion mode to pre-populate the database from existing log files, providing immediate historical context.

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

#### Local Monitoring
This is the simplest use case, monitoring a log file on the same machine.

```bash
# For a single node
storj_monitor --node "My-Node:/path/to/storagenode.log"

# For multiple nodes on the same machine
storj_monitor --node "Node1:/path/to/node1.log" --node "Node2:/path/to/node2.log"
```

#### Remote Monitoring
The monitor can connect to a simple TCP log stream from another machine. This requires a log forwarding utility on the storagenode machine (see `log_processor.py` included in this package) that streams new log lines to a network port.

```bash
# Example connecting to a remote node at 192.168.1.100 on port 9999
storj_monitor --node "Remote-Node:192.168.1.100:9999"

# You can seamlessly monitor a mix of local and remote nodes:
storj_monitor \
  --node "Local-Node:/var/log/localnode.log" \
  --node "Remote-Node-1:192.168.1.100:9999"
```

After starting the server, view the dashboard by opening a web browser to `http://localhost:8765`.

### 4. One-Time Log Ingestion

To pre-populate the database with historical data, use the `--ingest-log` argument. This command parses an entire log file and exits without starting the web server.

```bash
# Example: Ingest a log file for a node named "My-Node"
storj_monitor --ingest-log "My-Node:/path/to/storagenode.log"
```

## Credits

Created by Google Gemini Pro 2.5 and iterated on by Anthropic Opus 4.1 and Google Gemini Pro 2.5 under the unrelenting guidance of Sir Arrogant Rabbit.


## How it looks:
<img width="903" height="1009" alt="image" src="https://github.com/user-attachments/assets/b07aaa7f-efd4-42f9-9de3-46b8b44f5411" />
<img width="905" height="1038" alt="image" src="https://github.com/user-attachments/assets/88be66e8-7151-4b04-8df0-eb8954cdc60d" />
