#!/usr/bin/env python3
"""
Polygon.io REST API Client for Crypto 1-Minute Candles

Uses REST API polling instead of WebSocket to avoid connection limits.
Polls every 60 seconds for the latest completed minute candle for all pairs.
"""

import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, Callable, List, Set

logger = logging.getLogger(__name__)


class PolygonRestClient:
    """REST API client for Polygon.io crypto minute candles"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv('POLYGON_API_KEY')
        if not self.api_key:
            raise ValueError("POLYGON_API_KEY must be provided or set in environment")

        self.base_url = "https://api.polygon.io/v2/aggs/ticker"
        self.running = False
        self.subscribed_pairs: Set[str] = set()
        self.candle_handlers: List[Callable] = []
        self.session = None
        self.poll_interval = 60  # Poll every 60 seconds

        logger.info(f"Polygon REST Client initialized")

    def on_candle(self, handler: Callable):
        """Register a callback for candle updates"""
        self.candle_handlers.append(handler)

    def _coinbase_to_polygon(self, coinbase_symbol: str) -> str:
        """
        Convert Coinbase symbol to Polygon format
        Examples:
          X:BTC-USD → X:BTCUSD
          X:ETH-USD → X:ETHUSD
        """
        if coinbase_symbol.startswith('X:'):
            symbol = coinbase_symbol[2:]
        else:
            symbol = coinbase_symbol

        symbol = symbol.replace('-', '')
        return f"X:{symbol}"

    def _polygon_to_coinbase(self, polygon_symbol: str) -> str:
        """
        Convert Polygon symbol back to Coinbase format
        Examples:
          X:BTCUSD → X:BTC-USD
          X:ETHUSD → X:ETH-USD
        """
        if not polygon_symbol.startswith('X:'):
            return polygon_symbol

        base_symbol = polygon_symbol[2:]

        for quote in ['USD', 'USDT', 'USDC']:
            if base_symbol.endswith(quote):
                base = base_symbol[:-len(quote)]
                return f"X:{base}-{quote}"

        return polygon_symbol

    async def connect(self):
        """Initialize HTTP session"""
        self.session = aiohttp.ClientSession()
        logger.info("✅ HTTP session created")
        return True

    async def subscribe(self, coinbase_symbols: List[str]):
        """
        'Subscribe' to pairs (just adds them to the polling list)

        Args:
            coinbase_symbols: List of Coinbase-format symbols (e.g., ['X:BTC-USD', 'X:ETH-USD'])
        """
        self.subscribed_pairs.update(coinbase_symbols)
        logger.info(f"✅ Added {len(coinbase_symbols)} pairs to polling list (total: {len(self.subscribed_pairs)})")

    async def load_historical_data(self, minutes: int = 120):
        """
        Load historical candle data for all subscribed pairs on startup

        This prevents the 2-hour wait before trading can begin.

        Args:
            minutes: Number of minutes of history to load (default 120)
        """
        if not self.subscribed_pairs:
            logger.warning("No pairs subscribed, skipping historical data load")
            return

        logger.info(f"📥 Loading last {minutes} minutes of historical data for {len(self.subscribed_pairs)} pairs...")

        batch_size = 5  # Smaller batch to avoid rate limits
        pairs_list = list(self.subscribed_pairs)
        full_data = 0  # Pairs with 120 candles
        partial_data = 0  # Pairs with <120 candles (will accumulate from polling)
        no_data = 0  # Pairs with 0 candles (will start from polling)

        for i in range(0, len(pairs_list), batch_size):
            batch = pairs_list[i:i+batch_size]

            # Fetch batch concurrently
            tasks = [self._fetch_historical_candles(symbol, minutes) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for symbol, candles in zip(batch, results):
                if isinstance(candles, list) and len(candles) > 0:
                    # Send each historical candle through handlers
                    for candle in candles:
                        for handler in self.candle_handlers:
                            try:
                                if asyncio.iscoroutinefunction(handler):
                                    await handler(candle)
                                else:
                                    handler(candle)
                            except Exception as e:
                                logger.error(f"Error in candle handler for {symbol}: {e}")

                    if len(candles) >= minutes:
                        full_data += 1
                        logger.info(f"✅ {symbol}: {len(candles)} candles - READY TO TRADE")
                    else:
                        partial_data += 1
                        logger.info(f"⏳ {symbol}: {len(candles)}/{minutes} candles - accumulating")
                else:
                    no_data += 1

            # Delay between batches to respect rate limits
            await asyncio.sleep(1)

            # Progress update every 50 pairs
            if (i + batch_size) % 50 == 0:
                logger.info(f"   Progress: {i + batch_size}/{len(pairs_list)} pairs processed...")

        logger.info(f"✅ Historical data loaded:")
        logger.info(f"   • {full_data} pairs ready to trade (120+ candles)")
        logger.info(f"   • {partial_data} pairs accumulating (<120 candles, will reach 120 from live polling)")
        logger.info(f"   • {no_data} pairs starting fresh (0 candles, will accumulate from live polling)")
        logger.info(f"🎯 Bot is ready! Monitoring all {len(pairs_list)} pairs")

    async def _fetch_historical_candles(self, coinbase_symbol: str, minutes: int = 120) -> List[Dict]:
        """
        Fetch historical minute candles for a single pair

        Args:
            coinbase_symbol: Coinbase format symbol (e.g., 'X:BTC-USD')
            minutes: Number of minutes of history to fetch (default 120)

        Returns:
            List of candle dicts in chronological order (most recent 120 candles)
        """
        polygon_symbol = self._coinbase_to_polygon(coinbase_symbol)

        # Fetch extra minutes to account for gaps (request 150 minutes, use most recent 120)
        fetch_minutes = int(minutes * 1.25)  # 25% buffer
        now = datetime.now(timezone.utc)
        end_time = int(now.timestamp() * 1000)
        start_time = int((now - timedelta(minutes=fetch_minutes)).timestamp() * 1000)

        url = f"{self.base_url}/{polygon_symbol}/range/1/minute/{start_time}/{end_time}"
        params = {'apiKey': self.api_key, 'limit': 50000}  # Max limit

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('status') == 'OK' and data.get('results'):
                        all_candles = []
                        for candle in data['results']:
                            all_candles.append({
                                'symbol': coinbase_symbol,
                                'open': float(candle['o']),
                                'high': float(candle['h']),
                                'low': float(candle['l']),
                                'close': float(candle['c']),
                                'volume': float(candle['v']),
                                'start_timestamp': candle['t'],
                                'end_timestamp': candle['t'] + 60000,
                                'timestamp': datetime.fromtimestamp(candle['t'] / 1000, tz=timezone.utc)
                            })

                        # Take all candles we fetched (Polygon returns them in chronological order)
                        # If more than 'minutes' (120), take the most recent 120
                        # If less than 120, take all (trader will wait until 120 before trading)
                        candles = all_candles[-minutes:] if len(all_candles) > minutes else all_candles

                        # Accept any amount of historical data - trader will accumulate more from live polling
                        if len(candles) > 0:
                            if len(candles) < minutes:
                                logger.debug(f"{coinbase_symbol}: Loaded {len(candles)}/{minutes} candles, will accumulate rest from polling")
                            return candles
                        else:
                            logger.debug(f"{coinbase_symbol}: No historical data, will start from live polling")
                    else:
                        logger.warning(f"No historical data for {coinbase_symbol}: {data.get('status')}")
                else:
                    logger.warning(f"Failed to fetch historical {coinbase_symbol}: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Error fetching historical candles for {coinbase_symbol}: {e}")

        return []

    async def _fetch_candle(self, coinbase_symbol: str) -> Dict:
        """
        Fetch the latest minute candle for a single pair

        Returns the most recent completed 1-minute candle
        """
        polygon_symbol = self._coinbase_to_polygon(coinbase_symbol)

        # Get the last 2 minutes of data (to ensure we get the most recent completed candle)
        now = datetime.now(timezone.utc)
        end_time = int(now.timestamp() * 1000)
        start_time = int((now - timedelta(minutes=2)).timestamp() * 1000)

        url = f"{self.base_url}/{polygon_symbol}/range/1/minute/{start_time}/{end_time}"
        params = {'apiKey': self.api_key}

        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get('status') == 'OK' and data.get('results'):
                        # Get the most recent candle
                        latest = data['results'][-1]

                        return {
                            'symbol': coinbase_symbol,
                            'open': float(latest['o']),
                            'high': float(latest['h']),
                            'low': float(latest['l']),
                            'close': float(latest['c']),
                            'volume': float(latest['v']),
                            'start_timestamp': latest['t'],  # milliseconds
                            'end_timestamp': latest['t'] + 60000,  # Add 1 minute
                            'timestamp': datetime.fromtimestamp(latest['t'] / 1000, tz=timezone.utc)
                        }
                else:
                    logger.warning(f"Failed to fetch {coinbase_symbol}: HTTP {response.status}")

        except Exception as e:
            logger.error(f"Error fetching candle for {coinbase_symbol}: {e}")

        return None

    async def _poll_all_pairs(self):
        """Poll all subscribed pairs for latest candles"""
        if not self.subscribed_pairs:
            return

        logger.debug(f"Polling {len(self.subscribed_pairs)} pairs...")

        # Fetch in batches to avoid overwhelming the API
        batch_size = 10  # Process 10 pairs concurrently
        pairs_list = list(self.subscribed_pairs)

        for i in range(0, len(pairs_list), batch_size):
            batch = pairs_list[i:i+batch_size]

            # Fetch batch concurrently
            tasks = [self._fetch_candle(symbol) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for candle_data in results:
                if candle_data and isinstance(candle_data, dict):
                    # Call all registered handlers
                    for handler in self.candle_handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(candle_data)
                            else:
                                handler(candle_data)
                        except Exception as e:
                            logger.error(f"Error in candle handler: {e}")

            # Small delay between batches to respect rate limits
            await asyncio.sleep(0.5)

    async def run(self):
        """Main polling loop"""
        self.running = True
        logger.info(f"🔄 Starting Polygon REST polling (every {self.poll_interval}s)...")

        poll_count = 0

        try:
            while self.running:
                poll_count += 1

                # Log every poll for debugging
                logger.info(f"📊 Polling cycle #{poll_count} starting...")

                await self._poll_all_pairs()

                logger.info(f"✅ Polling cycle #{poll_count} complete, sleeping {self.poll_interval}s")

                # Wait for next poll interval
                await asyncio.sleep(self.poll_interval)

        except Exception as e:
            logger.error(f"Error in polling loop: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("Polling loop stopped")

    async def close(self):
        """Close HTTP session"""
        self.running = False
        if self.session:
            await self.session.close()
            logger.info("Polygon HTTP session closed")


async def test_polygon_rest():
    """Test function to verify Polygon REST polling"""
    logging.basicConfig(level=logging.INFO)

    client = PolygonRestClient()

    # Handler to print candles
    def print_candle(candle):
        print(f"\n📊 {candle['symbol']}")
        print(f"   Open:  ${candle['open']:.4f}")
        print(f"   High:  ${candle['high']:.4f}")
        print(f"   Low:   ${candle['low']:.4f}")
        print(f"   Close: ${candle['close']:.4f}")
        print(f"   Time:  {candle['timestamp']}")

    client.on_candle(print_candle)

    # Connect
    if await client.connect():
        # Subscribe to test pairs
        await client.subscribe(['X:BTC-USD', 'X:ETH-USD', 'X:XRP-USD'])

        # Run for 5 minutes (5 polls)
        client.poll_interval = 60
        asyncio.create_task(client.run())
        await asyncio.sleep(300)

        await client.close()


if __name__ == '__main__':
    asyncio.run(test_polygon_rest())
