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
import requests
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass, asdict

# Import Coinbase client for real trading
try:
    from coinbase_client import CoinbaseClient
    COINBASE_AVAILABLE = True
except ImportError:
    COINBASE_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Coinbase client not available - live trading disabled")

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "http://telegram-bot:8080/webhook")
AUTO_TRADE = os.getenv("AUTO_TRADE", "no").lower() in ("yes", "true", "1")
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "22:30")  # 10:30 PM
INITIAL_CAPITAL = float(os.getenv("DUMP_INITIAL_CAPITAL", "10000.0"))
POSITION_SIZE_PERCENT = float(os.getenv("DUMP_POSITION_SIZE_PERCENT", "25.0"))  # 25% per trade
MAX_CONCURRENT_POSITIONS = int(os.getenv("DUMP_MAX_CONCURRENT_POSITIONS", "4"))  # Max 4 positions
MAX_LOSS_PERCENT = float(os.getenv("DUMP_MAX_LOSS_PERCENT", "3.0"))  # Stop loss
MIN_PROFIT_TARGET = float(os.getenv("DUMP_MIN_PROFIT_TARGET", "2.0"))  # Min profit
TARGET_PROFIT = float(os.getenv("DUMP_TARGET_PROFIT", "4.0"))  # Target profit
TRAILING_THRESHOLD = float(os.getenv("DUMP_TRAILING_THRESHOLD", "0.7"))  # Trailing stop
MIN_HOLD_TIME_MINUTES = float(os.getenv("DUMP_MIN_HOLD_TIME_MINUTES", "5.0"))  # Min hold
MAX_HOLD_TIME_MINUTES = float(os.getenv("DUMP_MAX_HOLD_TIME_MINUTES", "15.0"))  # Max hold
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

        # Calculate current P&L (simplified - no fees)
        current_value = current_price * self.quantity
        pnl = current_value - self.cost_basis
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
        """Calculate profit/loss for this position (simplified - no fees)"""
        proceeds = self.quantity * exit_price
        pnl = proceeds - self.cost_basis
        pnl_percent = (pnl / self.cost_basis) * 100

        return {
            "proceeds": proceeds,
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

        # Initialize Coinbase client for live trading
        self.coinbase = None
        if AUTO_TRADE and COINBASE_AVAILABLE:
            try:
                self.coinbase = CoinbaseClient()
                # Fetch actual balance from Coinbase
                balance = self.coinbase.get_account_balance("USD")
                if balance is not None:
                    self.capital = balance
                    logger.info(f"✅ Coinbase connected - Live balance: ${balance:,.2f}")
            except Exception as e:
                logger.error(f"Failed to initialize Coinbase client: {e}")
                self.coinbase = None

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 60)
        logger.info("Dump Trading Bot Initialized")
        logger.info(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"Position Sizing: {POSITION_SIZE_PERCENT}% per trade")
        logger.info(f"Max Concurrent Positions: {MAX_CONCURRENT_POSITIONS}")
        logger.info(f"Auto-Trading: {'ENABLED' if AUTO_TRADE else 'DISABLED (alerts only)'}")
        logger.info(f"Max Loss: {MAX_LOSS_PERCENT}% stop loss")
        logger.info(f"Profit Targets: {MIN_PROFIT_TARGET}%-{TARGET_PROFIT}%")
        logger.info(f"Hold Time: {MIN_HOLD_TIME_MINUTES}-{MAX_HOLD_TIME_MINUTES} min")
        logger.info(f"Volume Surge Required: {VOLUME_SURGE_THRESHOLD}x average")
        logger.info(f"Default Buy Fee: {self.buy_fee_rate*100:.2f}%")
        logger.info(f"Default Sell Fee: {self.sell_fee_rate*100:.2f}%")
        logger.info(f"TA-Lib Available: {TALIB_AVAILABLE}")
        logger.info(f"Telegram Alerts: {'ENABLED' if TELEGRAM_WEBHOOK_URL else 'DISABLED'}")
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

    def send_telegram_alert(self, message: str, alert_type: str = "info"):
        """
        Send alert to Telegram via webhook

        Args:
            message: The message to send
            alert_type: Type of alert (info, success, warning, error)
        """
        if not TELEGRAM_WEBHOOK_URL:
            return

        try:
            payload = {
                "type": "dump_trading_alert",
                "alert_type": alert_type,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }

            response = requests.post(
                TELEGRAM_WEBHOOK_URL,
                json=payload,
                timeout=5
            )

            if response.status_code == 200:
                logger.debug(f"Telegram alert sent: {alert_type}")
            else:
                logger.warning(f"Telegram alert failed: {response.status_code}")

        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def open_position(self, symbol: str, entry_price: float, dump_pct: float, volume_surge: float):
        """
        Open position using POSITION_SIZE_PERCENT% of total capital

        Formula: spend_amount = (capital * position_size_pct / 100) / (1 + buy_fee_rate)
        This ensures: spend_amount + fee <= allocated amount
        """
        # Check if we already have a position
        if symbol in self.positions:
            logger.info(f"⏭️  Already have position in {symbol}, skipping")
            return

        # Check max concurrent positions limit
        if len(self.positions) >= MAX_CONCURRENT_POSITIONS:
            logger.info(f"⏭️  Max concurrent positions ({MAX_CONCURRENT_POSITIONS}) reached, skipping {symbol}")
            return

        # Check AUTO_TRADE flag - if disabled, only send alert
        if not AUTO_TRADE:
            alert_msg = f"🚨 DUMP ALERT (AUTO-TRADE DISABLED)\n\n" \
                       f"Symbol: {symbol}\n" \
                       f"Dump: {abs(dump_pct):.2f}%\n" \
                       f"Entry Price: ${entry_price:.6f}\n" \
                       f"Volume Surge: {volume_surge:.2f}x\n\n" \
                       f"Current Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}\n\n" \
                       f"Enable auto-trading to execute automatically."
            self.send_telegram_alert(alert_msg, "warning")
            logger.info(f"⚠️  AUTO_TRADE disabled - Alert sent for {symbol}")
            return

        logger.info("=" * 80)
        logger.info(f"💭 ENTRY DECISION ANALYSIS: {symbol}")
        logger.info("-" * 80)

        # Fetch dynamic fees before trade
        buy_fee_rate, sell_fee_rate = self.fetch_dynamic_fees()

        logger.info(f"📢 Dump Alert Received:")
        logger.info(f"   Dump Size: {abs(dump_pct):.2f}%")
        logger.info(f"   Entry Price: ${entry_price:.6f}")
        logger.info(f"   Volume Surge: {volume_surge:.2f}x average")
        logger.info("")

        # Simple position sizing: 25% of available capital
        total_positions_value = sum(p.cost_basis for p in self.positions.values())
        total_capital = self.capital + total_positions_value

        # Allocate 25% of total capital
        spend_amount = total_capital * (POSITION_SIZE_PERCENT / 100)

        if spend_amount > self.capital:
            logger.warning(f"⚠️  Insufficient capital for {symbol}")
            logger.info("=" * 80)
            return

        # Simple: just use spend_amount as cost basis (no fee calculations)
        cost_basis = spend_amount
        quantity = spend_amount / entry_price

        # Simple exit prices (no fee adjustments)
        break_even_price = entry_price
        min_profit_price = entry_price * (1 + MIN_PROFIT_TARGET / 100)
        target_profit_price = entry_price * (1 + TARGET_PROFIT / 100)
        stop_loss_price = entry_price * (1 - MAX_LOSS_PERCENT / 100)

        logger.info(f"💰 Position Sizing:")
        logger.info(f"   Total Capital: ${total_capital:.2f}")
        logger.info(f"   Open Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}")
        logger.info(f"   Position Size: {POSITION_SIZE_PERCENT}% = ${spend_amount:.2f}")
        logger.info(f"   Quantity: {quantity:.6f} {symbol.split('-')[0]}")
        logger.info("")

        logger.info(f"🎯 Exit Strategy:")
        logger.info(f"   Entry: ${entry_price:.6f}")
        logger.info(f"   Min Profit: ${min_profit_price:.6f} (+{MIN_PROFIT_TARGET}%)")
        logger.info(f"   Target: ${target_profit_price:.6f} (+{TARGET_PROFIT}%)")
        logger.info(f"   Stop Loss: ${stop_loss_price:.6f} (-{MAX_LOSS_PERCENT}%)")
        logger.info("")

        # Calculate risk/reward ratio
        risk_reward_ratio = TARGET_PROFIT / MAX_LOSS_PERCENT

        logger.info(f"🧠 Entry Rationale:")
        logger.info(f"   ✓ Significant dump detected ({abs(dump_pct):.2f}% drop)")
        logger.info(f"   ✓ Mean reversion strategy - expecting bounce")
        if volume_surge >= VOLUME_SURGE_THRESHOLD:
            logger.info(f"   ✓ Strong volume confirmation ({volume_surge:.2f}x average)")
        else:
            logger.info(f"   ⚠️ Volume below threshold ({volume_surge:.2f}x vs {VOLUME_SURGE_THRESHOLD}x target)")
        logger.info(f"   ✓ Tight stop loss ({MAX_LOSS_PERCENT}%) limits downside")
        logger.info(f"   ✓ Quick profit targets ({MIN_PROFIT_TARGET}%-{TARGET_PROFIT}%) for rapid exits")
        logger.info(f"   ✓ Risk/Reward favorable: 1:{risk_reward_ratio:.2f}")
        logger.info("")

        logger.info(f"⏱️ Time Constraints:")
        logger.info(f"   Min Hold: {MIN_HOLD_TIME_MINUTES} minutes (let bounce develop)")
        logger.info(f"   Max Hold: {MAX_HOLD_TIME_MINUTES} minutes (force exit if stagnant)")
        logger.info("")

        logger.info(f"🚀 EXECUTING BUY ORDER...")

        # Execute actual Coinbase trade if live trading enabled
        if self.coinbase:
            buy_result = self.coinbase.market_buy(symbol, spend_amount)
            if not buy_result.get('success'):
                error_msg = f"❌ Buy order failed: {buy_result.get('error')}"
                logger.error(error_msg)
                self.send_telegram_alert(error_msg, "error")
                logger.info("=" * 80)
                return
            logger.info(f"✅ Live trade executed: Order ID {buy_result.get('order_id')}")

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

        logger.info(f"✅ POSITION OPENED: {symbol}")
        logger.info(f"   Capital Remaining: ${self.capital:.2f}")
        logger.info("=" * 80)

        # Send Telegram alert for position open
        entry_alert = f"🟢 POSITION OPENED\n\n" \
                     f"Symbol: {symbol}\n" \
                     f"Entry: ${entry_price:.6f}\n" \
                     f"Quantity: {quantity:.6f}\n" \
                     f"Position Size: ${cost_basis:.2f} ({POSITION_SIZE_PERCENT}%)\n" \
                     f"Dump: {abs(dump_pct):.2f}%\n\n" \
                     f"Targets:\n" \
                     f"• Min: ${min_profit_price:.6f} (+{MIN_PROFIT_TARGET}%)\n" \
                     f"• Target: ${target_profit_price:.6f} (+{TARGET_PROFIT}%)\n" \
                     f"• Stop: ${stop_loss_price:.6f} (-{MAX_LOSS_PERCENT}%)\n\n" \
                     f"Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}"
        self.send_telegram_alert(entry_alert, "success")

        # Record trade entry
        self._record_trade_entry(position)

    def update_position(self, symbol: str, current_price: float, current_volume: float = 0, avg_volume: float = 0):
        """Update position with new price data"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Update price history for indicators
        position.update_price_history(current_price)

        # Calculate current metrics
        entry_time = datetime.fromisoformat(position.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60
        unrealized = position.calculate_pnl(current_price)
        price_change_pct = ((current_price - position.entry_price) / position.entry_price) * 100

        # Calculate indicator values
        rsi = position.calculate_rsi() if TALIB_AVAILABLE else None
        sma = position.calculate_sma(SMA_PERIOD) if TALIB_AVAILABLE else None

        # Log detailed position analysis
        logger.info("=" * 80)
        logger.info(f"💭 POSITION ANALYSIS: {symbol}")
        logger.info("-" * 80)
        logger.info(f"📊 Current State:")
        logger.info(f"   Entry: ${position.entry_price:.6f} → Current: ${current_price:.6f} ({price_change_pct:+.2f}%)")
        logger.info(f"   Time Held: {time_held_minutes:.1f} min / {MAX_HOLD_TIME_MINUTES:.0f} min max")
        logger.info(f"   Peak: ${position.peak_price:.6f}")
        logger.info(f"   Unrealized P&L: ${unrealized['pnl']:+.2f} ({unrealized['pnl_percent']:+.2f}%)")
        logger.info(f"   Capital at Risk: ${position.cost_basis:.2f}")
        logger.info("")
        logger.info(f"🎯 Exit Targets:")
        logger.info(f"   Break-Even: ${position.break_even_price:.6f} ({((position.break_even_price - current_price) / current_price * 100):+.2f}% away)")
        logger.info(f"   Min Profit: ${position.min_profit_price:.6f} ({((position.min_profit_price - current_price) / current_price * 100):+.2f}% away)")
        logger.info(f"   Target: ${position.target_profit_price:.6f} ({((position.target_profit_price - current_price) / current_price * 100):+.2f}% away)")
        logger.info(f"   Stop Loss: ${position.stop_loss_price:.6f} ({((position.stop_loss_price - current_price) / current_price * 100):+.2f}% away)")
        logger.info(f"   Trailing Stop: ${position.trailing_exit_price:.6f}")
        logger.info("")

        # Technical indicators
        if TALIB_AVAILABLE:
            logger.info(f"📈 Technical Indicators:")
            if rsi is not None:
                rsi_status = "OVERBOUGHT ⚠️" if rsi > RSI_OVERBOUGHT else "OK ✓"
                logger.info(f"   RSI(14): {rsi:.1f} - {rsi_status}")
            if sma is not None:
                sma_diff = ((current_price - sma) / sma) * 100
                sma_status = "ABOVE ✓" if current_price > sma else "BELOW ⚠️"
                logger.info(f"   SMA({SMA_PERIOD}): ${sma:.6f} - Price is {sma_status} ({sma_diff:+.2f}%)")
            logger.info("")

        # Volume analysis
        if avg_volume > 0 and current_volume > 0:
            volume_ratio = current_volume / avg_volume
            volume_status = "STRONG ✓" if volume_ratio > 1.0 else "WEAK ⚠️" if volume_ratio < 0.5 else "NORMAL"
            logger.info(f"📊 Volume Analysis:")
            logger.info(f"   Current: {current_volume:,.0f} / Average: {avg_volume:,.0f}")
            logger.info(f"   Ratio: {volume_ratio:.2f}x - {volume_status}")
            logger.info("")

        # Decision analysis
        logger.info(f"🧠 Thinking Process:")

        # Check each exit condition and explain why it's not triggered
        if current_price <= position.stop_loss_price:
            logger.info(f"   💀 STOP LOSS TRIGGERED - Price below ${position.stop_loss_price:.6f}")
        elif current_price >= position.target_profit_price:
            logger.info(f"   🎯 TARGET PROFIT HIT - Price reached ${position.target_profit_price:.6f}")
        elif position.peak_price >= position.min_profit_price and current_price < position.break_even_price:
            logger.info(f"   ⚖️ BREAK-EVEN EXIT TRIGGERED - Hit profit but now below entry")
        elif current_price >= position.min_profit_price and current_price <= position.trailing_exit_price:
            logger.info(f"   📉 TRAILING STOP HIT - Dropped from peak to ${position.trailing_exit_price:.6f}")
        elif time_held_minutes < MIN_HOLD_TIME_MINUTES:
            remaining = MIN_HOLD_TIME_MINUTES - time_held_minutes
            logger.info(f"   ⏱️ HOLDING - Minimum hold time not reached ({remaining:.1f} min remaining)")
            logger.info(f"   💭 Waiting for: either target hit, stop loss, or min hold time")
        elif rsi and rsi > RSI_OVERBOUGHT and unrealized['pnl_percent'] > 0:
            logger.info(f"   🌡️ RSI OVERBOUGHT + PROFIT - Consider exiting (RSI={rsi:.1f})")
        elif sma and current_price < sma:
            logger.info(f"   📉 PRICE BELOW SMA - Momentum weakening, consider exit")
        elif time_held_minutes >= MAX_HOLD_TIME_MINUTES:
            logger.info(f"   ⏰ MAX HOLD TIME REACHED - Force exit regardless of P&L")
        elif avg_volume > 0 and current_volume > 0 and (current_volume / avg_volume) < 0.5 and unrealized['pnl_percent'] > 0:
            logger.info(f"   💧 VOLUME DRIED UP - Exit with profit while possible")
        else:
            logger.info(f"   ✅ HOLDING POSITION - No exit conditions met")
            if unrealized['pnl_percent'] > 0:
                logger.info(f"   💭 In profit, watching for: target (${position.target_profit_price:.6f}) or trailing stop")
            else:
                logger.info(f"   💭 Building position, watching for: bounce to profit or stop loss")

        logger.info("=" * 80)

        # Update peak
        peak_updated = position.update_peak(current_price)
        if peak_updated:
            logger.info(f"🔼 {symbol}: New peak ${current_price:.6f}, "
                       f"Trailing exit updated to ${position.trailing_exit_price:.6f}")

        # Check exit conditions
        should_exit, exit_reason = position.should_exit(current_price, current_volume, avg_volume)
        if should_exit:
            self.close_position(symbol, current_price, exit_reason)

    def close_position(self, symbol: str, exit_price: float, reason: str):
        """Close position"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Execute actual Coinbase sell if live trading enabled
        if self.coinbase:
            sell_result = self.coinbase.market_sell(symbol, position.quantity)
            if not sell_result.get('success'):
                error_msg = f"❌ Sell order failed for {symbol}: {sell_result.get('error')}"
                logger.error(error_msg)
                self.send_telegram_alert(error_msg, "error")
                # Don't return - still close the position in our tracking
            else:
                logger.info(f"✅ Live sell executed: Order ID {sell_result.get('order_id')}")

        pnl_data = position.calculate_pnl(exit_price)

        # Calculate holding time
        entry_time = datetime.fromisoformat(position.entry_time)
        exit_time = datetime.now()
        holding_seconds = (exit_time - entry_time).total_seconds()
        holding_minutes = holding_seconds / 60

        # Calculate price movement
        price_change_pct = ((exit_price - position.entry_price) / position.entry_price) * 100
        peak_change_pct = ((position.peak_price - position.entry_price) / position.entry_price) * 100
        peak_to_exit_pct = ((exit_price - position.peak_price) / position.peak_price) * 100

        # Update capital (add proceeds back)
        self.capital += pnl_data['proceeds']

        # Get final indicator values if available
        rsi = position.calculate_rsi() if TALIB_AVAILABLE else None
        sma = position.calculate_sma(SMA_PERIOD) if TALIB_AVAILABLE else None

        logger.info("=" * 80)
        logger.info(f"💭 EXIT DECISION ANALYSIS: {symbol}")
        logger.info("-" * 80)

        logger.info(f"🔚 Exit Triggered:")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Exit Price: ${exit_price:.6f}")
        logger.info(f"   Time Held: {holding_minutes:.1f} minutes")
        logger.info("")

        logger.info(f"📊 Position Summary:")
        logger.info(f"   Entry: ${position.entry_price:.6f}")
        logger.info(f"   Peak: ${position.peak_price:.6f} ({peak_change_pct:+.2f}% from entry)")
        logger.info(f"   Exit: ${exit_price:.6f} ({price_change_pct:+.2f}% from entry)")
        logger.info(f"   Peak to Exit: {peak_to_exit_pct:+.2f}%")
        logger.info(f"   Quantity: {position.quantity:.6f}")
        logger.info("")

        logger.info(f"💰 Financial Results:")
        logger.info(f"   Cost Basis: ${position.cost_basis:.2f}")
        logger.info(f"   Proceeds: ${pnl_data['proceeds']:.2f}")
        logger.info(f"   P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)")
        logger.info("")

        # Explain which exit condition was met
        logger.info(f"🧠 Exit Condition Analysis:")
        if "Stop loss" in reason:
            max_loss = position.cost_basis * (MAX_LOSS_PERCENT / 100)
            logger.info(f"   💀 STOP LOSS HIT - Price fell below ${position.stop_loss_price:.6f}")
            logger.info(f"   📉 Protected against larger loss (max risk: ${max_loss:.2f})")
            logger.info(f"   🛡️ Risk management working as intended")
        elif "Target profit" in reason:
            logger.info(f"   🎯 TARGET PROFIT ACHIEVED - Price reached ${position.target_profit_price:.6f}")
            logger.info(f"   ✅ Hit {TARGET_PROFIT}% profit target")
            logger.info(f"   🎉 Clean win - strategy executed perfectly")
        elif "Break-even exit" in reason:
            logger.info(f"   ⚖️ BREAK-EVEN EXIT - Hit profit but price returned to entry")
            logger.info(f"   📊 Peak was ${position.peak_price:.6f} ({peak_change_pct:+.2f}%)")
            logger.info(f"   🛡️ Protected profit by exiting at break-even")
        elif "Trailing stop" in reason:
            logger.info(f"   📉 TRAILING STOP HIT - Price dropped {TRAILING_THRESHOLD}% from peak")
            logger.info(f"   📊 Peak: ${position.peak_price:.6f}, Trailing: ${position.trailing_exit_price:.6f}")
            logger.info(f"   ✅ Locked in profit of {pnl_data['pnl_percent']:+.2f}%")
        elif "RSI overbought" in reason:
            logger.info(f"   🌡️ RSI OVERBOUGHT SIGNAL - RSI > {RSI_OVERBOUGHT}")
            if rsi:
                logger.info(f"   📈 Final RSI: {rsi:.1f}")
            logger.info(f"   💡 Momentum exhausted, took profit at {pnl_data['pnl_percent']:+.2f}%")
        elif "SMA" in reason or "below" in reason.lower():
            logger.info(f"   📉 PRICE BELOW SMA - Momentum turned negative")
            if sma:
                logger.info(f"   📊 Final SMA({SMA_PERIOD}): ${sma:.6f}")
            logger.info(f"   💡 Trend weakening, exited at {pnl_data['pnl_percent']:+.2f}%")
        elif "Max hold time" in reason:
            logger.info(f"   ⏰ MAX HOLD TIME EXCEEDED - {holding_minutes:.1f} min > {MAX_HOLD_TIME_MINUTES} min")
            logger.info(f"   🔄 Position stagnant, forced exit")
            logger.info(f"   💡 Better to free capital for next opportunity")
        elif "Volume dried up" in reason:
            logger.info(f"   💧 VOLUME DRIED UP - Trading interest declined")
            logger.info(f"   💡 Exited with profit before liquidity disappears")
        elif "Min hold time" in reason:
            logger.info(f"   ⏱️ MIN HOLD TIME REACHED - Released position after {holding_minutes:.1f} min")
            logger.info(f"   💡 Held minimum time, exited with available profit")
        else:
            logger.info(f"   📋 Other: {reason}")
        logger.info("")

        # Performance rating
        if pnl_data['pnl_percent'] >= TARGET_PROFIT:
            rating = "🏆 EXCELLENT"
        elif pnl_data['pnl_percent'] >= MIN_PROFIT_TARGET:
            rating = "✅ GOOD"
        elif pnl_data['pnl_percent'] > 0:
            rating = "✓ PROFIT"
        elif pnl_data['pnl_percent'] > -MAX_LOSS_PERCENT / 2:
            rating = "⚠️ SMALL LOSS"
        else:
            rating = "❌ STOPPED OUT"

        logger.info(f"📈 Trade Rating: {rating}")
        logger.info(f"   Previous Capital: ${self.capital - pnl_data['proceeds']:.2f}")
        logger.info(f"   New Capital: ${self.capital:.2f}")
        logger.info("")

        # Strategy insights
        logger.info(f"💡 Trade Insights:")
        if pnl_data['pnl_percent'] >= TARGET_PROFIT:
            logger.info(f"   ✓ Perfect execution - dump bounced as expected")
        elif pnl_data['pnl_percent'] >= MIN_PROFIT_TARGET:
            logger.info(f"   ✓ Strategy working - captured {MIN_PROFIT_TARGET}-{TARGET_PROFIT}% bounce")
        elif pnl_data['pnl_percent'] > 0:
            logger.info(f"   ✓ Small win - could have held longer but took profit")
        else:
            logger.info(f"   ✗ Dump continued - stop loss protected capital")
            logger.info(f"   💡 Not all dumps bounce immediately - this is expected")

        logger.info("=" * 80)

        # Send Telegram alert for position close
        exit_emoji = "🟢" if pnl_data['pnl_percent'] > 0 else "🔴"
        exit_alert = f"{exit_emoji} POSITION CLOSED\n\n" \
                    f"Symbol: {symbol}\n" \
                    f"Entry: ${position.entry_price:.6f}\n" \
                    f"Exit: ${exit_price:.6f} ({price_change_pct:+.2f}%)\n" \
                    f"Time: {holding_minutes:.1f} min\n\n" \
                    f"P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)\n" \
                    f"Reason: {reason}\n\n" \
                    f"New Capital: ${self.capital:.2f}"
        alert_type = "success" if pnl_data['pnl_percent'] > 0 else "error"
        self.send_telegram_alert(exit_alert, alert_type)

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
            pnl_data['proceeds'],
            pnl_data['proceeds'],
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

                    # DISABLED: Volume surge threshold filter (testing performance without filter)
                    # if volume_surge < VOLUME_SURGE_THRESHOLD:
                    #     logger.info(f"⏭️  {symbol}: Volume surge {volume_surge:.2f}x below threshold "
                    #                f"({VOLUME_SURGE_THRESHOLD}x) - skipping")
                    #     return

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
                if not self.sio.connected:
                    self.sio.connect(BACKEND_URL)
                self.sio.wait()
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                if self.sio.connected:
                    self.sio.disconnect()
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

    def send_daily_summary(self):
        """Send daily P&L summary to Telegram"""
        stats = self.get_statistics()

        # Fetch live balance if Coinbase connected
        live_balance = None
        if self.coinbase:
            live_balance = self.coinbase.get_account_balance("USD")

        summary = f"📊 DAILY TRADING SUMMARY\n\n"
        summary += f"Date: {datetime.now().strftime('%Y-%m-%d')}\n\n"

        if live_balance is not None:
            summary += f"💰 Live Balance: ${live_balance:,.2f}\n"
            if live_balance != self.capital:
                summary += f"   Tracked: ${self.capital:,.2f}\n"
        else:
            summary += f"💰 Current Capital: ${stats['current_capital']:,.2f}\n"

        summary += f"📈 Total Return: {stats['total_return']:+.2f}%\n"
        summary += f"   P&L: ${stats['total_pnl']:+,.2f}\n\n"

        summary += f"📊 Today's Stats:\n"
        summary += f"   Total Trades: {stats['total_trades']}\n"
        summary += f"   Winners: {stats['winning_trades']} ({stats['win_rate']:.1f}%)\n"
        summary += f"   Losers: {stats['losing_trades']}\n\n"

        if stats['total_trades'] > 0:
            summary += f"   Best: ${stats['best_trade']:+,.2f}\n"
            summary += f"   Worst: ${stats['worst_trade']:+,.2f}\n"
            summary += f"   Avg: ${stats['avg_pnl']:+,.2f}\n\n"

        summary += f"🔄 Open Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}\n"

        self.send_telegram_alert(summary, "info")
        logger.info("📤 Daily summary sent")

    def schedule_daily_summary(self):
        """Background thread to send daily summary at specified time"""
        def scheduler():
            while self.running:
                try:
                    now = datetime.now()
                    hour, minute = map(int, DAILY_SUMMARY_TIME.split(':'))
                    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    # If target time has passed today, schedule for tomorrow
                    if now > target_time:
                        target_time += timedelta(days=1)

                    # Wait until target time
                    wait_seconds = (target_time - now).total_seconds()
                    logger.info(f"📅 Next daily summary at {target_time.strftime('%Y-%m-%d %H:%M')}")

                    time.sleep(wait_seconds)
                    self.send_daily_summary()

                except Exception as e:
                    logger.error(f"Error in daily summary scheduler: {e}")
                    time.sleep(60)  # Wait 1 minute before retrying

        thread = threading.Thread(target=scheduler, daemon=True)
        thread.start()
        logger.info(f"📅 Daily summary scheduler started (sends at {DAILY_SUMMARY_TIME})")

    def run(self):
        """Main bot loop"""
        try:
            # Start daily summary scheduler
            self.schedule_daily_summary()

            # Connect to WebSocket and start trading
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
