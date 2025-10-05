# API Integration Design for Multi-Node Architecture
## Convenient Data Source Configuration

---

## Current Multi-Node Design

The monitor currently supports simple, convenient node configuration:

```bash
# Local log file
storj_monitor --node "Local-Node:/var/log/storagenode.log"

# Remote log forwarder
storj_monitor --node "Remote-Node:192.168.1.100:9999"

# Multiple nodes
storj_monitor \
  --node "Node1:/var/log/node1.log" \
  --node "Node2:192.168.1.100:9999" \
  --node "Node3:/var/log/node3.log"
```

**Key Design Principle:** Simple, unified syntax for all data sources.

---

## Proposed API Integration - Option 1: Extended Node Syntax

### Syntax
```bash
--node "NodeName:log_source[:api_endpoint]"
```

### Examples

#### 1. Local Node (Log + API on same machine)
```bash
# Auto-discover API endpoint (default: http://localhost:14002)
storj_monitor --node "My-Node:/var/log/storagenode.log"

# Explicit API endpoint
storj_monitor --node "My-Node:/var/log/storagenode.log:http://localhost:14002"

# Custom API port
storj_monitor --node "My-Node:/var/log/storagenode.log:http://localhost:15002"
```

#### 2. Remote Node (Log via forwarder + API via HTTP)
```bash
# Remote log, remote API
storj_monitor --node "Remote-Node:192.168.1.100:9999:http://192.168.1.100:14002"

# Remote log, no API (log-only monitoring)
storj_monitor --node "Remote-Node:192.168.1.100:9999"
```

#### 3. Mixed Configuration
```bash
storj_monitor \
  --node "Local1:/var/log/node1.log" \
  --node "Local2:/var/log/node2.log:http://localhost:15002" \
  --node "Remote1:192.168.1.50:9999:http://192.168.1.50:14002" \
  --node "Remote2:192.168.1.51:9999"
```

### Parsing Logic

```python
def parse_node_config(node_string: str) -> dict:
    """
    Parse node configuration string.
    
    Format: "NodeName:log_source[:api_endpoint]"
    
    Returns:
        {
            'name': 'NodeName',
            'log_source': '/path/to/log.log' or 'ip:port',
            'api_endpoint': 'http://ip:port' or None,
            'log_type': 'file' or 'network'
        }
    """
    parts = node_string.split(':')
    
    if len(parts) < 2:
        raise ValueError(f"Invalid node config: {node_string}")
    
    node_name = parts[0]
    
    # Determine if log source is file or network
    if parts[1].startswith('/') or parts[1].startswith('.'):
        # File path
        log_source = parts[1]
        log_type = 'file'
        
        # API endpoint
        if len(parts) >= 3:
            # Explicit API: "Name:/path/to/log:http://localhost:14002"
            api_endpoint = ':'.join(parts[2:])
        else:
            # Auto-discover: try localhost:14002
            api_endpoint = 'http://localhost:14002'
            
    else:
        # Network: "Name:ip:port" or "Name:ip:port:http://ip:port"
        if len(parts) == 3:
            # "Name:ip:port" - log forwarder only
            log_source = f"{parts[1]}:{parts[2]}"
            log_type = 'network'
            api_endpoint = None
            
        elif len(parts) >= 4:
            # "Name:ip:port:http://ip:port" - log + API
            log_source = f"{parts[1]}:{parts[2]}"
            log_type = 'network'
            api_endpoint = ':'.join(parts[3:])
            
        else:
            raise ValueError(f"Invalid network node config: {node_string}")
    
    return {
        'name': node_name,
        'log_source': log_source,
        'api_endpoint': api_endpoint,
        'log_type': log_type
    }
```

### Auto-Discovery

When API endpoint is not specified, attempt auto-discovery:

