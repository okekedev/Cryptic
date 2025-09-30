#!/usr/bin/env python3
"""
Enhanced WebSocket Handler - Drop-in replacement for existing websocket-handler.js
Provides the same interface with multi-connection reliability
"""

import asyncio
import json
import logging
import time
import threading
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timedelta
import requests
from multi_ws_manager import MultiWSManager, TickerData, ConnectionStats

logger = logging.getLogger(__name__)

class EnhancedWebSocketHandler:
    """
    Enhanced WebSocket Handler with multi-connection support
    Drop-in replacement for the existing Node.js handler
    """

    def __init__(self, config: dict):
        self.config = {
            'wsUrl': config.get('wsUrl', 'wss://advanced-trade-ws.coinbase.com'),
            'cryptoConfig': config.get('cryptoConfig', None),
            'volumeThreshold': config.get('volumeThreshold', 1.5),
            'windowMinutes': config.get('windowMinutes', 5),
            'productsPerConnection': config.get('productsPerConnection', 15),
            'apiKey': config.get('apiKey'),
            'signingKey': config.get('signingKey')
        }

        # Data storage (same as original)
        self.currentTickers: Dict[str, Dict] = {}
        self.volumeWindows: Dict[str, List] = {}
        self.historicalAvgs: Dict[str, float] = {}

        # Priority system (same as original)
        self.priorityPairs = set()
        self.priorityUpdateInterval = 1000
        self.lastPriorityUpdate = {}

        # Multi-connection manager
        self.ws_manager: Optional[MultiWSManager] = None

        # Event callbacks (same interface as EventEmitter)
        self.event_callbacks: Dict[str, List[Callable]] = {}

        # Initialize flag
        self.initialized = False

    def on(self, event: str, callback: Callable):
        """Add event listener (same interface as EventEmitter)"""
        if event not in self.event_callbacks:
            self.event_callbacks[event] = []
        self.event_callbacks[event].append(callback)

    def emit(self, event: str, data: Any):
        """Emit event to all listeners"""
        if event in self.event_callbacks:
            for callback in self.event_callbacks[event]:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in event callback for {event}: {e}")

    async def fetchAvailableUSDPairs(self) -> List[str]:
        """Fetch available USD trading pairs (async version)"""
        try:
            logger.info('Fetching available USD trading pairs from Coinbase...')
            response = requests.get(
                'https://api.coinbase.com/api/v3/brokerage/market/products',
                timeout=10
            )
            response.raise_for_status()

            data = response.json()

            # Filter for USD pairs only and active products
            usd_pairs = [
                product['product_id']
                for product in data.get('products', [])
                if (product.get('quote_currency_id') == 'USD' and
                    product.get('status') == 'online' and
                    not product.get('trading_disabled', False))
            ]

            logger.info(f'Found {len(usd_pairs)} active USD trading pairs')
            return usd_pairs

        except Exception as e:
            logger.error(f'Failed to fetch USD pairs: {e}')
            return []

    async def initialize(self):
        """Initialize the enhanced WebSocket handler"""
        try:
            final_pairs = []

            # Handle different crypto configuration types (same logic as original)
            if isinstance(self.config['cryptoConfig'], list):
                # Custom pairs specified
                final_pairs = self.config['cryptoConfig']
                logger.info(f'Using custom pairs: {", ".join(final_pairs)}')

            elif (self.config['cryptoConfig'] and
                  isinstance(self.config['cryptoConfig'], dict) and
                  'topN' in self.config['cryptoConfig']):
                # Top N pairs requested
                all_pairs = await self.fetchAvailableUSDPairs()
                if all_pairs:
                    final_pairs = all_pairs[:self.config['cryptoConfig']['topN']]
                    logger.info(f'Using top {self.config["cryptoConfig"]["topN"]} pairs from {len(all_pairs)} available pairs')

            else:
                # Default: fetch all available USD pairs
                final_pairs = await self.fetchAvailableUSDPairs()
                logger.info(f'Using all {len(final_pairs)} available USD pairs')

            if not final_pairs:
                logger.warning('No USD pairs to monitor - initialization failed')
                return

            # Initialize volume tracking for all pairs
            for pair in final_pairs:
                self.volumeWindows[pair] = []
                self.historicalAvgs[pair] = 0
                self.currentTickers[pair] = None

            # Setup multi-connection WebSocket manager
            self.ws_manager = MultiWSManager(
                products=final_pairs,
                products_per_connection=self.config['productsPerConnection']
            )

            # Add callbacks
            self.ws_manager.add_data_callback(self._handle_ticker_update)
            self.ws_manager.add_stats_callback(self._handle_connection_stats)

            logger.info(f'Enhanced WebSocket handler initialized for {len(final_pairs)} pairs')
            self.initialized = True

            # Start connections
            self.ws_manager.start()

        except Exception as e:
            logger.error(f'Enhanced WebSocket handler initialization failed: {e}')
            raise

    def _handle_ticker_update(self, ticker_data: TickerData):
        """Handle ticker updates from multi-connection manager"""
        try:
            # Convert to original format
            ticker_dict = {
                'crypto': ticker_data.product_id,
                'price': ticker_data.price,
                'bid': ticker_data.best_bid or 0,
                'ask': ticker_data.best_ask or 0,
                'volume_24h': ticker_data.volume_24h or 0,
                'price_24h': 0,  # Not available in real-time ticker
                'low_24h': 0,    # Not available in real-time ticker
                'high_24h': 0,   # Not available in real-time ticker
                'price_change_24h': 0,
                'price_change_percent_24h': 0,
                'time': ticker_data.time,
                'sequence': ticker_data.sequence,
                'last_size': ticker_data.last_size
            }

            # Store in current tickers
            self.currentTickers[ticker_data.product_id] = ticker_dict

            # Emit ticker_update event (same as original)
            self.emit('ticker_update', ticker_dict)

            # Handle priority pairs
            if ticker_data.product_id in self.priorityPairs:
                now = time.time() * 1000  # Convert to milliseconds
                last_update = self.lastPriorityUpdate.get(ticker_data.product_id, 0)

                if now - last_update >= self.priorityUpdateInterval:
                    self.emit('priority_ticker_update', ticker_dict)
                    self.lastPriorityUpdate[ticker_data.product_id] = now

            # Process volume data if last_size available
            if ticker_data.last_size > 0:
                self._processVolumeData(ticker_data.product_id, ticker_data.last_size)

        except Exception as e:
            logger.error(f'Error handling ticker update: {e}')

    def _handle_connection_stats(self, stats: ConnectionStats):
        """Handle connection statistics"""
        # You can emit connection health events here if needed
        pass

    def _processVolumeData(self, crypto: str, size: float):
        """Process volume data (same logic as original)"""
        now = time.time() * 1000  # Convert to milliseconds
        window_ms = self.config['windowMinutes'] * 60 * 1000

        # Add to volume window
        self.volumeWindows[crypto].append({
            'time': now,
            'size': size
        })

        # Remove old entries outside the window
        self.volumeWindows[crypto] = [
            entry for entry in self.volumeWindows[crypto]
            if entry['time'] > now - window_ms
        ]

        # Calculate current window volume
        current_volume = sum(entry['size'] for entry in self.volumeWindows[crypto])

        # Check for volume surge
        if self.historicalAvgs[crypto] > 0:
            ratio = current_volume / self.historicalAvgs[crypto]

            if ratio > self.config['volumeThreshold']:
                alert = {
                    'crypto': crypto,
                    'current_vol': current_volume,
                    'avg_vol': self.historicalAvgs[crypto],
                    'threshold': self.config['volumeThreshold'],
                    'ratio': ratio,
                    'ticker': self.currentTickers.get(crypto)
                }

                self.emit('volume_alert', alert)

                # Update historical average (simple moving average)
                self.historicalAvgs[crypto] = (
                    self.historicalAvgs[crypto] * 0.9 + current_volume * 0.1
                )
        else:
            # Initialize historical average if not set
            self.historicalAvgs[crypto] = current_volume

    def getCurrentTicker(self, crypto: str) -> Optional[Dict]:
        """Get current ticker for a crypto (same interface as original)"""
        return self.currentTickers.get(crypto)

    def getAllTickers(self) -> Dict[str, Dict]:
        """Get all current tickers (same interface as original)"""
        return self.currentTickers.copy()

    def disconnect(self):
        """Disconnect all WebSocket connections"""
        if self.ws_manager:
            self.ws_manager.stop()

    # Priority pair management methods (same interface as original)
    def addPriorityPair(self, product_id: str):
        """Add a priority pair"""
        self.priorityPairs.add(product_id)
        self.lastPriorityUpdate[product_id] = 0
        logger.info(f'Added {product_id} to priority monitoring. Total priority pairs: {len(self.priorityPairs)}')

    def removePriorityPair(self, product_id: str):
        """Remove a priority pair"""
        self.priorityPairs.discard(product_id)
        self.lastPriorityUpdate.pop(product_id, None)
        logger.info(f'Removed {product_id} from priority monitoring. Total priority pairs: {len(self.priorityPairs)}')

    def getPriorityPairs(self) -> List[str]:
        """Get list of priority pairs"""
        return list(self.priorityPairs)

    def isPriorityPair(self, product_id: str) -> bool:
        """Check if product is a priority pair"""
        return product_id in self.priorityPairs

    def clearPriorityPairs(self):
        """Clear all priority pairs"""
        self.priorityPairs.clear()
        self.lastPriorityUpdate.clear()
        logger.info('Cleared all priority pairs')

    def getPriorityStats(self) -> Dict:
        """Get priority statistics"""
        return {
            'totalPriorityPairs': len(self.priorityPairs),
            'priorityPairs': list(self.priorityPairs),
            'updateInterval': self.priorityUpdateInterval,
            'lastUpdates': self.lastPriorityUpdate.copy()
        }

    def getHealthSummary(self) -> Dict:
        """Get health summary of the WebSocket connections"""
        if not self.ws_manager:
            return {'status': 'not_initialized'}

        return self.ws_manager.get_health_summary()


# Factory function to create enhanced handler (same interface as original)
def create_websocket_handler(config: dict) -> EnhancedWebSocketHandler:
    """Create an enhanced WebSocket handler"""
    return EnhancedWebSocketHandler(config)