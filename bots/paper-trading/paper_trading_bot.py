#!/usr/bin/env python3
"""
Paper Trading Bot with Sliding Window Profit Strategy

Listens to spike alerts and executes paper trades with a dynamic profit-taking strategy:
- Initial target: 3% profit after fees
- Trailing profit: Adjusts upward if price continues climbing
- Automatic exit when price drops below trailing threshold
"""
import os
import json
import time
import logging
import signal
import sqlite3
import socketio
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000.0"))  # $10,000 starting capital
POSITION_SIZE_PERCENT = float(os.getenv("POSITION_SIZE_PERCENT", "10.0"))  # 10% per trade (1st buy)
MIN_PROFIT_TARGET = float(os.getenv("MIN_PROFIT_TARGET", "3.0"))  # 3% minimum profit
TRAILING_THRESHOLD = float(os.getenv("TRAILING_THRESHOLD", "1.5"))  # Drop 1.5% from peak to exit
MIN_HOLD_TIME_MINUTES = float(os.getenv("MIN_HOLD_TIME_MINUTES", "30.0"))  # Minimum 30 min hold time
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "2.0"))  # 2% stop loss (protective)
BUY_FEE_PERCENT = float(os.getenv("BUY_FEE_PERCENT", "0.6"))  # Coinbase Advanced Trade taker fee
SELL_FEE_PERCENT = float(os.getenv("SELL_FEE_PERCENT", "0.4"))  # Coinbase Advanced Trade maker fee
DB_PATH = os.getenv("DB_PATH", "/app/data/paper_trading.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Phase 1 Enhancements: Dynamic Position Sizing & Volume Detection
MAX_POSITIONS_PER_ASSET = int(os.getenv("MAX_POSITIONS_PER_ASSET", "3"))  # Max 3 buys per asset in 24h
POSITION_SIZE_DECAY = [10.0, 7.0, 5.0]  # 10%, 7%, 5% for 1st, 2nd, 3rd buys
VOLUME_EXHAUSTION_THRESHOLD = float(os.getenv("VOLUME_EXHAUSTION_THRESHOLD", "0.3"))  # 70% volume drop
EMERGENCY_EXIT_PERCENT = float(os.getenv("EMERGENCY_EXIT_PERCENT", "3.0"))  # -3% emergency exit
MIN_HOLD_FOR_VOLUME_EXIT = float(os.getenv("MIN_HOLD_FOR_VOLUME_EXIT", "10.0"))  # 10 min before volume exit

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Position:
    """
    Represents an open paper trading position

    LIVE TRADING EXTENSION:
    =======================
    For real trading with Coinbase Advanced Trade API, extend this dataclass with:

    Additional Required Fields:
    - order_id: str                    # Exchange order ID from market buy
    - fill_id: str                     # Fill ID for tracking execution
    - actual_entry_price: float        # Actual fill price (may differ from spike price)
    - actual_quantity: float           # Actual filled quantity (may be partial)
    - order_status: str                # "open", "filled", "partially_filled", "cancelled"
    - last_sync_timestamp: str         # ISO timestamp of last exchange sync
    - exchange_fees: dict              # Actual fees charged by exchange
    - exit_order_id: str               # Order ID of exit/sell order (if placed)
    - partial_fills: list              # Array of partial fill records
    - reconciled: bool                 # Whether position matches exchange state

    Critical for Reconnection:
    - last_known_price: float          # Price before disconnect (detect gaps)
    - last_update_source: str          # "websocket", "rest_api", "restored_from_db"
    - needs_reconciliation: bool       # Flag for post-reconnect verification
    - stale_threshold_seconds: int     # Max age before requiring fresh exchange data

    All-Time High (ATH) Detection:
    - all_time_high: float             # Historical ATH price from Coinbase API
    - ath_mode_active: bool            # True when price is within ATH_PROXIMITY_PERCENT of ATH
    - ath_tight_stop: float            # Aggressive trailing stop (1% default) when near ATH
    - ath_first_detected_at: str       # ISO timestamp when ATH proximity first detected
    - ath_proximity_percent: float     # Configurable threshold to trigger ATH mode (default 1.0%)
    - ath_tight_stop_percent: float    # Configurable tight stop percentage (default 1.0%)

    Usage Pattern:
    1. On entry: Store order_id immediately after market buy
    2. On fill confirmation: Update actual_entry_price and actual_quantity
    3. Every update: Set last_sync_timestamp and last_known_price
    4. On disconnect: Set needs_reconciliation = True
    5. On reconnect: Fetch order status from REST API, reconcile differences
    6. On exit: Store exit_order_id and monitor fill status

    See Coinbase Advanced Trade API docs for order lifecycle:
    https://docs.cdp.coinbase.com/advanced-trade/docs/rest-api-orders
    """
    symbol: str
    entry_price: float
    entry_time: str
    quantity: float
    cost_basis: float  # Including fees
    min_exit_price: float  # Minimum price to exit (3% + fees)
    peak_price: float  # Highest price seen
    trailing_exit_price: float  # Current trailing stop
    stop_loss_price: float  # Hard stop loss (-2%)
    stop_loss_order_active: bool = True  # Simulates Coinbase stop-loss order
    status: str = "open"  # open, closed

    def update_peak(self, current_price: float) -> bool:
        """Update peak and trailing exit. Returns True if peak was updated."""
        if current_price > self.peak_price:
            self.peak_price = current_price
            # Trailing exit: drop TRAILING_THRESHOLD% from peak
            self.trailing_exit_price = self.peak_price * (1 - TRAILING_THRESHOLD / 100)
            # Never let trailing exit drop below minimum exit price
            self.trailing_exit_price = max(self.trailing_exit_price, self.min_exit_price)
            return True
        return False

    def should_exit(self, current_price: float, current_volume: float = 0, avg_volume: float = 0) -> tuple[bool, str]:
        """
        Check if position should be exited. Returns (should_exit, reason)

        FUTURE ENHANCEMENT - ALL-TIME HIGH (ATH) DETECTION:
        ====================================================
        When a coin reaches its all-time high, use aggressive exit strategy:

        Implementation Requirements:
        1. Fetch ATH from Coinbase REST API on position entry:
           GET /api/v3/brokerage/market/products/{product_id}/candles
           - Use 1-day candles, max=1000 (approx 3 years of data)
           - Extract highest high from all candles
           - Store as position.all_time_high field

        2. Monitor for ATH breakthrough during position tracking:
           if current_price >= position.all_time_high * 0.99:  # Within 1% of ATH
               # Switch to aggressive exit mode
               position.ath_mode_active = True
               position.ath_tight_stop = current_price * 0.99  # 1% trailing stop

        3. Aggressive exit logic when at/near ATH:
           if position.ath_mode_active:
               if current_price < position.ath_tight_stop:
                   return True, f"ATH tight stop hit (secured gains at near-ATH levels)"
               # Update tight stop as price climbs
               position.ath_tight_stop = max(position.ath_tight_stop, current_price * 0.99)

        4. Configuration (add to environment variables):
           - ATH_DETECTION_ENABLED: bool (default True)
           - ATH_TIGHT_STOP_PERCENT: float (default 1.0%)
           - ATH_PROXIMITY_PERCENT: float (default 1.0% - trigger when within 1% of ATH)

        5. Database schema additions for persistence:
           - all_time_high REAL
           - ath_mode_active INTEGER (boolean)
           - ath_tight_stop REAL
           - ath_first_detected_at TEXT (timestamp)

        Rationale:
        - ATH levels often face strong resistance and rejection
        - Quick 1% trailing stop captures maximum profit before potential reversal
        - Historical data shows many coins retrace 10-30% after hitting new ATH
        - Better to secure 99% of ATH gains than risk larger drawdown

        Example:
        - Coin ATH: $100
        - Current price: $99.50 (within 1% of ATH)
        - Trigger ATH mode, set tight stop at $98.50 (1% trailing)
        - If price hits $101 (new ATH), update stop to $99.99
        - Exit immediately if price drops below trailing stop

        API Reference:
        https://docs.cdp.coinbase.com/advanced-trade/reference/retailbrokerageapi_getcandles
        """
        # Calculate time held
        entry_time = datetime.fromisoformat(self.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # Calculate current P&L percentage
        current_value = current_price * self.quantity
        pnl_percent = ((current_value - self.cost_basis) / self.cost_basis) * 100

        # TODO: Add ATH detection logic here (see documentation above)
        # if hasattr(self, 'ath_mode_active') and self.ath_mode_active:
        #     if current_price < self.ath_tight_stop:
        #         return True, f"ATH tight stop hit at ${current_price:.6f}"

        # STOP LOSS ORDER CHECK: Simulates Coinbase stop-loss order at -2%
        # This would execute automatically on exchange regardless of bot status
        if self.stop_loss_order_active and current_price <= self.stop_loss_price:
            return True, f"Stop-loss order triggered at ${current_price:.6f} ({pnl_percent:.2f}%)"

        # EMERGENCY DUMP EXIT: Override min hold if losing 3%+ AND volume collapsed
        if pnl_percent <= -EMERGENCY_EXIT_PERCENT and avg_volume > 0:
            volume_exhausted = current_volume < (avg_volume * VOLUME_EXHAUSTION_THRESHOLD)
            if volume_exhausted:
                return True, f"Emergency dump exit ({pnl_percent:.2f}%, volume collapsed)"

        # VOLUME EXHAUSTION EXIT: Early profit-taking if volume dies
        if pnl_percent > MIN_PROFIT_TARGET and avg_volume > 0 and time_held_minutes >= MIN_HOLD_FOR_VOLUME_EXIT:
            volume_exhausted = current_volume < (avg_volume * VOLUME_EXHAUSTION_THRESHOLD)
            if volume_exhausted:
                return True, f"Volume exhaustion - profit secured early ({pnl_percent:.2f}%)"

        # PROFIT TARGET MET: Cancel stop-loss order and use trailing stop
        if current_price >= self.min_exit_price:
            # Cancel the stop-loss order when profit target reached (simulates real behavior)
            if self.stop_loss_order_active:
                self.stop_loss_order_active = False
                logger.info(f"📤 Simulated: Stop-loss order CANCELLED for {self.symbol} (profit target ${self.min_exit_price:.6f} reached)")

            if current_price <= self.trailing_exit_price:
                return True, f"Trailing stop hit (profit secured)"

        # MINIMUM HOLD TIME: Don't exit before 30 minutes unless stop loss or emergency
        if time_held_minutes < MIN_HOLD_TIME_MINUTES:
            return False, ""

        # AFTER 30 MIN: Exit if below trailing stop OR at any profit
        if current_price <= self.trailing_exit_price or pnl_percent > 0:
            return True, f"Min hold time reached ({time_held_minutes:.1f} min)"

        return False, ""

    def calculate_pnl(self, exit_price: float) -> Dict:
        """Calculate profit/loss for this position"""
        gross_proceeds = self.quantity * exit_price
        sell_fee = gross_proceeds * (SELL_FEE_PERCENT / 100)
        net_proceeds = gross_proceeds - sell_fee

        pnl = net_proceeds - self.cost_basis
        pnl_percent = (pnl / self.cost_basis) * 100

        return {
            "gross_proceeds": gross_proceeds,
            "sell_fee": sell_fee,
            "net_proceeds": net_proceeds,
            "pnl": pnl,
            "pnl_percent": pnl_percent
        }


class PaperTradingBot:
    """Paper trading bot that trades on spike alerts"""

    def __init__(self):
        self.capital = INITIAL_CAPITAL
        self.positions: Dict[str, Position] = {}  # symbol -> Position
        self.buy_history: Dict[str, list] = {}  # symbol -> [timestamps]
        self.sio = None
        self.running = True

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 60)
        logger.info("Paper Trading Bot Initialized")
        logger.info(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"Position Size: {POSITION_SIZE_PERCENT}% per trade (1st buy)")
        logger.info(f"Position Size Decay: {POSITION_SIZE_DECAY[0]}% → {POSITION_SIZE_DECAY[1]}% → {POSITION_SIZE_DECAY[2]}%")
        logger.info(f"Max Positions Per Asset: {MAX_POSITIONS_PER_ASSET} in 24h")
        logger.info(f"Min Profit Target: {MIN_PROFIT_TARGET}%")
        logger.info(f"Trailing Threshold: {TRAILING_THRESHOLD}%")
        logger.info(f"Min Hold Time: {MIN_HOLD_TIME_MINUTES} minutes")
        logger.info(f"Stop Loss: {STOP_LOSS_PERCENT}%")
        logger.info(f"Volume Exhaustion Threshold: {VOLUME_EXHAUSTION_THRESHOLD * 100}% of avg")
        logger.info(f"Buy Fee: {BUY_FEE_PERCENT}%")
        logger.info(f"Sell Fee: {SELL_FEE_PERCENT}%")
        logger.info("=" * 60)

    def _init_db(self):
        """Initialize database tables"""
        cursor = self.db.cursor()

        # Trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                gross_proceeds REAL,
                net_proceeds REAL,
                buy_fee REAL NOT NULL,
                sell_fee REAL,
                pnl REAL,
                pnl_percent REAL,
                peak_price REAL,
                status TEXT NOT NULL,
                spike_pct_change REAL,
                reason TEXT
            )
        """)

        # Portfolio value snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                open_positions INTEGER NOT NULL,
                total_trades INTEGER NOT NULL,
                winning_trades INTEGER NOT NULL,
                losing_trades INTEGER NOT NULL
            )
        """)

        # Open positions table (for persistence across restarts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                min_exit_price REAL NOT NULL,
                peak_price REAL NOT NULL,
                trailing_exit_price REAL NOT NULL,
                status TEXT NOT NULL,
                spike_pct_change REAL
                -- LIVE TRADING: Add these columns when implementing real trading:
                -- order_id TEXT                      # Exchange order ID
                -- fill_id TEXT                       # Fill ID from exchange
                -- actual_entry_price REAL            # Actual fill price
                -- actual_quantity REAL               # Actual filled quantity
                -- order_status TEXT                  # Order status from exchange
                -- last_sync_timestamp TEXT           # Last exchange sync time
                -- exchange_fees_json TEXT            # JSON of actual fees charged
                -- exit_order_id TEXT                 # Exit order ID
                -- partial_fills_json TEXT            # JSON array of partial fills
                -- reconciled INTEGER DEFAULT 0       # Boolean flag
                -- last_known_price REAL              # Price before disconnect
                -- last_update_source TEXT            # Source of last update
                -- needs_reconciliation INTEGER DEFAULT 0  # Boolean flag
                -- all_time_high REAL                 # Historical ATH from API
                -- ath_mode_active INTEGER DEFAULT 0  # ATH proximity mode active
                -- ath_tight_stop REAL                # Tight trailing stop when near ATH
                -- ath_first_detected_at TEXT         # When ATH proximity triggered
            )
        """)

        # Buy history table (Phase 1: Dynamic position sizing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS buy_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                buy_timestamp TEXT NOT NULL,
                position_size_percent REAL NOT NULL,
                buy_count INTEGER NOT NULL
            )
        """)

        self.db.commit()
        logger.info(f"Database initialized: {DB_PATH}")

        # Restore buy history and open positions from database
        self._restore_buy_history()
        self._restore_positions()

    def _restore_buy_history(self):
        """Restore buy history from database (last 24 hours)"""
        cursor = self.db.cursor()
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        cursor.execute("""
            SELECT symbol, buy_timestamp
            FROM buy_history
            WHERE buy_timestamp > ?
            ORDER BY buy_timestamp ASC
        """, (cutoff,))
        rows = cursor.fetchall()

        for symbol, timestamp in rows:
            if symbol not in self.buy_history:
                self.buy_history[symbol] = []
            self.buy_history[symbol].append(datetime.fromisoformat(timestamp))

        if rows:
            logger.info(f"📊 Restored buy history: {len(rows)} buys across {len(self.buy_history)} asset(s)")
            for symbol, timestamps in self.buy_history.items():
                logger.info(f"   {symbol}: {len(timestamps)} buy(s) in last 24h")

        # Clean up old buy history (>24 hours)
        cursor.execute("DELETE FROM buy_history WHERE buy_timestamp <= ?", (cutoff,))
        self.db.commit()

    def _restore_positions(self):
        """Restore open positions from database after restart"""
        cursor = self.db.cursor()
        cursor.execute("SELECT * FROM open_positions WHERE status = 'open'")
        rows = cursor.fetchall()

        if rows:
            logger.info("=" * 60)
            logger.info(f"🔄 Restoring {len(rows)} open position(s) from database")
            logger.info("=" * 60)

        for row in rows:
            symbol, entry_price, entry_time, quantity, cost_basis, min_exit_price, peak_price, trailing_exit_price, status, spike_pct = row

            # Calculate stop loss price if not stored (for backward compatibility)
            stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENT / 100)

            position = Position(
                symbol=symbol,
                entry_price=entry_price,
                entry_time=entry_time,
                quantity=quantity,
                cost_basis=cost_basis,
                min_exit_price=min_exit_price,
                peak_price=peak_price,
                trailing_exit_price=trailing_exit_price,
                stop_loss_price=stop_loss_price,
                stop_loss_order_active=True,  # Assume order still active on restore
                status=status
            )

            self.positions[symbol] = position
            self.capital -= cost_basis

            # Calculate time held
            entry_dt = datetime.fromisoformat(entry_time)
            time_held = (datetime.now() - entry_dt).total_seconds() / 60

            logger.info(f"✅ Restored: {symbol}")
            logger.info(f"   Entry: ${entry_price:.6f} at {entry_dt.strftime('%H:%M:%S')}")
            logger.info(f"   Peak: ${peak_price:.6f}, Trailing Exit: ${trailing_exit_price:.6f}")
            logger.info(f"   Time Held: {time_held:.1f} minutes")
            logger.info(f"   Cost Basis: ${cost_basis:.2f}")

        if rows:
            logger.info("=" * 60)
            logger.info(f"Current Capital: ${self.capital:.2f}")
            logger.info("=" * 60)

    def _persist_position(self, position: Position, spike_pct: float = 0.0):
        """Save position to database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO open_positions
            (symbol, entry_price, entry_time, quantity, cost_basis, min_exit_price,
             peak_price, trailing_exit_price, status, spike_pct_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.symbol,
            position.entry_price,
            position.entry_time,
            position.quantity,
            position.cost_basis,
            position.min_exit_price,
            position.peak_price,
            position.trailing_exit_price,
            position.status,
            spike_pct
        ))
        self.db.commit()

    def _remove_persisted_position(self, symbol: str):
        """Remove position from database when closed"""
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM open_positions WHERE symbol = ?", (symbol,))
        self.db.commit()

    def _get_position_size_for_asset(self, symbol: str) -> tuple[float, int]:
        """
        Get dynamic position size based on buy count in last 24 hours.
        Returns (position_size_percent, buy_count)
        """
        # Clean up old buy history (>24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        if symbol in self.buy_history:
            self.buy_history[symbol] = [t for t in self.buy_history[symbol] if t > cutoff]
            if not self.buy_history[symbol]:
                del self.buy_history[symbol]

        # Count recent buys for this asset
        buy_count = len(self.buy_history.get(symbol, []))

        # Check if we've hit max positions for this asset
        if buy_count >= MAX_POSITIONS_PER_ASSET:
            logger.warning(f"⛔ {symbol}: Max {MAX_POSITIONS_PER_ASSET} positions reached in 24h - skipping")
            return 0.0, buy_count

        # Get tiered position size
        position_size = POSITION_SIZE_DECAY[buy_count]

        logger.info(f"📊 {symbol}: Buy #{buy_count + 1} - using {position_size}% position size")

        return position_size, buy_count

    def _record_buy_in_history(self, symbol: str, position_size: float, buy_count: int):
        """Record buy in history (both in-memory and database)"""
        now = datetime.now()

        # In-memory tracking
        if symbol not in self.buy_history:
            self.buy_history[symbol] = []
        self.buy_history[symbol].append(now)

        # Database persistence
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT INTO buy_history (symbol, buy_timestamp, position_size_percent, buy_count)
            VALUES (?, ?, ?, ?)
        """, (symbol, now.isoformat(), position_size, buy_count + 1))
        self.db.commit()

    def open_position(self, symbol: str, entry_price: float, spike_pct: float):
        """Open a new paper trading position with dynamic sizing"""
        # Check if we already have a position in this symbol
        if symbol in self.positions:
            logger.info(f"⏭️  Already have position in {symbol}, skipping")
            return

        # Get dynamic position size based on buy count
        position_size_percent, buy_count = self._get_position_size_for_asset(symbol)
        if position_size_percent == 0:
            return  # Max positions reached for this asset

        # Calculate position value
        position_value = self.capital * (position_size_percent / 100)

        if position_value > self.capital:
            logger.warning(f"⚠️  Insufficient capital for {symbol}")
            return

        # Calculate quantity and fees
        quantity = position_value / entry_price
        buy_fee = position_value * (BUY_FEE_PERCENT / 100)
        cost_basis = position_value + buy_fee

        # Calculate minimum exit price (3% profit + fees)
        # Need to cover: cost_basis + 3% profit + sell fees
        # net_proceeds = exit_price * quantity * (1 - sell_fee_pct)
        # Solve for exit_price where net_proceeds = cost_basis * 1.03
        target_proceeds = cost_basis * (1 + MIN_PROFIT_TARGET / 100)
        min_exit_price = target_proceeds / (quantity * (1 - SELL_FEE_PERCENT / 100))

        # Initial peak is entry price
        peak_price = entry_price
        trailing_exit_price = max(min_exit_price, peak_price * (1 - TRAILING_THRESHOLD / 100))

        # Calculate stop loss price (-2% from entry)
        stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENT / 100)

        # Create position
        position = Position(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=datetime.now().isoformat(),
            quantity=quantity,
            cost_basis=cost_basis,
            min_exit_price=min_exit_price,
            peak_price=peak_price,
            trailing_exit_price=trailing_exit_price,
            stop_loss_price=stop_loss_price,
            stop_loss_order_active=True,  # Simulates Coinbase stop-loss order
            status="open"
        )

        # Log the simulated stop-loss order placement
        logger.info(f"📥 Simulated: Stop-loss order PLACED for {symbol} at ${stop_loss_price:.6f} (-{STOP_LOSS_PERCENT}%)")

        self.positions[symbol] = position
        self.capital -= cost_basis

        logger.info("=" * 60)
        logger.info(f"🟢 OPENED POSITION: {symbol}")
        logger.info(f"   Position Size: {position_size_percent}% (Buy #{buy_count + 1})")
        logger.info(f"   Entry Price: ${entry_price:.6f}")
        logger.info(f"   Quantity: {quantity:.4f}")
        logger.info(f"   Cost Basis: ${cost_basis:.2f} (including ${buy_fee:.2f} fee)")
        logger.info(f"   Min Exit Price: ${min_exit_price:.6f} ({MIN_PROFIT_TARGET}% profit)")
        logger.info(f"   Trailing Exit: ${trailing_exit_price:.6f}")
        logger.info(f"   Remaining Capital: ${self.capital:.2f}")
        logger.info(f"   Spike %: {spike_pct:.2f}%")
        logger.info("=" * 60)

        # Record buy in history
        self._record_buy_in_history(symbol, position_size_percent, buy_count)

        # Persist to database
        self._persist_position(position, spike_pct)

        # Record in database
        self._record_trade_entry(position, spike_pct)

    def update_position(self, symbol: str, current_price: float, current_volume: float = 0, avg_volume: float = 0):
        """Update position with new price data and volume"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Update peak and check if we should exit
        peak_updated = position.update_peak(current_price)

        if peak_updated:
            unrealized = position.calculate_pnl(current_price)
            volume_status = ""
            if avg_volume > 0:
                volume_pct = (current_volume / avg_volume) * 100
                volume_status = f", Volume: {volume_pct:.0f}% of avg"
            logger.info(f"🔼 {symbol}: New peak ${current_price:.6f}, "
                       f"Unrealized P&L: {unrealized['pnl_percent']:+.2f}%{volume_status}, "
                       f"Trailing exit now at ${position.trailing_exit_price:.6f}")
            # Update persisted position with new peak
            self._persist_position(position)

        # Check if we should exit (with volume data)
        should_exit, exit_reason = position.should_exit(current_price, current_volume, avg_volume)
        if should_exit:
            self.close_position(symbol, current_price, exit_reason)

    def close_position(self, symbol: str, exit_price: float, reason: str):
        """Close an open position"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]
        pnl_data = position.calculate_pnl(exit_price)

        # Update capital
        self.capital += pnl_data['net_proceeds']

        # Calculate holding time
        entry_time = datetime.fromisoformat(position.entry_time)
        exit_time = datetime.now()
        holding_seconds = (exit_time - entry_time).total_seconds()

        logger.info("=" * 60)
        logger.info(f"🔴 CLOSED POSITION: {symbol}")
        logger.info(f"   Entry: ${position.entry_price:.6f} -> Exit: ${exit_price:.6f}")
        logger.info(f"   Peak Price: ${position.peak_price:.6f}")
        logger.info(f"   Net Proceeds: ${pnl_data['net_proceeds']:.2f}")
        logger.info(f"   P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)")
        logger.info(f"   Holding Time: {holding_seconds/60:.1f} minutes")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   New Capital: ${self.capital:.2f}")
        logger.info("=" * 60)

        # Record in database
        self._record_trade_exit(position, exit_price, pnl_data, reason)

        # Remove from persisted positions
        self._remove_persisted_position(symbol)

        # Remove position
        del self.positions[symbol]

    def _record_trade_entry(self, position: Position, spike_pct: float):
        """Record trade entry in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, entry_time, entry_price, quantity, cost_basis,
                              buy_fee, peak_price, status, spike_pct_change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.symbol,
            position.entry_time,
            position.entry_price,
            position.quantity,
            position.cost_basis,
            position.cost_basis - (position.quantity * position.entry_price),
            position.peak_price,
            'open',
            spike_pct
        ))
        self.db.commit()

    def _record_trade_exit(self, position: Position, exit_price: float, pnl_data: Dict, reason: str):
        """Record trade exit in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            UPDATE trades
            SET exit_time = ?, exit_price = ?, gross_proceeds = ?, net_proceeds = ?,
                sell_fee = ?, pnl = ?, pnl_percent = ?, peak_price = ?,
                status = ?, reason = ?
            WHERE symbol = ? AND status = 'open'
        """, (
            datetime.now().isoformat(),
            exit_price,
            pnl_data['gross_proceeds'],
            pnl_data['net_proceeds'],
            pnl_data['sell_fee'],
            pnl_data['pnl'],
            pnl_data['pnl_percent'],
            position.peak_price,
            'closed',
            reason,
            position.symbol
        ))
        self.db.commit()

    def get_statistics(self) -> Dict:
        """Get trading statistics"""
        cursor = self.db.cursor()

        # Total trades
        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed'")
        total_trades = cursor.fetchone()[0]

        if total_trades == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl": 0,
                "best_trade": 0,
                "worst_trade": 0,
                "current_capital": self.capital,
                "total_return": 0
            }

        # Win/loss stats
        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed' AND pnl > 0")
        winning_trades = cursor.fetchone()[0]
        losing_trades = total_trades - winning_trades

        # P&L stats
        cursor.execute("SELECT SUM(pnl), AVG(pnl), MAX(pnl), MIN(pnl) FROM trades WHERE status = 'closed'")
        total_pnl, avg_pnl, best_trade, worst_trade = cursor.fetchone()

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": total_pnl or 0,
            "avg_pnl": avg_pnl or 0,
            "best_trade": best_trade or 0,
            "worst_trade": worst_trade or 0,
            "current_capital": self.capital,
            "total_return": ((self.capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100)
        }

    def connect_websocket(self):
        """
        Connect to backend Socket.IO for spike alerts and price updates

        RECONNECTION STRATEGY FOR LIVE TRADING:
        ========================================
        This paper trading bot uses SQLite persistence to survive restarts.
        For LIVE TRADING with real API connections, implement these critical features:

        1. AUTOMATIC RECONNECTION (Coinbase Advanced Trade WebSocket):
           - Use exponential backoff: 1s, 2s, 4s, 8s, up to 60s max
           - Implement heartbeat/ping-pong to detect stale connections
           - Set connection timeout and auto-reconnect on timeout
           - Reference: https://docs.cdp.coinbase.com/advanced-trade/docs/ws-overview

        2. POSITION STATE RECOVERY ON RECONNECT:
           - On reconnect, immediately call REST API to fetch:
             a) Current account balances (verify available capital)
             b) All open orders (check if any fills occurred during disconnect)
             c) Recent fills (catch any trades that executed while offline)
           - Cross-reference REST data with SQLite persisted positions
           - Reconcile any discrepancies (fills, partial fills, cancellations)

        3. SQLITE PERSISTENCE REQUIREMENTS FOR LIVE TRADING:
           Current implementation stores:
           ✅ Position entry details (price, quantity, cost basis)
           ✅ Peak tracking (peak_price, trailing_exit_price)
           ✅ Entry time and status

           Additional fields needed for live trading:
           - order_id: Exchange order ID for tracking fills
           - last_sync_time: Timestamp of last successful data sync
           - partial_fills: Array of partial fill records
           - exchange_status: Current order status from exchange
           - last_known_price: Price at last update (for gap detection)

        4. DISCONNECT DETECTION:
           - Monitor WebSocket connection health with periodic pings
           - Set reasonable timeout (30-60 seconds) for ping/pong
           - Log disconnect reason (network, exchange maintenance, etc.)
           - Immediately trigger reconnection + state recovery flow

        5. RACE CONDITION PREVENTION:
           - Use optimistic locking for position updates
           - Timestamp all database writes
           - On reconnect, fetch exchange state BEFORE resuming trading
           - Implement "recovery mode" that pauses new trades until state verified

        6. CIRCUIT BREAKER:
           - If reconnection fails repeatedly (e.g., 5 times), pause trading
           - Send critical alert (Telegram, email, SMS)
           - Require manual intervention to resume

        Implementation: For production, wrap REST API calls in try-catch with retries,
        implement a StateReconciliation class, and add comprehensive logging for auditing.
        """
        self.sio = socketio.Client(logger=False, engineio_logger=False)

        @self.sio.event
        def connect():
            logger.info("✅ Connected to backend Socket.IO")

        @self.sio.event
        def disconnect():
            logger.warning("❌ Disconnected from backend")
            # TODO: For live trading, implement immediate reconnection logic here
            # See RECONNECTION STRATEGY comment above for full implementation details

        @self.sio.on('spike_alert')
        def on_spike_alert(data):
            try:
                if data.get('event_type') == 'spike_start' and data.get('spike_type') == 'pump':
                    symbol = data['symbol']
                    entry_price = data['new_price']
                    spike_pct = data['pct_change']

                    logger.info(f"📢 Spike alert received: {symbol} +{spike_pct:.2f}%")
                    self.open_position(symbol, entry_price, spike_pct)
            except Exception as e:
                logger.error(f"Error processing spike alert: {e}")

        @self.sio.on('ticker_update')
        def on_ticker_update(data):
            try:
                symbol = data['crypto']
                price = data['price']

                # Extract volume data if available (Phase 1 enhancement)
                current_volume = data.get('volume_24h', 0)
                avg_volume = data.get('avg_volume', 0)

                # Update any open positions (with volume data)
                if symbol in self.positions:
                    self.update_position(symbol, price, current_volume, avg_volume)
            except Exception as e:
                logger.error(f"Error processing ticker update: {e}")

        # Connect and maintain connection
        while self.running:
            try:
                self.sio.connect(BACKEND_URL)
                self.sio.wait()
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                time.sleep(5)

    def print_summary(self):
        """Print trading summary"""
        stats = self.get_statistics()

        logger.info("\n" + "=" * 60)
        logger.info("PAPER TRADING SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"Current Capital: ${stats['current_capital']:,.2f}")
        logger.info(f"Total Return: {stats['total_return']:+.2f}%")
        logger.info(f"Total P&L: ${stats['total_pnl']:+,.2f}")
        logger.info("-" * 60)
        logger.info(f"Total Trades: {stats['total_trades']}")
        logger.info(f"Winning Trades: {stats['winning_trades']}")
        logger.info(f"Losing Trades: {stats['losing_trades']}")
        logger.info(f"Win Rate: {stats['win_rate']:.1f}%")
        logger.info(f"Avg P&L per Trade: ${stats['avg_pnl']:+,.2f}")
        logger.info(f"Best Trade: ${stats['best_trade']:+,.2f}")
        logger.info(f"Worst Trade: ${stats['worst_trade']:+,.2f}")
        logger.info("-" * 60)
        logger.info(f"Open Positions: {len(self.positions)}")
        logger.info("=" * 60 + "\n")

    def run(self):
        """Main bot loop"""
        try:
            self.connect_websocket()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Gracefully stop the bot"""
        logger.info("\n🛑 Stopping paper trading bot...")
        self.running = False

        # Close all open positions
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            # Use entry price as exit for forced close
            self.close_position(symbol, position.entry_price, "Bot shutdown")

        self.print_summary()

        if self.db:
            self.db.close()

        if self.sio:
            self.sio.disconnect()


def main():
    """Main entry point"""
    bot = PaperTradingBot()

    def signal_handler(sig, frame):
        bot.stop()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.run()


if __name__ == "__main__":
    main()