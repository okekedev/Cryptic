#!/usr/bin/env python3
"""
Drop Detector Bot - Monitors for price dumps and sends alerts

Detects significant price drops (dumps) in crypto prices and emits alerts
for the dump-trading bot to act on.

Features:
- Monitors all USD pairs for price drops
- Configurable drop threshold (e.g., -4% in 5 minutes)
- Emits spike_alert events to backend for dump-trading bot
- Simple, focused on dump detection only
"""
import os
import json
import time
import logging
import signal
import sqlite3
import socketio
from datetime import datetime, timedelta
from collections import deque
from typing import Dict, Optional

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
PRICE_DROP_THRESHOLD = float(os.getenv("PRICE_DROP_THRESHOLD", "8.0"))  # 8% drop (proven via backtesting)
PRICE_WINDOW_MINUTES = float(os.getenv("PRICE_WINDOW_MINUTES", "5"))  # 5-minute window
DB_PATH = os.getenv("DROP_DB_PATH", "/app/data/drop_detector.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PriceTracker:
    """Track price history for a single symbol"""

    def __init__(self, symbol: str, window_minutes: float):
        self.symbol = symbol
        self.window_seconds = window_minutes * 60
        self.prices = deque()  # (timestamp, price)
        self.last_drop_alert = None

    def add_price(self, price: float, timestamp: float):
        """Add new price and clean old data"""
        self.prices.append((timestamp, price))

        # Remove prices older than window
        cutoff = timestamp - self.window_seconds
        while self.prices and self.prices[0][0] < cutoff:
            self.prices.popleft()

    def check_for_drop(self, threshold_pct: float) -> Optional[Dict]:
        """Check if there's a significant drop in the window"""
        if len(self.prices) < 2:
            return None

        # Get highest and lowest price in window
        prices_only = [p[1] for p in self.prices]
        high_price = max(prices_only)
        current_price = prices_only[-1]

        # Calculate drop percentage
        drop_pct = ((current_price - high_price) / high_price) * 100

        # Check if drop exceeds threshold (drop_pct is negative)
        if drop_pct <= -threshold_pct:
            # Don't send duplicate alerts within 5 minutes
            now = time.time()
            if self.last_drop_alert and (now - self.last_drop_alert) < 300:
                return None

            self.last_drop_alert = now

            return {
                "symbol": self.symbol,
                "spike_type": "dump",
                "event_type": "spike_start",
                "pct_change": drop_pct,
                "old_price": high_price,
                "new_price": current_price,
                "time_span_seconds": self.window_seconds,
                "timestamp": datetime.now().isoformat(),
                "spike_time": datetime.now().isoformat()
            }

        return None


class DropDetectorBot:
    """Drop detector bot - monitors for price dumps"""

    def __init__(self):
        self.trackers: Dict[str, PriceTracker] = {}
        self.sio = None
        self.running = True

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 60)
        logger.info("Drop Detector Bot Initialized")
        logger.info(f"Drop Threshold: -{PRICE_DROP_THRESHOLD}%")
        logger.info(f"Time Window: {PRICE_WINDOW_MINUTES} minutes")
        logger.info(f"Backend: {BACKEND_URL}")
        logger.info("=" * 60)

    def _init_db(self):
        """Initialize database tables"""
        cursor = self.db.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drop_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                spike_type TEXT,
                pct_change REAL,
                old_price REAL,
                new_price REAL,
                time_span_seconds REAL,
                timestamp TEXT,
                spike_time TEXT
            )
        """)

        self.db.commit()
        logger.info(f"Database initialized: {DB_PATH}")

    def get_or_create_tracker(self, symbol: str) -> PriceTracker:
        """Get or create price tracker for symbol"""
        if symbol not in self.trackers:
            self.trackers[symbol] = PriceTracker(symbol, PRICE_WINDOW_MINUTES)
        return self.trackers[symbol]

    def record_drop_alert(self, alert_data: Dict):
        """Record drop alert in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT INTO drop_alerts (symbol, spike_type, pct_change, old_price, new_price,
                                    time_span_seconds, timestamp, spike_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert_data['symbol'],
            alert_data['spike_type'],
            alert_data['pct_change'],
            alert_data['old_price'],
            alert_data['new_price'],
            alert_data['time_span_seconds'],
            alert_data['timestamp'],
            alert_data['spike_time']
        ))
        self.db.commit()

    def connect_websocket(self):
        """Connect to backend Socket.IO for price updates"""
        self.sio = socketio.Client(logger=False, engineio_logger=False)

        @self.sio.event
        def connect():
            logger.info("✅ Connected to backend Socket.IO")

        @self.sio.event
        def disconnect():
            logger.warning("❌ Disconnected from backend")

        @self.sio.on('ticker_update')
        def on_ticker_update(data):
            """Monitor price updates and detect drops"""
            try:
                symbol = data['crypto']
                price = data['price']
                timestamp = time.time()

                # Get tracker for this symbol
                tracker = self.get_or_create_tracker(symbol)

                # Add price to history
                tracker.add_price(price, timestamp)

                # Check for drop
                drop_alert = tracker.check_for_drop(PRICE_DROP_THRESHOLD)

                if drop_alert:
                    logger.info(f"🔻 DROP DETECTED: {symbol} {drop_alert['pct_change']:.2f}% "
                               f"(${drop_alert['old_price']:.6f} → ${drop_alert['new_price']:.6f})")

                    # Record in database
                    self.record_drop_alert(drop_alert)

                    # Emit spike_alert to backend (which broadcasts to dump-trading bot)
                    self.sio.emit('spike_alert', drop_alert)

            except Exception as e:
                logger.error(f"Error processing ticker update: {e}")

        # Connection loop
        while self.running:
            try:
                if not self.sio.connected:
                    self.sio.connect(BACKEND_URL)
                self.sio.wait()
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                if self.sio.connected:
                    self.sio.disconnect()
                time.sleep(5)

    def run(self):
        """Main bot loop"""
        try:
            self.connect_websocket()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Gracefully stop the bot"""
        logger.info("\n🛑 Stopping drop detector bot...")
        self.running = False

        if self.db:
            self.db.close()

        if self.sio:
            self.sio.disconnect()

        logger.info("Drop detector stopped")


def main():
    """Main entry point"""
    bot = DropDetectorBot()

    def signal_handler(sig, frame):
        bot.stop()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
