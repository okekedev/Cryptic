"""
WebSocket connection management
"""
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connection state and reconnection logic"""
    
    def __init__(self, initial_backoff: int = 1, max_backoff: int = 60, multiplier: float = 2):
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.multiplier = multiplier
        self.current_backoff = initial_backoff
        self.is_connected = False
        self.connection_count = 0
        self.last_connection_time = 0
        
    def on_connect(self):
        """Called when connection is established"""
        self.is_connected = True
        self.connection_count += 1
        self.last_connection_time = time.time()
        self.current_backoff = self.initial_backoff
        logger.info(f"WebSocket connected (connection #{self.connection_count})")
        
    def on_disconnect(self):
        """Called when connection is lost"""
        self.is_connected = False
        uptime = time.time() - self.last_connection_time if self.last_connection_time else 0
        logger.info(f"WebSocket disconnected after {uptime:.1f} seconds")
        
    def get_reconnect_delay(self) -> float:
        """Get delay before next reconnection attempt"""
        delay = self.current_backoff
        self.current_backoff = min(self.current_backoff * self.multiplier, self.max_backoff)
        logger.info(f"Reconnecting in {delay} seconds...")
        return delay
        
    def reset_backoff(self):
        """Reset backoff to initial value"""
        self.current_backoff = self.initial_backoff
        
    def should_reconnect(self) -> bool:
        """Determine if we should attempt reconnection"""
        # You can add additional logic here (e.g., max attempts, time-based limits)
        return True


class AuthenticationManager:
    """Manages WebSocket authentication (if needed)"""
    
    def __init__(self, api_key: Optional[str] = None, 
                 api_secret: Optional[str] = None,
                 passphrase: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        
    def is_authenticated(self) -> bool:
        """Check if authentication credentials are available"""
        return all([self.api_key, self.api_secret, self.passphrase])
        
    def get_auth_headers(self, timestamp: str, signature: str) -> dict:
        """Get authentication headers for WebSocket"""
        if not self.is_authenticated():
            return {}
            
        return {
            'CB-ACCESS-KEY': self.api_key,
            'CB-ACCESS-SIGN': signature,
            'CB-ACCESS-TIMESTAMP': timestamp,
            'CB-ACCESS-PASSPHRASE': self.passphrase
        }