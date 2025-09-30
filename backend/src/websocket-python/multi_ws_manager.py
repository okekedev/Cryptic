#!/usr/bin/env python3
"""
Multi-Connection WebSocket Manager for Coinbase Advanced Trade
Monitors 300+ cryptocurrencies with high reliability and no downtime
"""

import asyncio
import json
import logging
import time
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Callable, Any
import websocket
import requests
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ConnectionConfig:
    """Configuration for a single WebSocket connection"""
    connection_id: str
    products: List[str]
    max_products_per_connection: int = 15
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    heartbeat_timeout: float = 30.0

@dataclass
class ConnectionStats:
    """Statistics for monitoring connection health"""
    connection_id: str
    products_count: int
    status: str = "disconnected"  # disconnected, connecting, connected, error
    last_heartbeat: Optional[datetime] = None
    last_message: Optional[datetime] = None
    reconnect_attempts: int = 0
    total_messages: int = 0
    error_count: int = 0
    uptime_start: Optional[datetime] = None

    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds"""
        if self.uptime_start and self.status == "connected":
            return (datetime.now() - self.uptime_start).total_seconds()
        return 0.0

@dataclass
class TickerData:
    """Standardized ticker data structure"""
    product_id: str
    price: float
    last_size: float
    time: str
    sequence: int
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    volume_24h: Optional[float] = None
    connection_id: Optional[str] = None
    received_at: datetime = field(default_factory=datetime.now)

class ConnectionManager:
    """Manages a single WebSocket connection with automatic reconnection"""

    def __init__(self, config: ConnectionConfig, data_callback: Callable, stats_callback: Callable):
        self.config = config
        self.data_callback = data_callback
        self.stats_callback = stats_callback

        self.ws: Optional[websocket.WebSocketApp] = None
        self.stats = ConnectionStats(
            connection_id=config.connection_id,
            products_count=len(config.products)
        )

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_heartbeat = time.time()
        self.heartbeat_monitor_thread: Optional[threading.Thread] = None

        # Reconnection logic
        self.reconnect_delay = config.reconnect_delay
        self.should_reconnect = True

    def start(self):
        """Start the connection manager"""
        if self.running:
            logger.warning(f"Connection {self.config.connection_id} already running")
            return

        self.running = True
        self.should_reconnect = True
        self.thread = threading.Thread(target=self._run_connection, daemon=True)
        self.thread.start()

        # Start heartbeat monitor
        self.heartbeat_monitor_thread = threading.Thread(target=self._monitor_heartbeat, daemon=True)
        self.heartbeat_monitor_thread.start()

        logger.info(f"Started connection manager {self.config.connection_id} for {len(self.config.products)} products")

    def stop(self):
        """Stop the connection manager"""
        self.running = False
        self.should_reconnect = False

        if self.ws:
            self.ws.close()

        if self.thread:
            self.thread.join(timeout=5.0)

        logger.info(f"Stopped connection manager {self.config.connection_id}")

    def _run_connection(self):
        """Main connection loop with automatic reconnection"""
        while self.running and self.should_reconnect:
            try:
                self._connect()
                if self.ws:
                    self.ws.run_forever(
                        ping_interval=20,  # Send ping every 20 seconds
                        ping_timeout=10    # Wait 10 seconds for pong
                    )
            except Exception as e:
                logger.error(f"Connection {self.config.connection_id} error: {e}")
                self.stats.error_count += 1
                self.stats.status = "error"
                self.stats_callback(self.stats)

            if self.should_reconnect and self.running:
                self._handle_reconnect()

    def _connect(self):
        """Establish WebSocket connection"""
        self.stats.status = "connecting"
        self.stats.reconnect_attempts += 1
        self.stats_callback(self.stats)

        ws_url = "wss://advanced-trade-ws.coinbase.com"

        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )

        logger.info(f"Connecting {self.config.connection_id} to {ws_url}")

    def _on_open(self, ws):
        """Handle WebSocket connection opened"""
        self.stats.status = "connected"
        self.stats.uptime_start = datetime.now()
        self.stats.reconnect_attempts = 0
        self.reconnect_delay = self.config.reconnect_delay  # Reset delay
        self.last_heartbeat = time.time()

        logger.info(f"Connection {self.config.connection_id} opened")

        # Subscribe to heartbeats first
        heartbeat_msg = {
            "type": "subscribe",
            "channel": "heartbeats"
        }
        ws.send(json.dumps(heartbeat_msg))

        # Subscribe to ticker channel for all products
        ticker_msg = {
            "type": "subscribe",
            "product_ids": self.config.products,
            "channel": "ticker"
        }
        ws.send(json.dumps(ticker_msg))

        logger.info(f"Subscribed {self.config.connection_id} to {len(self.config.products)} products")
        self.stats_callback(self.stats)

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            self.stats.total_messages += 1
            self.stats.last_message = datetime.now()

            # Handle different message types
            if data.get("channel") == "heartbeats":
                self.last_heartbeat = time.time()
                self.stats.last_heartbeat = datetime.now()

            elif data.get("channel") == "ticker":
                self._handle_ticker_message(data)

            elif data.get("type") == "subscriptions":
                logger.info(f"Subscription confirmed for {self.config.connection_id}")

            elif data.get("type") == "error":
                logger.error(f"WebSocket error in {self.config.connection_id}: {data}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message in {self.config.connection_id}: {e}")
        except Exception as e:
            logger.error(f"Error handling message in {self.config.connection_id}: {e}")

    def _handle_ticker_message(self, data):
        """Process ticker updates"""
        try:
            events = data.get("events", [])
            for event in events:
                tickers = event.get("tickers", [])
                for ticker in tickers:
                    ticker_data = TickerData(
                        product_id=ticker.get("product_id"),
                        price=float(ticker.get("price", 0)),
                        last_size=float(ticker.get("size", 0)),
                        time=ticker.get("time", ""),
                        sequence=int(ticker.get("sequence", 0)),
                        best_bid=float(ticker.get("best_bid", 0)) if ticker.get("best_bid") else None,
                        best_ask=float(ticker.get("best_ask", 0)) if ticker.get("best_ask") else None,
                        connection_id=self.config.connection_id
                    )

                    # Send to callback
                    self.data_callback(ticker_data)

        except Exception as e:
            logger.error(f"Error processing ticker in {self.config.connection_id}: {e}")

    def _on_error(self, ws, error):
        """Handle WebSocket errors"""
        logger.error(f"WebSocket error in {self.config.connection_id}: {error}")
        self.stats.error_count += 1
        self.stats.status = "error"
        self.stats_callback(self.stats)

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection closed"""
        logger.warning(f"Connection {self.config.connection_id} closed: {close_status_code} - {close_msg}")
        self.stats.status = "disconnected"
        self.stats.uptime_start = None
        self.stats_callback(self.stats)

    def _handle_reconnect(self):
        """Handle reconnection with exponential backoff"""
        if not self.should_reconnect or not self.running:
            return

        logger.info(f"Reconnecting {self.config.connection_id} in {self.reconnect_delay} seconds")
        time.sleep(self.reconnect_delay)

        # Exponential backoff
        self.reconnect_delay = min(
            self.reconnect_delay * 2,
            self.config.max_reconnect_delay
        )

    def _monitor_heartbeat(self):
        """Monitor heartbeat and force reconnect if needed"""
        while self.running:
            try:
                time.sleep(10)  # Check every 10 seconds

                if self.stats.status == "connected":
                    time_since_heartbeat = time.time() - self.last_heartbeat

                    if time_since_heartbeat > self.config.heartbeat_timeout:
                        logger.warning(
                            f"Heartbeat timeout in {self.config.connection_id} "
                            f"({time_since_heartbeat:.1f}s), forcing reconnect"
                        )

                        if self.ws:
                            self.ws.close()

            except Exception as e:
                logger.error(f"Heartbeat monitor error in {self.config.connection_id}: {e}")