```python
async def auto_discover_api(node_config: dict) -> str | None:
    """
    Attempt to auto-discover node API endpoint.
    
    Strategy:
    1. If log_type == 'file' and log is local, try localhost:14002
    2. If log_type == 'network', try same IP on port 14002
    3. Test connectivity, return endpoint if successful
    """
    if node_config['api_endpoint']:
        return node_config['api_endpoint']
    
    candidates = []
    
    if node_config['log_type'] == 'file':
        # Local file, try localhost
        candidates = [
            'http://localhost:14002',
            'http://127.0.0.1:14002',
        ]
    elif node_config['log_type'] == 'network':
        # Remote log, extract IP and try API on same host
        log_source = node_config['log_source']
        ip = log_source.split(':')[0]
        candidates = [
            f'http://{ip}:14002',
        ]
    
    # Test each candidate
    for endpoint in candidates:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'{endpoint}/api/sno', timeout=2) as resp:
                    if resp.status == 200:
                        log.info(f"Auto-discovered API endpoint: {endpoint} for node {node_config['name']}")
                        return endpoint
        except:
            continue
    
    log.warning(f"Could not auto-discover API for node {node_config['name']}. Enhanced features disabled.")
    return None
```

---

## Proposed API Integration - Option 2: Separate Flag

### Syntax
```bash
--node "NodeName:log_source"
--api "NodeName:api_endpoint"
```

### Examples

```bash
# Local node with API
storj_monitor \
  --node "My-Node:/var/log/storagenode.log" \
  --api "My-Node:http://localhost:14002"

# Multiple nodes with selective API
storj_monitor \
  --node "Node1:/var/log/node1.log" \
  --node "Node2:/var/log/node2.log" \
  --api "Node1:http://localhost:14002" \
  --api "Node2:http://localhost:15002"

# Remote nodes
storj_monitor \
  --node "Remote1:192.168.1.100:9999" \
  --node "Remote2:192.168.1.101:9999" \
  --api "Remote1:http://192.168.1.100:14002" \
  --api "Remote2:http://192.168.1.101:14002"
```

### Pros & Cons

**Option 1 (Extended Syntax):**
- ✅ Unified, compact syntax
- ✅ All config in one place per node
- ⚠️ Longer strings, more complex parsing
- ⚠️ Colon collision with URLs

**Option 2 (Separate Flag):**
- ✅ Clear separation of concerns
- ✅ Optional API config (easier to skip)
- ✅ No parsing ambiguity
- ⚠️ More verbose for multi-node setups

---

## Recommended: Option 1 with Intelligent Defaults

**Rationale:**
1. Maintains the spirit of the current simple syntax
2. Auto-discovery handles common cases automatically
3. Explicit override available when needed
4. Backward compatible (API is optional)

### Implementation

```python
# In __main__.py or config parsing

def setup_nodes(node_args: List[str]) -> Dict[str, dict]:
    """
    Parse node configuration from command-line arguments.
    
    Returns:
        {
            'NodeName': {
                'log_source': '/path/to/log' or 'ip:port',
                'log_type': 'file' or 'network',
                'api_endpoint': 'http://...' or None
            }
        }
    """
    nodes = {}
    
    for node_string in node_args:
        config = parse_node_config(node_string)
        nodes[config['name']] = config
    
    return nodes

# In startup sequence
async def initialize_nodes(app, nodes_config: Dict[str, dict]):
    """
    Initialize log processors and API clients for each node.
    """
    for node_name, config in nodes_config.items():
        # Setup log source (existing logic)
        if config['log_type'] == 'file':
            await setup_file_log_reader(app, node_name, config['log_source'])
        else:
            await setup_network_log_reader(app, node_name, config['log_source'])
        
        # Setup API client (new)
        api_endpoint = config['api_endpoint']
        if not api_endpoint:
            api_endpoint = await auto_discover_api(config)
        
        if api_endpoint:
            await setup_api_client(app, node_name, api_endpoint)
            log.info(f"Enhanced monitoring enabled for {node_name} via {api_endpoint}")
        else:
            log.warning(f"Enhanced monitoring disabled for {node_name} (no API endpoint)")
```

---

## Configuration File Support (Future Enhancement)

For complex multi-node setups, support YAML/TOML config file:

### Example: `storj_monitor.yaml`

