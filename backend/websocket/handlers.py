"""
WebSocket message handlers
"""
import json
import logging
from typing import Callable, Dict, Any, Optional

logger = logging.getLogger(__name__)


class MessageHandler:
    """Handles different types of WebSocket messages"""
    
    def __init__(self):
        self.handlers: Dict[str, Callable] = {
            'subscriptions': self._handle_subscription,
            'error': self._handle_error,
            'match': self._handle_match,
            'heartbeat': self._handle_heartbeat,
        }
        self.match_callback: Optional[Callable] = None
        
    def set_match_callback(self, callback: Callable):
        """Set callback for match messages"""
        self.match_callback = callback
        
    def handle_message(self, ws, message: str):
        """Route message to appropriate handler"""
        try:
            data = json.loads(message)
            message_type = data.get('type', '')
            
            handler = self.handlers.get(message_type, self._handle_unknown)
            handler(ws, data)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    def _handle_subscription(self, ws, data: Dict[str, Any]):
        """Handle subscription confirmation"""
        channels = data.get('channels', [])
        logger.info(f"Subscription confirmed for channels: {channels}")
        
    def _handle_error(self, ws, data: Dict[str, Any]):
        """Handle error messages"""
        error_message = data.get('message', 'Unknown error')
        logger.error(f"WebSocket error: {error_message}")
        
    def _handle_match(self, ws, data: Dict[str, Any]):
        """Handle match/trade messages"""
        if self.match_callback:
            try:
                self.match_callback(data)
            except Exception as e:
                logger.error(f"Error in match callback: {e}")
        else:
            logger.debug(f"Received match: {data.get('product_id')} - {data.get('size')}")
            
    def _handle_heartbeat(self, ws, data: Dict[str, Any]):
        """Handle heartbeat messages"""
        logger.debug("Heartbeat received")
        
    def _handle_unknown(self, ws, data: Dict[str, Any]):
        """Handle unknown message types"""
        message_type = data.get('type', 'unknown')
        logger.warning(f"Unknown message type: {message_type}")


def create_subscription_message(product_ids: list, channels: list) -> str:
    """Create subscription message for Coinbase WebSocket"""
    sub_msg = {
        "type": "subscribe",
        "product_ids": product_ids,
        "channels": channels
    }
    return json.dumps(sub_msg)