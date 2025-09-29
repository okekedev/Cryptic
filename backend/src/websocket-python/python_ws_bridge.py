#!/usr/bin/env python3
"""
Python WebSocket Bridge
Connects the enhanced multi-connection WebSocket manager with Node.js backend
"""

import sys
import json
import asyncio
import logging
import signal
import threading
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from enhanced_websocket_handler import EnhancedWebSocketHandler
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PythonWebSocketBridge:
    """Bridge between Node.js and Python WebSocket handler"""

    def __init__(self):
        self.handler: EnhancedWebSocketHandler = None
        self.running = False
        self.input_thread = None

    def setup_handler(self):
        """Setup the enhanced WebSocket handler"""
        try:
            # Get configuration from environment
            crypto_config_str = os.getenv('CRYPTO_CONFIG', 'null')
            crypto_config = json.loads(crypto_config_str) if crypto_config_str != 'null' else None

            config = {
                'wsUrl': 'wss://advanced-trade-ws.coinbase.com',
                'cryptoConfig': crypto_config,
                'productsPerConnection': int(os.getenv('PRODUCTS_PER_CONNECTION', '15')),
                'volumeThreshold': float(os.getenv('VOLUME_THRESHOLD', '1.5')),
                'windowMinutes': int(os.getenv('WINDOW_MINUTES', '5'))
            }

            self.handler = EnhancedWebSocketHandler(config)

            # Setup event listeners
            self.handler.on('ticker_update', self.handle_ticker_update)
            self.handler.on('volume_alert', self.handle_volume_alert)
            self.handler.on('priority_ticker_update', self.handle_priority_ticker_update)

            logger.info("WebSocket handler configured")

        except Exception as e:
            logger.error(f"Failed to setup handler: {e}")
            raise

    async def start(self):
        """Start the bridge"""
        try:
            self.running = True

            # Initialize the handler
            await self.handler.initialize()

            # Start input monitoring thread
            self.input_thread = threading.Thread(target=self.monitor_input, daemon=True)
            self.input_thread.start()

            # Send ready status
            self.send_status({'ready': True})

            # Start health monitoring
            await self.monitor_health()

        except Exception as e:
            logger.error(f"Failed to start bridge: {e}")
            self.send_status({'ready': False, 'error': str(e)})

    def monitor_input(self):
        """Monitor stdin for commands from Node.js"""
        while self.running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                command = json.loads(line)
                self.handle_command(command)

            except json.JSONDecodeError:
                logger.error(f"Invalid JSON command: {line}")
            except Exception as e:
                logger.error(f"Error processing command: {e}")

    def handle_command(self, command):
        """Handle commands from Node.js"""
        try:
            command_type = command.get('type')

            if command_type == 'priority_pair':
                action = command.get('action')
                product_id = command.get('product_id')

                if action == 'add':
                    self.handler.addPriorityPair(product_id)
                elif action == 'remove':
                    self.handler.removePriorityPair(product_id)

            elif command_type == 'clear_priority_pairs':
                self.handler.clearPriorityPairs()

            elif command_type == 'get_priority_stats':
                stats = self.handler.getPriorityStats()
                self.send_data('PRIORITY_STATS', stats)

            elif command_type == 'get_health':
                health = self.handler.getHealthSummary()
                self.send_data('HEALTH', health)

            else:
                logger.warning(f"Unknown command type: {command_type}")

        except Exception as e:
            logger.error(f"Error handling command: {e}")

    def handle_ticker_update(self, ticker_data):
        """Handle ticker update from WebSocket handler"""
        self.send_data('TICKER', ticker_data)

    def handle_volume_alert(self, alert_data):
        """Handle volume alert from WebSocket handler"""
        self.send_data('VOLUME_ALERT', alert_data)

    def handle_priority_ticker_update(self, ticker_data):
        """Handle priority ticker update"""
        self.send_data('PRIORITY_TICKER', ticker_data)

    def send_data(self, data_type, data):
        """Send data to Node.js via stdout"""
        try:
            message = f"{data_type}:{json.dumps(data)}"
            print(message, flush=True)
        except Exception as e:
            logger.error(f"Error sending data: {e}")

    def send_status(self, status):
        """Send status update to Node.js"""
        self.send_data('STATUS', status)

    async def monitor_health(self):
        """Monitor and report health status"""
        while self.running:
            try:
                await asyncio.sleep(30)  # Report every 30 seconds

                if self.handler and self.handler.ws_manager:
                    health = self.handler.getHealthSummary()
                    self.send_data('HEALTH', health)

            except Exception as e:
                logger.error(f"Error in health monitor: {e}")

    async def stop(self):
        """Stop the bridge"""
        self.running = False

        if self.handler:
            self.handler.disconnect()

        logger.info("Bridge stopped")

async def main():
    """Main function"""
    bridge = PythonWebSocketBridge()

    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.create_task(bridge.stop())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        bridge.setup_handler()
        await bridge.start()
    except Exception as e:
        logger.error(f"Bridge failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())