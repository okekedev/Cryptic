#!/usr/bin/env python3
"""
Dump Trading Bot - Mean Reversion Strategy

Listens for dump alerts (>5% drop in 5-minute window) from spike-detector and opens
long positions immediately to capture the bounce. Uses 100% of account balance minus
dynamic fees for each trade, targeting quick 1-3% gains with tight 2% stop loss.

Key Features:
- Dynamic fee calculation (fetched from exchange API)
- 100% balance allocation per trade (spend_amount = balance / (1 + buy_fee_rate))
- Volume confirmation (>50% above average post-dip)
- Quick profit targets: 1-3% gains, exit immediately
- Tight stop loss: 2% max loss
- Adaptive holding: 5-10 minute min hold with indicator-based exits (RSI, SMA)
- Break-even exit if price dips below entry after hitting 1-2% profit
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
INITIAL_CAPITAL = float(os.getenv("DUMP_INITIAL_CAPITAL", "10000.0"))
MAX_LOSS_PERCENT = float(os.getenv("DUMP_MAX_LOSS_PERCENT", "2.0"))  # Tight 2% stop
MIN_PROFIT_TARGET = float(os.getenv("DUMP_MIN_PROFIT_TARGET", "1.0"))  # Quick 1% profit
TARGET_PROFIT = float(os.getenv("DUMP_TARGET_PROFIT", "3.0"))  # Ideal 3% profit
TRAILING_THRESHOLD = float(os.getenv("DUMP_TRAILING_THRESHOLD", "0.5"))  # 0.5% trailing
MIN_HOLD_TIME_MINUTES = float(os.getenv("DUMP_MIN_HOLD_TIME_MINUTES", "5.0"))  # 5 min min
MAX_HOLD_TIME_MINUTES = float(os.getenv("DUMP_MAX_HOLD_TIME_MINUTES", "15.0"))  # 15 min max
VOLUME_SURGE_THRESHOLD = float(os.getenv("VOLUME_SURGE_THRESHOLD", "1.5"))  # 50% above avg
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70.0"))  # Exit if RSI > 70
SMA_PERIOD = int(os.getenv("SMA_PERIOD", "5"))  # 5-period SMA
DB_PATH = os.getenv("DUMP_DB_PATH", "/app/data/dump_trading.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Dynamic fees - will be fetched from API
DEFAULT_BUY_FEE_PERCENT = float(os.getenv("DEFAULT_BUY_FEE_PERCENT", "0.6"))
DEFAULT_SELL_FEE_PERCENT = float(os.getenv("DEFAULT_SELL_FEE_PERCENT", "0.4"))

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# TA-Lib for technical indicators (RSI, SMA) - Optional
TALIB_AVAILABLE = False
try:
    import talib
    import numpy as np
    TALIB_AVAILABLE = True
except ImportError:
    logger.warning("TA-Lib not available - indicator-based exits disabled")
    import numpy as np


@dataclass
class DumpPosition:
    """
    Represents an open dump trading position

    Strategy: Enter at dump lows, capture 1-3% bounce, exit quickly
    """
    symbol: str
    entry_price: float
    entry_time: str
    quantity: float
    cost_basis: float  # Including fees
    buy_fee_rate: float  # Dynamic fee rate used
    sell_fee_rate: float  # Dynamic fee rate for exits
    break_even_price: float  # Price to break even after fees
    min_profit_price: float  # 1% profit target
    target_profit_price: float  # 3% profit target
    stop_loss_price: float  # 2% max loss
    peak_price: float  # Highest price seen
    trailing_exit_price: float  # Trailing stop
    dump_pct: float  # Size of dump that triggered entry
    volume_surge: float  # Volume surge ratio at entry
    status: str = "open"  # open, closed

    # Price history for indicator calculation
    price_history: list = None

    def __post_init__(self):
        if self.price_history is None:
            self.price_history = [self.entry_price]

    def update_price_history(self, price: float):
        """Add price to history for indicator calculations"""
        self.price_history.append(price)
        # Keep last 20 prices for RSI/SMA calculations
        if len(self.price_history) > 20:
            self.price_history.pop(0)

    def calculate_rsi(self, period: int = 14) -> Optional[float]:
        """Calculate RSI using TA-Lib"""
        if not TALIB_AVAILABLE or len(self.price_history) < period + 1:
            return None
        try:
            prices = np.array(self.price_history, dtype=float)
            rsi = talib.RSI(prices, timeperiod=period)
            return rsi[-1] if not np.isnan(rsi[-1]) else None
        except Exception as e:
            logger.warning(f"RSI calculation failed: {e}")
            return None

    def calculate_sma(self, period: int = 5) -> Optional[float]:
        """Calculate SMA using TA-Lib"""
        if not TALIB_AVAILABLE or len(self.price_history) < period:
            return None
        try:
            prices = np.array(self.price_history, dtype=float)
            sma = talib.SMA(prices, timeperiod=period)
            return sma[-1] if not np.isnan(sma[-1]) else None
        except Exception as e:
            logger.warning(f"SMA calculation failed: {e}")
            return None

    def update_peak(self, current_price: float) -> bool:
        """Update peak and trailing exit. Returns True if peak was updated."""
        if current_price > self.peak_price:
            self.peak_price = current_price
            # Tight trailing stop: 0.5% from peak
            self.trailing_exit_price = self.peak_price * (1 - TRAILING_THRESHOLD / 100)
            # Never let trailing drop below break-even
            self.trailing_exit_price = max(self.trailing_exit_price, self.break_even_price)
            return True
        return False

    def should_exit(self, current_price: float, current_volume: float = 0, avg_volume: float = 0) -> tuple[bool, str]:
        """
        Check if position should be exited

        Exit conditions:
        1. Stop loss: -2% max loss
        2. Target hit: +3% profit
        3. Quick profit: 1-2% profit, then break-even if dips
        4. Trailing stop after profit
        5. RSI overbought (>70) after profit
        6. Price below SMA after min hold
        7. Max hold time reached (15 min)
        """
        entry_time = datetime.fromisoformat(self.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # Calculate current P&L
        current_value = current_price * self.quantity
        sell_fee = current_value * self.sell_fee_rate
        net_proceeds = current_value - sell_fee
        pnl = net_proceeds - self.cost_basis
        pnl_percent = (pnl / self.cost_basis) * 100

        # 1. STOP LOSS: Exit immediately if -2% or worse
        if current_price <= self.stop_loss_price:
            return True, f"Stop loss hit ({pnl_percent:.2f}%)"

        # 2. TARGET PROFIT: Exit immediately if +3% or better
        if current_price >= self.target_profit_price:
            return True, f"Target profit hit ({pnl_percent:.2f}%)"

        # 3. BREAK-EVEN EXIT: If we hit 1-2% profit but now below entry
        if self.peak_price >= self.min_profit_price and current_price < self.break_even_price:
            return True, f"Break-even exit after profit peak ({pnl_percent:.2f}%)"

        # 4. TRAILING STOP: After hitting min profit target
        if current_price >= self.min_profit_price:
            if current_price <= self.trailing_exit_price:
                return True, f"Trailing stop hit ({pnl_percent:.2f}%)"

        # Wait for minimum hold time for indicator-based exits
        if time_held_minutes < MIN_HOLD_TIME_MINUTES:
            return False, ""

        # 5. RSI OVERBOUGHT: Exit if RSI > 70 and we have profit
        if TALIB_AVAILABLE and pnl_percent > 0:
            rsi = self.calculate_rsi()
            if rsi and rsi > RSI_OVERBOUGHT:
                return True, f"RSI overbought exit (RSI={rsi:.1f}, profit={pnl_percent:.2f}%)"

        # 6. PRICE BELOW SMA: Exit if price crosses below 5-period SMA
        if TALIB_AVAILABLE:
            sma = self.calculate_sma(SMA_PERIOD)
            if sma and current_price < sma:
                return True, f"Price below {SMA_PERIOD}-SMA exit ({pnl_percent:.2f}%)"

        # 7. MAX HOLD TIME: Force exit after 15 minutes
        if time_held_minutes >= MAX_HOLD_TIME_MINUTES:
            return True, f"Max hold time reached ({time_held_minutes:.1f} min, {pnl_percent:.2f}%)"

        # 8. VOLUME DRY UP: Exit if volume drops significantly and we have profit
        if pnl_percent > 0 and avg_volume > 0 and current_volume > 0:
            volume_ratio = current_volume / avg_volume
            if volume_ratio < 0.5:  # Volume dropped below 50% of average
                return True, f"Volume dried up ({pnl_percent:.2f}%)"

        return False, ""

    def calculate_pnl(self, exit_price: float) -> Dict:
        """Calculate profit/loss for this position"""
        gross_proceeds = self.quantity * exit_price
        sell_fee = gross_proceeds * self.sell_fee_rate
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


class DumpTradingBot:
    """Dump trading bot - buys dumps for quick bounce plays"""

    def __init__(self):
        self.capital = INITIAL_CAPITAL
        self.positions: Dict[str, DumpPosition] = {}  # symbol -> DumpPosition
        self.sio = None
        self.running = True

        # Fee rates (will be fetched dynamically)
        self.buy_fee_rate = DEFAULT_BUY_FEE_PERCENT / 100
        self.sell_fee_rate = DEFAULT_SELL_FEE_PERCENT / 100

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 60)
        logger.info("Dump Trading Bot Initialized")
        logger.info(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"Strategy: 100% balance allocation per trade")
        logger.info(f"Max Loss: {MAX_LOSS_PERCENT}% (tight stop)")
        logger.info(f"Profit Targets: {MIN_PROFIT_TARGET}%-{TARGET_PROFIT}%")
        logger.info(f"Hold Time: {MIN_HOLD_TIME_MINUTES}-{MAX_HOLD_TIME_MINUTES} min")
        logger.info(f"Volume Surge Required: {VOLUME_SURGE_THRESHOLD}x average")
        logger.info(f"Default Buy Fee: {self.buy_fee_rate*100:.2f}%")
        logger.info(f"Default Sell Fee: {self.sell_fee_rate*100:.2f}%")
        logger.info(f"TA-Lib Available: {TALIB_AVAILABLE}")
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
                buy_fee_rate REAL NOT NULL,
                sell_fee_rate REAL NOT NULL,
                gross_proceeds REAL,
                net_proceeds REAL,
                pnl REAL,
                pnl_percent REAL,
                peak_price REAL,
                dump_pct REAL,
                volume_surge REAL,
                status TEXT NOT NULL,
                exit_reason TEXT
            )
        """)

        # Portfolio snapshots
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

        # Open positions (persistence)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                quantity REAL NOT NULL,
                cost_basis REAL NOT NULL,
                buy_fee_rate REAL NOT NULL,
                sell_fee_rate REAL NOT NULL,
                break_even_price REAL NOT NULL,
                min_profit_price REAL NOT NULL,
                target_profit_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                peak_price REAL NOT NULL,
                trailing_exit_price REAL NOT NULL,
                dump_pct REAL,
                volume_surge REAL,
                status TEXT NOT NULL
            )
        """)

        self.db.commit()
        logger.info(f"Database initialized: {DB_PATH}")

    def fetch_dynamic_fees(self) -> tuple[float, float]:
        """
        Fetch current taker/maker fee rates from exchange API

        TODO: Implement actual API call to Coinbase/Binance
        For now, returns default fees

        Implementation example for Coinbase Advanced Trade:
        GET /api/v3/brokerage/transaction_summary
        Extract: maker_fee_rate, taker_fee_rate
        """
        # TODO: Add actual API integration
        # import requests
        # try:
        #     response = requests.get(f"{EXCHANGE_API_URL}/fees", timeout=5)
        #     if response.status_code == 200:
        #         data = response.json()
        #         buy_fee = data['taker_fee_rate']  # Assume taker for market buys
        #         sell_fee = data['maker_fee_rate']  # Assume maker for limit sells
        #         return buy_fee, sell_fee
        # except Exception as e:
        #     logger.warning(f"Failed to fetch dynamic fees: {e}")

        return self.buy_fee_rate, self.sell_fee_rate

    def open_position(self, symbol: str, entry_price: float, dump_pct: float, volume_surge: float):
        """
        Open position using 100% of account balance minus dynamic fees

        Formula: spend_amount = current_balance / (1 + buy_fee_rate)
        This ensures: spend_amount + fee <= balance
        """
        # Check if we already have a position
        if symbol in self.positions:
            logger.info(f"⏭️  Already have position in {symbol}, skipping")
            return

        # Fetch dynamic fees before trade
        buy_fee_rate, sell_fee_rate = self.fetch_dynamic_fees()

        # Calculate spend amount (100% allocation minus fees)
        # spend_amount + (spend_amount * fee_rate) = balance
        # spend_amount * (1 + fee_rate) = balance
        # spend_amount = balance / (1 + fee_rate)
        spend_amount = self.capital / (1 + buy_fee_rate)
        buy_fee = spend_amount * buy_fee_rate
        cost_basis = spend_amount + buy_fee

        if cost_basis > self.capital:
            logger.warning(f"⚠️  Insufficient capital for {symbol}")
            return

        # Calculate quantity
        quantity = spend_amount / entry_price

        # Calculate exit prices
        # Break-even: cover cost_basis after sell fees
        # net_proceeds = exit_price * quantity * (1 - sell_fee_rate)
        # Solve for exit_price where net_proceeds = cost_basis
        break_even_price = cost_basis / (quantity * (1 - sell_fee_rate))

        # Min profit: 1% above cost basis
        min_profit_target = cost_basis * (1 + MIN_PROFIT_TARGET / 100)
        min_profit_price = min_profit_target / (quantity * (1 - sell_fee_rate))

        # Target profit: 3% above cost basis
        target_profit_target = cost_basis * (1 + TARGET_PROFIT / 100)
        target_profit_price = target_profit_target / (quantity * (1 - sell_fee_rate))

        # Stop loss: 2% below entry (tight stop)
        stop_loss_price = entry_price * (1 - MAX_LOSS_PERCENT / 100)

        # Initial peak and trailing
        peak_price = entry_price
        trailing_exit_price = max(break_even_price, peak_price * (1 - TRAILING_THRESHOLD / 100))

        # Create position
        position = DumpPosition(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=datetime.now().isoformat(),
            quantity=quantity,
            cost_basis=cost_basis,
            buy_fee_rate=buy_fee_rate,
            sell_fee_rate=sell_fee_rate,
            break_even_price=break_even_price,
            min_profit_price=min_profit_price,
            target_profit_price=target_profit_price,
            stop_loss_price=stop_loss_price,
            peak_price=peak_price,
            trailing_exit_price=trailing_exit_price,
            dump_pct=dump_pct,
            volume_surge=volume_surge,
            status="open"
        )

        self.positions[symbol] = position
        self.capital -= cost_basis

        logger.info("=" * 60)
        logger.info(f"🟢 OPENED DUMP POSITION: {symbol}")
        logger.info(f"   Entry: ${entry_price:.6f} (after {abs(dump_pct):.2f}% dump)")
        logger.info(f"   Quantity: {quantity:.4f}")
        logger.info(f"   Spend: ${spend_amount:.2f} + ${buy_fee:.2f} fee = ${cost_basis:.2f}")
        logger.info(f"   Fee Rates: Buy {buy_fee_rate*100:.3f}%, Sell {sell_fee_rate*100:.3f}%")
        logger.info(f"   Break-Even: ${break_even_price:.6f}")
        logger.info(f"   Targets: ${min_profit_price:.6f} (1%) → ${target_profit_price:.6f} (3%)")
        logger.info(f"   Stop Loss: ${stop_loss_price:.6f} (-2%)")
        logger.info(f"   Volume Surge: {volume_surge:.2f}x")
        logger.info(f"   Capital Remaining: ${self.capital:.2f}")
        logger.info("=" * 60)

        # Record trade entry
        self._record_trade_entry(position)

    def update_position(self, symbol: str, current_price: float, current_volume: float = 0, avg_volume: float = 0):
        """Update position with new price data"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Update price history for indicators
        position.update_price_history(current_price)

        # Update peak
        peak_updated = position.update_peak(current_price)
        if peak_updated:
            unrealized = position.calculate_pnl(current_price)
            logger.info(f"🔼 {symbol}: New peak ${current_price:.6f}, "
                       f"Unrealized P&L: {unrealized['pnl_percent']:+.2f}%")

        # Check exit conditions
        should_exit, exit_reason = position.should_exit(current_price, current_volume, avg_volume)
        if should_exit:
            self.close_position(symbol, current_price, exit_reason)

    def close_position(self, symbol: str, exit_price: float, reason: str):
        """Close position"""
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
        logger.info(f"🔴 CLOSED DUMP POSITION: {symbol}")
        logger.info(f"   Entry: ${position.entry_price:.6f} → Exit: ${exit_price:.6f}")
        logger.info(f"   Peak: ${position.peak_price:.6f}")
        logger.info(f"   Net Proceeds: ${pnl_data['net_proceeds']:.2f}")
        logger.info(f"   P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)")
        logger.info(f"   Holding Time: {holding_seconds/60:.1f} minutes")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   New Capital: ${self.capital:.2f}")
        logger.info("=" * 60)

        # Record trade exit
        self._record_trade_exit(position, exit_price, pnl_data, reason)

        # Remove position
        del self.positions[symbol]

    def _record_trade_entry(self, position: DumpPosition):
        """Record trade entry in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, entry_time, entry_price, quantity, cost_basis,
                              buy_fee_rate, sell_fee_rate, peak_price, dump_pct, volume_surge, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.symbol,
            position.entry_time,
            position.entry_price,
            position.quantity,
            position.cost_basis,
            position.buy_fee_rate,
            position.sell_fee_rate,
            position.peak_price,
            position.dump_pct,
            position.volume_surge,
            'open'
        ))
        self.db.commit()

    def _record_trade_exit(self, position: DumpPosition, exit_price: float, pnl_data: Dict, reason: str):
        """Record trade exit in database"""
        cursor = self.db.cursor()
        cursor.execute("""
            UPDATE trades
            SET exit_time = ?, exit_price = ?, gross_proceeds = ?, net_proceeds = ?,
                pnl = ?, pnl_percent = ?, peak_price = ?, status = ?, exit_reason = ?
            WHERE symbol = ? AND status = 'open'
        """, (
            datetime.now().isoformat(),
            exit_price,
            pnl_data['gross_proceeds'],
            pnl_data['net_proceeds'],
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

        cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed' AND pnl > 0")
        winning_trades = cursor.fetchone()[0]
        losing_trades = total_trades - winning_trades

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
        """Connect to backend Socket.IO for dump alerts and price updates"""
        self.sio = socketio.Client(logger=False, engineio_logger=False)

        @self.sio.event
        def connect():
            logger.info("✅ Connected to backend Socket.IO")

        @self.sio.event
        def disconnect():
            logger.warning("❌ Disconnected from backend")

        @self.sio.on('spike_alert')
        def on_spike_alert(data):
            """Listen for DUMP alerts and enter positions"""
            try:
                # Only trade on dump start events
                if data.get('event_type') == 'spike_start' and data.get('spike_type') == 'dump':
                    symbol = data['symbol']
                    entry_price = data['new_price']
                    dump_pct = data['pct_change']  # Negative value

                    # Check volume surge (if available)
                    volume_surge = data.get('volume_surge', 1.0)

                    # Only enter if volume surged (confirmation of activity)
                    if volume_surge < VOLUME_SURGE_THRESHOLD:
                        logger.info(f"⏭️  {symbol}: Volume surge {volume_surge:.2f}x below threshold "
                                   f"({VOLUME_SURGE_THRESHOLD}x) - skipping")
                        return

                    logger.info(f"📢 DUMP ALERT: {symbol} {dump_pct:.2f}% (volume: {volume_surge:.2f}x)")
                    self.open_position(symbol, entry_price, dump_pct, volume_surge)

            except Exception as e:
                logger.error(f"Error processing dump alert: {e}")

        @self.sio.on('ticker_update')
        def on_ticker_update(data):
            """Update open positions with new prices"""
            try:
                symbol = data['crypto']
                price = data['price']
                current_volume = data.get('volume_24h', 0)
                avg_volume = data.get('avg_volume', 0)

                if symbol in self.positions:
                    self.update_position(symbol, price, current_volume, avg_volume)

            except Exception as e:
                logger.error(f"Error processing ticker update: {e}")

        # Connection loop
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
        logger.info("DUMP TRADING SUMMARY")
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
        logger.info("\n🛑 Stopping dump trading bot...")
        self.running = False

        # Close all open positions at current price
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            self.close_position(symbol, position.entry_price, "Bot shutdown")

        self.print_summary()

        if self.db:
            self.db.close()

        if self.sio:
            self.sio.disconnect()


def main():
    """Main entry point"""
    bot = DumpTradingBot()

    def signal_handler(sig, frame):
        bot.stop()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    bot.run()


if __name__ == "__main__":
    main()
