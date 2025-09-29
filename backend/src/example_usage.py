#!/usr/bin/env python3
"""
Example usage of the Multi-Connection WebSocket Manager
Shows how to integrate with your trading bot
"""

import time
import logging
import requests
from multi_ws_manager import MultiWSManager, TickerData, ConnectionStats

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TradingBotIntegration:
    """Example integration with a trading bot"""

    def __init__(self):
        self.ws_manager: Optional[MultiWSManager] = None
        self.price_alerts = {}
        self.volume_trackers = {}

    async def fetch_usd_pairs(self) -> list:
        """Fetch all available USD trading pairs from Coinbase"""
        try:
            logger.info("Fetching USD trading pairs...")
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

            logger.info(f"Found {len(usd_pairs)} USD trading pairs")
            return sorted(usd_pairs)

        except Exception as e:
            logger.error(f"Failed to fetch USD pairs: {e}")
            # Fallback to major pairs
            return [
                "BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD",
                "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "MATIC-USD",
                "LTC-USD", "BCH-USD", "ETC-USD", "ALGO-USD", "ATOM-USD"
            ]

    def setup_websocket_manager(self, products: list, products_per_connection: int = 15):
        """Setup the multi-connection WebSocket manager"""
        logger.info(f"Setting up WebSocket manager for {len(products)} products")

        self.ws_manager = MultiWSManager(
            products=products,
            products_per_connection=products_per_connection
        )

        # Add callbacks
        self.ws_manager.add_data_callback(self.handle_price_update)
        self.ws_manager.add_stats_callback(self.handle_connection_stats)

        return self.ws_manager

    def handle_price_update(self, ticker_data: TickerData):
        """Handle incoming price updates - integrate with your trading logic"""
        product_id = ticker_data.product_id
        price = ticker_data.price

        # Example: Basic price change detection
        if product_id in self.price_alerts:
            last_price = self.price_alerts[product_id]
            pct_change = ((price - last_price) / last_price) * 100

            # Example: Alert on 5% price change
            if abs(pct_change) >= 5.0:
                logger.warning(
                    f"🚨 PRICE ALERT: {product_id} changed {pct_change:.2f}% "
                    f"(${last_price:.4f} → ${price:.4f})"
                )

                # Here you would integrate with your spike detection system
                self.send_to_spike_detector(ticker_data, pct_change)

        # Update stored price
        self.price_alerts[product_id] = price

        # Example: Volume tracking (basic)
        if ticker_data.last_size > 0:
            if product_id not in self.volume_trackers:
                self.volume_trackers[product_id] = {'total': 0, 'count': 0}

            self.volume_trackers[product_id]['total'] += ticker_data.last_size
            self.volume_trackers[product_id]['count'] += 1

    def send_to_spike_detector(self, ticker_data: TickerData, pct_change: float):
        """Send price spike data to your existing spike detection system"""
        spike_data = {
            'symbol': ticker_data.product_id,
            'price': ticker_data.price,
            'last_size': ticker_data.last_size,
            'pct_change': pct_change,
            'time': ticker_data.time,
            'connection_id': ticker_data.connection_id
        }

        # Example: Send to existing spike detection service
        # You would replace this with your actual implementation
        logger.info(f"Sending spike data to detector: {spike_data}")

    def handle_connection_stats(self, stats: ConnectionStats):
        """Handle connection statistics - monitor health"""
        if stats.status == "connected":
            logger.debug(f"Connection {stats.connection_id}: {stats.products_count} products, "
                        f"uptime: {stats.uptime_seconds():.0f}s")
        elif stats.status == "error":
            logger.error(f"Connection {stats.connection_id} error: {stats.error_count} total errors")
        elif stats.status == "disconnected":
            logger.warning(f"Connection {stats.connection_id} disconnected")

    def start_monitoring(self):
        """Start the monitoring system"""
        if not self.ws_manager:
            raise ValueError("WebSocket manager not setup. Call setup_websocket_manager first.")

        logger.info("Starting price monitoring...")
        self.ws_manager.start()

        # Wait for connections to establish
        time.sleep(5)

        logger.info("Price monitoring started successfully")

    def stop_monitoring(self):
        """Stop the monitoring system"""
        if self.ws_manager:
            logger.info("Stopping price monitoring...")
            self.ws_manager.stop()
            logger.info("Price monitoring stopped")

    def get_current_price(self, product_id: str) -> float:
        """Get current price for a product"""
        if not self.ws_manager:
            return None

        ticker = self.ws_manager.get_latest_price(product_id)
        return ticker.price if ticker else None

    def get_all_prices(self) -> dict:
        """Get all current prices"""
        if not self.ws_manager:
            return {}

        return {
            product_id: ticker.price
            for product_id, ticker in self.ws_manager.get_all_latest_prices().items()
        }

    def print_health_report(self):
        """Print system health report"""
        if not self.ws_manager:
            print("WebSocket manager not initialized")
            return

        health = self.ws_manager.get_health_summary()

        print("\n" + "="*50)
        print("WEBSOCKET HEALTH REPORT")
        print("="*50)
        print(f"Connections: {health['connected_connections']}/{health['total_connections']} active")
        print(f"Products: {health['active_products']}/{health['total_products']} receiving data")
        print(f"Coverage: {health['coverage_percentage']:.1f}%")
        print(f"Total Messages: {health['total_messages_received']:,}")
        print(f"Total Errors: {health['total_errors']}")
        print("="*50)

        # Connection details
        stats = self.ws_manager.get_connection_stats()
        for conn_id, stat in stats.items():
            status_icon = "✅" if stat.status == "connected" else "❌"
            print(f"{status_icon} {conn_id}: {stat.products_count} products, "
                  f"{stat.total_messages:,} msgs, {stat.error_count} errors")

def main():
    """Example main function"""
    trading_bot = TradingBotIntegration()

    try:
        # Fetch USD pairs
        products = trading_bot.fetch_usd_pairs()

        # Limit to first 100 for testing (remove this for full deployment)
        products = products[:100]

        # Setup WebSocket manager
        trading_bot.setup_websocket_manager(products, products_per_connection=15)

        # Start monitoring
        trading_bot.start_monitoring()

        print(f"Monitoring {len(products)} products across multiple WebSocket connections")
        print("Press Ctrl+C to stop...")

        # Monitor for a while
        try:
            while True:
                time.sleep(30)  # Report every 30 seconds
                trading_bot.print_health_report()

                # Example: Get specific prices
                btc_price = trading_bot.get_current_price("BTC-USD")
                eth_price = trading_bot.get_current_price("ETH-USD")

                if btc_price and eth_price:
                    print(f"\nCurrent Prices: BTC=${btc_price:.2f}, ETH=${eth_price:.2f}")

        except KeyboardInterrupt:
            print("\nShutdown requested...")

    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        trading_bot.stop_monitoring()
        print("Monitoring stopped")

if __name__ == "__main__":
    main()