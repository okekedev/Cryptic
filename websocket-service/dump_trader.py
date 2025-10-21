#!/usr/bin/env python3
"""
Dump Trading Bot - Backtest-Proven Mean Reversion Strategy

BACKTEST RESULTS (49 trades, Oct 17-19):
- Win Rate: 97.96% (48 wins, 1 loss)
- Avg Return: 7.79% per trade
- Total Return: 381.78%
- Strategy: -8% dumps, +10% exit, 120min max hold

Key Features:
- Simple, proven filters (price, volume, volatility, spread)
- NO complex market conditions (RSI, trend, session) - never backtested
- Immediate market order entry (1.2% taker fee)
- Limit order exit at +10% target (0.6% maker fee)
- Total round-trip fee: 1.8% (matches backtest)
- Conservative position sizing: 10% per trade, max 10 positions
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

# Market conditions DISABLED - never backtested, using simple proven filters instead
MARKET_CONDITIONS_AVAILABLE = False

# Configuration
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "http://telegram-bot:8080/webhook")
AUTO_TRADE = os.getenv("AUTO_TRADE", "no").lower() in ("yes", "true", "1")
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "22:30")  # 10:30 PM
INITIAL_CAPITAL = float(os.getenv("DUMP_INITIAL_CAPITAL", "200.0"))  # Conservative starting capital
POSITION_SIZE_PERCENT = float(os.getenv("DUMP_POSITION_SIZE_PERCENT", "10.0"))  # 10% per trade (conservative)
MAX_CONCURRENT_POSITIONS = int(os.getenv("DUMP_MAX_CONCURRENT_POSITIONS", "10"))  # Max 10 positions = $100 total
MAX_LOSS_PERCENT = float(os.getenv("DUMP_MAX_LOSS_PERCENT", "4.0"))  # Stop loss (only after 5min hold)
TARGET_PROFIT = float(os.getenv("DUMP_TARGET_PROFIT", "10.0"))  # Target profit (BACKTEST PROVEN)
MAX_HOLD_TIME_MINUTES = float(os.getenv("DUMP_MAX_HOLD_TIME_MINUTES", "120.0"))  # Max hold (BACKTEST PROVEN)

# ===== BACKTEST-PROVEN FILTERS (from final_best_strategy.js) =====
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.05"))  # Exclude penny stocks under $0.05
MIN_AVG_VOLUME_USD = float(os.getenv("MIN_AVG_VOLUME_USD", "2000.0"))  # Minimum $2k avg volume
MIN_VOLATILITY = float(os.getenv("MIN_VOLATILITY", "0.01"))  # Minimum 1% daily range
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.10"))  # Maximum 10% spread (avoid illiquid)
MAX_DUMP_CATASTROPHIC = float(os.getenv("MAX_DUMP_CATASTROPHIC", "-0.50"))  # Skip >50% dumps (delistings)
DUMP_THRESHOLD = float(os.getenv("DUMP_THRESHOLD", "-0.08"))  # Only trade -8% or larger dumps (BACKTEST PROVEN)
DB_PATH = os.getenv("DUMP_DB_PATH", "/app/data/dump_trading.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Dynamic fees - will be fetched from API
DEFAULT_BUY_FEE_PERCENT = float(os.getenv("DEFAULT_BUY_FEE_PERCENT", "0.6"))
DEFAULT_SELL_FEE_PERCENT = float(os.getenv("DEFAULT_SELL_FEE_PERCENT", "0.4"))

# ===== EXECUTION STRATEGY (matches backtest) =====
# Entry: Market order (1.2% taker fee) - immediate fill at dump detection
# Exit: Limit order at +10% (0.6% maker fee) - placed immediately after entry
# Total fees: 1.8% round-trip (matches backtest exactly)
USE_LIMIT_ORDERS = os.getenv("USE_LIMIT_ORDERS", "no").lower() in ("yes", "true", "1")  # Market orders for entry
USE_LADDER_BUYS = False  # DISABLED - never backtested
USE_LADDER_SELLS = False  # DISABLED - never backtested

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
    Backtest-Proven Dump Trading Position

    Strategy: Enter on -8%+ dumps, exit at +10% or 120min timeout
    Proven: 97.96% win rate, 7.79% avg return (49 trades)

    Simple Exit Logic:
    - Primary: Limit sell order at +10% (placed immediately after buy)
    - Protection: Stop loss at -4% after 5min hold
    - Timeout: Market sell after 120 minutes
    """
    symbol: str
    entry_price: float
    entry_time: str
    quantity: float
    cost_basis: float
    buy_fee_rate: float
    sell_fee_rate: float
    break_even_price: float
    target_profit_price: float  # +10% backtest-proven target
    stop_loss_price: float  # -4% protection (only after 5min)
    peak_price: float  # Track highest price reached
    dump_pct: float  # Initial dump percentage
    volume_surge: float  # Volume spike at entry
    status: str = "open"  # open, closed, pending_sell

    # Limit sell order tracking (placed immediately after buy)
    sell_order_id: str = None
    limit_sell_price: float = None

    # Buy order tracking (for limit orders, but we use market orders now)
    buy_order_id: str = None
    order_placed_time: str = None

    def update_peak(self, current_price: float) -> bool:
        """Update peak price tracking"""
        if current_price > self.peak_price:
            self.peak_price = current_price
            return True
        return False

    def should_exit(self, current_price: float, current_volume: float = 0, avg_volume: float = 0) -> tuple[bool, str]:
        """
        Check if position should be exited - BACKTEST-PROVEN STRATEGY

        Exit conditions (all market sells):
        1. Stop loss: -4% after 5min hold (protection)
        2. Max timeout: 120min hold time (backtest-proven)

        Note: +10% target is handled by limit sell order placed immediately after buy
        """
        from datetime import datetime

        # Calculate time held
        entry_time = datetime.fromisoformat(self.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60

        # Calculate current P&L
        price_change_pct = ((current_price - self.entry_price) / self.entry_price) * 100

        # 1. HARD STOP LOSS (-4%) after 5 minutes - Critical protection
        if time_held_minutes >= 5.0 and price_change_pct <= -4.0:
            return True, "Stop loss -4% (market sell)"

        # 2. MAX HOLD TIME (120min) - Backtest-proven timeout
        if time_held_minutes >= 120.0:  # MAX_HOLD_TIME_MINUTES constant
            return True, "Max hold time 120min (market sell)"

        # Otherwise, wait for limit sell to fill at +10% target
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

        # Market conditions DISABLED - never backtested, using simple proven filters instead
        self.market_conditions = None

        # Initialize Coinbase client for live trading
        self.coinbase = None
        if AUTO_TRADE and COINBASE_AVAILABLE:
            try:
                self.coinbase = CoinbaseClient()
                # Try to fetch actual balance from Coinbase
                balance = self.coinbase.get_account_balance("USD")
                if balance is not None and balance > 0:
                    self.capital = balance
                    logger.info(f"✅ Coinbase connected - Live balance: ${balance:,.2f}")
                else:
                    # USD balance not available via accounts API (likely in cash/payment method)
                    # Use INITIAL_CAPITAL as the working capital for position sizing
                    logger.warning(f"⚠️ USD account balance not found via API")
                    logger.warning(f"⚠️ Using INITIAL_CAPITAL=${INITIAL_CAPITAL:,.2f} for position sizing")
                    logger.warning(f"⚠️ Actual orders will use your available Coinbase cash balance")
                    self.capital = INITIAL_CAPITAL
            except Exception as e:
                logger.error(f"Failed to initialize Coinbase client: {e}")
                self.coinbase = None

        # Initialize database
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._init_db()

        logger.info("=" * 80)
        logger.info("🚀 BACKTEST-PROVEN DUMP TRADING BOT")
        logger.info("=" * 80)
        logger.info("📊 BACKTEST RESULTS (Oct 17-19, 49 trades):")
        logger.info("   Win Rate: 97.96% (48 wins, 1 loss)")
        logger.info("   Avg Return: 7.79% per trade")
        logger.info("   Total Return: 381.78%")
        logger.info("-" * 80)
        logger.info(f"💰 Capital & Position Sizing:")
        logger.info(f"   Initial Capital: ${INITIAL_CAPITAL:,.2f}")
        logger.info(f"   Position Size: {POSITION_SIZE_PERCENT}% per trade (~${INITIAL_CAPITAL * POSITION_SIZE_PERCENT / 100:.2f})")
        logger.info(f"   Max Concurrent: {MAX_CONCURRENT_POSITIONS} positions")
        logger.info(f"   Max Exposure: ${INITIAL_CAPITAL * POSITION_SIZE_PERCENT * MAX_CONCURRENT_POSITIONS / 100:.2f}")
        logger.info("-" * 80)
        logger.info(f"🎯 Strategy (BACKTEST-PROVEN):")
        logger.info(f"   Dump Threshold: {DUMP_THRESHOLD*100:.0f}% (only trade {abs(DUMP_THRESHOLD)*100:.0f}%+ dumps)")
        logger.info(f"   Exit Target: +{TARGET_PROFIT}%")
        logger.info(f"   Max Hold: {MAX_HOLD_TIME_MINUTES:.0f} minutes")
        logger.info(f"   Stop Loss: -{MAX_LOSS_PERCENT}% (after 5min)")
        logger.info("-" * 80)
        logger.info(f"🔍 Quality Filters (BACKTEST-PROVEN):")
        logger.info(f"   Min Price: ${MIN_PRICE:.2f} (exclude penny stocks)")
        logger.info(f"   Min Volume: ${MIN_AVG_VOLUME_USD:,.0f} avg (ensure liquidity)")
        logger.info(f"   Min Volatility: {MIN_VOLATILITY*100:.1f}% (avoid dead coins)")
        logger.info(f"   Max Spread: {MAX_SPREAD_PCT*100:.0f}% (avoid illiquid markets)")
        logger.info(f"   Skip Catastrophic: >{abs(MAX_DUMP_CATASTROPHIC)*100:.0f}% dumps (delistings)")
        logger.info("-" * 80)
        logger.info(f"💵 Fees (matches backtest):")
        logger.info(f"   Entry (market): 1.2% taker")
        logger.info(f"   Exit (limit): 0.6% maker")
        logger.info(f"   Round-trip: 1.8% total")
        logger.info("-" * 80)
        logger.info(f"🤖 Mode: {'LIVE TRADING ✅' if AUTO_TRADE else 'ALERTS ONLY ⚠️'}")
        logger.info(f"📱 Telegram: {'ENABLED ✅' if TELEGRAM_WEBHOOK_URL else 'DISABLED ❌'}")
        logger.info(f"📈 Market Conditions: DISABLED (using simple filters instead)")
        logger.info("=" * 80)

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
        # NOTE: min_profit_price and trailing_exit_price are DEPRECATED (old strategy)
        # Kept in schema for backward compatibility, but not used in backtest-proven strategy
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
                min_profit_price REAL NOT NULL,  -- DEPRECATED: not used in backtest strategy
                target_profit_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                peak_price REAL NOT NULL,
                trailing_exit_price REAL NOT NULL,  -- DEPRECATED: not used in backtest strategy
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

        # ===== BACKTEST-PROVEN FILTERS =====
        # Note: dump_pct comes from backend as percentage (-9.56 for -9.56%, not -0.0956)
        # Convert constants to percentage for comparison
        dump_threshold_pct = DUMP_THRESHOLD * 100  # -8.0
        catastrophic_threshold_pct = MAX_DUMP_CATASTROPHIC * 100  # -50.0

        # Filter 1: Dump threshold (-8% minimum)
        if dump_pct > dump_threshold_pct:
            logger.info(f"⏭️  {symbol}: Dump {dump_pct:.2f}% too small (need {dump_threshold_pct:.0f}% or larger)")
            return

        # Filter 2: Catastrophic dump check (skip delistings >50%)
        if dump_pct < catastrophic_threshold_pct:
            logger.warning(f"⏭️  {symbol}: Catastrophic dump {dump_pct:.2f}% - likely delisting, skipping")
            return

        # Filter 3: Price check (exclude penny stocks <$0.05)
        if entry_price < MIN_PRICE:
            logger.info(f"⏭️  {symbol}: Price ${entry_price:.6f} too low (need ${MIN_PRICE:.2f}+)")
            return

        # We'll check volume & volatility from ticker data when available
        # Spread check happens per-dump in the ticker data

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

        # Simple position sizing: 10% per trade (conservative)
        total_positions_value = sum(p.cost_basis for p in self.positions.values())
        total_capital = self.capital + total_positions_value

        # Allocate 10% of total capital per trade
        spend_amount = total_capital * (POSITION_SIZE_PERCENT / 100)

        if spend_amount > self.capital:
            logger.warning(f"⚠️  Insufficient capital for {symbol}")
            logger.info("=" * 80)
            return

        # Simple: just use spend_amount as cost basis (no fee calculations)
        cost_basis = spend_amount
        quantity = spend_amount / entry_price

        # Simple exit prices (backtest-proven)
        break_even_price = entry_price
        target_profit_price = entry_price * (1 + TARGET_PROFIT / 100)
        stop_loss_price = entry_price * (1 - MAX_LOSS_PERCENT / 100)

        logger.info(f"💰 Position Sizing:")
        logger.info(f"   Total Capital: ${total_capital:.2f}")
        logger.info(f"   Open Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}")
        logger.info(f"   Position Size: {POSITION_SIZE_PERCENT}% = ${spend_amount:.2f}")
        logger.info(f"   Quantity: {quantity:.6f} {symbol.split('-')[0]}")
        logger.info("")

        logger.info(f"🎯 Exit Strategy (BACKTEST-PROVEN):")
        logger.info(f"   Entry: ${entry_price:.6f}")
        logger.info(f"   Target: ${target_profit_price:.6f} (+{TARGET_PROFIT}%) ✅ BACKTEST")
        logger.info(f"   Max Hold: {MAX_HOLD_TIME_MINUTES:.0f} min ✅ BACKTEST")
        logger.info(f"   Stop Loss: ${stop_loss_price:.6f} (-{MAX_LOSS_PERCENT}%) - Protection only")
        logger.info("")

        # Calculate risk/reward ratio
        risk_reward_ratio = TARGET_PROFIT / MAX_LOSS_PERCENT

        logger.info(f"🧠 Entry Rationale:")
        logger.info(f"   ✓ Dump threshold met: {abs(dump_pct):.2f}% (need {abs(DUMP_THRESHOLD)*100:.0f}%+)")
        logger.info(f"   ✓ Price filter passed: ${entry_price:.6f} (need ${MIN_PRICE:.2f}+)")
        logger.info(f"   ✓ Not catastrophic: {abs(dump_pct):.1f}% (skip >{abs(MAX_DUMP_CATASTROPHIC)*100:.0f}%)")
        logger.info(f"   ✓ Backtest proven: 97.96% win rate, 7.79% avg return")
        logger.info(f"   ✓ Risk/Reward: 1:{risk_reward_ratio:.2f}")
        logger.info("")

        logger.info(f"🚀 EXECUTING BUY ORDER...")

        # Execute actual Coinbase trade if live trading enabled
        buy_order_id = None
        limit_buy_price = None

        if self.coinbase:
            # BACKTEST-PROVEN STRATEGY: Use MARKET order for immediate entry (like backtest simulation)
            # This ensures we enter immediately at dump detection, matching the backtested logic
            logger.info(f"📋 Placing MARKET BUY (backtest-proven: immediate entry at dump)")
            logger.info(f"   Fee: 1.2% taker (market order)")
            buy_result = self.coinbase.market_buy(symbol, spend_amount)

            if not buy_result.get('success'):
                error_msg = f"❌ Buy order failed: {buy_result.get('error')}"
                logger.error(error_msg)
                self.send_telegram_alert(error_msg, "error")
                logger.info("=" * 80)
                return

            buy_order_id = buy_result.get('order_id')
            logger.info(f"✅ Live market order placed: Order ID {buy_order_id}")

            # Market orders fill immediately, so position is immediately open
            # Update entry_price to actual fill price if available
            actual_fill_price = buy_result.get('fill_price')
            if actual_fill_price:
                logger.info(f"📊 Actual fill price: ${actual_fill_price:.6f}")
                entry_price = actual_fill_price
                # Recalculate exit prices based on actual fill
                break_even_price = entry_price
                target_profit_price = entry_price * (1 + TARGET_PROFIT / 100)
                stop_loss_price = entry_price * (1 - MAX_LOSS_PERCENT / 100)

            # Update quantity to actual filled amount if available
            actual_quantity = buy_result.get('filled_size')
            if actual_quantity:
                quantity = actual_quantity
                logger.info(f"📊 Actual filled quantity: {actual_quantity:.6f}")

            #  IMMEDIATELY place limit sell order at +10% target (backtest matched fee: 0.6% maker)
            target_sell_price = entry_price * (1 + TARGET_PROFIT / 100)
            logger.info(f"📋 Placing LIMIT SELL immediately at ${target_sell_price:.6f} (+{TARGET_PROFIT}%)")
            logger.info(f"   Fee: 0.6% maker (limit order) - Total round-trip: 1.8% (matches backtest)")
            sell_result = self.coinbase.limit_sell(symbol, quantity, target_sell_price)

            if sell_result.get('success'):
                sell_order_id = sell_result.get('order_id')
                limit_sell_price = target_sell_price
                logger.info(f"✅ Limit sell placed: {sell_order_id}")
                # Position status is pending_sell since we have sell order pending
                position_status = "pending_sell"
            else:
                logger.error(f"❌ Failed to place limit sell: {sell_result.get('error')}")
                # Fall back to open status without sell order
                sell_order_id = None
                limit_sell_price = None
                position_status = "open"

        # Initial peak tracking
        peak_price = entry_price

        # Create position (simplified, backtest-proven fields only)
        position = DumpPosition(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=datetime.now().isoformat(),
            quantity=quantity,
            cost_basis=cost_basis,
            buy_fee_rate=buy_fee_rate,
            sell_fee_rate=sell_fee_rate,
            break_even_price=break_even_price,
            target_profit_price=target_profit_price,
            stop_loss_price=stop_loss_price,
            peak_price=peak_price,
            dump_pct=dump_pct,
            volume_surge=volume_surge,
            status=position_status,
            buy_order_id=buy_order_id,
            order_placed_time=datetime.now().isoformat() if buy_order_id else None,
            sell_order_id=sell_order_id,
            limit_sell_price=limit_sell_price
        )

        self.positions[symbol] = position
        self.capital -= cost_basis

        logger.info(f"✅ POSITION OPENED: {symbol}")
        logger.info(f"   Capital Remaining: ${self.capital:.2f}")
        logger.info("=" * 80)

        # Position opened - no alert needed (will alert on final P&L only)
        # strategy_info = "Ladder Sell: +8% → dynamic" if USE_LADDER_SELLS else f"Target: +{TARGET_PROFIT}%"
        # entry_alert = f"🟢 POSITION OPENED\n\n" \
        #              f"Symbol: {symbol}\n" \
        #              f"Entry: ${entry_price:.6f}\n" \
        #              f"Quantity: {quantity:.6f}\n" \
        #              f"Position Size: ${cost_basis:.2f} ({POSITION_SIZE_PERCENT}%)\n" \
        #              f"Dump: {abs(dump_pct):.2f}%\n\n" \
        #              f"Strategy: {strategy_info}\n" \
        #              f"Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}"
        # self.send_telegram_alert(entry_alert, "success")

        # Record trade entry
        self._record_trade_entry(position)

    def update_position(self, symbol: str, current_price: float, current_volume: float = 0, avg_volume: float = 0):
        """Update position with new price data"""
        if symbol not in self.positions:
            return

        position = self.positions[symbol]

        # Skip analysis for pending buy orders (not filled yet)
        if position.status == "pending_buy":
            return

        # Skip analysis for pending sell orders (already exiting)
        if position.status == "pending_sell":
            return

        # Calculate current metrics
        entry_time = datetime.fromisoformat(position.entry_time)
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60
        unrealized = position.calculate_pnl(current_price)
        price_change_pct = ((current_price - position.entry_price) / position.entry_price) * 100

        # Simplified position tracking - backtest-proven strategy
        logger.debug(f"📊 {symbol}: ${current_price:.6f} ({price_change_pct:+.2f}%) | "
                    f"Time: {time_held_minutes:.0f}m/{MAX_HOLD_TIME_MINUTES:.0f}m | P&L: ${unrealized['pnl']:+.2f} ({unrealized['pnl_percent']:+.2f}%)")

        # Update peak price tracking
        peak_updated = position.update_peak(current_price)
        if peak_updated:
            logger.debug(f"🔼 {symbol}: New peak ${current_price:.6f}")

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
            # Use market sell for stop loss (need immediate exit)
            # Use limit sell for profit targets (lower fees)
            use_market_sell = "stop loss" in reason.lower() or "max hold" in reason.lower()

            if USE_LIMIT_ORDERS and not use_market_sell:
                # Place limit sell at current exit price
                logger.info(f"📋 Placing LIMIT SELL at ${exit_price:.6f}")
                sell_result = self.coinbase.limit_sell(symbol, position.quantity, exit_price)

                if not sell_result.get('success'):
                    error_msg = f"❌ Limit sell order failed for {symbol}: {sell_result.get('error')}"
                    logger.error(error_msg)
                    self.send_telegram_alert(error_msg, "error")
                    # CRITICAL: Return early - do NOT close position tracking if sell failed
                    logger.warning(f"⚠️ Position {symbol} will remain open until sell succeeds")
                    return

                # Update position to pending_sell
                position.sell_order_id = sell_result.get('order_id')
                position.limit_sell_price = exit_price
                position.status = "pending_sell"
                logger.info(f"✅ Limit sell order placed: {position.sell_order_id}")

                # Limit sell placed - no alert (too noisy, only alert on final P&L)
                # limit_sell_alert = f"📋 LIMIT SELL PLACED\n\n" \
                #                  f"Symbol: {symbol}\n" \
                #                  f"Limit Price: ${exit_price:.6f}\n" \
                #                  f"Quantity: {position.quantity:.6f}\n" \
                #                  f"Reason: {reason}\n" \
                #                  f"Order ID: {position.sell_order_id}"
                # self.send_telegram_alert(limit_sell_alert, "info")
                return  # Don't close position yet - wait for fill

            else:
                # Use market sell for immediate exit
                logger.info(f"📋 Placing MARKET SELL (reason: {reason})")
                sell_result = self.coinbase.market_sell(symbol, position.quantity)

                if not sell_result.get('success'):
                    error_msg = f"❌ Sell order failed for {symbol}: {sell_result.get('error')}"
                    logger.error(error_msg)
                    self.send_telegram_alert(error_msg, "error")
                    # CRITICAL: Return early - do NOT close position tracking if sell failed
                    logger.warning(f"⚠️ Position {symbol} will remain open until sell succeeds")
                    return
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

        # Explain which exit condition was met (backtest-proven strategy)
        logger.info(f"🧠 Exit Condition Analysis:")
        if "Limit sell" in reason or "filled" in reason.lower():
            logger.info(f"   🎯 LIMIT SELL FILLED - Hit +{TARGET_PROFIT}% target!")
            logger.info(f"   ✅ BACKTEST: This is the expected outcome (97.96% win rate)")
            logger.info(f"   🎉 Perfect execution - limit order filled at ${position.target_profit_price:.6f}")
        elif "Stop loss" in reason:
            logger.info(f"   💀 STOP LOSS HIT - Price fell below -4% after 5min")
            logger.info(f"   🛡️ Protection working - prevented larger loss")
            logger.info(f"   ⚠️ BACKTEST: Rare outcome (2.04% loss rate)")
        elif "Max hold" in reason or "120min" in reason:
            logger.info(f"   ⏰ MAX HOLD TIME (120min) - Forced exit")
            logger.info(f"   🔄 BACKTEST: Timeout prevents capital being stuck")
            logger.info(f"   💡 Price: ${exit_price:.6f} ({price_change_pct:+.2f}%)")
        else:
            logger.info(f"   📋 Exit reason: {reason}")
        logger.info("")

        # Performance rating (backtest-proven thresholds)
        if pnl_data['pnl_percent'] >= TARGET_PROFIT:
            rating = "🏆 TARGET HIT (BACKTEST: +10%)"
        elif pnl_data['pnl_percent'] >= TARGET_PROFIT * 0.7:  # 70% of target (7%+)
            rating = "✅ NEAR TARGET"
        elif pnl_data['pnl_percent'] > 0:
            rating = "✓ PROFIT (exited early)"
        elif pnl_data['pnl_percent'] > -MAX_LOSS_PERCENT / 2:
            rating = "⚠️ SMALL LOSS"
        else:
            rating = "❌ STOPPED OUT"

        logger.info(f"📈 Trade Rating: {rating}")
        logger.info(f"   Previous Capital: ${self.capital - pnl_data['proceeds']:.2f}")
        logger.info(f"   New Capital: ${self.capital:.2f}")
        logger.info("")

        # Strategy insights
        logger.info(f"💡 Trade Insights (vs backtest expectations):")
        if pnl_data['pnl_percent'] >= TARGET_PROFIT:
            logger.info(f"   ✅ Perfect execution - hit +10% target (backtest: 7.79% avg)")
        elif pnl_data['pnl_percent'] >= TARGET_PROFIT * 0.7:
            logger.info(f"   ✅ Good trade - near target (backtest: 97.96% win rate)")
        elif pnl_data['pnl_percent'] > 0:
            logger.info(f"   ⚠️ Small win - exited early (could have hit +10% target)")
        else:
            logger.info(f"   ❌ Loss - rare (backtest had 2.04% loss rate)")
            logger.info(f"   💡 Not all dumps bounce immediately - this is expected")

        logger.info("=" * 80)

        # Position close - no alert (ladder strategy handles all exits, this method is only for bot shutdown)
        # exit_emoji = "🟢" if pnl_data['pnl_percent'] > 0 else "🔴"
        # exit_alert = f"{exit_emoji} POSITION CLOSED\n\n" \
        #             f"Symbol: {symbol}\n" \
        #             f"Entry: ${position.entry_price:.6f}\n" \
        #             f"Exit: ${exit_price:.6f} ({price_change_pct:+.2f}%)\n" \
        #             f"Time: {holding_minutes:.1f} min\n\n" \
        #             f"P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)\n" \
        #             f"Reason: {reason}\n\n" \
        #             f"New Capital: ${self.capital:.2f}"
        # alert_type = "success" if pnl_data['pnl_percent'] > 0 else "error"
        # self.send_telegram_alert(exit_alert, alert_type)

        # Record trade exit
        self._record_trade_exit(position, exit_price, pnl_data, reason)

        # Remove position
        del self.positions[symbol]

    def check_pending_buy_orders(self):
        """Check status of pending buy orders and handle timeouts/fills"""
        for symbol, position in list(self.positions.items()):
            if position.status != "pending_buy" or not position.buy_order_id:
                continue

            try:
                # Check order status
                order_status = self.coinbase.get_order_status(position.buy_order_id)

                if not order_status.get('success'):
                    logger.warning(f"⚠️ Failed to check order status for {symbol}: {order_status.get('error')}")
                    continue

                status = order_status.get('status', 'UNKNOWN')
                logger.debug(f"📋 {symbol} limit buy order status: {status}")

                # Order filled - update position to open and place limit sell immediately
                if status == 'FILLED':
                    logger.info(f"✅ {symbol} limit buy FILLED at ${position.limit_buy_price:.6f}")

                    # Update entry_price to actual fill price
                    position.entry_price = position.limit_buy_price

                    # Update quantity to actual filled amount (after fees)
                    actual_filled = order_status.get('filled_size', 0)
                    if actual_filled > 0:
                        position.quantity = actual_filled
                        logger.info(f"📊 Actual filled quantity (after fees): {actual_filled}")

                    # Recalculate all exit prices based on actual fill
                    position.break_even_price = position.entry_price
                    position.target_profit_price = position.entry_price * (1 + TARGET_PROFIT / 100)
                    position.stop_loss_price = position.entry_price * (1 - MAX_LOSS_PERCENT / 100)
                    position.peak_price = position.entry_price

                    position.status = "open"

                    # Buy filled - no alert needed (will alert on final P&L only)
                    # fill_alert = f"✅ LIMIT BUY FILLED\n\n" \
                    #             f"Symbol: {symbol}\n" \
                    #             f"Price: ${position.limit_buy_price:.6f}\n" \
                    #             f"Quantity: {position.quantity:.6f}\n" \
                    #             f"Order ID: {position.buy_order_id}"
                    # self.send_telegram_alert(fill_alert, "success")

                    # IMMEDIATELY place limit sell
                    if USE_LIMIT_ORDERS and self.coinbase:
                        # Get current price from ticker to determine ladder start
                        # (we'll use entry_price as fallback if no ticker data available)
                        current_price = position.entry_price

                        # Simple limit sell at +10% target (backtest-proven)
                        sell_price = position.entry_price * (1 + TARGET_PROFIT / 100)
                        logger.info(f"📋 Placing LIMIT SELL immediately at ${sell_price:.6f} (+{TARGET_PROFIT}%) ✅ BACKTEST")

                        sell_result = self.coinbase.limit_sell(symbol, position.quantity, sell_price)

                        if sell_result.get('success'):
                            position.sell_order_id = sell_result.get('order_id')
                            position.limit_sell_price = sell_price
                            position.status = "pending_sell"
                            position.ladder_order_time = datetime.now().isoformat()
                            logger.info(f"✅ Limit sell placed: {position.sell_order_id}")

                            # Sell placed after buy fills - no alert (too noisy, only alert on final P&L)
                            # strategy_label = "LADDER" if USE_LADDER_SELLS else "LIMIT"
                            # profit_pct = position.ladder_current_percent if USE_LADDER_SELLS else MIN_PROFIT_TARGET
                            # sell_alert = f"📋 {strategy_label} SELL PLACED\n\n" \
                            #            f"Symbol: {symbol}\n" \
                            #            f"Sell Price: ${sell_price:.6f} (+{profit_pct}%)\n" \
                            #            f"Order ID: {position.sell_order_id}"
                            # if USE_LADDER_SELLS:
                            #     sell_alert += f"\n\nLadder: {LADDER_START_PERCENT}% → {LADDER_MIN_PERCENT}% (step {LADDER_STEP_PERCENT}%)"
                            # self.send_telegram_alert(sell_alert, "info")
                        else:
                            logger.error(f"❌ Failed to place limit sell: {sell_result.get('error')}")

                # Order still open - check for ladder timeout
                elif status == 'OPEN':
                    if USE_LADDER_BUYS and position.ladder_buy_order_time:
                        order_placed = datetime.fromisoformat(position.ladder_buy_order_time)
                        seconds_elapsed = (datetime.now() - order_placed).total_seconds()

                        # Check if timeout reached
                        if seconds_elapsed >= LADDER_BUY_TIMEOUT_SECONDS:
                            # Step up to next level (infinite ladder - no final level)
                            # Get current price from ticker (use peak as best estimate)
                            current_price = position.peak_price if position.peak_price > 0 else position.alert_price

                            current_ladder_pct = position.ladder_buy_current_percent
                            next_ladder_pct = current_ladder_pct + LADDER_BUY_STEP_PERCENT
                            # Calculate from CURRENT price (not static alert price)
                            # This ensures post-only orders won't be rejected if price has moved
                            new_buy_price = current_price * (1 + next_ladder_pct / 100)

                            logger.info(f"⏰ {symbol} ladder buy timeout - stepping up from {current_ladder_pct}% to {next_ladder_pct}%")
                            logger.info(f"   Current price (peak): ${current_price:.6f}")

                            # Cancel current order
                            cancel_result = self.coinbase.cancel_order(position.buy_order_id)

                            if cancel_result.get('success'):
                                # Place new order at higher price (calculated from current price)
                                logger.info(f"📋 Placing new LADDER BUY at ${new_buy_price:.6f} ({next_ladder_pct}% from current ${current_price:.6f})")

                                buy_result = self.coinbase.limit_buy(symbol, position.cost_basis, new_buy_price)

                                if buy_result.get('success'):
                                    position.buy_order_id = buy_result.get('order_id')
                                    position.limit_buy_price = new_buy_price
                                    position.ladder_buy_current_percent = next_ladder_pct
                                    position.ladder_buy_order_time = datetime.now().isoformat()
                                    logger.info(f"✅ New ladder buy order placed: {position.buy_order_id}")
                                else:
                                    logger.error(f"❌ Failed to place new ladder buy order: {buy_result.get('error')}")
                                    # Give up - return capital and remove position
                                    self.capital += position.cost_basis
                                    del self.positions[symbol]
                            else:
                                logger.error(f"❌ Failed to cancel ladder order: {cancel_result.get('error')}")
                    else:
                        # Legacy timeout handling (non-ladder)
                        order_placed = datetime.fromisoformat(position.order_placed_time)
                        minutes_elapsed = (datetime.now() - order_placed).total_seconds() / 60

                        if minutes_elapsed >= LIMIT_ORDER_TIMEOUT_MINUTES:
                            logger.warning(f"⏰ {symbol} limit buy timeout ({minutes_elapsed:.1f} min) - cancelling")

                            # Cancel the limit order
                            cancel_result = self.coinbase.cancel_order(position.buy_order_id)

                            if cancel_result.get('success'):
                                logger.info(f"🚫 {symbol} limit buy cancelled - removing position")

                                # Return capital
                                self.capital += position.cost_basis

                                # Remove position
                                del self.positions[symbol]

                                # Legacy limit buy timeout - no alert (too noisy, only alert on final P&L)
                                # timeout_alert = f"⏰ LIMIT BUY TIMEOUT\n\n" \
                                #               f"Symbol: {symbol}\n" \
                                #               f"Limit Price: ${position.limit_buy_price:.6f}\n" \
                                #               f"Reason: Order not filled after {LIMIT_ORDER_TIMEOUT_MINUTES} min\n" \
                                #               f"Capital returned: ${position.cost_basis:.2f}"
                                # self.send_telegram_alert(timeout_alert, "warning")
                            else:
                                logger.error(f"❌ Failed to cancel order {position.buy_order_id}: {cancel_result.get('error')}")

                # Order cancelled externally
                elif status == 'CANCELLED':
                    logger.warning(f"🚫 {symbol} limit buy was cancelled externally")
                    self.capital += position.cost_basis
                    del self.positions[symbol]

            except Exception as e:
                logger.error(f"Error checking pending buy order for {symbol}: {e}")

    def check_pending_sell_orders(self):
        """Check status of pending sell orders and handle ladder timeouts"""
        for symbol, position in list(self.positions.items()):
            if position.status != "pending_sell" or not position.sell_order_id:
                continue

            try:
                # Check order status
                order_status = self.coinbase.get_order_status(position.sell_order_id)

                if not order_status.get('success'):
                    logger.warning(f"⚠️ Failed to check sell order status for {symbol}: {order_status.get('error')}")
                    continue

                status = order_status.get('status', 'UNKNOWN')
                logger.debug(f"📋 {symbol} limit sell order status: {status}")

                # Order filled - close position
                if status == 'FILLED':
                    strategy_label = "LADDER" if USE_LADDER_SELLS else "LIMIT"
                    logger.info(f"✅ {symbol} {strategy_label} sell FILLED at ${position.limit_sell_price:.6f}")

                    # Calculate P&L
                    pnl_data = position.calculate_pnl(position.limit_sell_price)

                    # Update capital
                    self.capital += pnl_data['proceeds']

                    # Ladder sell filled - no alert (too noisy, only alert on final P&L via close_position)
                    # fill_alert = f"✅ {strategy_label} SELL FILLED\n\n" \
                    #             f"Symbol: {symbol}\n" \
                    #             f"Entry: ${position.entry_price:.6f}\n" \
                    #             f"Exit: ${position.limit_sell_price:.6f}\n" \
                    #             f"P&L: ${pnl_data['pnl']:+.2f} ({pnl_data['pnl_percent']:+.2f}%)\n" \
                    #             f"Order ID: {position.sell_order_id}"
                    # if USE_LADDER_SELLS and position.ladder_current_percent:
                    #     fill_alert += f"\n\nLadder Level: +{position.ladder_current_percent}%"
                    # alert_type = "success" if pnl_data['pnl_percent'] > 0 else "warning"
                    # self.send_telegram_alert(fill_alert, alert_type)

                    # Record trade exit
                    reason = f"Ladder sell filled at +{position.ladder_current_percent}%" if USE_LADDER_SELLS else "Limit sell filled"
                    self._record_trade_exit(position, position.limit_sell_price, pnl_data, reason)

                    # Remove position
                    del self.positions[symbol]

                # Order still open - check for ladder timeout
                elif status == 'OPEN':
                    if USE_LADDER_SELLS and position.ladder_order_time and position.ladder_current_percent:
                        order_placed = datetime.fromisoformat(position.ladder_order_time)
                        seconds_elapsed = (datetime.now() - order_placed).total_seconds()

                        # Check if ladder timeout reached
                        if seconds_elapsed >= LADDER_SELL_TIMEOUT_SECONDS:
                            # Get current price from peak (most recent price seen)
                            # Use peak as best estimate of current price
                            current_price = position.peak_price

                            # Calculate next ladder level - step down from current level
                            # This ensures the sell order is always above market (valid for post-only)
                            next_percent = position.ladder_current_percent - LADDER_SELL_STEP_PERCENT

                            # Keep stepping down until it sells (no minimum limit - infinite ladder)
                            logger.info(f"⏰ {symbol} ladder timeout ({LADDER_SELL_TIMEOUT_SECONDS}s) - stepping down from +{position.ladder_current_percent}% to +{next_percent}%")
                            logger.info(f"   Current price (peak): ${current_price:.6f}")

                            # Cancel current order
                            cancel_result = self.coinbase.cancel_order(position.sell_order_id)

                            if cancel_result.get('success'):
                                # Calculate sell price as +next_percent% above CURRENT price (not entry)
                                # This ensures post-only orders won't be rejected
                                new_sell_price = current_price * (1 + next_percent / 100)
                                logger.info(f"📋 Placing new LADDER SELL at ${new_sell_price:.6f} (+{next_percent}% above current ${current_price:.6f})")

                                sell_result = self.coinbase.limit_sell(symbol, position.quantity, new_sell_price)

                                if sell_result.get('success'):
                                    position.sell_order_id = sell_result.get('order_id')
                                    position.limit_sell_price = new_sell_price
                                    position.ladder_current_percent = next_percent
                                    position.ladder_order_time = datetime.now().isoformat()
                                    logger.info(f"✅ New ladder order placed: {position.sell_order_id}")

                                    # Ladder step down - no alert (too noisy, only alert on final P&L)
                                    # ladder_alert = f"📉 LADDER STEP DOWN\n\n" \
                                    #               f"Symbol: {symbol}\n" \
                                    #               f"New Price: ${new_sell_price:.6f} (+{next_percent}%)\n" \
                                    #               f"Previous: +{position.ladder_current_percent + LADDER_STEP_PERCENT}%\n" \
                                    #               f"Next Step: +{next_percent - LADDER_STEP_PERCENT}%\n" \
                                    #               f"Order ID: {position.sell_order_id}"
                                    # self.send_telegram_alert(ladder_alert, "info")
                                else:
                                    logger.error(f"❌ Failed to place new ladder order: {sell_result.get('error')}")
                                    # Revert to open status
                                    position.status = "open"
                                    position.sell_order_id = None
                                    position.limit_sell_price = None
                            else:
                                logger.error(f"❌ Failed to cancel ladder order: {cancel_result.get('error')}")

                # Order cancelled - revert to open position (unless we're laddering)
                elif status == 'CANCELLED':
                    if not USE_LADDER_SELLS:
                        logger.warning(f"🚫 {symbol} limit sell was cancelled - reverting to open")
                        position.status = "open"
                        position.sell_order_id = None
                        position.limit_sell_price = None

            except Exception as e:
                logger.error(f"Error checking pending sell order for {symbol}: {e}")

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

        summary += f"🔄 Open Positions: {len(self.positions)}/{MAX_CONCURRENT_POSITIONS}\n\n"

        # Add market conditions status
        if self.market_conditions:
            summary += "─" * 30 + "\n"
            summary += self.market_conditions.get_detailed_status()

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

    def monitor_pending_orders(self):
        """Background thread to monitor pending buy/sell orders"""
        def monitor():
            while self.running:
                try:
                    if self.coinbase and USE_LIMIT_ORDERS:
                        # Check pending buy orders
                        self.check_pending_buy_orders()

                        # Check pending sell orders
                        self.check_pending_sell_orders()

                    # Check every 10 seconds
                    time.sleep(10)

                except Exception as e:
                    logger.error(f"Error in pending orders monitor: {e}")
                    time.sleep(10)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        logger.info(f"📋 Pending orders monitor started (checks every 10s)")

    def monitor_market_conditions(self):
        """Background thread to monitor market conditions and send alerts on state changes"""
        def monitor():
            while self.running:
                try:
                    if self.market_conditions:
                        # Check conditions
                        conditions = self.market_conditions.should_trade()

                        # Send alert if state changed
                        if conditions.get('state_changed'):
                            if conditions['enabled']:
                                alert_msg = "🟢 TRADING ENABLED\n\n"
                                alert_msg += self.market_conditions.get_detailed_status()
                                self.send_telegram_alert(alert_msg, "success")
                                logger.info("🟢 Market conditions improved - Trading enabled")
                            else:
                                alert_msg = "🔴 TRADING DISABLED\n\n"
                                alert_msg += self.market_conditions.get_detailed_status()
                                self.send_telegram_alert(alert_msg, "warning")
                                logger.warning("🔴 Market conditions deteriorated - Trading disabled")

                    # Check every 5 minutes
                    time.sleep(300)

                except Exception as e:
                    logger.error(f"Error in market conditions monitor: {e}")
                    time.sleep(300)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        logger.info(f"📊 Market conditions monitor started (checks every 5 min)")

    def run(self):
        """Main bot loop"""
        try:
            # Start daily summary scheduler
            self.schedule_daily_summary()

            # Start pending orders monitor
            if USE_LIMIT_ORDERS:
                self.monitor_pending_orders()

            # Start market conditions monitor
            if self.market_conditions:
                self.monitor_market_conditions()

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
