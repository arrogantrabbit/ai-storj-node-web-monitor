"""
Storj Node API Client

This module provides a client for interacting with the Storj node API
to retrieve enhanced monitoring data like reputation scores, storage
capacity, and earnings estimates.

Default API endpoint: http://localhost:14002
"""

import asyncio
import logging
from typing import Optional, Dict, Any
import aiohttp

from .config import NODE_API_TIMEOUT, NODE_API_DEFAULT_PORT, ALLOW_REMOTE_API

log = logging.getLogger("StorjMonitor.APIClient")


class StorjNodeAPIClient:
    """
    Client for interacting with Storj Node API.
    Each node gets its own client instance with automatic reconnection.
    """
    
    def __init__(self, node_name: str, api_endpoint: str, timeout: int = NODE_API_TIMEOUT):
        self.node_name = node_name
        self.api_endpoint = api_endpoint.rstrip('/')
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_available = False
        self._last_error = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._health_check_interval = 30  # seconds
        self._last_successful_request = None
        self._connection_state = 'disconnected'  # disconnected, connecting, connected, error
        
    async def start(self):
        """Initialize the API client and verify connectivity with auto-reconnect."""
        self._connection_state = 'connecting'
        await self._connect()
        
        # Start health check task
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
    async def _connect(self):
        """Establish connection to API endpoint."""
        try:
            # Create session with redirect support (Storj API uses 301 redirects for trailing slash)
            if self.session:
                await self.session.close()
                
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                connector=aiohttp.TCPConnector(limit=10),
                raise_for_status=False  # Handle status codes manually
            )
            
            # Test connectivity
            data = await self.get_dashboard()
            
            # Debug: Log what we received
            log.debug(f"[{self.node_name}] API response type: {type(data)}")
            if data:
                log.debug(f"[{self.node_name}] API response keys: {list(data.keys())[:10]}")
            
            if data and 'nodeID' in data:
                self.is_available = True
                self._connection_state = 'connected'
                self._reconnect_attempts = 0
                self._last_successful_request = asyncio.get_event_loop().time()
                node_id = data.get('nodeID', 'unknown')[:12]
                wallet = data.get('wallet', 'unknown')[:12]
                version = data.get('version', 'unknown')
                log.info(
                    f"[{self.node_name}] API client connected to {self.api_endpoint} "
                    f"(Node ID: {node_id}..., Wallet: {wallet}..., Version: {version})"
                )
                return True
            else:
                log.warning(
                    f"[{self.node_name}] API endpoint responded but data format unexpected. "
                    f"Data type: {type(data)}, Has nodeID: {data and 'nodeID' in data if data else 'No data'}"
                )
                self.is_available = False
                self._connection_state = 'error'
                return False
        except Exception as e:
            self._last_error = str(e)
            self._connection_state = 'error'
            log.error(f"[{self.node_name}] Failed to connect to API: {e}")
            self.is_available = False
            # Clean up session if initialization failed
            if self.session:
                await self.session.close()
                self.session = None
            return False
    
    async def _health_check_loop(self):
        """Periodically check API health and reconnect if needed."""
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                
                # Check if we're connected
                if not self.is_available:
                    # Attempt reconnection
                    if self._reconnect_attempts < self._max_reconnect_attempts:
                        self._reconnect_attempts += 1
                        backoff = min(2 ** self._reconnect_attempts, 300)  # Max 5 minutes
                        log.info(
                            f"[{self.node_name}] API reconnection attempt {self._reconnect_attempts}/"
                            f"{self._max_reconnect_attempts} in {backoff}s"
                        )
                        await asyncio.sleep(backoff)
                        await self._connect()
                    else:
                        log.warning(
                            f"[{self.node_name}] Max reconnection attempts reached. "
                            f"Will retry in {self._health_check_interval * 2}s"
                        )
                        await asyncio.sleep(self._health_check_interval)
                        self._reconnect_attempts = 0  # Reset for next cycle
                else:
                    # Perform health check
                    try:
                        data = await self.get_dashboard()
                        if data and 'nodeID' in data:
                            self._last_successful_request = asyncio.get_event_loop().time()
                            log.debug(f"[{self.node_name}] API health check passed")
                        else:
                            log.warning(f"[{self.node_name}] API health check failed - invalid response")
                            self.is_available = False
                            self._connection_state = 'error'
                    except Exception as e:
                        log.warning(f"[{self.node_name}] API health check failed: {e}")
                        self.is_available = False
                        self._connection_state = 'error'
                        
            except asyncio.CancelledError:
                log.info(f"[{self.node_name}] Health check loop cancelled")
                break
            except Exception as e:
                log.error(f"[{self.node_name}] Error in health check loop: {e}", exc_info=True)
    
    async def stop(self):
        """Clean up the API client."""
        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Close session
        if self.session:
            await self.session.close()
            log.info(f"[{self.node_name}] API client connection closed")
        
        self._connection_state = 'disconnected'
    
    async def _get(self, path: str) -> Optional[Dict[str, Any]]:
        """Generic GET request to API endpoint with redirect support and auto-recovery."""
        if not self.session:
            log.debug(f"[{self.node_name}] No session available for {path}")
            # Try to reconnect if we don't have a session
            if await self._connect():
                # Session created, retry the request
                return await self._get(path)
            return None
        
        # Ensure path has trailing slash (Storj API requirement)
        if not path.endswith('/'):
            path = path + '/'
        
        url = f"{self.api_endpoint}{path}"
        log.debug(f"[{self.node_name}] Requesting {url}")
        
        try:
            # Allow redirects (Storj API uses 301 for trailing slash)
            async with self.session.get(url, allow_redirects=True, max_redirects=2) as resp:
                log.debug(f"[{self.node_name}] API status: {resp.status} for {url}")
                
                if resp.status == 200:
                    json_data = await resp.json()
                    self._last_successful_request = asyncio.get_event_loop().time()
                    # Handle null/None responses (valid JSON for periods with no data)
                    if json_data is None:
                        log.debug(f"[{self.node_name}] API returned null for {path}")
                        return None
                    elif isinstance(json_data, dict):
                        log.debug(f"[{self.node_name}] API returned JSON dict with {len(json_data)} keys")
                    elif isinstance(json_data, list):
                        log.debug(f"[{self.node_name}] API returned JSON list with {len(json_data)} items")
                    else:
                        log.debug(f"[{self.node_name}] API returned JSON: {type(json_data)}")
                    return json_data
                else:
                    response_text = await resp.text()
                    log.warning(
                        f"[{self.node_name}] API returned status {resp.status} for {path}. "
                        f"Response preview: {response_text[:200]}"
                    )
                    # Mark as unavailable on persistent errors
                    if resp.status >= 500:
                        self.is_available = False
                        self._connection_state = 'error'
                    return None
        except asyncio.TimeoutError:
            log.warning(f"[{self.node_name}] API request timeout for {path}")
            self.is_available = False
            self._connection_state = 'error'
            return None
        except aiohttp.ClientError as e:
            log.error(f"[{self.node_name}] API request failed for {path}: {e}")
            self.is_available = False
            self._connection_state = 'error'
            return None
        except Exception as e:
            log.error(
                f"[{self.node_name}] Unexpected error in API request for {path}: {e}",
                exc_info=True
            )
            self.is_available = False
            self._connection_state = 'error'
            return None
    
    def get_connection_state(self) -> Dict[str, Any]:
        """Get current connection state for monitoring."""
        return {
            'state': self._connection_state,
            'is_available': self.is_available,
            'last_error': self._last_error,
            'reconnect_attempts': self._reconnect_attempts,
            'last_successful_request': self._last_successful_request
        }
    
    async def get_dashboard(self) -> Optional[Dict]:
        """
        Get general dashboard data.
        
        Returns dict with keys: nodeID, wallet, diskSpace, satellites, bandwidth, etc.
        """
        return await self._get('/api/sno')
    
    async def get_satellites(self) -> Optional[Dict]:
        """
        Get per-satellite statistics.
        
        Returns dict with satellite IDs as keys, each containing:
        - id, url, disqualified, suspended
        - audit: {score, successCount, totalCount, alpha, beta, unknownAlpha, unknownBeta}
        - suspension: {score, successCount, totalCount, alpha, beta}
        - online: {score, successCount, totalCount, alpha, beta}
        """
        return await self._get('/api/sno/satellites')
    
    async def get_satellite_info(self, satellite_id: str) -> Optional[Dict]:
        """
        Get detailed info for specific satellite.
        
        Returns detailed stats for one satellite.
        """
        return await self._get(f'/api/sno/satellites/{satellite_id}')
    
    async def get_estimated_payout(self) -> Optional[Dict]:
        """
        Get earnings estimates for current period.
        
        Returns dict with payout estimates per satellite.
        """
        return await self._get('/api/sno/estimated-payout')
    
    async def get_payout_paystubs(self, period: str) -> Optional[list]:
        """
        Get payout paystubs for a specific period.
        
        Args:
            period: Period in YYYY-MM format (required)
        
        Returns:
            List of paystub dicts, each containing satellite payout info with 'paid' field in micro-dollars.
            Returns None if period has no data or API error.
        
        API endpoint: /api/heldamount/paystubs/{period}/{period}
        Example response: [{"satelliteID": "...", "period": "2025-10", "paid": 28112100, ...}, ...]
        """
        return await self._get(f'/api/heldamount/paystubs/{period}/{period}')