```yaml
nodes:
  - name: "Local-Node-1"
    log:
      type: file
      path: "/var/log/storagenode1.log"
    api:
      endpoint: "http://localhost:14002"
      poll_interval: 300  # seconds
    
  - name: "Local-Node-2"
    log:
      type: file
      path: "/var/log/storagenode2.log"
    api:
      endpoint: "http://localhost:15002"
  
  - name: "Remote-Node-1"
    log:
      type: network
      host: "192.168.1.100"
      port: 9999
    api:
      endpoint: "http://192.168.1.100:14002"
      timeout: 10
  
  - name: "Remote-Node-2"
    log:
      type: network
      host: "192.168.1.101"
      port: 9999
    # No API configured - log-only monitoring

settings:
  database: "/var/lib/storj_monitor/storj_stats.db"
  geoip_database: "/usr/share/GeoIP/GeoLite2-City.mmdb"
  server:
    host: "0.0.0.0"
    port: 8765

alerts:
  storage_warning_percent: 80
  storage_critical_percent: 95
  audit_score_warning: 85.0
  
notifications:
  email:
    enabled: false
  webhook:
    enabled: false
```

### Usage
```bash
storj_monitor --config storj_monitor.yaml
```

---

## API Client Architecture

### Module: `storj_api_client.py`

```python
import asyncio
import logging
from typing import Optional, Dict, Any
import aiohttp

log = logging.getLogger("StorjMonitor.APIClient")


class StorjNodeAPIClient:
    """
    Client for interacting with Storj Node API.
    Each node gets its own client instance.
    """
    
    def __init__(self, node_name: str, api_endpoint: str, timeout: int = 10):
        self.node_name = node_name
        self.api_endpoint = api_endpoint.rstrip('/')
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_available = False
        
    async def start(self):
        """Initialize the API client and verify connectivity."""
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        
        # Test connectivity
        try:
            data = await self.get_dashboard()
            if data:
                self.is_available = True
                log.info(f"[{self.node_name}] API client connected to {self.api_endpoint}")
            else:
                log.warning(f"[{self.node_name}] API endpoint responded but data format unexpected")
        except Exception as e:
            log.error(f"[{self.node_name}] Failed to connect to API: {e}")
            self.is_available = False
    
    async def stop(self):
        """Clean up the API client."""
        if self.session:
            await self.session.close()
    
    async def _get(self, path: str) -> Optional[Dict[str, Any]]:
        """Generic GET request to API endpoint."""
        if not self.session or not self.is_available:
            return None
        
        url = f"{self.api_endpoint}{path}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    log.warning(f"[{self.node_name}] API returned status {resp.status} for {path}")
                    return None
        except asyncio.TimeoutError:
            log.warning(f"[{self.node_name}] API request timeout for {path}")
            return None
        except Exception as e:
            log.error(f"[{self.node_name}] API request failed for {path}: {e}")
            return None
    
    async def get_dashboard(self) -> Optional[Dict]:
        """Get general dashboard data."""
        return await self._get('/api/sno')
    
    async def get_satellites(self) -> Optional[Dict]:
        """Get per-satellite statistics."""
        return await self._get('/api/sno/satellites')
    
    async def get_satellite_info(self, satellite_id: str) -> Optional[Dict]:
        """Get detailed info for specific satellite."""
        return await self._get(f'/api/sno/satellites/{satellite_id}')
    
    async def get_estimated_payout(self) -> Optional[Dict]:
        """Get earnings estimates."""
        return await self._get('/api/sno/estimated-payout')


# Background task to poll API for all nodes
async def api_polling_task(app):
    """
    Periodically poll node APIs for enhanced data.
    This runs as a background task.
    """
    from .config import NODE_API_POLL_INTERVAL
    
    log.info("API polling task started")
    
    # Wait for initial setup
    await asyncio.sleep(5)
    
    while True:
        try:
            # Poll each node's API
            for node_name, api_client in app.get('api_clients', {}).items():
                if not api_client.is_available:
                    continue
                
                # Fetch data
                dashboard = await api_client.get_dashboard()
                satellites = await api_client.get_satellites()
                payout = await api_client.get_estimated_payout()
                
                # Process and store
                if dashboard:
                    await process_storage_data(app, node_name, dashboard)
                
                if satellites:
                    await process_reputation_data(app, node_name, satellites)
                
                if payout:
                    await process_earnings_data(app, node_name, payout)
            
            # Wait before next poll
            await asyncio.sleep(NODE_API_POLL_INTERVAL)
            
        except asyncio.CancelledError:
            log.info("API polling task cancelled")
            break
        except Exception:
            log.error("Error in API polling task", exc_info=True)
            await asyncio.sleep(60)  # Back off on error


async def setup_api_client(app, node_name: str, api_endpoint: str):
    """
    Setup and register API client for a node.
    Called during node initialization.
    """
    if 'api_clients' not in app:
        app['api_clients'] = {}
    
    client = StorjNodeAPIClient(node_name, api_endpoint)
    await client.start()
    
    if client.is_available:
        app['api_clients'][node_name] = client
    else:
        log.warning(f"API client for {node_name} not available. Enhanced features disabled.")


async def cleanup_api_clients(app):
    """
    Clean up all API clients on shutdown.
    Called during app cleanup.
    """
    for client in app.get('api_clients', {}).values():
        await client.stop()
```

