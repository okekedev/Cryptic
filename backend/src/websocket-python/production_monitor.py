#!/usr/bin/env python3
"""
Production-ready multi-connection WebSocket monitor for 300+ cryptocurrency pairs
"""

import asyncio
import time
import logging
import signal
import sys
from datetime import datetime
from enhanced_websocket_handler import EnhancedWebSocketHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProductionMonitor:
    """Production cryptocurrency monitor"""

    def __init__(self):
        self.handler = None
        self.running = False
        self.stats = {
            'start_time': datetime.now(),
            'total_messages': 0,
            'pairs_active': 0,
            'last_health_check': None
        }

    def setup_handler(self, max_pairs=None):
        """Setup the enhanced WebSocket handler for production"""
        try:
            # Production configuration
            config = {
                'cryptoConfig': None,  # Monitor ALL USD pairs
                'productsPerConnection': 15,  # Optimal for reliability
                'volumeThreshold': 1.5,
                'windowMinutes': 5
            }

            # If testing with limited pairs
            if max_pairs:
                config['cryptoConfig'] = {'topN': max_pairs}
                logger.info(f"Testing mode: limiting to top {max_pairs} pairs")

            self.handler = EnhancedWebSocketHandler(config)

            # Setup event listeners
            self.handler.on('ticker_update', self._handle_ticker_update)
            self.handler.on('volume_alert', self._handle_volume_alert)

            logger.info("Production monitor configured")
            return True

        except Exception as e:
            logger.error(f"Failed to setup production handler: {e}")
            return False

    def _handle_ticker_update(self, ticker_data):
        """Handle ticker updates"""
        self.stats['total_messages'] += 1

        # Log significant price movements
        if hasattr(self, '_last_prices'):
            symbol = ticker_data['crypto']
            current_price = ticker_data['price']

            if symbol in self._last_prices:
                last_price = self._last_prices[symbol]
                pct_change = ((current_price - last_price) / last_price) * 100

                # Log significant moves
                if abs(pct_change) >= 2.0:  # 2% threshold for logging
                    logger.info(f"MOVE {symbol}: {pct_change:+.2f}% (${last_price:.4f} → ${current_price:.4f})")

            self._last_prices[symbol] = current_price
        else:
            self._last_prices = {ticker_data['crypto']: ticker_data['price']}

    def _handle_volume_alert(self, alert_data):
        """Handle volume alerts"""
        logger.warning(
            f"VOLUME SPIKE {alert_data['crypto']}: "
            f"{alert_data['ratio']:.2f}x normal volume "
            f"({alert_data['current_vol']:.2f} vs {alert_data['avg_vol']:.2f})"
        )

    async def start_monitoring(self):
        """Start production monitoring"""
        if not self.handler:
            logger.error("Handler not configured. Call setup_handler first.")
            return False

        try:
            self.running = True

            logger.info("Initializing production monitoring system...")
            await self.handler.initialize()

            logger.info("Production monitoring started successfully")

            # Start health monitoring loop
            await self._monitoring_loop()

        except Exception as e:
            logger.error(f"Production monitoring failed: {e}")
            return False

    async def _monitoring_loop(self):
        """Main monitoring loop with health checks"""
        while self.running:
            try:
                await asyncio.sleep(30)  # Health check every 30 seconds

                if self.handler and self.handler.ws_manager:
                    health = self.handler.getHealthSummary()
                    self.stats['pairs_active'] = health.get('active_products', 0)
                    self.stats['last_health_check'] = datetime.now()

                    # Log health status
                    logger.info(
                        f"HEALTH: {health.get('connected_connections', 0)}/{health.get('total_connections', 0)} connections, "
                        f"{health.get('active_products', 0)}/{health.get('total_products', 0)} pairs active "
                        f"({health.get('coverage_percentage', 0):.1f}% coverage), "
                        f"{health.get('total_messages_received', 0):,} messages, "
                        f"{health.get('total_errors', 0)} errors"
                    )

                    # Alert on poor coverage
                    coverage = health.get('coverage_percentage', 0)
                    if coverage < 80:
                        logger.warning(f"LOW COVERAGE: Only {coverage:.1f}% of pairs receiving data")

                    # Alert on high error rate
                    total_msgs = health.get('total_messages_received', 1)
                    total_errors = health.get('total_errors', 0)
                    error_rate = (total_errors / total_msgs) * 100 if total_msgs > 0 else 0

                    if error_rate > 5:  # More than 5% error rate
                        logger.warning(f"HIGH ERROR RATE: {error_rate:.2f}% ({total_errors}/{total_msgs})")

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")

    def stop_monitoring(self):
        """Stop production monitoring"""
        self.running = False

        if self.handler:
            self.handler.disconnect()

        uptime = (datetime.now() - self.stats['start_time']).total_seconds()
        logger.info(f"Production monitor stopped after {uptime:.0f}s runtime")

    def get_stats(self):
        """Get monitoring statistics"""
        uptime = (datetime.now() - self.stats['start_time']).total_seconds()

        return {
            'uptime_seconds': uptime,
            'uptime_formatted': f"{uptime/3600:.1f} hours",
            'total_messages': self.stats['total_messages'],
            'pairs_active': self.stats['pairs_active'],
            'message_rate': self.stats['total_messages'] / uptime if uptime > 0 else 0,
            'last_health_check': self.stats['last_health_check'].isoformat() if self.stats['last_health_check'] else None,
            'status': 'running' if self.running else 'stopped'
        }

async def main():
    """Main function"""
    monitor = ProductionMonitor()

    # Signal handlers
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        monitor.stop_monitoring()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # For demonstration, we'll test with 50 pairs
        # In production, remove max_pairs parameter to monitor ALL USD pairs
        max_pairs = 50  # Remove this line for full 300+ pairs

        print("Starting Production Cryptocurrency Monitor")
        print(f"   Max pairs: {'ALL USD pairs' if not max_pairs else f'Top {max_pairs} pairs'}")
        print(f"   Products per connection: 15")
        print(f"   Volume threshold: 1.5x")
        print(f"   Health checks: Every 30 seconds")
        print()

        if monitor.setup_handler(max_pairs=max_pairs):
            await monitor.start_monitoring()
        else:
            print("FAILED to setup monitor")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nShutdown requested...")
    except Exception as e:
        logger.error(f"Production monitor crashed: {e}")
        sys.exit(1)
    finally:
        monitor.stop_monitoring()
        print("Production monitor shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())