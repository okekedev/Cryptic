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
        
        # Momentum tracking
        self.momentum_tracking = False
        self.momentum_start_price = 0
        self.momentum_peak_price = 0
        self.momentum_start_time = 0
        self.momentum_peak_time = 0
        self.peak_change = 0

    def add_price(self, price: float, timestamp: float) -> Optional[dict]:
        """Add price to history and check for spike"""
        self.price_history.append((timestamp, price))
        cutoff_time = timestamp - self.window_seconds
        while self.price_history and self.price_history[0][0] < cutoff_time:
            self.price_history.popleft()
        
        if len(self.price_history) < 2:
            return None
        
        # If we're in momentum tracking mode
        if self.momentum_tracking:
            return self._track_momentum(price, timestamp)
        
        # Otherwise check for initial spike
        oldest_time, oldest_price = self.price_history[0]
        newest_time, newest_price = self.price_history[-1]
        
        if oldest_price == 0:
            return None
            
        pct_change = ((newest_price - oldest_price) / oldest_price) * 100
        
        if abs(pct_change) >= self.threshold and (timestamp - self.last_spike_time) > self.cooldown_seconds:
            self.last_spike_time = timestamp
            
            # Start momentum tracking
            self.momentum_tracking = True
            self.momentum_start_price = oldest_price
            self.momentum_peak_price = newest_price
            self.momentum_start_time = timestamp
            self.momentum_peak_time = timestamp
            self.peak_change = pct_change
            
            return {
                "symbol": self.symbol,
                "spike_type": "pump" if pct_change > 0 else "dump",
                "pct_change": pct_change,
                "old_price": oldest_price,
                "new_price": newest_price,
                "time_span_seconds": newest_time - oldest_time,
                "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                "spike_time": datetime.fromtimestamp(timestamp).isoformat(),
                "event_type": "spike_start"
            }
        return None
    
    def _track_momentum(self, current_price: float, timestamp: float) -> Optional[dict]:
        """Track momentum after initial spike"""
        # Calculate current change from start
        current_change = ((current_price - self.momentum_start_price) / self.momentum_start_price) * 100
        
        # Update peak if we're still climbing
        if abs(current_change) > abs(self.peak_change):
            self.momentum_peak_price = current_price
            self.momentum_peak_time = timestamp
            self.peak_change = current_change
        
        # Check if momentum has ended - price dropped 2% below initial spike threshold
        # For a 5% threshold, end tracking when it drops below 3%
        exit_threshold = self.threshold - 2.0
        
        # For pumps: current change falls below exit threshold
        # For dumps: current change rises above negative exit threshold
        momentum_ended = False
        if self.peak_change > 0:  # Pump
            momentum_ended = current_change < exit_threshold
        else:  # Dump
            momentum_ended = current_change > -exit_threshold
        
        if momentum_ended:
            # Momentum has ended
            duration = timestamp - self.momentum_start_time
            
            result = {
                "symbol": self.symbol,
                "spike_type": "pump" if self.peak_change > 0 else "dump",
                "pct_change": self.peak_change,  # Store peak as main change
                "old_price": self.momentum_start_price,
                "new_price": current_price,
                "peak_price": self.momentum_peak_price,
                "peak_change": self.peak_change,
                "final_change": current_change,
                "exit_change": current_change,
                "time_span_seconds": duration,
                "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                "spike_time": datetime.fromtimestamp(self.momentum_start_time).isoformat(),
                "peak_time": datetime.fromtimestamp(self.momentum_peak_time).isoformat(),
                "event_type": "momentum_end",
                "exit_threshold": exit_threshold
            }
            
            # Reset momentum tracking
            self.momentum_tracking = False
            self.momentum_start_price = 0
            self.momentum_peak_price = 0
            self.momentum_start_time = 0
            self.momentum_peak_time = 0
            self.peak_change = 0
            
            return result
            
        return None

