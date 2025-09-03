#!/usr/bin/env python3
import os
import json
import time
import sqlite3
from collections import deque
from datetime import datetime, timedelta
import requests
from typing import Dict, Deque, Optional, Tuple
import logging
import signal
import socketio

# Configuration from environment variables
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:3000")
PRICE_SPIKE_THRESHOLD = float(os.getenv("PRICE_SPIKE_THRESHOLD", "5.0"))
PRICE_WINDOW_MINUTES = int(os.getenv("PRICE_WINDOW_MINUTES", "5"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PriceTracker:
    """Tracks price history and detects spikes for a single crypto"""
    def __init__(self, symbol: str, window_minutes: int, threshold: float):
        self.symbol = symbol
        self.window_seconds = window_minutes * 60
        self.threshold = threshold
        self.price_history: Deque[Tuple[float, float]] = deque()  # (timestamp, price)
        self.last_spike_time = 0
        self.cooldown_seconds = 60  # Avoid spamming alerts

    def add_price(self, price: float, timestamp: float) -> Optional[dict]:
        """Add price to history and check for spike"""
        self.price_history.append((timestamp, price))
        cutoff_time = timestamp - self.window_seconds
        while self.price_history and self.price_history[0][0] < cutoff_time:
            self.price_history.popleft()
        if len(self.price_history) < 2:
            return None
        oldest_time, oldest_price = self.price_history[0]
        newest_time, newest_price = self.price_history[-1]
        if oldest_price == 0:
            return None
        pct_change = ((newest_price - oldest_price) / oldest_price) * 100
        if abs(pct_change) >= self.threshold and (timestamp - self.last_spike_time) > self.cooldown_seconds:
            self.last_spike_time = timestamp
            return {
                "symbol": self.symbol,
                "spike_type": "pump" if pct_change > 0 else "dump",
                "pct_change": pct_change,
                "old_price": oldest_price,
                "new_price": newest_price,
                "time_span_seconds": newest_time - oldest_time,
                "timestamp": datetime.fromtimestamp(timestamp).isoformat()
            }
        return None

class PriceSpikeBot:
    """Main bot that monitors WebSocket feed for price spikes"""
    def __init__(self):
        self.trackers: Dict[str, PriceTracker] = {}
        self.ws = None
        self.running = True
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        # Initialize SQLite database
        db_path = os.getenv("DB_PATH", "/app/data/spike_alerts.db")
        try:
            logger.info(f"Connecting to database at {db_path}")
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            self.db_path = db_path
            self._init_db()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def _init_db(self):
        """Initialize database tables"""
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    spike_type TEXT,
                    pct_change REAL,
                    old_price REAL,
                    new_price REAL,
                    time_span_seconds REAL,
                    timestamp TEXT
                )
            """)
            self.db.commit()
            logger.info("Stats table created or already exists")
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}")
            raise

    def get_stats(self, hours: int) -> list:
        """Retrieve stats for the last N hours"""
        try:
            cursor = self.db.cursor()
            cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            cursor.execute("""
                SELECT symbol, spike_type, pct_change, timestamp
                FROM stats
                WHERE timestamp >= ?
            """, (cutoff_time,))
            rows = cursor.fetchall()
            return [{"symbol": row[0], "spike_type": row[1], "pct_change": row[2], "timestamp": row[3]} for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch stats: {e}")
            return []

    def send_alert(self, spike_data: dict):
        """Send spike alert via multiple channels and store in DB"""
        alert_msg = (
            f"🚨 PRICE {spike_data['spike_type'].upper()} ALERT 🚨\n"
            f"Symbol: {spike_data['symbol']}\n"
            f"Change: {spike_data['pct_change']:.2f}%\n"
            f"Price: ${spike_data['old_price']:.6f} → ${spike_data['new_price']:.6f}\n"
            f"Time span: {spike_data['time_span_seconds']:.0f}s\n"
            f"Time: {spike_data['timestamp']}"
        )
        if spike_data['spike_type'] == 'pump':
            logger.warning(f"PUMP ALERT: {alert_msg}")
        else:
            logger.warning(f"DUMP ALERT: {alert_msg}")
        if WEBHOOK_URL:
            try:
                if "slack" in WEBHOOK_URL:
                    payload = {"text": alert_msg}
                elif "telegram" in WEBHOOK_URL:
                    payload = {"text": alert_msg}
                else:
                    payload = {"content": alert_msg}
                response = requests.post(WEBHOOK_URL, json=payload, timeout=5)
                if response.status_code == 200:
                    logger.info("Alert sent to webhook successfully")
                else:
                    logger.error(f"Webhook error: {response.status_code}")
            except Exception as e:
                logger.error(f"Failed to send webhook: {e}")
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                INSERT INTO stats (symbol, spike_type, pct_change, old_price, new_price, time_span_seconds, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                spike_data['symbol'],
                spike_data['spike_type'],
                spike_data['pct_change'],
                spike_data['old_price'],
                spike_data['new_price'],
                spike_data['time_span_seconds'],
                spike_data['timestamp']
            ))
            self.db.commit()
            logger.info("Alert stored in database")
        except Exception as e:
            logger.error(f"Failed to store in database: {e}")
        try:
            requests.post(f"{BACKEND_URL}/spike-alert", json=spike_data, timeout=5)
        except Exception as e:
            logger.debug(f"Failed to send to backend: {e}")

    def connect_websocket(self):
        """Connect to backend Socket.IO WebSocket"""
        sio = socketio.Client(logger=False, engineio_logger=False)
        
        @sio.event
        def connect():
            logger.info("Connected to backend Socket.IO")
            self.reconnect_delay = 1
            
        @sio.event
        def disconnect():
            logger.warning("Disconnected from backend")
            
        @sio.on('ticker_update')
        def on_ticker_update(data):
            try:
                symbol = data['crypto']
                price = data['price']
                timestamp = time.time()
                if symbol not in self.trackers:
                    self.trackers[symbol] = PriceTracker(
                        symbol, PRICE_WINDOW_MINUTES, PRICE_SPIKE_THRESHOLD
                    )
                    logger.info(f"Created tracker for {symbol}")
                spike = self.trackers[symbol].add_price(price, timestamp)
                if spike:
                    self.send_alert(spike)
            except Exception as e:
                logger.error(f"Error processing ticker: {e}")
        
        while self.running:
            try:
                sio.connect(BACKEND_URL)
                sio.wait()
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                time.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    def run(self):
        """Main bot loop"""
        logger.info(f"Price Spike Bot Starting...")
        logger.info(f"Threshold: {PRICE_SPIKE_THRESHOLD}%")
        logger.info(f"Window: {PRICE_WINDOW_MINUTES} minutes")
        logger.info(f"Backend: {BACKEND_URL}")
        logger.info(f"Database: {self.db_path}")
        try:
            stats = self.get_stats(24)
            logger.info(f"Last 24h stats: {stats}")
        except Exception as e:
            logger.error(f"Failed to fetch initial stats: {e}")
        self.connect_websocket()

    def stop(self):
        """Gracefully stop the bot"""
        self.running = False
        if hasattr(self, 'db') and self.db:
            self.db.close()
            logger.info("Database connection closed")
        logger.info("Bot stopping...")

def main():
    """Main entry point"""
    try:
        bot = PriceSpikeBot()
        def signal_handler(sig, frame):
            logger.info("Received shutdown signal")
            bot.stop()
            exit(0)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        bot.run()
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        if 'bot' in locals():
            bot.stop()
        exit(1)

if __name__ == "__main__":
    main()