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
from enhanced_price_tracker import EnhancedPriceTracker

# Configuration from environment variables
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
TELEGRAM_SOCKET_URL = os.getenv("TELEGRAM_SOCKET_URL", "http://telegram-bot:8081")
PRICE_SPIKE_THRESHOLD = float(os.getenv("PRICE_SPIKE_THRESHOLD", "5.0"))
PRICE_WINDOW_MINUTES = int(os.getenv("PRICE_WINDOW_MINUTES", "5"))
MOMENTUM_EXIT_MULTIPLIER = float(os.getenv("MOMENTUM_EXIT_MULTIPLIER", "0.6"))  # Exit at 60% of spike threshold
TRACKER_CLEANUP_HOURS = int(os.getenv("TRACKER_CLEANUP_HOURS", "1"))  # Cleanup inactive trackers after 1 hour
MAX_PRICE_MULTIPLIER = float(os.getenv("MAX_PRICE_MULTIPLIER", "10.0"))  # Max 10x price jump (data validation)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PriceTracker:
    """
    Tracks price history and detects spikes for a single crypto

    FUTURE ENHANCEMENT - DUMP RECOVERY TRACKING:
    ============================================
    When a DUMP is detected, track the recovery at 5% intervals to catch bounce opportunities.

    Implementation Strategy:
    1. Detect Dump Event:
       - When spike_type == "dump" and pct_change < -PRICE_SPIKE_THRESHOLD
       - Start dump recovery tracking mode
       - Record dump_bottom_price (lowest price during dump)
       - Record dump_start_price (price before dump)

    2. Track Recovery at 5% Intervals:
       Recovery milestones from dump bottom:
       - +5%:  "Bounce detected - early recovery"
       - +10%: "Strong bounce - continued recovery"
       - +15%: "Significant recovery"
       - +20%: "Major recovery - potential reversal"
       - +25%: "Full reversal momentum"

    3. Alert Logic:
       Send alerts at each 5% milestone with:
       - Current price
       - Recovery percentage from dump bottom
       - Distance from original pre-dump price
       - Volume analysis (if available)
       - Recommendation: "Consider entry" or "Wait for confirmation"

    4. Exit Recovery Tracking When:
       - Price recovers to within 2% of pre-dump price (full recovery)
       - Price drops back down 3% from recovery peak (failed bounce)
       - 30 minutes elapsed without further recovery (stalled)

    5. Configuration (add to environment):
       - DUMP_RECOVERY_ENABLED: bool (default True)
       - DUMP_RECOVERY_INTERVAL: float (default 5.0% - alert every 5%)
       - DUMP_RECOVERY_TIMEOUT_MINUTES: int (default 30)
       - DUMP_RECOVERY_FAILED_RETRACE: float (default 3.0% - exit if drops 3% from peak)

    6. Database Schema Additions:
       Add to spike_alerts table:
       - dump_bottom_price REAL          # Lowest price during dump
       - recovery_peak_price REAL        # Highest price during recovery
       - recovery_peak_pct REAL          # Max recovery percentage reached
       - recovery_alerts_sent INTEGER    # Count of 5% milestone alerts sent
       - recovery_outcome TEXT           # "full_recovery", "failed", "stalled"
       - recovery_duration_seconds REAL  # Time from bottom to outcome

    7. Alert Format Example:
       🟢 RECOVERY ALERT: BTC-USD
       Dump Recovery: +10.5% from bottom
       Bottom: $58,245 → Current: $64,360
       Pre-dump: $65,800 (still -2.2% from origin)
       Status: Strong bounce detected
       Recommendation: Monitor for entry on continued momentum

    8. Trading Strategy Integration:
       - Paper trading bot could enter on +10% recovery (confirmed bounce)
       - Use tight stop loss (3-5% below entry)
       - Target: Return to 95% of pre-dump price
       - Higher risk but potential for 10-20% gains on successful V-shaped recovery

    Rationale:
    - Dumps often overcorrect due to panic selling
    - Strong bounces frequently follow dumps when no fundamental issues
    - 5% intervals provide actionable entry points
    - V-shaped recoveries can produce quick 15-25% gains
    - Failed bounces are identified quickly to minimize risk

    Implementation Location:
    - Add dump_recovery_tracking flag to __init__
    - Modify _track_momentum() to handle dump recovery separately
    - Create _track_dump_recovery() method for recovery logic
    - Emit recovery milestone events to telegram bot
    """
    def __init__(self, symbol: str, window_minutes: int, threshold: float):
        self.symbol = symbol
        self.window_seconds = window_minutes * 60
        self.threshold = threshold
        self.price_history: Deque[Tuple[float, float]] = deque()  # (timestamp, price)
        self.last_spike_time = 0
        self.last_update_time = time.time()  # Track last activity for cleanup
        self.cooldown_seconds = 60  # Avoid spamming alerts
        self.last_price = 0  # Track last price for validation

        # Momentum tracking
        self.momentum_tracking = False
        self.momentum_start_price = 0
        self.momentum_peak_price = 0
        self.momentum_start_time = 0
        self.momentum_peak_time = 0
        self.peak_change = 0

        # TODO: Add dump recovery tracking fields
        # self.dump_recovery_tracking = False
        # self.dump_start_price = 0
        # self.dump_bottom_price = 0
        # self.dump_bottom_time = 0
        # self.recovery_peak_price = 0
        # self.last_recovery_milestone = 0  # Track last 5% milestone alerted

    def add_price(self, price: float, timestamp: float) -> Optional[dict]:
        """Add price to history and check for spike"""
        # Update last activity time
        self.last_update_time = timestamp

        # Validate price
        if price <= 0:
            logger.warning(f"{self.symbol}: Invalid price {price} (must be > 0)")
            return None

        # Check for extreme price jumps (likely bad data)
        if self.last_price > 0 and price > self.last_price * MAX_PRICE_MULTIPLIER:
            logger.warning(f"{self.symbol}: Rejected price {price} (>{MAX_PRICE_MULTIPLIER}x jump from {self.last_price})")
            return None

        self.last_price = price
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

        # ONLY DETECT DUMPS (negative price changes) - ignore pumps
        if pct_change <= -self.threshold and (timestamp - self.last_spike_time) > self.cooldown_seconds:
            self.last_spike_time = timestamp

            # Start momentum tracking
            self.momentum_tracking = True
            self.momentum_start_price = oldest_price
            self.momentum_peak_price = newest_price
            self.momentum_start_time = timestamp
            self.momentum_peak_time = timestamp
            self.peak_change = pct_change

            # TODO: If this is a DUMP, initiate dump recovery tracking
            # if pct_change < 0:  # Dump detected
            #     self.dump_recovery_tracking = True
            #     self.dump_start_price = oldest_price
            #     self.dump_bottom_price = newest_price
            #     self.dump_bottom_time = timestamp
            #     self.last_recovery_milestone = 0
            #     logger.info(f"{self.symbol}: Starting dump recovery tracking from ${newest_price:.6f}")

            return {
                "symbol": self.symbol,
                "spike_type": "dump",
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

        # Check if momentum has ended - price dropped below percentage-based threshold
        # Use MOMENTUM_EXIT_MULTIPLIER (default 0.6) for proportional exit
        # For a 5% threshold, end tracking at 3% (5 * 0.6)
        # For a 10% threshold, end tracking at 6% (10 * 0.6)
        exit_threshold = self.threshold * MOMENTUM_EXIT_MULTIPLIER
        
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

    # TODO: Implement dump recovery tracking method
    # def _track_dump_recovery(self, current_price: float, timestamp: float) -> Optional[dict]:
    #     """
    #     Track recovery from dump at 5% intervals
    #
    #     Returns alert dict when:
    #     - Price crosses 5% recovery milestone
    #     - Full recovery achieved (within 2% of pre-dump price)
    #     - Failed bounce detected (3% retrace from recovery peak)
    #     - Recovery stalls (30 min timeout)
    #     """
    #     # Update dump bottom if price continues falling
    #     if current_price < self.dump_bottom_price:
    #         self.dump_bottom_price = current_price
    #         self.dump_bottom_time = timestamp
    #         return None
    #
    #     # Calculate recovery percentage from bottom
    #     recovery_pct = ((current_price - self.dump_bottom_price) / self.dump_bottom_price) * 100
    #
    #     # Check for 5% milestone crossings
    #     current_milestone = int(recovery_pct / 5) * 5  # Round down to nearest 5%
    #     if current_milestone > self.last_recovery_milestone and current_milestone >= 5:
    #         self.last_recovery_milestone = current_milestone
    #
    #         # Calculate distance from pre-dump price
    #         distance_from_origin = ((current_price - self.dump_start_price) / self.dump_start_price) * 100
    #
    #         return {
    #             "symbol": self.symbol,
    #             "event_type": "dump_recovery_milestone",
    #             "recovery_pct": recovery_pct,
    #             "milestone": current_milestone,
    #             "current_price": current_price,
    #             "dump_bottom": self.dump_bottom_price,
    #             "pre_dump_price": self.dump_start_price,
    #             "distance_from_origin_pct": distance_from_origin,
    #             "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
    #             "recovery_status": self._get_recovery_status(recovery_pct)
    #         }
    #
    #     # Check for full recovery
    #     distance_from_origin_pct = ((current_price - self.dump_start_price) / self.dump_start_price) * 100
    #     if distance_from_origin_pct >= -2.0:  # Within 2% of pre-dump price
    #         result = {
    #             "symbol": self.symbol,
    #             "event_type": "dump_recovery_complete",
    #             "recovery_pct": recovery_pct,
    #             "outcome": "full_recovery",
    #             "timestamp": datetime.fromtimestamp(timestamp).isoformat()
    #         }
    #         self._reset_dump_recovery()
    #         return result
    #
    #     # Check for failed bounce (3% retrace from peak)
    #     if current_price > self.recovery_peak_price:
    #         self.recovery_peak_price = current_price
    #     retrace_from_peak = ((self.recovery_peak_price - current_price) / self.recovery_peak_price) * 100
    #     if retrace_from_peak >= 3.0:
    #         result = {
    #             "symbol": self.symbol,
    #             "event_type": "dump_recovery_failed",
    #             "outcome": "failed_bounce",
    #             "timestamp": datetime.fromtimestamp(timestamp).isoformat()
    #         }
    #         self._reset_dump_recovery()
    #         return result
    #
    #     return None
    #
    # def _get_recovery_status(self, recovery_pct: float) -> str:
    #     """Get human-readable status for recovery percentage"""
    #     if recovery_pct >= 25:
    #         return "Full reversal momentum"
    #     elif recovery_pct >= 20:
    #         return "Major recovery - potential reversal"
    #     elif recovery_pct >= 15:
    #         return "Significant recovery"
    #     elif recovery_pct >= 10:
    #         return "Strong bounce - continued recovery"
    #     elif recovery_pct >= 5:
    #         return "Bounce detected - early recovery"
    #     return "Monitoring"
    #
    # def _reset_dump_recovery(self):
    #     """Reset dump recovery tracking state"""
    #     self.dump_recovery_tracking = False
    #     self.dump_start_price = 0
    #     self.dump_bottom_price = 0
    #     self.dump_bottom_time = 0
    #     self.recovery_peak_price = 0
    #     self.last_recovery_milestone = 0

class PriceSpikeBot:
    """Main bot that monitors WebSocket feed for price spikes"""
    def __init__(self):
        self.trackers: Dict[str, PriceTracker] = {}
        self.ws = None
        self.running = True
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        self.backend_sio = None  # Socket.IO client for backend price data
        self.telegram_sio = None  # Socket.IO client for direct telegram alerts
        self.last_cleanup_time = time.time()  # Track last cleanup
        self.alerts_sent = 0  # Health metrics
        self.db_writes = 0
        self.connection_errors = 0

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

    def cleanup_inactive_trackers(self):
        """Remove trackers that haven't been updated in TRACKER_CLEANUP_HOURS"""
        current_time = time.time()
        cleanup_threshold = TRACKER_CLEANUP_HOURS * 3600  # Convert hours to seconds

        inactive_symbols = [
            symbol for symbol, tracker in self.trackers.items()
            if (current_time - tracker.last_update_time) > cleanup_threshold
        ]

        for symbol in inactive_symbols:
            del self.trackers[symbol]
            logger.info(f"Cleaned up inactive tracker: {symbol}")

        if inactive_symbols:
            logger.info(f"Removed {len(inactive_symbols)} inactive trackers")

    def log_health_status(self):
        """Log health metrics for monitoring"""
        uptime = time.time() - self.last_cleanup_time
        backend_status = "connected" if (self.backend_sio and self.backend_sio.connected) else "disconnected"
        telegram_status = "connected" if (self.telegram_sio and self.telegram_sio.connected) else "disconnected"

        logger.info(f"📊 Health Status: Active trackers: {len(self.trackers)}, "
                   f"Alerts sent: {self.alerts_sent}, DB writes: {self.db_writes}, "
                   f"Backend: {backend_status}, Telegram: {telegram_status}, "
                   f"Connection errors: {self.connection_errors}")

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

        # Emit DIRECTLY to telegram bot via dedicated Socket.IO connection
        if hasattr(self, 'telegram_sio') and self.telegram_sio and self.telegram_sio.connected:
            try:
                self.telegram_sio.emit('spike_alert', spike_data)
                self.alerts_sent += 1
                logger.info(f"⚡ Alert sent directly to Telegram: {spike_data['symbol']} {event_type}")
            except Exception as e:
                self.connection_errors += 1
                logger.error(f"Failed to emit direct alert to Telegram: {e}")

        # Emit to backend for paper trading bot
        if hasattr(self, 'backend_sio') and self.backend_sio and self.backend_sio.connected:
            try:
                self.backend_sio.emit('spike_alert', spike_data)
                logger.info(f"⚡ Alert sent to backend relay: {spike_data['symbol']} {event_type}")
            except Exception as e:
                self.connection_errors += 1
                logger.error(f"Failed to emit to backend relay: {e}")
        
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
        
        # Store in database (save all events) with retry logic
        max_retries = 3
        for attempt in range(max_retries):
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
                self.db_writes += 1
                logger.info(f"Alert stored in database (event_type: {event_type})")
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Database write failed (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                else:
                    logger.error(f"Failed to store in database after {max_retries} attempts: {e}")
        
        # Commented out the HTTP POST since backend doesn't have this endpoint
        # try:
        #     requests.post(f"{BACKEND_URL}/spike-alert", json=spike_data, timeout=5)
        # except Exception as e:
        #     logger.debug(f"Failed to send to backend: {e}")

    def connect_websocket(self):
        """Connect to both backend (for prices) and telegram bot (for alerts)"""
        # Initialize backend Socket.IO client for price data
        self.backend_sio = socketio.Client(logger=False, engineio_logger=False)

        @self.backend_sio.event
        def connect():
            logger.info(f"✅ Connected to backend for price data: {BACKEND_URL}")
            self.reconnect_delay = 1

        @self.backend_sio.event
        def disconnect():
            logger.warning("❌ Disconnected from backend")

        @self.backend_sio.on('ticker_update')
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

                # Periodic cleanup and health logging (every 30 minutes)
                if (timestamp - self.last_cleanup_time) > 1800:  # 30 minutes
                    self.cleanup_inactive_trackers()
                    self.log_health_status()
                    self.last_cleanup_time = timestamp

            except Exception as e:
                logger.error(f"Error processing ticker: {e}")

        # Initialize direct telegram Socket.IO client for alerts
        self.telegram_sio = socketio.Client(logger=False, engineio_logger=False)

        @self.telegram_sio.event
        def connect():
            logger.info(f"⚡ Connected directly to Telegram bot: {TELEGRAM_SOCKET_URL}")
            # Send ping to test connection
            self.telegram_sio.emit('ping', {'timestamp': time.time()})

        @self.telegram_sio.event
        def disconnect():
            logger.warning("❌ Disconnected from Telegram bot")

        @self.telegram_sio.on('connection_ack')
        def on_connection_ack(data):
            logger.info(f"📡 Telegram bot acknowledged: {data.get('message', 'Connected')}")

        @self.telegram_sio.on('alert_received')
        def on_alert_received(data):
            if data.get('status') == 'success':
                logger.debug(f"✓ Alert confirmed by Telegram: {data.get('symbol')}")
            else:
                logger.error(f"Alert error from Telegram: {data.get('message')}")

        @self.telegram_sio.on('pong')
        def on_pong(data):
            logger.info(f"🏓 Pong from Telegram - Connected clients: {data.get('connected_clients', 0)}")

        # Connection loop
        while self.running:
            try:
                # Connect to backend for price data
                if not self.backend_sio.connected:
                    logger.info(f"Connecting to backend: {BACKEND_URL}")
                    self.backend_sio.connect(BACKEND_URL)

                # Connect to telegram for direct alerts
                if not self.telegram_sio.connected:
                    logger.info(f"Connecting directly to Telegram bot: {TELEGRAM_SOCKET_URL}")
                    self.telegram_sio.connect(TELEGRAM_SOCKET_URL)

                # Wait for backend events (main loop)
                self.backend_sio.wait()

            except Exception as e:
                self.connection_errors += 1
                logger.error(f"Connection error (total: {self.connection_errors}): {e}")

                # Exponential backoff with jitter
                time.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

                # Circuit breaker: if too many errors, increase max delay
                if self.connection_errors > 10:
                    self.max_reconnect_delay = 300  # 5 minutes for sustained failures
                    logger.warning("Circuit breaker activated: Increased reconnection delay to 5 minutes")

    def run(self):
        """Main bot loop"""
        logger.info(f"Price Spike Bot Starting...")
        logger.info(f"Threshold: {PRICE_SPIKE_THRESHOLD}%")
        logger.info(f"Window: {PRICE_WINDOW_MINUTES} minutes")
        logger.info(f"Momentum Exit Multiplier: {MOMENTUM_EXIT_MULTIPLIER} ({PRICE_SPIKE_THRESHOLD * MOMENTUM_EXIT_MULTIPLIER:.1f}% exit threshold)")
        logger.info(f"Tracker Cleanup: {TRACKER_CLEANUP_HOURS} hour(s)")
        logger.info(f"Max Price Jump: {MAX_PRICE_MULTIPLIER}x")
        logger.info(f"Backend: {BACKEND_URL}")
        logger.info(f"Telegram Direct: {TELEGRAM_SOCKET_URL}")
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

        # Disconnect Socket.IO clients
        if hasattr(self, 'backend_sio') and self.backend_sio:
            self.backend_sio.disconnect()
            logger.info("Disconnected from backend")

        if hasattr(self, 'telegram_sio') and self.telegram_sio:
            self.telegram_sio.disconnect()
            logger.info("Disconnected from Telegram bot")

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