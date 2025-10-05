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
    Each node gets its own client instance.
    """
    
    def __init__(self, node_name: str, api_endpoint: str, timeout: int = NODE_API_TIMEOUT):
        self.node_name = node_name
        self.api_endpoint = api_endpoint.rstrip('/')
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_available = False
        self._last_error = None
        
    async def start(self):
        """Initialize the API client and verify connectivity."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        
        # Test connectivity
        try:
            data = await self.get_dashboard()
            if data:
                self.is_available = True
                node_id = data.get('nodeID', 'unknown')[:12]
                wallet = data.get('wallet', 'unknown')[:12]
                log.info(
                    f"[{self.node_name}] API client connected to {self.api_endpoint} "
                    f"(Node ID: {node_id}..., Wallet: {wallet}...)"
                )
            else:
                log.warning(
                    f"[{self.node_name}] API endpoint responded but data format unexpected"
                )
        except Exception as e:
            self._last_error = str(e)
            log.error(f"[{self.node_name}] Failed to connect to API: {e}")
            self.is_available = False
    
    async def stop(self):
        """Clean up the API client."""
        if self.session:
            await self.session.close()
            log.info(f"[{self.node_name}] API client connection closed")
    
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
                    log.warning(
                        f"[{self.node_name}] API returned status {resp.status} for {path}"
                    )
                    return None
        except asyncio.TimeoutError:
            log.warning(f"[{self.node_name}] API request timeout for {path}")
            return None
        except aiohttp.ClientError as e:
            log.error(f"[{self.node_name}] API request failed for {path}: {e}")
            return None
        except Exception as e:
            log.error(
                f"[{self.node_name}] Unexpected error in API request for {path}: {e}",
                exc_info=True
            )
            return None
    
    async def get_dashboard(self) -> Optional[Dict]:
        """
        Get general dashboard data.
        
        Returns dict with keys: nodeID, wallet, diskSpace, diskSpaceUsed, 
        diskSpaceAvailable, bandwidth, etc.
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
        Get earnings estimates.
        
        Returns dict with payout estimates per satellite.
        """
        return await self._get('/api/sno/estimated-payout')


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