async def auto_discover_api_endpoint(node_config: Dict[str, Any]) -> Optional[str]:
    """
    Attempt to auto-discover node API endpoint.
    
    Strategy:
    1. If node has file log and no explicit API, try localhost:14002
    2. If node has network log, try same IP on port 14002
    3. Test connectivity, return endpoint if successful
    
    Args:
        node_config: Node configuration dict with 'type', 'api_endpoint', etc.
    
    Returns:
        API endpoint URL if discovered, None otherwise
    """
    if node_config.get('api_endpoint'):
        # Already has explicit endpoint
        return node_config['api_endpoint']
    
    candidates = []
    node_type = node_config.get('type')
    node_name = node_config.get('name', 'unknown')
    
    if node_type == 'file':
        # Local file, try localhost
        candidates = [
            f'http://localhost:{NODE_API_DEFAULT_PORT}',
            f'http://127.0.0.1:{NODE_API_DEFAULT_PORT}',
        ]
        log.info(f"[{node_name}] Attempting auto-discovery for local node...")
    elif node_type == 'network':
        # Remote log, extract IP and try API on same host
        host = node_config.get('host')
        if host:
            # Check if remote API is allowed
            if not ALLOW_REMOTE_API and not _is_localhost(host):
                log.warning(
                    f"[{node_name}] Remote API access disabled for {host}. "
                    f"Set ALLOW_REMOTE_API=True to enable."
                )
                return None
            
            candidates = [f'http://{host}:{NODE_API_DEFAULT_PORT}']
            log.info(
                f"[{node_name}] Attempting auto-discovery for remote node at {host}..."
            )
    
    # Test each candidate
    for endpoint in candidates:
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f'{endpoint}/api/sno') as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and 'nodeID' in data:
                            log.info(
                                f"[{node_name}] Auto-discovered API endpoint: {endpoint}"
                            )
                            return endpoint
        except Exception:
            # Silently continue to next candidate
            continue
    
    log.info(
        f"[{node_name}] Could not auto-discover API. Enhanced features disabled. "
        f"Specify API endpoint explicitly to enable them."
    )
    return None