class PriceSpikeBot:
    """Main bot that monitors WebSocket feed for price spikes"""
    def __init__(self):
        self.trackers: Dict[str, PriceTracker] = {}
        self.ws = None
        self.running = True
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        self.sio = None  # Initialize sio as None
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
            
            # Create table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    spike_type TEXT,
                    event_type TEXT DEFAULT 'spike',
                    pct_change REAL,
                    old_price REAL,
                    new_price REAL,
                    peak_price REAL,
                    peak_change REAL,
                    final_change REAL,
                    exit_change REAL,
                    time_span_seconds REAL,
                    timestamp TEXT,
                    spike_time TEXT,
                    peak_time TEXT,
                    exit_threshold REAL
                )
            """)
            
            # Check which columns exist
            cursor.execute("PRAGMA table_info(stats)")
            columns = [column[1] for column in cursor.fetchall()]
            
            # Add missing columns if they don't exist
            new_columns = [
                ("event_type", "TEXT DEFAULT 'spike'"),
                ("peak_price", "REAL"),
                ("peak_change", "REAL"),
                ("final_change", "REAL"),
                ("exit_change", "REAL"),
                ("spike_time", "TEXT"),
                ("peak_time", "TEXT"),
                ("exit_threshold", "REAL")
            ]
            
            for column_name, column_type in new_columns:
                if column_name not in columns:
                    cursor.execute(f"ALTER TABLE stats ADD COLUMN {column_name} {column_type}")
                    logger.info(f"Added column {column_name} to stats table")
            
            self.db.commit()
            logger.info("Database schema updated successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def get_stats(self, hours: int) -> list:
        """Retrieve stats for the last N hours"""
        try:
            cursor = self.db.cursor()
            cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
            cursor.execute("""
                SELECT symbol, spike_type, pct_change, timestamp, event_type
                FROM stats
                WHERE timestamp >= ?
            """, (cutoff_time,))
            rows = cursor.fetchall()
            return [{"symbol": row[0], "spike_type": row[1], "pct_change": row[2], "timestamp": row[3], "event_type": row[4] if len(row) > 4 else 'spike'} for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch stats: {e}")
            return []

    def send_alert(self, spike_data: dict):
        """Send spike alert via multiple channels and store in DB"""
        event_type = spike_data.get('event_type', 'spike')
        
        if event_type == 'spike_start':
            alert_msg = (
                f"🚨 PRICE {spike_data['spike_type'].upper()} ALERT 🚨\n"
                f"Symbol: {spike_data['symbol']}\n"
                f"Initial spike: {spike_data['pct_change']:.2f}%\n"
                f"Price: ${spike_data['old_price']:.6f} → ${spike_data['new_price']:.6f}\n"
                f"Time span: {spike_data['time_span_seconds']:.0f}s\n"
                f"🔥 NOW TRACKING MOMENTUM..."
            )
        elif event_type == 'momentum_end':
            alert_msg = (
                f"📊 MOMENTUM ENDED: {spike_data['symbol']}\n"
                f"Peak gain: {spike_data['peak_change']:.2f}%\n"
                f"Exit at: {spike_data['exit_change']:.2f}% (below {spike_data['exit_threshold']:.1f}% threshold)\n"
                f"Peak price: ${spike_data['peak_price']:.6f}\n"
                f"Exit price: ${spike_data['new_price']:.6f}\n"
                f"Duration: {spike_data['time_span_seconds']/60:.1f} minutes"
            )
        else:
            # Default format for backward compatibility
            alert_msg = (
                f"🚨 PRICE {spike_data['spike_type'].upper()} ALERT 🚨\n"
                f"Symbol: {spike_data['symbol']}\n"
                f"Change: {spike_data['pct_change']:.2f}%\n"
                f"Price: ${spike_data['old_price']:.6f} → ${spike_data['new_price']:.6f}\n"
                f"Time span: {spike_data['time_span_seconds']:.0f}s\n"
                f"Time: {spike_data['timestamp']}"
            )
        
        # Console logging
        logger.warning(alert_msg)
        
        # Emit via Socket.IO to all connected clients
        if hasattr(self, 'sio') and self.sio and self.sio.connected:
            try:
                # Emit the spike_alert event that telegram-bot is listening for
                self.sio.emit('spike_alert', spike_data)
                logger.info(f"Alert emitted via Socket.IO: {spike_data['symbol']} {event_type}")
            except Exception as e:
                logger.error(f"Failed to emit Socket.IO event: {e}")
        
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
        
        # Store in database (save all events)
        try:
            cursor = self.db.cursor()
            cursor.execute("""
                INSERT INTO stats (symbol, spike_type, event_type, pct_change, old_price, new_price, 
                                 peak_price, peak_change, final_change, exit_change, time_span_seconds, 
                                 timestamp, spike_time, peak_time, exit_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                spike_data['symbol'],
                spike_data['spike_type'],
                spike_data.get('event_type', 'spike'),
                spike_data['pct_change'],
                spike_data['old_price'],
                spike_data['new_price'],
                spike_data.get('peak_price'),
                spike_data.get('peak_change'),
                spike_data.get('final_change'),
                spike_data.get('exit_change'),
                spike_data['time_span_seconds'],
                spike_data['timestamp'],
                spike_data.get('spike_time'),
                spike_data.get('peak_time'),
                spike_data.get('exit_threshold')
            ))
            self.db.commit()
            logger.info(f"Alert stored in database (event_type: {event_type})")
        except Exception as e:
            logger.error(f"Failed to store in database: {e}")
        
        # Commented out the HTTP POST since backend doesn't have this endpoint
        # try:
        #     requests.post(f"{BACKEND_URL}/spike-alert", json=spike_data, timeout=5)
        # except Exception as e:
        #     logger.debug(f"Failed to send to backend: {e}")

    def connect_websocket(self):
        """Connect to backend Socket.IO WebSocket"""
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        
        @self.sio.event
        def connect():
            logger.info("Connected to backend Socket.IO")
            self.reconnect_delay = 1
            
        @self.sio.event
        def disconnect():
            logger.warning("Disconnected from backend")
            
        @self.sio.on('ticker_update')
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
                self.sio.connect(BACKEND_URL)
                self.sio.wait()
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
            logger.info(f"Last 24h stats: {len(stats)} events")
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