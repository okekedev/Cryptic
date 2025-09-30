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
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000.0"))  # $10,000 starting capital
POSITION_SIZE_PERCENT = float(os.getenv("POSITION_SIZE_PERCENT", "10.0"))  # 10% per trade
MIN_PROFIT_TARGET = float(os.getenv("MIN_PROFIT_TARGET", "3.0"))  # 3% minimum profit
TRAILING_THRESHOLD = float(os.getenv("TRAILING_THRESHOLD", "1.5"))  # Drop 1.5% from peak to exit
MIN_HOLD_TIME_MINUTES = float(os.getenv("MIN_HOLD_TIME_MINUTES", "30.0"))  # Minimum 30 min hold time
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "5.0"))  # 5% stop loss
BUY_FEE_PERCENT = float(os.getenv("BUY_FEE_PERCENT", "0.6"))  # Coinbase Advanced Trade taker fee
SELL_FEE_PERCENT = float(os.getenv("SELL_FEE_PERCENT", "0.4"))  # Coinbase Advanced Trade maker fee
DB_PATH = os.getenv("DB_PATH", "/app/data/paper_trading.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open paper trading position"""
    symbol: str
    entry_price: float
    entry_time: str
    quantity: float
    cost_basis: float  # Including fees
    min_exit_price: float  # Minimum price to exit (3% + fees)
    peak_price: float  # Highest price seen
    trailing_exit_price: float  # Current trailing stop
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

    def should_exit(self, current_price: float) -> tuple[bool, str]:
        """Check if position should be exited. Returns (should_exit, reason)"""
        # Calculate time held
        entry_time = datetime.fromisoformat(self.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # Calculate current P&L percentage
        current_value = current_price * self.quantity
        pnl_percent = ((current_value - self.cost_basis) / self.cost_basis) * 100

        # STOP LOSS: Exit immediately if down 5% or more
        if pnl_percent <= -STOP_LOSS_PERCENT:
            return True, f"Stop loss hit ({pnl_percent:.2f}%)"

        # PROFIT TARGET MET: Use trailing stop if price reached min profit target
        if current_price >= self.min_exit_price:
            if current_price <= self.trailing_exit_price:
                return True, f"Trailing stop hit (profit secured)"

        # MINIMUM HOLD TIME: Don't exit before 30 minutes unless stop loss
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
        self.sio = None
        self.running = True

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 60)
        logger.info("Paper Trading Bot Initialized")
        logger.info(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"Position Size: {POSITION_SIZE_PERCENT}% per trade")
        logger.info(f"Min Profit Target: {MIN_PROFIT_TARGET}%")
        logger.info(f"Trailing Threshold: {TRAILING_THRESHOLD}%")
        logger.info(f"Min Hold Time: {MIN_HOLD_TIME_MINUTES} minutes")
        logger.info(f"Stop Loss: {STOP_LOSS_PERCENT}%")
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

        self.db.commit()
        logger.info(f"Database initialized: {DB_PATH}")

    def open_position(self, symbol: str, entry_price: float, spike_pct: float):
        """Open a new paper trading position"""
        # Check if we already have a position in this symbol
        if symbol in self.positions:
            logger.info(f"⏭️  Already have position in {symbol}, skipping")
            return

        # Calculate position size
        position_value = self.capital * (POSITION_SIZE_PERCENT / 100)

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
            status="open"
        )

        self.positions[symbol] = position
        self.capital -= cost_basis

        logger.info("=" * 60)
        logger.info(f"🟢 OPENED POSITION: {symbol}")
        logger.info(f"   Entry Price: ${entry_price:.6f}")
        logger.info(f"   Quantity: {quantity:.4f}")
        logger.info(f"   Cost Basis: ${cost_basis:.2f} (including ${buy_fee:.2f} fee)")
        logger.info(f"   Min Exit Price: ${min_exit_price:.6f} ({MIN_PROFIT_TARGET}% profit)")
        logger.info(f"   Trailing Exit: ${trailing_exit_price:.6f}")
        logger.info(f"   Remaining Capital: ${self.capital:.2f}")
        logger.info(f"   Spike %: {spike_pct:.2f}%")
        logger.info("=" * 60)

        # Record in database
        self._record_trade_entry(position, spike_pct)

    def update_position(self, symbol: str, current_price: float):
        """Update position with new price data"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Update peak and check if we should exit
        peak_updated = position.update_peak(current_price)

        if peak_updated:
            unrealized = position.calculate_pnl(current_price)
            logger.info(f"🔼 {symbol}: New peak ${current_price:.6f}, "
                       f"Unrealized P&L: {unrealized['pnl_percent']:+.2f}%, "
                       f"Trailing exit now at ${position.trailing_exit_price:.6f}")

        # Check if we should exit
        should_exit, exit_reason = position.should_exit(current_price)
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
        """Connect to backend Socket.IO for spike alerts and price updates"""
        self.sio = socketio.Client(logger=False, engineio_logger=False)

        @self.sio.event
        def connect():
            logger.info("✅ Connected to backend Socket.IO")

        @self.sio.event
        def disconnect():
            logger.warning("❌ Disconnected from backend")

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

                # Update any open positions
                if symbol in self.positions:
                    self.update_position(symbol, price)
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