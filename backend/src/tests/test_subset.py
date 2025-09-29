#!/usr/bin/env python3
"""
Test the enhanced WebSocket system with a subset of pairs
"""

import asyncio
import time
import logging
import sys
import os
from enhanced_websocket_handler import EnhancedWebSocketHandler

# Fix Windows console encoding
if sys.platform.startswith('win'):
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TestMonitor:
    def __init__(self):
        self.price_updates = {}
        self.last_update_times = {}
        self.total_messages = 0

    def handle_ticker_update(self, ticker_data):
        """Handle ticker updates"""
        symbol = ticker_data['crypto']
        price = ticker_data['price']

        self.price_updates[symbol] = price
        self.last_update_times[symbol] = time.time()
        self.total_messages += 1

        logger.info(f"PRICE {symbol}: ${price:.4f}")

    def print_stats(self):
        """Print monitoring statistics"""
        now = time.time()
        active_symbols = sum(1 for symbol, last_time in self.last_update_times.items()
                           if now - last_time < 60)  # Active in last minute

        print(f"\nSTATS: {active_symbols}/{len(self.price_updates)} symbols active, {self.total_messages} total messages")

        # Show recent prices
        for symbol, price in sorted(self.price_updates.items()):
            last_update = self.last_update_times.get(symbol, 0)
            age = now - last_update
            status = "ACTIVE" if age < 30 else "STALE" if age < 60 else "DEAD"
            print(f"  {status} {symbol}: ${price:.4f} ({age:.0f}s ago)")

async def test_subset():
    """Test with a small subset of major pairs"""

    # Test with major pairs only
    test_pairs = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"]

    config = {
        'cryptoConfig': test_pairs,
        'productsPerConnection': 3,  # Small batches for testing
        'volumeThreshold': 1.5,
        'windowMinutes': 5
    }

    monitor = TestMonitor()
    handler = EnhancedWebSocketHandler(config)

    # Setup event listeners
    handler.on('ticker_update', monitor.handle_ticker_update)

    try:
        print(f"Testing enhanced WebSocket system with {len(test_pairs)} pairs:")
        print(f"   {', '.join(test_pairs)}")
        print("   Starting connections...")

        # Initialize
        await handler.initialize()

        print("Connections established, monitoring for 2 minutes...")

        # Monitor for 2 minutes
        for i in range(24):  # 24 * 5 seconds = 2 minutes
            await asyncio.sleep(5)

            if i % 4 == 0:  # Every 20 seconds
                monitor.print_stats()

            # Check if we're getting data
            if monitor.total_messages == 0 and i > 4:  # No messages after 20 seconds
                print("WARNING: No messages received, checking connections...")
                if handler.ws_manager:
                    health = handler.getHealthSummary()
                    print(f"   Health: {health}")

        print(f"\nTest completed successfully!")
        monitor.print_stats()

        if handler.ws_manager:
            health = handler.getHealthSummary()
            print(f"\nFinal Health Summary:")
            print(f"   Connected: {health.get('connected_connections', 0)}/{health.get('total_connections', 0)}")
            print(f"   Coverage: {health.get('coverage_percentage', 0):.1f}%")
            print(f"   Messages: {health.get('total_messages_received', 0)}")
            print(f"   Errors: {health.get('total_errors', 0)}")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise
    finally:
        # Clean shutdown
        if handler:
            handler.disconnect()
        print("Test cleanup completed")

if __name__ == "__main__":
    asyncio.run(test_subset())