---

## User Experience

### Help Text Update

```bash
storj_monitor --help

Usage: storj_monitor [OPTIONS]

Monitor Storj storage nodes with real-time dashboard.

Node Configuration:
  --node TEXT           Node configuration in format:
                        "NodeName:log_source[:api_endpoint]"
                        
                        Examples:
                          Local file with auto-discovered API:
                            --node "My-Node:/var/log/storagenode.log"
                          
                          Local file with explicit API:
                            --node "My-Node:/var/log/storagenode.log:http://localhost:14002"
                          
                          Remote log forwarder:
                            --node "Remote-Node:192.168.1.100:9999"
                          
                          Remote log + API:
                            --node "Remote-Node:192.168.1.100:9999:http://192.168.1.100:14002"
                        
                        Can be specified multiple times for multi-node monitoring.
                        
  --ingest-log TEXT     One-time log ingestion (node_name:log_path)

Server Options:
  --host TEXT           Server host (default: 0.0.0.0)
  --port INTEGER        Server port (default: 8765)
  --db TEXT             Database file path
  
Enhanced Features:
  API endpoints enable additional monitoring features:
    - Earnings estimates and financial tracking
    - Storage capacity monitoring and forecasting
    - Reputation scores and node health tracking
    - Enhanced performance analytics
  
  If API endpoint is not specified, the system will attempt
  auto-discovery using default ports.

Examples:
  # Single local node with auto-discovered API
  storj_monitor --node "My-Node:/var/log/storagenode.log"
  
  # Multiple local nodes with different API ports
  storj_monitor \
    --node "Node1:/var/log/node1.log:http://localhost:14002" \
    --node "Node2:/var/log/node2.log:http://localhost:15002"
  
  # Mix of local and remote nodes
  storj_monitor \
    --node "Local:/var/log/node.log" \
    --node "Remote:192.168.1.100:9999:http://192.168.1.100:14002"
  
  # One-time historical log ingestion
  storj_monitor --ingest-log "My-Node:/var/log/old-logs.log"
```

### Dashboard Indicator

When a node has API enabled, show indicator in the UI:

```
Node Selector:
  [ Aggregate ▼ ]  [ Node1 ✓ ]  [ Node2 ✓ ]  [ Node3 ○ ]
  
  ✓ = Enhanced monitoring (API connected)
  ○ = Basic monitoring (logs only)
```

---

## Graceful Degradation

### Feature Availability Matrix

| Feature | Log Only | Log + API |
|---------|----------|-----------|
| Live traffic heatmap | ✓ | ✓ |
| Success rates | ✓ | ✓ |
| Bandwidth charts | ✓ | ✓ |
| Error analysis | ✓ | ✓ |
| Hashstore monitoring | ✓ | ✓ |
| **Earnings tracking** | ✗ | ✓ |
| **Storage capacity** | ✗ | ✓ |
| **Reputation scores** | ✗ | ✓ |
| **Latency percentiles** | Partial | ✓ |
| **Predictive insights** | ✗ | ✓ |