def _is_localhost(host: str) -> bool:
    """Check if host is localhost."""
    return host in ('localhost', '127.0.0.1', '::1', '0.0.0.0')


async def setup_api_client(
    app: Dict[str, Any],
    node_name: str,
    api_endpoint: str
) -> Optional[StorjNodeAPIClient]:
    """
    Setup and register API client for a node.
    
    Called during node initialization.
    
    Args:
        app: Application context
        node_name: Name of the node
        api_endpoint: API endpoint URL
    
    Returns:
        StorjNodeAPIClient instance if successful, None otherwise
    """
    if 'api_clients' not in app:
        app['api_clients'] = {}
    
    client = StorjNodeAPIClient(node_name, api_endpoint)
    await client.start()
    
    if client.is_available:
        app['api_clients'][node_name] = client
        log.info(f"[{node_name}] Enhanced monitoring features enabled")
        return client
    else:
        log.warning(
            f"[{node_name}] API client not available. "
            f"Enhanced features disabled. Last error: {client._last_error}"
        )
        return None


async def cleanup_api_clients(app: Dict[str, Any]):
    """
    Clean up all API clients on shutdown.
    
    Called during app cleanup.
    """
    for client in app.get('api_clients', {}).values():
        await client.stop()
    log.info("All API clients cleaned up")