class MultiWSManager:
    """Main manager for multiple WebSocket connections"""

    def __init__(self, products: List[str], products_per_connection: int = 15):
        self.products = products
        self.products_per_connection = products_per_connection

        # Data storage
        self.latest_prices: Dict[str, TickerData] = {}
        self.price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

        # Connection management
        self.connections: Dict[str, ConnectionManager] = {}
        self.connection_stats: Dict[str, ConnectionStats] = {}

        # Callbacks
        self.data_callbacks: List[Callable] = []
        self.stats_callbacks: List[Callable] = []

        # Monitoring
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None

        self._setup_connections()

    def _setup_connections(self):
        """Create connection configurations"""
        logger.info(f"Setting up connections for {len(self.products)} products")

        # Split products across multiple connections
        connection_configs = []
        for i in range(0, len(self.products), self.products_per_connection):
            batch = self.products[i:i + self.products_per_connection]
            connection_id = f"conn_{i // self.products_per_connection:03d}"

            config = ConnectionConfig(
                connection_id=connection_id,
                products=batch,
                max_products_per_connection=self.products_per_connection
            )
            connection_configs.append(config)

        logger.info(f"Created {len(connection_configs)} connection configurations")

        # Create connection managers
        for config in connection_configs:
            manager = ConnectionManager(
                config=config,
                data_callback=self._handle_ticker_data,
                stats_callback=self._handle_connection_stats
            )
            self.connections[config.connection_id] = manager

    def add_data_callback(self, callback: Callable):
        """Add callback for ticker data updates"""
        self.data_callbacks.append(callback)

    def add_stats_callback(self, callback: Callable):
        """Add callback for connection statistics"""
        self.stats_callbacks.append(callback)

    def start(self):
        """Start all connections"""
        if self.running:
            logger.warning("MultiWSManager already running")
            return

        self.running = True

        logger.info(f"Starting {len(self.connections)} WebSocket connections")

        # Start all connections
        for connection_id, manager in self.connections.items():
            manager.start()
            time.sleep(0.1)  # Small delay between connections

        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_connections, daemon=True)
        self.monitor_thread.start()

        logger.info("All connections started")

    def stop(self):
        """Stop all connections"""
        self.running = False

        logger.info("Stopping all connections")

        # Stop all connections
        for manager in self.connections.values():
            manager.stop()

        logger.info("All connections stopped")

    def _handle_ticker_data(self, ticker_data: TickerData):
        """Handle incoming ticker data"""
        # Store latest price
        self.latest_prices[ticker_data.product_id] = ticker_data

        # Store in history
        self.price_history[ticker_data.product_id].append(ticker_data)

        # Notify callbacks
        for callback in self.data_callbacks:
            try:
                callback(ticker_data)
            except Exception as e:
                logger.error(f"Error in data callback: {e}")

    def _handle_connection_stats(self, stats: ConnectionStats):
        """Handle connection statistics updates"""
        self.connection_stats[stats.connection_id] = stats

        # Notify callbacks
        for callback in self.stats_callbacks:
            try:
                callback(stats)
            except Exception as e:
                logger.error(f"Error in stats callback: {e}")

    def _monitor_connections(self):
        """Monitor connection health and report statistics"""
        while self.running:
            try:
                time.sleep(30)  # Report every 30 seconds

                total_connections = len(self.connections)
                connected_count = sum(1 for stats in self.connection_stats.values()
                                    if stats.status == "connected")
                total_products_monitored = len([p for p, ticker in self.latest_prices.items()
                                              if ticker.received_at > datetime.now() - timedelta(minutes=2)])

                logger.info(
                    f"Connection Health: {connected_count}/{total_connections} connected, "
                    f"{total_products_monitored}/{len(self.products)} products receiving data"
                )

                # Check for stale connections
                for connection_id, stats in self.connection_stats.items():
                    if stats.status == "connected" and stats.last_message:
                        time_since_message = (datetime.now() - stats.last_message).total_seconds()
                        if time_since_message > 120:  # 2 minutes without messages
                            logger.warning(f"Connection {connection_id} appears stale ({time_since_message:.0f}s)")

            except Exception as e:
                logger.error(f"Monitor thread error: {e}")

    def get_latest_price(self, product_id: str) -> Optional[TickerData]:
        """Get latest price for a product"""
        return self.latest_prices.get(product_id)

    def get_all_latest_prices(self) -> Dict[str, TickerData]:
        """Get all latest prices"""
        return self.latest_prices.copy()

    def get_connection_stats(self) -> Dict[str, ConnectionStats]:
        """Get all connection statistics"""
        return self.connection_stats.copy()

    def get_health_summary(self) -> Dict[str, Any]:
        """Get overall system health summary"""
        connected_count = sum(1 for stats in self.connection_stats.values()
                            if stats.status == "connected")
        total_messages = sum(stats.total_messages for stats in self.connection_stats.values())
        total_errors = sum(stats.error_count for stats in self.connection_stats.values())

        active_products = len([p for p, ticker in self.latest_prices.items()
                             if ticker.received_at > datetime.now() - timedelta(minutes=2)])

        return {
            "total_connections": len(self.connections),
            "connected_connections": connected_count,
            "total_products": len(self.products),
            "active_products": active_products,
            "total_messages_received": total_messages,
            "total_errors": total_errors,
            "coverage_percentage": (active_products / len(self.products) * 100) if self.products else 0
        }