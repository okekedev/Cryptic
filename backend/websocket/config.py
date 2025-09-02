"""
WebSocket configuration settings
"""
import os

# WebSocket connection settings
WS_URL = 'wss://ws-feed.exchange.coinbase.com'

# Subscription settings
DEFAULT_CHANNELS = ['matches']
DEFAULT_PRODUCT_IDS = os.getenv('MONITORING_CRYPTOS', 'BTC-USD,ETH-USD').split(',')

# Reconnection settings
INITIAL_BACKOFF = 1  # seconds
MAX_BACKOFF = 60  # seconds
BACKOFF_MULTIPLIER = 2

# Connection timeouts
CONNECTION_TIMEOUT = 30  # seconds
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds