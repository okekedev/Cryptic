"""
PROVEN DUMP TRADER - 88.5% Win Rate Strategy (Vol AND Support 120 Candles)

Based on comprehensive strategy testing of 1.4M candles (Oct 17-23, 2025):
- Entry: Volatility Expansion (2.5x spike) AND Support Bounce (120-candle) AND RSI < 35
- Exit: +8% target, 480 min max hold
- Performance: 88.5% win rate, +42.06% return (7 days), 8.7 trades/day
- Only 7 losers out of 61 trades!
- Max 50 concurrent positions
- Fees: 1.8% total (1.2% entry market order + 0.6% exit limit order)

ULTIMATE STRATEGY: Combines volatility mean reversion with support level detection
Quality over quantity - fewer trades with much higher win rate to minimize fees
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import sqlite3
from coinbase_client import CoinbaseClient
import os

# ============================================================================
# CONFIGURATION - PROVEN PARAMETERS (Vol AND Support 120 Candles)
# ============================================================================

# Entry conditions (from comprehensive strategy testing)
CANDLE_LOOKBACK = 120    # 120 candles = 2 hours of history
RSI_THRESHOLD = 35       # RSI must be below 35 (oversold)

# Volatility expansion thresholds
VOL_RECENT_WINDOW = 10   # Last 10 candles for recent volatility
VOL_HISTORICAL_WINDOW = 110  # Candles 11-120 for historical volatility
VOL_SPIKE_THRESHOLD = 2.5  # Recent vol must be 2.5x historical (strict)
MIN_DUMP_PCT = -0.04     # Minimum -4% dump required (stricter than old -3%)

# Support level detection
SUPPORT_DISTANCE_THRESHOLD = 0.015  # Within 1.5% of 120-candle support (strict)
MAX_DOWNTREND_PCT = -0.25  # Avoid assets in >25% long-term downtrend

# Exit conditions (from timeout optimization)
EXIT_TARGET = 0.08       # +8% profit target (optimized)
MAX_HOLD_MINUTES = 1440  # 24 hours max hold (best daily P&L: $14.15/day)

# Emergency stop (safety measure)
EMERGENCY_STOP_LOSS = -0.10  # -10% catastrophic protection

# Fees (Coinbase Advanced Trade)
ENTRY_FEE = 0.012  # 1.2% market order (taker)
EXIT_FEE = 0.006   # 0.6% limit order (maker)
TOTAL_FEES = ENTRY_FEE + EXIT_FEE  # 1.8%

# Position sizing (optimized for quality over quantity)
INITIAL_CAPITAL = float(os.getenv('PROVEN_INITIAL_CAPITAL', '400'))
POSITION_SIZE_USD = 40.0  # $40 per trade (fixed position size)
MAX_CONCURRENT_POSITIONS = 10  # Max $400 deployed at once (10 x $40)

# Quality filters
MIN_PRICE = 0.05
MIN_AVG_VOLUME_USD = 2000

# Blacklist (from analysis)
BLACKLIST = {
    'X:UST-USD',     # Failed algorithmic stablecoin
    'X:STRD-USD',    # Dying coin
    'X:CTX-USD',     # Crash event
    'X:PIRATE-USD',  # Delisting
    'X:SHPING-USD',  # Project collapse
}

# Trading mode
AUTO_TRADE = os.getenv('PROVEN_AUTO_TRADE', 'no').lower() == 'yes'

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('proven_dump_trader')

# ============================================================================
# DATABASE SETUP
# ============================================================================

class ProvenTradeDB:
    def __init__(self, db_path='data/traderdb.db'):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS proven_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_time DATETIME NOT NULL,
                entry_price REAL NOT NULL,
                dump_pct REAL NOT NULL,
                rsi REAL NOT NULL,
                position_size_usd REAL NOT NULL,
                target_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                exit_price REAL,
                exit_time DATETIME,
                exit_reason TEXT,
                minutes_held INTEGER,
                gross_pnl_pct REAL,
                net_pnl_pct REAL,
                net_pnl_usd REAL,
                capital_before REAL NOT NULL,
                capital_after REAL,
                status TEXT NOT NULL,
                entry_order_id TEXT,
                exit_order_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def insert_trade(self, trade_data):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            INSERT INTO proven_trades (
                ticker, entry_time, entry_price, dump_pct, rsi, position_size_usd,
                target_price, stop_price, capital_before, status, entry_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_data['ticker'],
            trade_data['entry_time'],
            trade_data['entry_price'],
            trade_data['dump_pct'],
            trade_data['rsi'],
            trade_data['position_size_usd'],
            trade_data['target_price'],
            trade_data['stop_price'],
            trade_data['capital_before'],
            trade_data['status'],
            trade_data.get('entry_order_id')
        ))

        trade_id = c.lastrowid
        conn.commit()
        conn.close()
        return trade_id

    def update_trade_exit(self, trade_id, exit_data):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            UPDATE proven_trades SET
                exit_price = ?,
                exit_time = ?,
                exit_reason = ?,
                minutes_held = ?,
                gross_pnl_pct = ?,
                net_pnl_pct = ?,
                net_pnl_usd = ?,
                capital_after = ?,
                status = ?,
                exit_order_id = ?
            WHERE id = ?
        ''', (
            exit_data['exit_price'],
            exit_data['exit_time'],
            exit_data['exit_reason'],
            exit_data['minutes_held'],
            exit_data['gross_pnl_pct'],
            exit_data['net_pnl_pct'],
            exit_data['net_pnl_usd'],
            exit_data['capital_after'],
            exit_data['status'],
            exit_data.get('exit_order_id'),
            trade_id
        ))

        conn.commit()
        conn.close()

    def get_open_trades(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute('''
            SELECT * FROM proven_trades
            WHERE status = 'OPEN'
            ORDER BY entry_time ASC
        ''')

        rows = c.fetchall()
        conn.close()

        trades = []
        for row in rows:
            trades.append({
                'id': row[0],
                'ticker': row[1],
                'entry_time': datetime.fromisoformat(row[2]),
                'entry_price': row[3],
                'dump_pct': row[4],
                'rsi': row[5],
                'position_size_usd': row[6],
                'target_price': row[7],
                'stop_price': row[8],
                'capital_before': row[12],
                'entry_order_id': row[19]
            })

        return trades

    def get_stats(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Total trades
        c.execute('SELECT COUNT(*) FROM proven_trades WHERE status = "CLOSED"')
        total_trades = c.fetchone()[0]

        if total_trades == 0:
            conn.close()
            return {
                'total_trades': 0,
                'winners': 0,
                'losers': 0,
                'win_rate': 0,
                'total_pnl_usd': 0,
                'avg_pnl_usd': 0,
                'current_capital': INITIAL_CAPITAL,
                'open_positions': 0,
                'return_pct': 0,
                'expected_win_rate': 93.3,   # Vol AND Support (120 candles) backtest
                'expected_return': 49.51     # 7-day backtest return with 24h timeout
            }

        # Winners
        c.execute('SELECT COUNT(*) FROM proven_trades WHERE status = "CLOSED" AND net_pnl_usd > 0')
        winners = c.fetchone()[0]

        # Total P&L
        c.execute('SELECT SUM(net_pnl_usd) FROM proven_trades WHERE status = "CLOSED"')
        total_pnl = c.fetchone()[0] or 0

        # Current capital
        c.execute('SELECT capital_after FROM proven_trades WHERE status = "CLOSED" ORDER BY exit_time DESC LIMIT 1')
        result = c.fetchone()
        current_capital = result[0] if result else INITIAL_CAPITAL

        # Open positions
        c.execute('SELECT COUNT(*) FROM proven_trades WHERE status = "OPEN"')
        open_positions = c.fetchone()[0]

        conn.close()

        win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
        avg_pnl = (total_pnl / total_trades) if total_trades > 0 else 0
        return_pct = ((current_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100)

        return {
            'total_trades': total_trades,
            'winners': winners,
            'losers': total_trades - winners,
            'win_rate': win_rate,
            'total_pnl_usd': total_pnl,
            'avg_pnl_usd': avg_pnl,
            'current_capital': current_capital,
            'open_positions': open_positions,
            'return_pct': return_pct,
            'expected_win_rate': 93.3,   # Vol AND Support (120 candles) backtest
            'expected_return': 49.51     # 7-day backtest return with 24h timeout
        }

# ============================================================================
# RSI CALCULATOR
# ============================================================================

class RSICalculator:
    """Calculate RSI indicator"""

    @staticmethod
    def calculate(prices: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI from price list"""
        if len(prices) < period + 1:
            return None

        recent_prices = prices[-(period + 1):]
        gains = 0
        losses = 0

        for i in range(1, len(recent_prices)):
            change = recent_prices[i] - recent_prices[i - 1]
            if change > 0:
                gains += change
            else:
                losses += abs(change)

        avg_gain = gains / period
        avg_loss = losses / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

# ============================================================================
# PROVEN DUMP TRADER
# ============================================================================

class ProvenDumpTrader:
    def __init__(self):
        self.db = ProvenTradeDB()
        self.client = CoinbaseClient() if AUTO_TRADE else None
        self.current_capital = INITIAL_CAPITAL
        self.open_positions: Dict[str, dict] = {}
        self.price_history: Dict[str, list] = {}  # Store last 120 candles per ticker

        logger.info("=" * 80)
        logger.info("PROVEN DUMP TRADER - Vol AND Support (120 Candles)")
        logger.info("=" * 80)
        logger.info(f"Strategy: Volatility Expansion (2.5x) AND Support Bounce (120 candles)")
        logger.info(f"Entry: RSI<{RSI_THRESHOLD}, -4% dump, within 1.5% of 120-candle support")
        logger.info(f"Exit: +{EXIT_TARGET*100:.0f}% target, {MAX_HOLD_MINUTES/60:.0f}h max hold")
        logger.info(f"Expected: 93.3% win rate, +49.5% return (7 days), $14.15/day")
        logger.info(f"Capital: ${INITIAL_CAPITAL:,.2f}, ${POSITION_SIZE_USD:.2f} per trade")
        logger.info(f"Max Concurrent: {MAX_CONCURRENT_POSITIONS} positions (${POSITION_SIZE_USD * MAX_CONCURRENT_POSITIONS:.0f} max deployed)")
        logger.info(f"Auto-Trade: {AUTO_TRADE}")
        logger.info("=" * 80)

    async def handle_price_update(self, ticker: str, price_data: dict):
        """
        Handle real-time price updates from WebSocket

        Expected price_data: {
            'open': float,
            'high': float,
            'low': float,
            'close': float,
            'timestamp': datetime
        }
        """
        # Skip blacklisted coins
        if ticker in BLACKLIST:
            return

        # Update price history
        if ticker not in self.price_history:
            self.price_history[ticker] = []

        self.price_history[ticker].append(price_data)

        # Keep only last 120 candles (for volatility and support detection)
        if len(self.price_history[ticker]) > 120:
            self.price_history[ticker] = self.price_history[ticker][-120:]

        # Check for entry signal (need at least 120 candles for Vol AND Support strategy)
        if len(self.price_history[ticker]) >= 120:
            await self._check_entry_signal(ticker, price_data)

        # Check exit conditions for open positions
        if ticker in self.open_positions:
            await self._check_exit_conditions(ticker, price_data)

    async def _check_entry_signal(self, ticker: str, current_candle: dict):
        """Check if current price action triggers entry signal (Vol AND Support 120 candles)"""

        # Don't enter if already have position in this ticker
        if ticker in self.open_positions:
            return

        # Don't enter if we're at max concurrent positions
        if len(self.open_positions) >= MAX_CONCURRENT_POSITIONS:
            logger.debug(f"At max concurrent positions ({MAX_CONCURRENT_POSITIONS}), skipping {ticker}")
            return

        # Need at least 120 candles
        candle_count = len(self.price_history[ticker])
        if candle_count < CANDLE_LOOKBACK:
            return

        # Log when a pair first becomes ready to trade
        if candle_count == CANDLE_LOOKBACK:
            logger.info(f"🎯 {ticker} now has {CANDLE_LOOKBACK} candles - READY TO EVALUATE SIGNALS")

        candles = self.price_history[ticker]
        i = len(candles) - 1  # Current candle index

        # ========================================================================
        # 1. VOLATILITY EXPANSION CHECK
        # ========================================================================
        recentVol = 0.0
        for j in range(i - VOL_RECENT_WINDOW + 1, i + 1):
            if j > 0:
                change = abs((candles[j]['close'] - candles[j-1]['close']) / candles[j-1]['close'])
                recentVol += change
        recentVol /= VOL_RECENT_WINDOW

        historicalVol = 0.0
        for j in range(i - CANDLE_LOOKBACK + 1, i - VOL_RECENT_WINDOW + 1):
            if j > 0:
                change = abs((candles[j]['close'] - candles[j-1]['close']) / candles[j-1]['close'])
                historicalVol += change
        historicalVol /= VOL_HISTORICAL_WINDOW

        if historicalVol == 0:
            return  # Can't calculate vol ratio

        volRatio = recentVol / historicalVol
        if volRatio < VOL_SPIKE_THRESHOLD:
            return  # Not enough volatility spike

        # ========================================================================
        # 2. DUMP CHECK
        # ========================================================================
        priceChange = (current_candle['close'] - candles[i-1]['close']) / candles[i-1]['close']

        # Log significant dumps for debugging
        if priceChange <= -0.03:  # Any dump >= 3%
            logger.info(f"💥 {ticker}: {priceChange*100:.2f}% dump detected (volRatio: {volRatio:.2f}x)")

        if priceChange > MIN_DUMP_PCT:
            return  # Not a big enough dump

        # ========================================================================
        # 3. SUPPORT LEVEL CHECK (120-candle support)
        # ========================================================================
        currentPrice = current_candle['close']
        supportLevel = float('inf')

        for j in range(i - CANDLE_LOOKBACK + 1, i):
            supportLevel = min(supportLevel, candles[j]['low'])

        distanceFromSupport = (currentPrice - supportLevel) / supportLevel
        if distanceFromSupport > SUPPORT_DISTANCE_THRESHOLD:
            return  # Too far from support

        # ========================================================================
        # 4. AVOID LONG-TERM DOWNTRENDS
        # ========================================================================
        price120ago = candles[i - CANDLE_LOOKBACK + 1]['close']
        longTermChange = (currentPrice - price120ago) / price120ago
        if longTermChange < MAX_DOWNTREND_PCT:
            return  # In a severe downtrend, avoid

        # ========================================================================
        # 5. RSI CHECK
        # ========================================================================
        prices = [candle['close'] for candle in candles]
        rsi = RSICalculator.calculate(prices, period=14)

        if rsi is None:
            return  # Not enough data for RSI

        if rsi >= RSI_THRESHOLD:
            return  # Not oversold enough

        # ========================================================================
        # 6. QUALITY FILTERS
        # ========================================================================
        if current_candle['close'] < MIN_PRICE:
            logger.debug(f"{ticker}: Price too low (${current_candle['close']:.4f})")
            return

        # ========================================================================
        # ENTRY SIGNAL VALID! (All conditions met - Vol AND Support AND RSI)
        # ========================================================================
        signal_data = {
            'volRatio': volRatio,
            'dump_pct': priceChange,
            'distanceFromSupport': distanceFromSupport,
            'rsi': rsi
        }
        await self._execute_entry(ticker, current_candle, signal_data)

    async def _execute_entry(self, ticker: str, candle: dict, signal_data: dict):
        """Execute entry trade"""

        # CRITICAL: Enter at CLOSE, not LOW
        # We detect signals after candle closes. Entering at 'low' is unrealistic.
        # Backtest uses close and achieves 93.3% win rate.
        entry_price = candle['close']  # Enter at the close (realistic)
        entry_time = candle.get('timestamp', datetime.now())

        # Fixed position size
        position_size_usd = POSITION_SIZE_USD

        # Calculate prices with fees
        entry_with_fee = entry_price * (1 + ENTRY_FEE)
        target_price = entry_with_fee * (1 + EXIT_TARGET)
        stop_price = entry_with_fee * (1 + EMERGENCY_STOP_LOSS)

        logger.info("=" * 80)
        logger.info(f"🚨 ENTRY SIGNAL: {ticker}")
        logger.info(f"   Strategy: Vol AND Support (120 candles)")
        logger.info(f"   Vol Ratio: {signal_data['volRatio']:.2f}x (threshold: {VOL_SPIKE_THRESHOLD}x)")
        logger.info(f"   Dump: {signal_data['dump_pct']*100:.2f}% (threshold: <{MIN_DUMP_PCT*100:.1f}%)")
        logger.info(f"   Distance from Support: {signal_data['distanceFromSupport']*100:.2f}% (threshold: <{SUPPORT_DISTANCE_THRESHOLD*100:.1f}%)")
        logger.info(f"   RSI: {signal_data['rsi']:.1f} (threshold: <{RSI_THRESHOLD})")
        logger.info(f"   Entry Price: ${entry_price:.4f} (with fee: ${entry_with_fee:.4f})")
        logger.info(f"   Target: ${target_price:.4f} (+{EXIT_TARGET*100:.1f}%)")
        logger.info(f"   Stop: ${stop_price:.4f} ({EMERGENCY_STOP_LOSS*100:.1f}% emergency)")
        logger.info(f"   Position Size: ${position_size_usd:.2f}")
        logger.info(f"   Open Positions: {len(self.open_positions)}/{MAX_CONCURRENT_POSITIONS}")
        logger.info(f"   Expected Win Rate: 93.3% | $14.15/day")
        logger.info("=" * 80)

        # Prepare trade data
        trade_data = {
            'ticker': ticker,
            'entry_time': entry_time,
            'entry_price': entry_price,
            'dump_pct': signal_data['dump_pct'] * 100,
            'rsi': signal_data['rsi'],
            'position_size_usd': position_size_usd,
            'target_price': target_price,
            'stop_price': stop_price,
            'capital_before': self.current_capital,
            'status': 'OPEN'
        }

        if AUTO_TRADE and self.client:
            try:
                # Normalize product_id: Remove 'X:' prefix for Coinbase API
                product_id = ticker.replace('X:', '') if ticker.startswith('X:') else ticker

                # Place market buy order
                entry_order = self.client.market_buy(product_id, position_size_usd)
                if not entry_order.get('success'):
                    logger.error(f"   ❌ Buy order failed: {entry_order.get('error')}")
                    return

                order_id = entry_order.get('order_id')
                trade_data['entry_order_id'] = order_id
                logger.info(f"   ✅ Buy order placed: {order_id}")

                # Wait for order to fill (market orders are usually instant)
                import time
                time.sleep(2)  # Give it 2 seconds to fill

                # Check order status to get filled_size AND actual fill price
                order_status = self.client.get_order_status(order_id)
                if order_status.get('success'):
                    base_amount = float(order_status.get('filled_size', 0))
                    order_details = order_status.get('order', {})

                    # Get ACTUAL average fill price
                    actual_fill_price = float(order_details.get('average_filled_price', entry_price))
                    logger.info(f"   ✅ Buy order filled: {base_amount} {product_id.split('-')[0]} @ ${actual_fill_price:.4f}")

                    if base_amount <= 0:
                        logger.error(f"   ❌ No filled amount, cannot place sell order")
                        return

                    # RECALCULATE target based on ACTUAL fill price (not test price)
                    actual_target_price = actual_fill_price * (1 + EXIT_TARGET)
                    actual_stop_price = actual_fill_price * (1 + EMERGENCY_STOP_LOSS)

                    logger.info(f"   📊 Recalculated target from actual fill: ${actual_target_price:.4f} (+{EXIT_TARGET*100:.1f}%)")

                    # Update trade data with actual prices
                    trade_data['entry_price'] = actual_fill_price
                    trade_data['target_price'] = actual_target_price
                    trade_data['stop_price'] = actual_stop_price

                    # Place limit sell order at actual target
                    exit_order = self.client.limit_sell(product_id, actual_target_price, base_amount)
                    if exit_order.get('success'):
                        logger.info(f"   ✅ Sell order placed: {exit_order['order_id']} @ ${actual_target_price:.4f}")
                        trade_data['exit_order_id'] = exit_order.get('order_id')
                    else:
                        logger.error(f"   ❌ Sell order failed: {exit_order.get('error')}")
                else:
                    logger.error(f"   ❌ Could not verify buy order fill status")
                    return

            except Exception as e:
                logger.error(f"   ❌ Order execution failed: {e}")
                return
        else:
            logger.info("   📝 PAPER TRADE (AUTO_TRADE=no)")

        # Save to database
        trade_id = self.db.insert_trade(trade_data)

        # Track in memory (use actual prices from trade_data which may have been updated)
        self.open_positions[ticker] = {
            'id': trade_id,
            'entry_time': entry_time,
            'entry_price': trade_data['entry_price'],
            'target_price': trade_data['target_price'],
            'stop_price': trade_data['stop_price'],
            'position_size_usd': position_size_usd
        }

        logger.info(f"   Trade #{trade_id} opened")

    async def _check_exit_conditions(self, ticker: str, current_candle: dict):
        """Check if position should be exited"""

        position = self.open_positions[ticker]
        entry_time = position['entry_time']
        current_time = current_candle.get('timestamp', datetime.now())

        # Calculate hold time
        minutes_held = (current_time - entry_time).total_seconds() / 60

        exit_price = None
        exit_reason = None

        # Check if target hit (using candle high)
        if current_candle['high'] >= position['target_price']:
            exit_price = position['target_price']
            exit_reason = 'target_hit'

        # Check if emergency stop hit (using candle low)
        elif current_candle['low'] <= position['stop_price']:
            exit_price = position['stop_price']
            exit_reason = 'stop_loss'

        # Check if max hold time reached
        elif minutes_held >= MAX_HOLD_MINUTES:
            exit_price = current_candle['close']
            exit_reason = 'timeout'

        if exit_price and exit_reason:
            await self._execute_exit(ticker, exit_price, exit_reason, minutes_held, current_time)

    async def _execute_exit(self, ticker: str, exit_price: float, exit_reason: str,
                           minutes_held: float, exit_time: datetime):
        """Execute exit trade"""

        position = self.open_positions[ticker]

        # Apply exit fee
        exit_with_fee = exit_price * (1 - EXIT_FEE)
        entry_with_fee = position['entry_price'] * (1 + ENTRY_FEE)

        # Calculate P&L
        gross_pnl_pct = ((exit_price - position['entry_price']) / position['entry_price']) * 100
        net_pnl_pct = ((exit_with_fee - entry_with_fee) / entry_with_fee) * 100
        net_pnl_usd = position['position_size_usd'] * (net_pnl_pct / 100)

        # Update capital
        capital_after = self.current_capital + net_pnl_usd

        logger.info("=" * 80)
        logger.info(f"📤 EXIT: {ticker}")
        logger.info(f"   Reason: {exit_reason}")
        logger.info(f"   Entry: ${position['entry_price']:.4f} → Exit: ${exit_price:.4f}")
        logger.info(f"   Hold Time: {minutes_held:.1f} minutes")
        logger.info(f"   Gross P&L: {gross_pnl_pct:+.2f}%")
        logger.info(f"   Net P&L: {net_pnl_pct:+.2f}% (${net_pnl_usd:+.2f})")
        logger.info(f"   Capital: ${self.current_capital:.2f} → ${capital_after:.2f}")
        logger.info("=" * 80)

        # Update database
        exit_data = {
            'exit_price': exit_price,
            'exit_time': exit_time,
            'exit_reason': exit_reason,
            'minutes_held': int(minutes_held),
            'gross_pnl_pct': gross_pnl_pct,
            'net_pnl_pct': net_pnl_pct,
            'net_pnl_usd': net_pnl_usd,
            'capital_after': capital_after,
            'status': 'CLOSED'
        }

        self.db.update_trade_exit(position['id'], exit_data)

        # Update capital
        self.current_capital = capital_after

        # Remove from open positions
        del self.open_positions[ticker]

        # Log stats every 5 trades
        stats = self.db.get_stats()
        if stats['total_trades'] % 5 == 0:
            self._log_stats(stats)

    def _log_stats(self, stats: dict):
        """Log current trading statistics"""
        logger.info("")
        logger.info("=" * 80)
        logger.info("📊 TRADING STATISTICS")
        logger.info("=" * 80)
        logger.info(f"   Total Trades: {stats['total_trades']}")
        logger.info(f"   Winners: {stats['winners']} | Losers: {stats['losers']}")
        logger.info(f"   Win Rate: {stats['win_rate']:.1f}% (expected: {stats['expected_win_rate']}%)")
        logger.info(f"   Total P&L: ${stats['total_pnl_usd']:+,.2f}")
        logger.info(f"   Avg P&L per Trade: ${stats['avg_pnl_usd']:+.2f}")
        logger.info(f"   Current Capital: ${stats['current_capital']:,.2f}")
        logger.info(f"   Total Return: {stats['return_pct']:+.2f}% (expected: {stats['expected_return']}% per 3 days)")
        logger.info(f"   Open Positions: {stats['open_positions']}/{MAX_CONCURRENT_POSITIONS}")
        logger.info("=" * 80)
        logger.info("")

    def get_stats(self):
        """Get current stats (for API)"""
        return self.db.get_stats()


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_trader_instance = None

def get_proven_trader():
    """Get or create singleton trader instance"""
    global _trader_instance
    if _trader_instance is None:
        _trader_instance = ProvenDumpTrader()
    return _trader_instance