### UI Behavior

When API is not available for a node:
1. Hide/disable enhanced feature cards
2. Show info tooltip: "Connect API endpoint for enhanced features"
3. Provide link to documentation on how to enable API
4. All existing features continue to work normally

---

## Security Considerations

### API Access
- Default: localhost only (127.0.0.1)
- Remote: Consider SSH tunneling or VPN
- No authentication on Storj node API (design choice by Storj Labs)

### Configuration
```python
# config.py
ALLOW_REMOTE_API = False  # Security flag

# Validation in setup
if not ALLOW_REMOTE_API and not is_localhost(api_endpoint):
    log.warning(f"Remote API access disabled. Enable ALLOW_REMOTE_API to use {api_endpoint}")
    return None
```

### Recommended Remote Setup
```bash
# On monitoring machine, create SSH tunnel
ssh -N -L 14002:localhost:14002 user@storagenode-host

# Then use localhost endpoint
storj_monitor --node "Remote-Node:storagenode-host:9999:http://localhost:14002"
```

---

## Migration Path

### For Existing Users

**No Changes Required**
```bash
# Existing command continues to work exactly as before
storj_monitor --node "My-Node:/var/log/storagenode.log"

# Enhanced features auto-discovered on localhost
# If not available, gracefully degrades to log-only monitoring
```

**Opt-In Enhancement**
```bash
# Explicitly enable API for additional features
storj_monitor --node "My-Node:/var/log/storagenode.log:http://localhost:14002"
```

### Documentation Update

Add section to README.md:

```markdown
## Enhanced Monitoring Features

The monitor supports optional API integration for additional features:
- Real-time earnings estimates
- Storage capacity forecasting
- Node reputation tracking
- Advanced performance analytics

### Enabling Enhanced Features

**Local Node (Automatic):**
```bash
storj_monitor --node "My-Node:/var/log/storagenode.log"
```
The system will automatically discover the API on localhost:14002

**Local Node (Explicit):**
```bash
storj_monitor --node "My-Node:/var/log/storagenode.log:http://localhost:14002"
```

**Remote Node:**
```bash
# Log forwarder + API
storj_monitor --node "Remote:192.168.1.100:9999:http://192.168.1.100:14002"

# For security, consider using SSH tunnel:
ssh -N -L 14002:localhost:14002 user@192.168.1.100
storj_monitor --node "Remote:192.168.1.100:9999:http://localhost:14002"
```

### Checking API Status

The dashboard shows API connection status for each node:
- ✓ = Enhanced monitoring active
- ○ = Basic monitoring (logs only)
```

---

## Implementation Checklist

- [ ] Update command-line argument parsing
  - [ ] Support extended node syntax with optional API endpoint
  - [ ] Maintain backward compatibility
  - [ ] Add validation and helpful error messages

- [ ] Create [`storj_api_client.py`](storj_api_client.py)
  - [ ] Implement `StorjNodeAPIClient` class
  - [ ] Add connection testing and auto-discovery
  - [ ] Implement API polling task

- [ ] Update [`tasks.py`](tasks.py)
  - [ ] Register API polling as background task
  - [ ] Handle graceful shutdown

- [ ] Update [`server.py`](server.py)
  - [ ] Initialize API clients during startup
  - [ ] Clean up API clients during shutdown

- [ ] Update UI to show API connection status
  - [ ] Node selector indicators
  - [ ] Feature availability tooltips
  - [ ] Settings panel for API configuration

- [ ] Update documentation
  - [ ] README.md with new syntax examples
  - [ ] Security recommendations
  - [ ] Troubleshooting guide

- [ ] Testing
  - [ ] Test with various node configurations
  - [ ] Test auto-discovery
  - [ ] Test graceful degradation
  - [ ] Test multi-node mixed scenarios

---

**Summary:** This design maintains the simplicity and convenience of the current multi-node architecture while seamlessly adding API integration for enhanced features. The auto-discovery mechanism ensures that most users get enhanced features automatically, while power users can explicitly configure complex scenarios.