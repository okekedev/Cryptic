"""
WebSocket module for Coinbase feed integration
"""
from .client import CoinbaseWebSocketClient
from .handlers import MessageHandler, create_subscription_message
from .connection import ConnectionManager, AuthenticationManager
from .config import *

__all__ = [
    'CoinbaseWebSocketClient',
    'MessageHandler',
    'ConnectionManager',
    'AuthenticationManager',
    'create_subscription_message',
]