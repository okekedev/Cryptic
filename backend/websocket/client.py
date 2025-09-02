import time
import logging
import threading
from typing import Optional, Callable, List

try:
    from websocket import WebSocketApp
except ImportError:
    import websocket
    WebSocketApp = websocket.WebSocketApp

from .config import (
    WS_URL, DEFAULT_CHANNELS, DEFAULT_PRODUCT_IDS,
    INITIAL_BACKOFF, MAX_BACKOFF, BACKOFF_MULTIPLIER
)
from .handlers import MessageHandler, create_subscription_message
from .connection import ConnectionManager

logger = logging.getLogger(__name__)


class CoinbaseWebSocketClient:
    """WebSocket client for Coinbase feed"""
    
    def __init__(self, 
                 url: str = WS_URL,
                 product_ids: Optional[List[str]] = None,
                 channels: Optional[List[str]] = None,
                 on_match_callback: Optional[Callable] = None):
        
        self.url = url
        self.product_ids = product_ids or DEFAULT_PRODUCT_IDS
        self.channels = channels or DEFAULT_CHANNELS
        
        # Initialize components
        self.connection_manager = ConnectionManager(
            initial_backoff=INITIAL_BACKOFF,
            max_backoff=MAX_BACKOFF,
            multiplier=BACKOFF_MULTIPLIER
        )
        
        self.message_handler = MessageHandler()
        if on_match_callback:
            self.message_handler.set_match_callback(on_match_callback)
            
        self.ws: Optional[WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.running = False
        
    def start(self):
        """Start WebSocket client in a separate thread"""
        if self.running:
            logger.warning("WebSocket client already running")
            return
            
        self.running = True
        self.ws_thread = threading.Thread(target=self._run_forever, daemon=True)
        self.ws_thread.start()
        logger.info("WebSocket client started")
        
    def stop(self):
        """Stop WebSocket client"""
        self.running = False
        if self.ws:
            self.ws.close()
        if self.ws_thread:
            self.ws_thread.join(timeout=5)
        logger.info("WebSocket client stopped")
        
    def _run_forever(self):
        """Run WebSocket with automatic reconnection"""
        while self.running:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"WebSocket exception: {e}")
                
            if self.running and self.connection_manager.should_reconnect():
                delay = self.connection_manager.get_reconnect_delay()
                time.sleep(delay)
                
    def _connect(self):
        """Create and run WebSocket connection"""
        self.ws = WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        # Run WebSocket
        self.ws.run_forever()
        
    def _on_open(self, ws):
        """Handle connection open"""
        self.connection_manager.on_connect()
        
        # Subscribe to channels
        sub_msg = create_subscription_message(self.product_ids, self.channels)
        ws.send(sub_msg)
        logger.info(f"Subscribed to {self.channels} for {self.product_ids}")
        
    def _on_message(self, ws, message):
        """Handle incoming messages"""
        self.message_handler.handle_message(ws, message)
        
    def _on_error(self, ws, error):
        """Handle WebSocket errors"""
        logger.error(f"WebSocket error: {error}")
        
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle connection close"""
        self.connection_manager.on_disconnect()
        logger.info(f"WebSocket closed: {close_status_code} {close_msg}")
        
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self.connection_manager.is_connected
        
    def update_subscription(self, product_ids: Optional[List[str]] = None, 
                          channels: Optional[List[str]] = None):
        """Update subscription (requires reconnection)"""
        if product_ids:
            self.product_ids = product_ids
        if channels:
            self.channels = channels
            
        # If connected, we need to reconnect with new subscription
        if self.is_connected() and self.ws:
            self.ws.close()  # This will trigger reconnection with new params