#!/usr/bin/env python3
"""
Health Monitor for the Enhanced WebSocket System
Provides REST API endpoint and monitoring dashboard
"""

import asyncio
import time
import json
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template_string
from threading import Thread
from enhanced_websocket_handler import EnhancedWebSocketHandler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HealthMonitorAPI:
    def __init__(self, port=5001):
        self.app = Flask(__name__)
        self.port = port
        self.handler = None
        self.stats_history = []
        self.price_samples = {}
        self.start_time = datetime.now()

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup Flask routes"""

        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Main health check endpoint"""
            if not self.handler or not self.handler.ws_manager:
                return jsonify({
                    'status': 'not_initialized',
                    'message': 'WebSocket handler not initialized'
                }), 503

            health = self.handler.getHealthSummary()

            # Add additional metrics
            health['uptime_seconds'] = (datetime.now() - self.start_time).total_seconds()
            health['price_samples'] = len(self.price_samples)
            health['last_updated'] = datetime.now().isoformat()

            # Status determination
            if health.get('connected_connections', 0) == 0:
                health['status'] = 'critical'
                status_code = 503
            elif health.get('coverage_percentage', 0) < 50:
                health['status'] = 'warning'
                status_code = 200
            else:
                health['status'] = 'healthy'
                status_code = 200

            return jsonify(health), status_code

        @self.app.route('/connections', methods=['GET'])
        def connections_status():
            """Detailed connection status"""
            if not self.handler or not self.handler.ws_manager:
                return jsonify({'error': 'Not initialized'}), 503

            connection_stats = self.handler.ws_manager.get_connection_stats()

            connections = []
            for conn_id, stats in connection_stats.items():
                conn_info = {
                    'connection_id': stats.connection_id,
                    'status': stats.status,
                    'products_count': stats.products_count,
                    'total_messages': stats.total_messages,
                    'error_count': stats.error_count,
                    'uptime_seconds': stats.uptime_seconds(),
                    'last_heartbeat': stats.last_heartbeat.isoformat() if stats.last_heartbeat else None,
                    'last_message': stats.last_message.isoformat() if stats.last_message else None
                }
                connections.append(conn_info)

            return jsonify({
                'total_connections': len(connections),
                'connected': sum(1 for c in connections if c['status'] == 'connected'),
                'connections': connections
            })

        @self.app.route('/prices', methods=['GET'])
        def current_prices():
            """Get current prices for all monitored pairs"""
            if not self.handler:
                return jsonify({'error': 'Not initialized'}), 503

            limit = request.args.get('limit', type=int, default=50)

            all_tickers = self.handler.getAllTickers()

            # Sort by most recently updated
            sorted_pairs = sorted(
                all_tickers.items(),
                key=lambda x: x[1].get('time', ''),
                reverse=True
            )[:limit]

            prices = {}
            for symbol, ticker in sorted_pairs:
                prices[symbol] = {
                    'price': ticker['price'],
                    'last_updated': ticker.get('time', ''),
                    'volume_24h': ticker.get('volume_24h', 0)
                }

            return jsonify({
                'count': len(prices),
                'prices': prices,
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/price/<symbol>', methods=['GET'])
        def get_price(symbol):
            """Get price for a specific symbol"""
            if not self.handler:
                return jsonify({'error': 'Not initialized'}), 503

            ticker = self.handler.getCurrentTicker(symbol.upper())
            if not ticker:
                return jsonify({'error': f'Symbol {symbol} not found'}), 404

            return jsonify({
                'symbol': symbol.upper(),
                'price': ticker['price'],
                'bid': ticker.get('bid', 0),
                'ask': ticker.get('ask', 0),
                'last_updated': ticker.get('time', ''),
                'volume_24h': ticker.get('volume_24h', 0)
            })

        @self.app.route('/dashboard', methods=['GET'])
        def dashboard():
            """Simple HTML dashboard"""
            if not self.handler or not self.handler.ws_manager:
                return "WebSocket handler not initialized", 503

            health = self.handler.getHealthSummary()
            connection_stats = self.handler.ws_manager.get_connection_stats()

            dashboard_html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>WebSocket Health Dashboard</title>
                <meta http-equiv="refresh" content="10">
                <style>
                    body { font-family: Arial, sans-serif; margin: 20px; }
                    .status-healthy { color: green; }
                    .status-warning { color: orange; }
                    .status-critical { color: red; }
                    .connection { margin: 10px 0; padding: 10px; border: 1px solid #ccc; }
                    .metrics { display: flex; gap: 20px; }
                    .metric { padding: 10px; background: #f0f0f0; border-radius: 5px; }
                </style>
            </head>
            <body>
                <h1>WebSocket Health Dashboard</h1>
                <p><strong>Status:</strong> <span class="status-{{ status }}">{{ status_text }}</span></p>
                <p><strong>Last Updated:</strong> {{ timestamp }}</p>

                <div class="metrics">
                    <div class="metric">
                        <h3>Connections</h3>
                        <p>{{ connected_connections }}/{{ total_connections }} Active</p>
                    </div>
                    <div class="metric">
                        <h3>Coverage</h3>
                        <p>{{ active_products }}/{{ total_products }} Products</p>
                        <p>{{ coverage_percentage }}% Coverage</p>
                    </div>
                    <div class="metric">
                        <h3>Messages</h3>
                        <p>{{ total_messages_received }} Total</p>
                        <p>{{ total_errors }} Errors</p>
                    </div>
                </div>

                <h2>Connection Details</h2>
                {% for conn_id, stats in connections.items() %}
                <div class="connection">
                    <h3>{{ conn_id }}</h3>
                    <p><strong>Status:</strong> {{ stats.status }}</p>
                    <p><strong>Products:</strong> {{ stats.products_count }}</p>
                    <p><strong>Messages:</strong> {{ stats.total_messages }}</p>
                    <p><strong>Errors:</strong> {{ stats.error_count }}</p>
                    <p><strong>Uptime:</strong> {{ "%.0f"|format(stats.uptime_seconds()) }}s</p>
                </div>
                {% endfor %}
            </body>
            </html>
            """

            status_text = "Healthy" if health.get('coverage_percentage', 0) > 80 else "Warning"
            status = "healthy" if health.get('coverage_percentage', 0) > 80 else "warning"

            return render_template_string(
                dashboard_html,
                status=status,
                status_text=status_text,
                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                connections=connection_stats,
                **health
            )

    def setup_handler(self, test_pairs=None):
        """Setup the WebSocket handler"""
        try:
            if test_pairs:
                config = {
                    'cryptoConfig': test_pairs,
                    'productsPerConnection': 10,
                    'volumeThreshold': 1.5,
                    'windowMinutes': 5
                }
            else:
                config = {
                    'cryptoConfig': None,  # Monitor all USD pairs
                    'productsPerConnection': 15,
                    'volumeThreshold': 1.5,
                    'windowMinutes': 5
                }

            self.handler = EnhancedWebSocketHandler(config)

            # Setup event listeners
            self.handler.on('ticker_update', self._handle_ticker_update)

            logger.info("Health monitor handler configured")
            return True

        except Exception as e:
            logger.error(f"Failed to setup handler: {e}")
            return False

    def _handle_ticker_update(self, ticker_data):
        """Handle ticker updates for monitoring"""
        symbol = ticker_data['crypto']
        self.price_samples[symbol] = {
            'price': ticker_data['price'],
            'timestamp': datetime.now()
        }

        # Keep only recent samples (last 10 minutes)
        cutoff = datetime.now() - timedelta(minutes=10)
        self.price_samples = {
            k: v for k, v in self.price_samples.items()
            if v['timestamp'] > cutoff
        }

    async def start_websocket_handler(self):
        """Start the WebSocket handler"""
        if self.handler:
            await self.handler.initialize()
            logger.info("WebSocket handler started")
        else:
            logger.error("Handler not setup")

    def run(self, debug=False):
        """Run the Flask app"""
        logger.info(f"Starting health monitor API on port {self.port}")
        self.app.run(host='0.0.0.0', port=self.port, debug=debug, threaded=True)

async def main():
    """Main function for testing"""
    monitor = HealthMonitorAPI(port=5001)

    # Setup with test pairs
    test_pairs = ["BTC-USD", "ETH-USD", "SOL-USD", "DOGE-USD", "XRP-USD"]

    if monitor.setup_handler(test_pairs):
        # Start WebSocket handler in background
        websocket_task = asyncio.create_task(monitor.start_websocket_handler())

        # Start Flask server in a thread
        server_thread = Thread(target=monitor.run, daemon=True)
        server_thread.start()

        print("Health monitor started!")
        print("Dashboard: http://localhost:5001/dashboard")
        print("Health API: http://localhost:5001/health")
        print("Press Ctrl+C to stop...")

        try:
            await websocket_task
        except KeyboardInterrupt:
            print("Stopping...")
    else:
        print("Failed to setup handler")

if __name__ == "__main__":
    asyncio.run(main())